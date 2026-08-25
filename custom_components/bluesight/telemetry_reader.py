"""Isolated Home Assistant surface for the BlueSight ESPHome telemetry.

The twin of :mod:`.adapter`: this is the ONLY module that knows how the
telemetry reaches Home Assistant, so a change in that surface touches one file.

Discovery matches on the entity registry's ``original_name``. Not
``entity_id`` -- users rename those. Not ``unique_id`` -- that is the ESPHome
integration's internal format, and coupling to another integration's
implementation detail is what this architecture exists to avoid. Our own
component names these sensors in codegen, so the name is ours and it is stable.

Read-only, and structurally so: the two callables it is handed are a registry
query and a state query. There is no write surface here to misuse.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from typing import Any

from .model import normalize_address
from .telemetry import ProxyTelemetry, parse_addresses, parse_counts, parse_idle_seconds

_LOGGER = logging.getLogger(__name__)

#: Sensor names the ESPHome component publishes. These are the data contract:
#: change one and every already-flashed proxy goes dark. Pinned by
#: ``test_the_sensor_names_are_the_wire_contract`` so that break is a failing
#: test rather than a silent fleet-wide outage.
SMP_NAME = "BlueSight SMP failures"
BONDS_NAME = "BlueSight bonds"
SLOTS_NAME = "BlueSight slots"

#: Iterated to seed the per-name candidate lists, so adding a fourth signal is
#: one constant and one field rather than a scattered edit.
_SENSOR_NAMES = (SMP_NAME, BONDS_NAME, SLOTS_NAME)


def _read[T](
    raws: list[str | None], parse: Callable[[str | None], T | None]
) -> T | None:
    """Return the first candidate that parses to an actual reading.

    Normally there is one candidate and this is just ``parse(raw)``. There are
    two when a device carries a second entity under the same name -- renaming a
    sensor in ESPHome, or re-adopting a node, leaves the old entity behind on
    the same device, enabled and stuck at ``unavailable``. Registry order is
    not ours to rely on, so the tie has to be broken on merit: a reading beats
    a non-reading, and the first reading wins.

    The parser is the sole judge of what counts as a reading. Inspecting the
    raw states here instead would mean a second copy of :mod:`.telemetry`'s
    ``_NO_SIGNAL`` set -- a second source of truth for a wire contract, which
    is exactly what this module exists to prevent.

    Note the direction of the bias. Letting a dead orphan overwrite a live bond
    list would report that a proxy has no bonds when it has them: not a lost
    reading but the opposite of one, and enough to suppress every BOND_LOST
    incident from that proxy, since that detector needs both halves reported.
    """
    for raw in raws:
        reading = parse(raw)
        if reading is not None:
            return reading
    return None


def read_proxy_telemetry(
    source: str,
    device_id: str,
    entries_for_device: Callable[[str], list[Any]],
    state_of: Callable[[str], str | None],
) -> ProxyTelemetry:
    """Read one proxy's telemetry, tolerating a proxy that has none.

    Callables are injected rather than imported so this stays unit-testable
    without a running Home Assistant -- a step beyond :mod:`.adapter`, which
    still needs its one isolated ``habluetooth`` import. Nothing here needs a
    Home Assistant symbol at all. The caller supplies:

    * ``entries_for_device`` -- ``er.async_entries_for_device(registry,
      device_id, include_disabled_entities=False)``. Disabled entities are
      excluded deliberately: a disabled entity has no state, so including one
      would only add a dead candidate to shadow a live one.
    * ``state_of`` -- the entity's state *string*, e.g.
      ``st.state if (st := hass.states.get(entity_id)) else None``.

    ``source`` is canonicalised on the way in, exactly as :mod:`.adapter` does
    for ``ProxySlots.source``. It is a correlation key, not a payload: the
    friendly-name lookup in :func:`.detector.detect_bond_lost` and
    :func:`.detector.detect_idle_slots` is keyed on the canonical form (as are
    ``coordinator._names``, ``ProxySlots.source`` and ``ProxyHealth.source``),
    and a raw source would miss it and name a MAC at the user in every incident
    this proxy raises.

    Every failure mode -- no component, a partial flash, an unreadable
    registry, an unreadable state -- lands on ``None`` for the affected signal,
    never on a zero that would read as "nothing wrong". Failures are contained
    per signal rather than per proxy: one unreadable sensor must not cost the
    two that read fine, mirroring how :func:`.adapter.current_proxy_health`
    skips one unreadable scanner instead of aborting the snapshot.
    """
    source = normalize_address(source)
    try:
        entries = entries_for_device(device_id) or []
    except Exception:  # a broken lookup must not blank the whole snapshot
        # Broad on purpose, and narrower than it looks: the guarded expression
        # is a single call into another integration's surface and nothing of
        # ours. `coordinator._AVAILABILITY_ERRORS` is a named tuple because its
        # `try` wraps our own index-building loop, where a blind catch would
        # hide our own bugs; there is no such code to hide here, and this module
        # exists precisely because that foreign surface may move in ways we did
        # not predict.
        _LOGGER.debug("Telemetry lookup failed for %s", source, exc_info=True)
        return ProxyTelemetry(source)

    candidates: dict[str, list[str | None]] = {name: [] for name in _SENSOR_NAMES}
    for entry in entries:
        # getattr, not attribute access: a registry entry is another
        # integration's object, read as defensively as adapter.py reads
        # habluetooth's scanners.
        name = getattr(entry, "original_name", None)
        if name not in candidates:
            continue
        entity_id = getattr(entry, "entity_id", None)
        if not entity_id:
            _LOGGER.debug("Telemetry entry for %s has no entity_id: %r", source, entry)
            continue
        try:
            candidates[name].append(state_of(entity_id))
        except Exception:  # one unreadable state, not three
            _LOGGER.debug(
                "Telemetry state read failed for %s (%s)",
                entity_id,
                source,
                exc_info=True,
            )

    for name, raws in candidates.items():
        if len(raws) > 1:
            # Worth saying out loud: two entities claiming one signal is a real
            # misconfiguration, and _read is quietly papering over it.
            _LOGGER.debug(
                "Proxy %s has %d entities named %r; using the first that reports",
                source,
                len(raws),
                name,
            )

    # DECIDED (Task 8): the per-poll warning from `telemetry._is_total_rejection`
    # is NOT throttled, here or anywhere, and this is the reasoning rather than
    # an oversight.
    #
    # The house pattern (`coordinator._flag_degraded`) is "warn once, set a
    # flag, surface the flag as state" -- and the surfacing is the load-bearing
    # half, not the muting. A warn-once with nowhere to show the flag is a mute
    # button on a condition that stays broken until someone reflashes, which is
    # strictly worse than noise. Making the flag visible means a
    # `ProxyTelemetry` field, a `BlueSightData` field, an entity attribute and
    # a card row: a new user-facing contract, and one this milestone's later
    # tasks are not expecting. That is a design decision, not a wiring detail,
    # and it does not belong smuggled into the coordinator hookup.
    #
    # The exposure is bounded meanwhile. Total rejection means the firmware and
    # `telemetry.py` disagree about the wire format, and the only firmware that
    # speaks this format is the one in this repository; its hardware
    # verification exercises exactly this parser. A third-party or
    # badly-out-of-date build could still spam, and that is the accepted cost:
    # loud and wrong beats quiet and wrong while the format is this young.
    #
    # Whoever picks this up: the missing piece is a signal, not a dedupe. A
    # `ProxyTelemetry` field reads `None` for "sensor absent" and for "sensor
    # unparseable" alike, so neither this module nor the coordinator can tell
    # them apart without a second copy of `telemetry._NO_SIGNAL`. Have the
    # parsers report rejection where the parse happens; do not re-derive it out
    # here, and do not hold module-level state in this pure module to do it.
    return ProxyTelemetry(
        source=source,
        smp_failures=_read(candidates[SMP_NAME], parse_counts),
        bonds=_read(candidates[BONDS_NAME], parse_addresses),
        slot_idle_seconds=_read(candidates[SLOTS_NAME], parse_idle_seconds),
    )


def read_fleet_telemetry(
    sources: Iterable[str],
    device_id_for: Callable[[str], str | None],
    entries_for_device: Callable[[str], list[Any]],
    state_of: Callable[[str], str | None],
) -> list[ProxyTelemetry]:
    """Read every proxy's telemetry, keeping only the ones that reported.

    The fleet-level half of :func:`read_proxy_telemetry`, kept here rather than
    in the coordinator for the same reason the per-proxy half is: it is the
    knowledge of *how* telemetry reaches Home Assistant, and it is testable
    with three lambdas.

    ``sources`` are habluetooth scanner sources, which arrive from two places
    in the caller (the health snapshot and the allocation snapshot) and overlap
    almost entirely. They are canonicalised and de-duplicated here, preserving
    first-seen order: the result feeds ``Incident`` construction, and an order
    that shifted between snapshots would reshuffle the incident list for no
    reason. De-duplicating matters for more than tidiness -- a source read
    twice would hand :class:`.CounterDeltas` the same counter twice in one
    snapshot, and the second read would book a delta of zero over a baseline
    the first read had just advanced.

    A proxy with no reporting sensor is **left out** rather than included as an
    all-``None`` entry, so ``BlueSightData.telemetry`` means what it says: the
    proxies running the component. A rebooting proxy drops out for the same
    reason (its entities go ``unavailable``), which is exactly right --
    :meth:`.CounterDeltas.update` keeps a baseline it is not told about, so the
    proxy resumes where it left off instead of replaying its whole counter.
    """
    out: list[ProxyTelemetry] = []
    seen: set[str] = set()
    for raw_source in sources:
        source = normalize_address(raw_source)
        if source in seen:
            continue
        seen.add(source)
        device_id = device_id_for(source)
        if device_id is None:
            # Not a proxy Home Assistant has a device for (a local adapter, or
            # a scanner from an integration that registers none). Nothing to
            # read; not an error.
            continue
        telemetry = read_proxy_telemetry(
            source, device_id, entries_for_device, state_of
        )
        if telemetry.has_signal:
            out.append(telemetry)
    return out
