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
from dataclasses import replace
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from . import adapter
from .adapter import (
    ScannerAdapter,
    SlotAdapter,
    current_proxy_health,
    current_proxy_slots,
)
from .availability import is_device_alive
from .const import (
    DEFAULT_OFFLINE_GRACE_S,
    DEFAULT_REBOOT_THRESHOLD,
    DEFAULT_REBOOT_WINDOW_S,
    DEFAULT_STALLED_THRESHOLD_S,
    DOMAIN,
)
from .coordinator_data import BlueSightData, build_triage_data
from .model import normalize_address
from .rendering import Catalogue
from .storm_signal import ReleaseTracker
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

    #: Strings for Home Assistant's configured language, resolved once at
    #: setup. Declared at class level so an instance built without
    #: ``__init__`` (the shell tests do this deliberately) still snapshots,
    #: with incident details left unrendered rather than crashing.
    catalogue: Catalogue | None = None

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        config_entry: ConfigEntry,
        storm_window_s: float,
        storm_threshold: int,
        poll_interval_s: int,
        stalled_threshold_s: float = DEFAULT_STALLED_THRESHOLD_S,
        reboot_window_s: float = DEFAULT_REBOOT_WINDOW_S,
        reboot_threshold: int = DEFAULT_REBOOT_THRESHOLD,
        offline_grace_s: float = DEFAULT_OFFLINE_GRACE_S,
        catalogue: Catalogue | None = None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=poll_interval_s),
        )
        self.catalogue = catalogue
        self._window = FailureWindow(
            storm_window_s, storm_threshold, clock=time.monotonic
        )
        self._manager = adapter.get_manager()
        self._adapter = SlotAdapter(self._manager, on_change=self._handle_push)
        # Proxy-health wiring (Task 7). Reboots are counted in their own rolling
        # window (a proxy REMOVED event == one reboot signal), reusing the same
        # monotonic clock as the slot window.
        self._stalled_threshold_s = stalled_threshold_s
        self._reboot_window = FailureWindow(
            reboot_window_s, reboot_threshold, clock=time.monotonic
        )
        # Monotonic timestamp of the last snapshot in which each source was
        # seen online, so offline detection only fires for proxies we have
        # actually observed AND only once they have been missing longer than
        # the grace period (an OTA update drops a proxy for ~20-30s).
        self._last_online: dict[str, float] = {}
        self._offline_grace_s = offline_grace_s
        # Last friendly name observed per source, so a proxy that drops out of
        # the health snapshot keeps its name instead of reverting to its MAC.
        self._names: dict[str, str] = {}
        self._scanner_adapter = ScannerAdapter(
            self._manager,
            on_change=self._handle_push,
            on_removed=self._record_reboot,
        )
        # Turns slot releases into connection-failure events for the storm
        # window; see :mod:`.storm_signal` for why a release, not an
        # availability flap, is the observable signal.
        self._release_tracker = ReleaseTracker()
        # Set once if the availability lookup ever fails, so a broken signal
        # is observable instead of masquerading as "all devices present".
        self._availability_degraded = False
        # Set at shutdown so a push already queued on the loop cannot snapshot
        # a torn-down coordinator.
        self._stopped = False

    @property
    def storm_window(self) -> FailureWindow:
        """Rolling connection-failure window (read-only, for diagnostics)."""
        return self._window

    @property
    def reboot_window(self) -> FailureWindow:
        """Rolling proxy-reboot window (read-only, for diagnostics)."""
        return self._reboot_window

    @property
    def tracked_sources(self) -> set[str]:
        """Proxy sources seen online at least once since setup."""
        return set(self._last_online)

    async def async_setup(self) -> None:
        """Take the first snapshot, then start the push subscription.

        Refresh-before-start is deliberate: if the first refresh raises
        (ConfigEntryNotReady), the habluetooth callback was never registered,
        so there is no subscription to leak and no push can interleave with
        setup. HA retries setup from a clean slate.
        """
        await self.async_config_entry_first_refresh()
        self._adapter.start()
        self._scanner_adapter.start()

    def _record_reboot(self, source: str) -> None:
        """Record a proxy REMOVED event as a reboot signal. Scheduled on the
        loop because the habluetooth scanner callback may run in an executor
        thread; mutating the reboot window off-loop would race the snapshot.

        Ordering: ``ScannerAdapter._handle`` schedules this record BEFORE it
        schedules the snapshot (via ``on_change``/``_handle_push``), both from
        the same thread through ``call_soon_threadsafe``, so the loop runs
        record-before-snapshot (FIFO) and the reboot is visible to the very
        snapshot triggered by the same event.

        Skipped while Home Assistant is stopping: shutdown unregisters every
        scanner, which would otherwise book a full set of phantom reboots.
        """
        if self._stopped or self.hass.is_stopping:
            return
        self.hass.loop.call_soon_threadsafe(
            self._reboot_window.record, normalize_address(source)
        )

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
        if self._stopped:
            return
        self.hass.loop.call_soon_threadsafe(self._push_snapshot)

    @callback
    def _push_snapshot(self) -> None:
        """Publish a snapshot from the loop, unless we shut down meanwhile.

        The stopped check is repeated here because ``async_shutdown`` may run
        between the ``call_soon_threadsafe`` scheduling and this callback.
        """
        if self._stopped:
            return
        self.async_set_updated_data(self._snapshot())

    async def _async_update_data(self) -> BlueSightData:
        """Poll backstop; the push path is the primary trigger."""
        return self._snapshot()

    def _snapshot(self) -> BlueSightData:
        now = time.monotonic()
        # Health first: it carries the friendly scanner names that _name_for
        # hands to the slot snapshot below. Normalize sources so they match the
        # reboot-window keys and _last_online (habluetooth yields upper-case,
        # but be defensive).
        health = [
            replace(h, source=normalize_address(h.source))
            for h in current_proxy_health(self._manager)
        ]
        for h in health:
            if h.name:
                self._names[h.source] = h.name
            if h.online:
                self._last_online[h.source] = now
        offline_for = {src: now - seen for src, seen in self._last_online.items()}

        proxies = current_proxy_slots(self._manager, self._name_for)
        # Resolve availability from the device's HA entities rather than its
        # advertising presence: a connected device (holding a slot) stops
        # advertising, so advertisement-presence falsely flagged working,
        # persistently-connected devices as ghost slots. Build the MAC->device
        # index and grab the entity registry once per snapshot and reuse them
        # for every allocated address (avoid O(devices) per address).
        ent_reg = er.async_get(self.hass)
        mac_index = self._build_mac_index()
        # Memoized per snapshot: the release tracker asks about addresses that
        # are no longer allocated, so the answer set is wider than `proxies`.
        verdicts: dict[str, bool] = {}

        def alive(address: str) -> bool:
            norm = normalize_address(address)
            if norm not in verdicts:
                verdicts[norm] = self._device_is_alive(norm, mac_index, ent_reg)
            return verdicts[norm]

        # Normalize each allocated address exactly once and key availability by
        # that normalized form, matching what build_triage_data's detectors
        # normalize to internally.
        allocated = [a for p in proxies for a in p.allocated]
        availability = {normalize_address(a): alive(a) for a in allocated}
        for address in self._release_tracker.update(allocated, alive):
            self._window.record(address)

        return build_triage_data(
            proxies,
            availability,
            self._window,
            proxies_health=health,
            known_sources=set(self._last_online),
            reboot_window=self._reboot_window,
            stalled_threshold_s=self._stalled_threshold_s,
            offline_for=offline_for,
            offline_grace_s=self._offline_grace_s,
            availability_degraded=self._availability_degraded,
            catalogue=self.catalogue,
        )

    def forget_proxy(self, source: str) -> bool:
        """Stop tracking a source, clearing any open ``proxy_offline`` incident.

        A retired or replaced proxy stays in ``_last_online`` forever and would
        otherwise alert as offline for good. Returns True if the source was
        actually being tracked. The caller is responsible for refreshing.
        """
        norm = normalize_address(source)
        self._names.pop(norm, None)
        return self._last_online.pop(norm, None) is not None

    def _name_for(self, source: str) -> str:
        """Friendly name for a proxy MAC, from the last health snapshot.

        Falls back to the raw MAC for a source we have never seen as a
        registered scanner. Names are remembered across snapshots so a proxy
        that drops off the bus keeps the name its entities were created with —
        every entity on a proxy device must report the SAME device name, or the
        device registry ends up storing whichever entity was written last.
        """
        return self._names.get(normalize_address(source), source)

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
            devices = list(dr.async_get(self.hass).devices.values())
            # Two passes, weakest evidence first, so the result never depends on
            # registry iteration order: a MAC-shaped identifier is a convention,
            # an explicit CONNECTION_BLUETOOTH connection is a declaration, and
            # the declaration must win when both name the same address.
            for device in devices:
                for ident in device.identifiers:
                    if _looks_like_mac(ident[1]):
                        index.setdefault(normalize_address(ident[1]), device.id)
            for device in devices:
                for conn in device.connections:
                    if conn[0] == dr.CONNECTION_BLUETOOTH and _looks_like_mac(conn[1]):
                        index[normalize_address(conn[1])] = device.id
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
        """Stop the push subscriptions and tear down the coordinator.

        ``_stopped`` is set before unsubscribing so a push already queued on the
        loop no-ops instead of snapshotting a torn-down coordinator.
        """
        self._stopped = True
        self._scanner_adapter.stop()
        self._adapter.stop()
        await super().async_shutdown()
