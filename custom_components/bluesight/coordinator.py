"""Thin DataUpdateCoordinator shell for BlueSight.

All the interesting correlation logic lives in the pure, HA-free
:mod:`.coordinator_data` module (and the detectors it drives), which is
fully unit-tested under plain pytest. This shell only wires the habluetooth
push/poll surface to that pure function and hands the result to Home
Assistant. Keep it deliberately minimal.
"""
from __future__ import annotations

import logging
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
    DEFAULT_IDLE_SLOT_THRESHOLD_S,
    DEFAULT_OFFLINE_GRACE_S,
    DEFAULT_REBOOT_THRESHOLD,
    DEFAULT_REBOOT_WINDOW_S,
    DEFAULT_STALLED_THRESHOLD_S,
    DOMAIN,
)
from .coordinator_data import BlueSightData, build_triage_data
from .device_index import (
    DeviceIndex,
    build_device_index,
    build_proxy_index,
    resolve_proxy_names,
)
from .model import DeviceRef, normalize_address
from .rendering import Catalogue
from .storm_signal import ReleaseTracker
from .telemetry import CounterDeltas
from .telemetry_reader import read_fleet_telemetry
from .window import FailureWindow

_LOGGER = logging.getLogger(__name__)

# Availability-lookup failures we treat as "signal temporarily unavailable"
# rather than crashing the coordinator: the device/entity registries not yet
# loaded / torn down (RuntimeError, KeyError, AttributeError) or an API
# signature drift (TypeError). Anything outside this set is a genuine bug and
# is allowed to surface instead of being silently swallowed.
_AVAILABILITY_ERRORS = (RuntimeError, KeyError, AttributeError, TypeError)

# Home Assistant's own record of which device provides which Bluetooth scanner.
# The `bluetooth` integration creates one config entry per external scanner and
# stores the scanner's `source` string alongside the `source_device_id` of the
# device providing it. Literals rather than an import: these live in
# `homeassistant.components.bluetooth.const`, which is that integration's
# private module, and a missing key here costs nothing -- the proxy simply
# falls through to MAC correlation. `bluetooth` is already a manifest
# dependency, so the entries are there to read.
_BLUETOOTH_DOMAIN = "bluetooth"
_CONF_SOURCE = "source"
_CONF_SOURCE_DEVICE_ID = "source_device_id"


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
        idle_threshold_s: float = DEFAULT_IDLE_SLOT_THRESHOLD_S,
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
        # ESPHome proxy telemetry (v1.5). The deltas object is what makes the
        # firmware's monotonic SMP counters usable: it holds one baseline per
        # proxy across snapshots, so a counter that restarts reads as a reboot
        # rather than as a burst of failures. It therefore has to live as long
        # as the coordinator, and is pruned only by ``forget_proxy``.
        self._counter_deltas = CounterDeltas()
        # How long a slot may sit without GATT traffic before the firmware's
        # reading is treated as a stuck slot. Bounded by the options schema;
        # ``detect_idle_slots`` deliberately carries no internal guard.
        self._idle_threshold_s = idle_threshold_s
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
    def counter_baselines(self) -> dict[str, dict[str, int]]:
        """SMP counter baselines per proxy (read-only copy, for diagnostics).

        The copy is made by :attr:`.CounterDeltas.baselines`; nothing reached
        through this property can rearm a counter.
        """
        return self._counter_deltas.baselines

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

        # Resolve availability from the device's HA entities rather than its
        # advertising presence: a connected device (holding a slot) stops
        # advertising, so advertisement-presence falsely flagged working,
        # persistently-connected devices as ghost slots. Build the MAC->device
        # index and grab the entity registry once per snapshot and reuse them
        # for every allocated address (avoid O(devices) per address).
        #
        # Built BEFORE the slot snapshot, not after: the same index also names
        # the devices holding the slots, and `current_proxy_slots` resolves
        # those names as it builds each `ProxySlots`.
        ent_reg = er.async_get(self.hass)
        index = self._build_device_index()
        mac_index = index.peripherals

        def device_for(address: str) -> DeviceRef | None:
            """Who holds this slot, for the card to show under the pips.

            Deliberately the *peripheral* index: a name is a claim about which
            device is on the other end of a BLE connection, and a Wi-Fi MAC is
            not evidence for that. An address it cannot account for gets no
            reference at all, and the card marks it -- which is the diagnostic,
            not a display defect: an address Home Assistant knows nothing about
            is holding one of a handful of connection slots.

            `address` arrives canonicalised from the adapter, matching how the
            index is keyed; normalized again here so this stays correct for any
            caller and costs a string operation.
            """
            device_id = mac_index.get(normalize_address(address))
            if device_id is None:
                return None
            return DeviceRef(name=index.names.get(device_id, ""), device_id=device_id)

        # `_name_for` and NOT the resolved display name: `ProxySlots.name` is
        # what the proxy's Home Assistant device is created with, and feeding
        # a registry-derived name back into device creation would write the
        # user's rename into the device's `name` field -- after which clearing
        # the rename would restore the rename. See `_display_names_for`.
        proxies = current_proxy_slots(self._manager, self._name_for, device_for)
        # What to *say* about each proxy, resolved once for every detector
        # that names one.
        display_names = self._display_names_for(index)
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

        # Proxy telemetry, read off each proxy's own Home Assistant device.
        # Sources come from the health snapshot first (every registered
        # scanner, the superset) and then from the allocation snapshot, so a
        # proxy that somehow reports allocations without a scanner entry is
        # still read; ``read_fleet_telemetry`` canonicalises and de-duplicates.
        # Both registry handles are hoisted: ``ent_reg`` and ``index`` are
        # built once per snapshot above and merely closed over here, so the
        # per-proxy work is one dict lookup plus one entity-registry query.
        proxy_index = build_proxy_index(self._scanner_device_records(), index.proxies)
        telemetry = read_fleet_telemetry(
            [h.source for h in health] + [p.source for p in proxies],
            proxy_index.get,
            lambda device_id: er.async_entries_for_device(
                ent_reg, device_id, include_disabled_entities=False
            ),
            lambda entity_id: (
                s.state if (s := self.hass.states.get(entity_id)) else None
            ),
        )

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
            telemetry=telemetry,
            counter_deltas=self._counter_deltas,
            idle_threshold_s=self._idle_threshold_s,
            # A fresh dict per snapshot: `_names` keeps growing across
            # snapshots and the detectors must not be handed a mapping that
            # mutates under them. `resolve_proxy_names` builds one.
            proxy_names=display_names,
            # Every address Home Assistant can resolve to a device -- NOT the
            # keys of `availability`, which cover every *allocated* address with
            # the unmanaged ones biased to "alive"; passing those would stand
            # `detect_idle_slots` down for exactly the devices it exists to
            # cover. Nor the allocated subset of the index: allocation is a
            # separate question that `detect_idle_slots` asks separately, per
            # proxy, from `proxies` above -- an address habluetooth does not
            # list as allocated is not thereby *unmanaged*, and narrowing this
            # set by allocation would call a registry device unmanaged the
            # moment its slot went missing from a snapshot (a stale text sensor
            # between publishes is the ordinary cause) and hand it to the
            # detector that judges devices Home Assistant cannot judge. See
            # `DeviceIndex.managed_addresses`.
            managed_addresses=index.managed_addresses,
            # Deliberately the *peripheral* index, for the same reason
            # `device_for` uses it: a name is a claim about which device is on
            # the other end of a BLE link, and a Wi-Fi MAC is not evidence for
            # that. Addresses it cannot account for are simply absent, and the
            # incident sensor publishes "" for them.
            device_names={
                address: index.names.get(device_id, "")
                for address, device_id in index.peripherals.items()
            },
        )

    def forget_proxy(self, source: str) -> bool:
        """Stop tracking a source, clearing any open ``proxy_offline`` incident.

        A retired or replaced proxy stays in ``_last_online`` forever and would
        otherwise alert as offline for good. Returns True if the source was
        actually being tracked. The caller is responsible for refreshing.
        """
        norm = normalize_address(source)
        self._names.pop(norm, None)
        # A retired proxy must not keep a counter baseline. A replacement that
        # reuses the MAC would otherwise inherit a stranger's counter and
        # replay the whole difference as a burst of measured failures.
        self._counter_deltas.forget(norm)
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

    def _display_names_for(self, index: DeviceIndex) -> dict[str, str]:
        """What to *call* each proxy in incident text, per snapshot.

        habluetooth's scanner name for a current ESPHome proxy is a node name
        with its MAC glued on -- "atomebuanderie (D0:CF:13:0F:05:5A)". It is a
        reasonable device name, seen once; it is unreadable inside a
        ``bond_lost`` detail, which names the proxy twice on purpose (where it
        failed, and where to re-pair, because Home Assistant picks the route
        and "re-pair the device" would not be advice). When the user has
        renamed the proxy's BlueSight device, that name is what the card, the
        proxy's sensors and everything else in Home Assistant already show, so
        it is what the sentence must say.

        Resolved at *render* time and never fed back into device creation.
        ``_name_for`` -- the resolver `current_proxy_slots` fills
        ``ProxySlots.name`` from, which `sensor` and `binary_sensor` pass to
        ``DeviceInfo`` -- deliberately still answers the habluetooth name. Two
        reasons, and either alone would settle it:

        * Home Assistant never writes ``name_by_user``; an integration writes
          ``name``. Naming the device from its own ``name_by_user`` would copy
          the rename into ``name``, and a user who then cleared the rename
          would get the rename back. The way out of the loop is not to enter
          it: the field an integration owns keeps the value that integration
          knows.
        * Every entity on a proxy device must report the SAME device name (see
          ``_name_for``). Entities are constructed at different times, and a
          rename landing between two constructions would give the registry two
          different answers to store. The habluetooth name is one answer for
          all of them.

        Names for proxies no scanner currently reports are carried through
        rather than dropped: a retired proxy's incident stays readable, and
        the detectors only look up sources they hold.
        """
        return resolve_proxy_names(self._names, index.proxy_user_names)

    def _build_device_index(self) -> DeviceIndex:
        """Index the device registry by MAC; see :mod:`.device_index`.

        Only the Home Assistant half of the job lives here: fetching the
        devices and naming the two connection types. The rule itself is pure
        and unit-tested without Home Assistant, which matters because it is
        where two subsystems' idea of "the address of a thing" must agree --
        habluetooth's peripheral addresses AND its scanner sources, the latter
        being the ESPHome device's *network* MAC.

        Built once per snapshot and reused for every allocated address and
        every proxy. On a registry-lookup failure we fail toward empty indexes
        -- every device reads as alive, and no proxy resolves, so telemetry is
        simply absent for that snapshot -- and flag the signal as degraded,
        matching the rest of the availability path.
        """
        try:
            return build_device_index(
                dr.async_get(self.hass).devices.values(),
                bluetooth_connection=dr.CONNECTION_BLUETOOTH,
                network_connection=dr.CONNECTION_NETWORK_MAC,
                # Also read back what the user renamed *our own* per-proxy
                # devices to, so incident text calls a proxy what the card
                # and its sensors call it. See `_display_names_for`.
                own_domain=DOMAIN,
            )
        except _AVAILABILITY_ERRORS:
            self._flag_degraded("<device index build>")
            return DeviceIndex(peripherals={}, proxies={})

    def _scanner_device_records(self) -> list[tuple[str | None, str | None]]:
        """``(source, device_id)`` for every Bluetooth scanner config entry.

        This is how a proxy is resolved to its Home Assistant device: an
        identity Home Assistant already records, rather than a MAC correlation
        that a current ESPHome proxy would lose (see
        :func:`.device_index.build_proxy_index`). Read once per snapshot.

        A failure here is **not** flagged as a degraded availability signal: it
        costs telemetry, not ghost detection, and the MAC fallback still
        answers for older proxies. Debug-logged and shrugged off.
        """
        try:
            return [
                (
                    entry.data.get(_CONF_SOURCE),
                    entry.data.get(_CONF_SOURCE_DEVICE_ID),
                )
                for entry in self.hass.config_entries.async_entries(_BLUETOOTH_DOMAIN)
            ]
        except _AVAILABILITY_ERRORS:
            _LOGGER.debug(
                "Could not read the Bluetooth scanner config entries; falling "
                "back to MAC correlation for proxy telemetry",
                exc_info=True,
            )
            return []

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
