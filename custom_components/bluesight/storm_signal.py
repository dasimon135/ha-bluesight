"""Pure connection-failure signal for the pairing-storm detector.

No Home Assistant dependency; fully unit-testable with plain pytest.

Why this exists
---------------
The storm detector needs "this device failed to connect" events. Home Assistant
exposes no SMP/bond failure counter (that lands with the v1.5 ESPHome
component), so v1 has to infer failures from the slot allocations it can see.

The naive signal — "a device that was alive last snapshot is unavailable this
snapshot" — cannot work: a failed connection RELEASES the slot, so the address
disappears from the allocation list entirely and the transition is never
observed. What is observable is the *release itself*:

    a slot released while the device it belonged to is unavailable
    == one failed connection

A healthy poll cycle (connect, read, disconnect) also releases its slot, but
leaves the device's entities available, so it is not counted. A device that is
simply gone (powered off) releases once and then stops churning, so it cannot
reach the storm threshold on its own.

Entity states settle asynchronously, so a device may still read as "alive" at
the exact snapshot its slot is released. Each release is therefore re-checked
once, on the following snapshot: still gone and now unavailable counts as a
failure, back in the allocation list counts as a recovery.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable

from .model import normalize_address


class ReleaseTracker:
    """Turn per-snapshot slot allocations into connection-failure events.

    Feed it the set of currently-allocated addresses plus a callable that
    answers "is this device alive?"; it returns the addresses that just failed.
    Stateful across snapshots, but pure: no clock, no I/O, no HA.
    """

    def __init__(self) -> None:
        # Addresses allocated at the previous snapshot.
        self._previous: set[str] = set()
        # Released addresses that still looked alive when released; each gets
        # exactly one re-check on the next snapshot, then is dropped either way.
        self._pending: set[str] = set()

    def update(
        self, allocated: Iterable[str], is_alive: Callable[[str], bool]
    ) -> list[str]:
        """Advance one snapshot; return the addresses that counted as failures.

        ``is_alive`` is called at most once per address per snapshot and only
        for addresses under judgement, so the caller can keep it lazy.
        """
        current = {normalize_address(a) for a in allocated}
        failures: list[str] = []

        # Re-check last snapshot's undecided releases before computing new ones,
        # so an address released twice in a row is judged once per release.
        for address in sorted(self._pending):
            if address in current:
                continue  # reconnected: not a failure
            if not is_alive(address):
                failures.append(address)
        self._pending = set()

        for address in sorted(self._previous - current):
            if not is_alive(address):
                failures.append(address)
            else:
                # Undecided: entity states may not have settled yet.
                self._pending.add(address)

        self._previous = current
        return failures
