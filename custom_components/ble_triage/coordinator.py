"""Thin DataUpdateCoordinator shell for BLE Triage.

All the interesting correlation logic lives in the pure, HA-free
:mod:`.coordinator_data` module (and the detectors it drives), which is
fully unit-tested under plain pytest. This shell only wires the habluetooth
push/poll surface to that pure function and hands the result to Home
Assistant. Keep it deliberately minimal.
"""
from __future__ import annotations

import logging
import time
from datetime import timedelta

from homeassistant.components.bluetooth import async_address_present
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from . import adapter
from .adapter import SlotAdapter, current_proxy_slots
from .const import DOMAIN
from .coordinator_data import BleTriageData, build_triage_data
from .model import normalize_address
from .window import FailureWindow

_LOGGER = logging.getLogger(__name__)

# Availability-lookup failures we treat as "signal temporarily unavailable"
# rather than crashing the coordinator: the bluetooth integration not yet
# loaded / torn down (RuntimeError, KeyError, AttributeError) or an API
# signature drift (TypeError). Anything outside this set is a genuine bug and
# is allowed to surface instead of being silently swallowed.
_AVAILABILITY_ERRORS = (RuntimeError, KeyError, AttributeError, TypeError)


class BleTriageCoordinator(DataUpdateCoordinator[BleTriageData]):
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

    async def _async_update_data(self) -> BleTriageData:
        """Poll backstop; the push path is the primary trigger."""
        return self._snapshot()

    def _snapshot(self) -> BleTriageData:
        proxies = current_proxy_slots(self._manager, self._name_for)
        # Normalize each allocated address exactly once and key availability by
        # that normalized form, matching what build_triage_data's detectors
        # normalize to internally.
        availability: dict[str, bool] = {}
        for p in proxies:
            for a in p.allocated:
                norm = normalize_address(a)
                availability[norm] = self._is_available(norm)
        self._record_flaps(availability)
        return build_triage_data(proxies, availability, self._window)

    def _record_flaps(self, availability: dict[str, bool]) -> None:
        """Record present->absent transitions of allocated devices as
        failures for the storm window.

        v1 best-effort signal: a slot that was advertising last snapshot and
        is now absent is treated as one connection failure. To be replaced by
        the ESPHome component's raw SMP failure counts in v1.5.
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

    def _is_available(self, address: str) -> bool:
        """v1 availability signal: is the address currently advertising?

        A held slot whose address is not currently present is a ghost-slot
        candidate. If the lookup breaks we fail toward ``True`` (present) on
        purpose: returning ``False`` would fabricate a ghost incident for
        every address, which is just as trust-destroying as hiding real ones.
        Instead we flag the signal as degraded and warn (once) so the
        breakage is loud and observable rather than silently masking incidents.
        """
        try:
            return async_address_present(self.hass, address, connectable=False)
        except _AVAILABILITY_ERRORS:
            if not self._availability_degraded:
                _LOGGER.warning(
                    "BLE Triage availability lookup failed; ghost-slot "
                    "detection is degraded (assuming devices present). "
                    "First failure for %s",
                    address,
                    exc_info=True,
                )
            self._availability_degraded = True
            return True

    async def async_shutdown(self) -> None:
        """Stop the push subscription and tear down the coordinator."""
        self._adapter.stop()
        await super().async_shutdown()
