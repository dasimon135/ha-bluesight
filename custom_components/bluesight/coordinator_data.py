"""Pure snapshot container + assembly for BlueSight.

No Home Assistant dependency: the whole correlation/assembly step lives here
so it is fully unit-testable with plain pytest. The ``DataUpdateCoordinator``
subclass stays a thin shell that only feeds this function a snapshot.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from .detector import (
    detect_bond_lost,
    detect_deadlocks,
    detect_ghost_slots,
    detect_idle_slots,
    detect_offline_proxies,
    detect_reboot_storm,
    detect_stalled_proxies,
    detect_storm,
)
from .incident_policy import dedupe_incidents
from .model import Incident, ProxyHealth, ProxySlots
from .rendering import Catalogue, plural_count, render
from .telemetry import CounterDeltas, ProxyTelemetry
from .window import FailureWindow


@dataclass(frozen=True, slots=True)
class BlueSightData:
    proxies: list[ProxySlots] = field(default_factory=list)
    incidents: list[Incident] = field(default_factory=list)
    proxies_health: list[ProxyHealth] = field(default_factory=list)
    # True once an availability lookup has failed: ghost-slot detection is then
    # biased toward "alive" and its verdicts must be read with that in mind.
    # Surfaced on the incident sensor and in diagnostics so a broken signal is
    # observable instead of silently reading as "nothing wrong".
    availability_degraded: bool = False
    # What each proxy reported this snapshot, carried verbatim so diagnostics
    # and the card can show the raw readings the verdicts were drawn from. A
    # proxy without the ESPHome component is simply absent from this list.
    telemetry: list[ProxyTelemetry] = field(default_factory=list)
    # What Home Assistant calls each peripheral, keyed by canonical address.
    # Presentation only -- no detector reads it, and a name is never evidence.
    # Covers every address the registry can account for via Bluetooth, so an
    # address absent here is one Home Assistant cannot name (see
    # `device_index.DeviceIndex.peripherals`).
    device_names: dict[str, str] = field(default_factory=dict)
    # What to *call* each proxy, keyed by canonical source. The same map the
    # detectors render `{proxy}` from, carried through so the card names a
    # proxy identically to the sentence beside it.
    proxy_display_names: dict[str, str] = field(default_factory=dict)


def build_triage_data(
    proxies: list[ProxySlots],
    availability: dict[str, bool],
    storm_window: FailureWindow,
    *,
    proxies_health: list[ProxyHealth] | None = None,
    known_sources: set[str] | None = None,
    reboot_window: FailureWindow | None = None,
    stalled_threshold_s: float = 180.0,
    offline_for: dict[str, float] | None = None,
    offline_grace_s: float = 0.0,
    availability_degraded: bool = False,
    catalogue: Catalogue | None = None,
    telemetry: list[ProxyTelemetry] | None = None,
    counter_deltas: CounterDeltas | None = None,
    # Mirrors `const.DEFAULT_IDLE_SLOT_THRESHOLD_S` by value and deliberately
    # not by import: this module is pure assembly, owns no configuration, and
    # its one production caller always passes the option explicitly. The
    # mirror is not left to memory either -- `test_coordinator_data` pins this
    # default to the constant -- because the alternative is a suite quietly
    # exercising a threshold no install runs at. `stalled_threshold_s` above
    # mirrors its constant the same way; `offline_grace_s` does not mirror
    # anything, its 0.0 being the neutral "no grace period" value rather than
    # a copy of the shipped default.
    idle_threshold_s: float = 1800.0,
    proxy_names: dict[str, str] | None = None,
    managed_addresses: set[str] | None = None,
    # Presentation only, and deliberately the last argument: no detector reads
    # it, so it cannot change a verdict -- it rides through to the snapshot for
    # the incident sensor to publish beside each address.
    device_names: dict[str, str] | None = None,
) -> BlueSightData:
    """Pure assembly: run all detectors over a snapshot + the rolling failure
    windows and return the combined incident list. No HA, no I/O.

    Incidents are emitted independently: one address may surface as several
    kinds at once (deadlock + ghost + storm). Any dedup/precedence policy is
    the notification layer's job (Task 10), not this assembly step's.

    Detectors emit a translation key and parameters, never prose. With a
    ``catalogue`` the incidents come back with ``detail`` rendered in that
    language; without one they are returned exactly as the detectors built
    them, which is the honest default for a pure function and keeps every
    detector test independent of any catalogue.

    ``telemetry`` holds one entry per proxy running the BlueSight ESPHome
    component. A real fleet is mixed, so the storm heuristic is replaced **per
    proxy and never globally**: a proxy that reports SMP counters contributes
    measured failures, a proxy that does not keeps contributing inferred ones
    (recorded into ``storm_window`` by the caller), and both land in the *same*
    window. One window means one threshold and one storm concept -- only
    ``Incident.evidence`` and the named ``sources`` differ.

    ``managed_addresses`` must be the addresses Home Assistant can *judge* --
    those resolving to a device in the registry -- and never the keys of
    ``availability``, which the coordinator fills for every allocated address
    with unknown ones biased to "alive". Passing the latter would stand
    :func:`detect_idle_slots` down for exactly the unmanaged devices it exists
    to cover, and nothing would look wrong.

    That detector reads ``proxies`` as well, and needs both: the firmware
    reports every GATT connection on its node, so an idle reading is judged
    only where habluetooth says Home Assistant holds a slot for it *and* the
    registry cannot judge the device itself.
    """
    proxies_health = proxies_health or []
    known_sources = known_sources or set()
    telemetry = telemetry or []
    proxy_names = proxy_names or {}
    incidents: list[Incident] = []
    # Slot-layer incidents
    incidents += detect_deadlocks(proxies)
    incidents += detect_ghost_slots(proxies, availability, proxy_names)
    # Measured SMP failures, fed into the same rolling window the heuristic
    # uses. Feeding one window keeps a single storm threshold and a single
    # storm concept: only the evidence label differs. The proxy that measured
    # each failure is recorded with it, so attribution outlives the snapshot
    # the counter moved in and cannot be lost to an address-spelling
    # disagreement between the two routes into this window.
    if counter_deltas is not None:
        for tel in telemetry:
            for address, count in counter_deltas.update(
                tel.source, tel.smp_failures
            ).items():
                # Cap the replay. `count` is a firmware-supplied delta with no
                # upper bound, so a corrupt-but-well-formed 4294967295 would
                # spin 4.3 billion iterations on Home Assistant's event loop.
                # `detect_storm` fires at `count >= window.threshold`, so
                # anything at or above the threshold is already a storm and the
                # extra iterations buy nothing. The trade: the incident's
                # reported count understates a genuinely huge burst.
                for _ in range(min(count, storm_window.threshold)):
                    storm_window.record(address, tel.source)
    for addr in storm_window.addresses():
        inc = detect_storm(addr, storm_window)
        if inc is None:
            continue
        # Every proxy that measured a live failure for this address, not just
        # the last one heard from: one device failing on two proxies is worse
        # news than on one, and it is the second proxy that says so.
        #
        # A window holding both kinds of event labels "smp" and names the
        # proxies that measured theirs. The heuristic can name nobody -- a
        # released slot does not say which proxy dropped it -- so there is no
        # fuller answer to give, and the measured half is the stronger claim
        # and the actionable one.
        #
        # This attribution can vanish under a storm that is still open: the
        # measured events age out of the window while inferred ones hold the
        # count above threshold, and the incident drops back to "heuristic"
        # with no sources. That is honest -- the evidence really is gone -- and
        # it does not re-alert, because `Incident.key` excludes `sources` for
        # `STORM` (see `KINDS_WHOSE_SOURCES_ARE_EVIDENCE` in `model`).
        sources = storm_window.sources(addr)
        if sources:
            inc = replace(inc, sources=sources, evidence="smp")
        incidents.append(inc)
    incidents += detect_bond_lost(telemetry, proxy_names)
    # `proxies` is handed in whole rather than as a pre-built per-source
    # allocated map: it is the same plain snapshot `detect_deadlocks` and
    # `detect_ghost_slots` already take, there is exactly one thing a caller
    # could pass, and the canonicalisation of habluetooth's raw address list
    # stays inside the detector where its own tests reach it. A derived map
    # would put that step here, in the one place no detector test covers, and
    # would be a second set-shaped argument next to `managed_addresses` --
    # which is precisely the pair this call site has already been mixed up
    # once for.
    incidents += detect_idle_slots(
        telemetry, proxies, managed_addresses or set(), idle_threshold_s, proxy_names
    )
    # Proxy-health incidents
    incidents += detect_offline_proxies(
        proxies_health, known_sources, offline_for, offline_grace_s
    )
    incidents += detect_stalled_proxies(proxies_health, stalled_threshold_s)
    if reboot_window is not None:
        for src in reboot_window.addresses():
            inc = detect_reboot_storm(src, reboot_window)
            if inc is not None:
                incidents.append(inc)
    # Precedence, applied HERE and not only by whoever consumes the list.
    #
    # `dedupe_incidents` decides which of two overlapping verdicts is the one
    # worth raising -- a missing pairing key over the storm it causes, a
    # deadlock over the ghost slot that is what a deadlock looks like. It used
    # to be applied by `notify` and by `diagnostics` and by nothing else, so
    # the rules governed push notifications while `binary_sensor` published the
    # raw list: the card drew two rows for one fault, `incident_count` counted
    # it twice, and every automation keyed on that count inherited the error.
    # A real fleet found it -- one thermostat rendered as both a storm and a
    # bond_lost, side by side, naming the same proxy.
    #
    # Before rendering, so no prose is composed for an incident about to be
    # dropped. The two remaining call sites now operate on an already-deduped
    # list and are left in place: the function is idempotent, and each has
    # tests that pin the precedence at its own layer.
    incidents = dedupe_incidents(incidents)
    # `detail` is a published contract: it lands in the `incidents` attribute
    # of `binary_sensor.bluesight_incident`, and user automations format push
    # notifications from it. Rendering it here -- once, where the incident
    # list is assembled -- is what keeps that automation producing prose.
    if catalogue is not None:
        incidents = [
            replace(
                i,
                detail=render(
                    i.detail_key,
                    i.detail_params,
                    catalogue,
                    count=plural_count(i.detail_params),
                ),
            )
            if i.detail_key
            else i
            for i in incidents
        ]
    return BlueSightData(
        proxies=proxies,
        incidents=incidents,
        proxies_health=proxies_health,
        availability_degraded=availability_degraded,
        telemetry=telemetry,
        device_names=dict(device_names or {}),
        proxy_display_names=dict(proxy_names or {}),
    )
