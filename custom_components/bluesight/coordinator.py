"""Thin DataUpdateCoordinator shell for BlueSight.

All the interesting correlation logic lives in the pure, HA-free
:mod:`.coordinator_data` module (and the detectors it drives), which is
fully unit-tested under plain pytest. This shell only wires the habluetooth
push/poll surface to that pure function and hands the result to Home
Assistant. Keep it deliberately minimal.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from . import adapter
from .adapter import SlotAdapter, current_proxy_slots
from .availability import is_device_alive
from .const import DOMAIN
from .coordinator_data import BlueSightData, build_triage_data
from .model import normalize_address
from .window import FailureWindow

_LOGGER = logging.getLogger(__name__)

# Availability-lookup failures we treat as "signal temporarily unavailable"
# rather than crashing the coordinator: the device/entity registries not yet
# loaded / torn down (RuntimeError, KeyError, AttributeError) or an API
# signature drift (TypeError). Anything outside this set is a genuine bug and
# is allowed to surface instead of being silently swallowed.
_AVAILABILITY_ERRORS = (RuntimeError, KeyError, AttributeError, TypeError)

# A canonicalized BLE MAC: six colon-separated hex octets. Used to keep the
# MAC->device index clean of non-MAC identifiers (integrations put all sorts
# of strings in the second identifier element).
_MAC_RE = re.compile(r"^[0-9A-F]{2}(:[0-9A-F]{2}){5}$")


def _looks_like_mac(value: str) -> bool:
    """True if ``value`` is a plausible colon-separated MAC address."""
    return (
        isinstance(value, str)
        and ":" in value
        and _MAC_RE.match(normalize_address(value)) is not None
    )


class BlueSightCoordinator(DataUpdateCoordinator[BlueSightData]):
    """Feed per-proxy slot snapshots through the pure assembly function.

    Updates arrive two ways: a push from habluetooth's allocation callback
    (the fast path) and a slow poll backstop via ``update_interval``.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        config_entry: ConfigEntry,
        storm_window_s: float,
        storm_threshold: int,
        poll_interval_s: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=poll_interval_s),
        )
        self._window = FailureWindow(
            storm_window_s, storm_threshold, clock=time.monotonic
        )
        self._manager = adapter.get_manager()
        self._adapter = SlotAdapter(self._manager, on_change=self._handle_push)
        # Previous snapshot's availability for allocated addresses, used to
        # detect present->absent flaps that feed the storm window (v1 signal).
        self._prev_availability: dict[str, bool] = {}
        # Set once if the availability lookup ever fails, so a broken signal
        # is observable instead of masquerading as "all devices present".
        self._availability_degraded = False

    async def async_setup(self) -> None:
        """Take the first snapshot, then start the push subscription.

        Refresh-before-start is deliberate: if the first refresh raises
        (ConfigEntryNotReady), the habluetooth callback was never registered,
        so there is no subscription to leak and no push can interleave with
        setup. HA retries setup from a clean slate.
        """
        await self.async_config_entry_first_refresh()
        self._adapter.start()

    @callback
    def _handle_push(self) -> None:
        """Allocation-change push handler.

        habluetooth may invoke the registered callback from the event loop or
        an executor thread, so hop back onto the loop before touching
        coordinator state via ``async_set_updated_data``.
        """
        # M3 (deferred): each push produces its own snapshot; coalescing rapid
        # bursts into a single snapshot is an efficiency-only concern, out of
        # scope for v1.
        self.hass.loop.call_soon_threadsafe(
            lambda: self.async_set_updated_data(self._snapshot())
        )

    async def _async_update_data(self) -> BlueSightData:
        """Poll backstop; the push path is the primary trigger."""
        return self._snapshot()

    def _snapshot(self) -> BlueSightData:
        proxies = current_proxy_slots(self._manager, self._name_for)
        # Resolve availability from the device's HA entities rather than its
        # advertising presence: a connected device (holding a slot) stops
        # advertising, so advertisement-presence falsely flagged working,
        # persistently-connected devices as ghost slots. Build the MAC->device
        # index and grab the entity registry once per snapshot and reuse them
        # for every allocated address (avoid O(devices) per address).
        ent_reg = er.async_get(self.hass)
        mac_index = self._build_mac_index()
        # Normalize each allocated address exactly once and key availability by
        # that normalized form, matching what build_triage_data's detectors
        # normalize to internally.
        availability: dict[str, bool] = {}
        for p in proxies:
            for a in p.allocated:
                norm = normalize_address(a)
                availability[norm] = self._device_is_alive(
                    norm, mac_index, ent_reg
                )
        self._record_flaps(availability)
        return build_triage_data(proxies, availability, self._window)

    def _record_flaps(self, availability: dict[str, bool]) -> None:
        """Record alive->dead transitions of allocated devices as failures for
        the storm window.

        v1 best-effort signal: a slot whose device was alive last snapshot and
        whose entities all went ``unavailable`` this snapshot is treated as one
        connection failure. This is a better storm signal than the old
        advertisement flap (it tracks a real device disconnect, not an
        advertising gap of a healthy connected device). To be replaced by the
        ESPHome component's raw SMP failure counts in v1.5.
        """
        for addr, present in availability.items():
            if self._prev_availability.get(addr) is True and present is False:
                self._window.record(addr)
        self._prev_availability = availability

    def _name_for(self, source: str) -> str:
        """Friendly name for a proxy MAC.

        v1: fall back to the raw source MAC. A registry-backed lookup can be
        layered on later without touching the pure assembly path.
        """
        return source

    def _build_mac_index(self) -> dict[str, str]:
        """Build a ``normalized-MAC -> device_id`` index from the device
        registry, scanning both ``connections`` and ``identifiers``.

        A BLE device's MAC may live in ``connections`` as
        ``(CONNECTION_BLUETOOTH, mac)`` (some integrations) or in
        ``identifiers`` as ``(domain, mac)`` (e.g. daikin_madoka). Only
        Bluetooth connections whose value is MAC-shaped are indexed: a device's
        non-Bluetooth connection (e.g. its Wi-Fi ``CONNECTION_NETWORK_MAC``)
        must never enter the index, or an allocated BLE address could collide
        with some dead device's network MAC and be falsely flagged. Identifier
        values are likewise only mapped when MAC-shaped, to avoid polluting the
        index with the many non-MAC identifiers integrations register.

        Built once per snapshot and reused for every allocated address. On a
        registry-lookup failure we fail toward an empty index (every device
        reads as alive) and flag the signal as degraded, matching the rest of
        the availability path.
        """
        try:
            index: dict[str, str] = {}
            for device in dr.async_get(self.hass).devices.values():
                for conn in device.connections:
                    if conn[0] == dr.CONNECTION_BLUETOOTH and _looks_like_mac(conn[1]):
                        index[normalize_address(conn[1])] = device.id
                for ident in device.identifiers:
                    value = ident[1]
                    if _looks_like_mac(value):
                        index[normalize_address(value)] = device.id
            return index
        except _AVAILABILITY_ERRORS:
            self._flag_degraded("<device index build>")
            return {}

    def _device_is_alive(
        self, address: str, mac_index: dict[str, str], ent_reg: er.EntityRegistry
    ) -> bool:
        """Availability signal: is the device holding this slot still alive?

        Resolves the address to its HA device (via the prebuilt MAC index) and
        judges liveness from that device's entity states (see
        :func:`is_device_alive`). Biased toward "alive" on purpose: the point
        is to stop false ghost positives for working, persistently-connected
        devices; genuine slot leaks are still caught exactly by the deadlock
        detector. If the lookup breaks we fail toward alive, flag the signal as
        degraded, and warn (once) so the breakage is loud and observable.
        """
        try:
            device_id = mac_index.get(normalize_address(address))
            if device_id is None:
                return is_device_alive(None)
            states = [
                st.state
                for e in er.async_entries_for_device(
                    ent_reg, device_id, include_disabled_entities=False
                )
                if (st := self.hass.states.get(e.entity_id)) is not None
            ]
            return is_device_alive(states)
        except _AVAILABILITY_ERRORS:
            self._flag_degraded(address)
            return True

    def _flag_degraded(self, address: str) -> None:
        """Warn once and mark the availability signal as degraded."""
        if not self._availability_degraded:
            _LOGGER.warning(
                "BlueSight availability lookup failed; ghost-slot "
                "detection is degraded (assuming devices alive). "
                "First failure for %s",
                address,
                exc_info=True,
            )
        self._availability_degraded = True

    async def async_shutdown(self) -> None:
        """Stop the push subscription and tear down the coordinator."""
        self._adapter.stop()
        await super().async_shutdown()
