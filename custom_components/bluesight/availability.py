"""Pure ghost-slot availability decision.

No Home Assistant dependency, so this is fully unit-testable with plain
pytest. The coordinator resolves a BLE address to its Home Assistant device
and the states of that device's entities; this function turns those states
into a single "is the device still alive?" verdict.
"""
from __future__ import annotations

_DEAD_STATES = {"unavailable"}


def is_device_alive(entity_states: list[str] | None) -> bool:
    """Decide whether a BLE device holding a slot is still alive, from the
    states of its Home Assistant entities.

    None  -> device not found in the registry: treat as alive (conservative;
             we don't flag devices we can't judge).
    []    -> device found but has no entities: alive (conservative).
    Dead ONLY if every entity is 'unavailable'. Any other state (including
    'unknown', e.g. buttons) counts as alive, so ghost detection stays high
    precision and never fires on a working, persistently-connected device.
    """
    if entity_states is None:
        return True
    if not entity_states:
        return True
    return not all(s in _DEAD_STATES for s in entity_states)
