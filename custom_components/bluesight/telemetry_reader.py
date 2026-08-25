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
from collections.abc import Callable
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

    # NOTE (Task 8): `telemetry._is_total_rejection` warns on every parse, so a
    # permanently format-mismatched firmware warns once per poll cycle, forever.
    # It is deliberately not throttled here: this function is pure, and the only
    # state it could hold is module-level, which would survive a config-entry
    # reload and would never be pruned when `forget_proxy` retires a proxy. The
    # coordinator owns exactly that lifecycle (`_names`, `_last_online`, both
    # popped in `forget_proxy`) and already has the house pattern for this in
    # `_flag_degraded`: warn once, set a flag, surface the flag as state instead
    # of repeating it in the log. The missing piece is the signal -- a
    # `ProxyTelemetry` field reads `None` for "sensor absent" and for "sensor
    # unparseable" alike, and the difference cannot be recovered downstream
    # without a second copy of `telemetry._NO_SIGNAL`. So the throttle wants
    # `telemetry.py` to *report* rejection (a per-signal flag, set where the
    # parse happens) rather than a dedupe bolted onto this function.
    return ProxyTelemetry(
        source=source,
        smp_failures=_read(candidates[SMP_NAME], parse_counts),
        bonds=_read(candidates[BONDS_NAME], parse_addresses),
        slot_idle_seconds=_read(candidates[SLOTS_NAME], parse_idle_seconds),
    )
