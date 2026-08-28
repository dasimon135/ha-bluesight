"""Pure detectors for BLE connection-layer incidents.

No Home Assistant dependency; fully unit-testable with plain pytest.
"""
from __future__ import annotations

from collections import defaultdict

from .model import Incident, IncidentKind, ProxyHealth, ProxySlots, normalize_address
from .telemetry import ProxyTelemetry
from .window import FailureWindow

#: The allocated set of a proxy habluetooth reports nothing for. A shared
#: frozen empty set, so the lookup default allocates nothing per snapshot and
#: cannot be mutated by accident.
_NO_SLOTS: frozenset[str] = frozenset()


def detect_deadlocks(proxies: list[ProxySlots]) -> list[Incident]:
    """A single BLE peripheral can be connected to one central at a time.
    An address held by >=2 DISTINCT proxies is a stale duplicate allocation
    (core issue #176516). We correlate over distinct proxy sources so a
    single proxy that lists the same address twice does not fabricate a
    deadlock."""
    by_addr: dict[str, set[str]] = defaultdict(set)
    for p in proxies:
        for addr in p.allocated:
            by_addr[normalize_address(addr)].add(p.source)
    return [
        Incident(
            IncidentKind.DEADLOCK, addr, sorted(sources),
            detail_key="incident.deadlock.detail",
            detail_params={"count": str(len(sources))},
        )
        for addr, sources in by_addr.items() if len(sources) >= 2
    ]


def detect_ghost_slots(
    proxies: list[ProxySlots],
    availability: dict[str, bool],
    names: dict[str, str] | None = None,
) -> list[Incident]:
    """A slot held for a device whose entity is unavailable is likely stale.

    ``names`` maps a canonical proxy source to what the user calls that proxy,
    the same map :func:`detect_bond_lost` and :func:`detect_idle_slots` take.
    It is what fills ``{proxy}``, so every detector that names a proxy names
    it the same way -- the detail and the notification built beside it sit in
    one output and must not call one proxy two things.

    ``ProxySlots.name`` is the fallback and not the source, because it is
    habluetooth's scanner name: correct for creating the proxy's Home
    Assistant *device*, which is exactly what it is used for, but not what the
    user renamed that device to. Falling back to it rather than to the raw
    source keeps this identical to the pre-rename behaviour for a proxy the
    map does not cover, and keeps ``{proxy}`` always present -- the notifier's
    "an unspecified proxy" last resort stays as unreachable as it was.
    """
    avail = {normalize_address(k): v for k, v in availability.items()}
    names = names or {}
    out: list[Incident] = []
    for p in proxies:
        name = names.get(normalize_address(p.source), p.name)
        for addr in p.allocated:
            norm = normalize_address(addr)
            if avail.get(norm, True) is False:
                out.append(Incident(
                    IncidentKind.GHOST_SLOT, norm, [p.source],
                    detail_key="incident.ghost_slot.detail",
                    detail_params={"proxy": name},
                ))
    return out


def detect_offline_proxies(
    proxies: list[ProxyHealth],
    known_sources: set[str],
    offline_for: dict[str, float] | None = None,
    grace_s: float = 0.0,
) -> list[Incident]:
    """A source we have seen online before, now absent from the scanners.

    ``offline_for`` maps a source to how many seconds it has been missing, and
    ``grace_s`` is how long a source may be missing before it is reported. An
    ESPHome proxy unregisters and re-registers on every OTA update and on every
    reload of its config entry, so reporting the very first absent snapshot
    raises (and immediately clears) an alert on entirely routine events. A
    source with no entry in ``offline_for`` is treated as freshly missing.

    The parameters deliberately carry no elapsed time: they are rendered into
    a detail that lands in entity attributes, which would otherwise churn on
    every single snapshot.
    """
    online = {p.source for p in proxies if p.online}
    offline_for = offline_for or {}
    return [
        Incident(
            IncidentKind.PROXY_OFFLINE, src, [src],
            detail_key="incident.proxy_offline.detail",
        )
        for src in sorted(known_sources - online)
        if offline_for.get(src, 0.0) >= grace_s
    ]


def detect_stalled_proxies(
    proxies: list[ProxyHealth], threshold_s: float
) -> list[Incident]:
    """Online scanner that has not seen any advertisement for too long."""
    return [
        Incident(
            IncidentKind.PROXY_STALLED, p.source, [p.source],
            detail_key="incident.proxy_stalled.detail",
            # int() truncates, exactly as the prose it replaces did.
            detail_params={"seconds": str(int(p.seconds_since_detection))},
        )
        for p in proxies
        if p.online and p.seconds_since_detection > threshold_s
    ]


def detect_storm(address: str, window: FailureWindow) -> Incident | None:
    count = window.count(address)
    if count >= window.threshold:
        return Incident(
            IncidentKind.STORM, address, [],
            detail_key="incident.storm.detail",
            detail_params={
                "count": str(count),
                "seconds": str(int(window.window_s)),
            },
        )
    return None


def detect_reboot_storm(source: str, window: FailureWindow) -> Incident | None:
    count = window.count(source)
    if count >= window.threshold:
        return Incident(
            IncidentKind.PROXY_REBOOT_STORM, source, [source],
            detail_key="incident.proxy_reboot_storm.detail",
            detail_params={
                "count": str(count),
                "seconds": str(int(window.window_s)),
            },
        )
    return None


def detect_bond_lost(
    telemetry: list[ProxyTelemetry],
    names: dict[str, str],
    window: FailureWindow,
    threshold: int,
) -> list[Incident]:
    """A device whose pairing keeps failing *now* on a proxy holding no bond.

    This is the one diagnosis that needs the firmware: Home Assistant can see
    neither SMP failures nor the proxy's NVS bond store.

    The failures are read from ``window`` -- the same rolling window
    :func:`detect_storm` uses, filled from the same deltas by the caller -- and
    never from ``ProxyTelemetry.smp_failures``. That field is the firmware's
    counter, monotonic since the proxy booted, so testing it directly made a
    single refusal in a proxy's entire life open an incident that closed only
    on a reboot: a device connected and exchanging normally through the proxy
    that holds its bond stayed flagged indefinitely because some *other* proxy
    had refused it once, long ago. Reading the window puts both halves of the
    measured evidence on one clock, and lets a fault that stopped happening
    stop being reported.

    Two consequences of reading the window rather than the snapshot, both
    wanted. Only *measured* events count, because ``count_by_source`` excludes
    inferred ones -- the release heuristic cannot name the proxy that dropped a
    slot, and this verdict implicates one by name. And a proxy that stops
    publishing its counters for a snapshot while still publishing its bonds
    keeps its incident, because the evidence outlives the snapshot it arrived
    in; the same reasoning that makes ``CounterDeltas`` hold a baseline through
    a blip instead of dropping it.

    ``tel.bonds is None`` still yields nothing, and must: an absent bond list
    cannot distinguish "no bond" from "not told", and asserting the first from
    the second would fire BOND_LOST across a whole fleet over nothing worse
    than a firmware formatting disagreement.

    Bonds are per-central: every proxy keeps its own store, so a device paired
    through one proxy genuinely has no bond on the next, and Home Assistant
    will still route connections there. The verdict is therefore per proxy, and
    so is the threshold -- pooling two proxies' failures would implicate each
    on evidence the other gathered. A bond held elsewhere neither excuses nor
    suppresses the proxy that is failing, which is why the remedy is exact and
    worth stating: re-pair through this specific proxy, not whichever one Home
    Assistant picks next.

    Both sides of the comparison are canonicalised, as everywhere else
    addresses are correlated. The window's keys arrive canonical from
    ``CounterDeltas``, so this guards the seam rather than the wire -- but the
    failure it guards against is the worst one available here: a bond read as
    absent purely because it arrived in the other case would assert the
    opposite of what the proxy reported.
    """
    # Read the window once into a per-proxy view, rather than per proxy into
    # the window: the window is the single source and does not change while
    # this runs, and a fleet is polled far more often than it grows.
    measured: dict[str, dict[str, int]] = {}
    for raw_address in window.addresses():
        address = normalize_address(raw_address)
        for source, count in window.count_by_source(raw_address).items():
            per_proxy = measured.setdefault(source, {})
            # Two spellings of one address would otherwise yield two incidents
            # sharing a key -- a duplicate row that re-alerts as one fault. The
            # higher wins: they are readings of the same counter, so summing
            # would invent failures nobody measured.
            per_proxy[address] = max(per_proxy.get(address, 0), count)
    out: list[Incident] = []
    for tel in telemetry:
        if tel.bonds is None:
            continue
        name = names.get(tel.source, tel.source)
        bonds = {normalize_address(address) for address in tel.bonds}
        for address, count in sorted(measured.get(tel.source, {}).items()):
            if count < threshold or address in bonds:
                continue
            out.append(Incident(
                IncidentKind.BOND_LOST, address, [tel.source],
                detail_key="incident.bond_lost.detail",
                detail_params={
                    "count": str(count),
                    "proxy": name,
                    "seconds": str(int(window.window_s)),
                },
                evidence="smp"))
    return out



def detect_idle_slots(
    telemetry: list[ProxyTelemetry],
    proxies: list[ProxySlots],
    managed_addresses: set[str],
    threshold_s: float,
    names: dict[str, str],
) -> list[Incident]:
    """A slot held with no GATT traffic, for a device Home Assistant cannot judge.

    ``detect_ghost_slots`` decides from entity availability, which only works
    for devices in the registry; an unmanaged peripheral is conservatively
    treated as alive there (see :func:`availability.is_device_alive`, which
    answers "alive" for a device it cannot find). The firmware sees the
    connection itself, so it can measure the silence directly -- the one way to
    judge a device Home Assistant knows nothing about.

    ``proxies`` bounds the whole verdict to Home Assistant's own slots, and it
    is not a refinement: the firmware's slots sensor reports **every GATT
    client connection on the node**, because it watches the controller's event
    stream rather than ``bluetooth_proxy``'s connection table. A node that also
    runs ``ble_client:`` links of its own therefore reports connections Home
    Assistant never asked for, which draw on ``esp32_ble.max_connections`` and
    not on the slots the proxy advertises. Flagging one would be a true
    measurement under a false frame -- ``GHOST_SLOT`` says a *slot* is stuck and
    its remedy says to restart the proxy to free it, and that restart would free
    no slot Home Assistant was waiting on, for a link that is doing its job.

    Nothing of the intended case is lost to the filter. habluetooth tracks an
    allocation per *address*, with no notion of Home Assistant's device
    registry (:attr:`HaBluetoothSlotAllocations.allocated` is literally
    "addresses of connected devices"), so a connection Home Assistant opened
    through a proxy for a device its registry cannot account for is in
    ``allocated`` all the same -- which is exactly this detector's target case.
    What the filter removes is the set of connections that were never Home
    Assistant's slots to begin with.

    The reverse mismatch -- habluetooth has released the allocation while the
    firmware still reports the connection -- resolves to **silence**, which is
    what falling outside ``allocated`` already does. Its ordinary cause is
    staleness, not a leak: the two readings are taken from different places at
    different instants, and a slots sensor between publishes lags a disconnect
    Home Assistant has already booked. Even where the link really is stuck on
    the node, the slot is one Home Assistant considers free and will hand to
    the next device, so the ``GHOST_SLOT`` frame misdescribes it in the same way
    a ``ble_client:`` link does. One snapshot cannot tell those two apart, and
    the common one is routine, so the quiet answer is the honest one.

    ``managed_addresses`` must be the addresses Home Assistant can *judge* --
    those resolving to a device in the registry -- and not merely the ones it
    has seen allocated. The coordinator keys its availability map by every
    allocated address, unmanaged ones included (mapped to "alive" by the rule
    above); handing that map's keys in here would stand this detector down for
    exactly the devices it exists to cover.

    Managed addresses are skipped for a harder reason than tidiness: the
    entity-based verdict is the more semantic signal, and both detectors raise
    ``GHOST_SLOT`` for the same address from the same proxy, so a slot judged
    by both would produce two incidents identical under ``Incident.key`` --
    one fault drawn twice on the card, and one clearance counted as two by the
    policy layer.

    ``threshold_s`` is a duration, and strictly exceeding it is the incident,
    as in :func:`detect_stalled_proxies`. It has to sit above the slowest
    legitimate quiet period on the network: a device that only notifies on
    change can hold a healthy connection in silence for a long time, and that
    -- not a stuck slot -- is the false positive this threshold exists to
    exclude. The caller supplies a validated value; the options schema bounds
    every other duration BlueSight takes, and this one must be bounded too.

    Addresses are canonicalised on every side of both membership tests, as
    everywhere else addresses are correlated -- and here the wire, not merely
    the seam, depends on it. ``managed_addresses`` comes from the device
    registry and the idle readings come from the firmware, so reading a managed
    device as unmanaged would flag a device Home Assistant has judged alive, for
    every such device at once. ``ProxySlots.allocated`` is worse still: it
    carries habluetooth's **raw** spelling, because
    :func:`adapter.current_proxy_slots` normalises ``source`` and hands the
    address list through verbatim. Compared raw against a telemetry address that
    :func:`telemetry.expand_compact_mac` has already canonicalised, a
    lower-case habluetooth would put *every* address outside ``allocated`` and
    silence this detector completely, with nothing to see but an absence.

    The proxy sides are canonicalised for the same reason and not because
    either is known to be raw: ``ProxySlots.source`` is normalised by
    :mod:`.adapter` and ``ProxyTelemetry.source`` by :mod:`.telemetry_reader`,
    which is two modules agreeing rather than one rule, and a mismatch there
    would empty the allocated set for the proxy rather than for one address.
    """
    managed = {normalize_address(address) for address in managed_addresses}
    # Union rather than last-wins, so a snapshot listing one source twice
    # cannot drop the slots of the first entry.
    allocated_by_source: dict[str, set[str]] = {}
    for proxy in proxies:
        allocated_by_source.setdefault(normalize_address(proxy.source), set()).update(
            normalize_address(address) for address in proxy.allocated
        )
    out: list[Incident] = []
    for tel in telemetry:
        if tel.slot_idle_seconds is None:
            continue
        # A proxy habluetooth reports no allocations for holds no Home
        # Assistant slots, so nothing it reports can be a stuck one.
        allocated = allocated_by_source.get(normalize_address(tel.source), _NO_SLOTS)
        name = names.get(tel.source, tel.source)
        # Merged on the normalised key rather than iterated directly, so two
        # spellings of one address cannot yield two incidents sharing a key.
        # The *lowest* reading wins, where `detect_bond_lost` keeps the
        # highest: an SMP counter only climbs, so the larger reading there is
        # the later one, but an idle timer resets to zero on traffic, so the
        # smaller reading here is the fresher observation -- and the one that
        # says the slot is alive.
        idle_seconds: dict[str, float] = {}
        for raw_address, idle in tel.slot_idle_seconds.items():
            address = normalize_address(raw_address)
            previous = idle_seconds.get(address)
            idle_seconds[address] = idle if previous is None else min(previous, idle)
        for address, idle in sorted(idle_seconds.items()):
            if address not in allocated or address in managed or idle <= threshold_s:
                continue
            out.append(Incident(
                IncidentKind.GHOST_SLOT, address, [tel.source],
                detail_key="incident.ghost_slot.idle_detail",
                # int() truncates, as in `detect_stalled_proxies`: the same
                # kind of reading, reported the same way.
                detail_params={"proxy": name, "seconds": str(int(idle))},
                evidence="smp"))
    return out
