"""How long a proxy spends with nowhere left to put a connection.

Pure logic with an injected clock, like :mod:`.window`; no Home Assistant and
no wall clock, so the whole thing is testable with plain pytest.

This measures a **pressure**, not a fault, and the difference is the reason the
module exists. A saturated proxy is not broken: every slot it holds is doing
useful work, and no detector should raise anything. But the next device that
needs that proxy will not get in, and it will go ``unavailable`` with no error
and no log line -- which is precisely the symptom this integration exists to
explain. Saturation is that symptom, visible before it happens, and it is the
one signal here that nothing else in Home Assistant can produce: it needs
per-proxy slot accounting.

**Nothing judges this yet, deliberately.** The point at which "busy" becomes
"too busy" is not knowable from one fleet -- a proxy dedicated to three
permanent connections is saturated *by design*, not in trouble. Inventing a
threshold now would repeat the mistake ``idle_threshold_s`` had to be measured
out of, where 300s was an argument and 1800s was a measurement. This publishes
the measurement so a threshold can be chosen from data, on more fleets than
one.

Three numbers rather than one, because they answer different questions and a
single ratio hides the difference: ten one-second squeezes and one ten-minute
lockout produce the same ratio and mean nothing alike.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Callable


class SaturationWindow:
    """Time spent with zero free slots, over a rolling window.

    Fed one reading per snapshot via :meth:`record`; only *changes* are
    stored, so a proxy whose state never moves costs two entries rather than
    one per poll for the life of the integration.
    """

    def __init__(self, window_s: float, clock: Callable[[], float]) -> None:
        self.window_s = window_s
        self._clock = clock
        # (timestamp, saturated) at each change of state, oldest first. The
        # entry in force at the window's start is kept even once its timestamp
        # has aged out -- without it there is no way to know what the state
        # *was* at the cutoff, and a stretch that began before the window
        # would vanish rather than being clipped to it.
        self._transitions: deque[tuple[float, bool]] = deque()
        self._started: float | None = None

    # ------------------------------------------------------------- recording

    def record(self, saturated: bool) -> None:
        """Note the proxy's current state. Call once per snapshot."""
        now = self._clock()
        if self._started is None:
            self._started = now
        if not self._transitions or self._transitions[-1][1] != saturated:
            self._transitions.append((now, saturated))
        self._evict(now)

    def _evict(self, now: float) -> None:
        """Drop entries wholly older than the window, keeping the one in force.

        The check is on the *following* entry: an entry is only droppable once
        the next one is itself at or before the cutoff, because until then it
        is still describing the state at the window's start.
        """
        cutoff = now - self.window_s
        while len(self._transitions) >= 2 and self._transitions[1][0] <= cutoff:
            self._transitions.popleft()

    # -------------------------------------------------------------- readings

    def observed_s(self) -> float:
        """How much of the window is actually covered by observations.

        Reported alongside :meth:`ratio` because "no pressure" and "no
        evidence" are different claims, and a ratio alone cannot tell them
        apart: a proxy adopted a minute ago has not been quiet for the rest of
        the window, it has not been *watched* for it.
        """
        if self._started is None:
            return 0.0
        now = self._clock()
        return min(now - self._started, self.window_s)

    def ratio(self) -> float:
        """Fraction of the observed span spent with no free slot, 0.0 to 1.0.

        Divided by what was observed rather than by the whole window, so a
        freshly-adopted proxy that has been full since the moment it appeared
        reads as full rather than as barely busy.
        """
        observed = self.observed_s()
        if observed <= 0:
            return 0.0
        return self._saturated_s() / observed

    def longest_s(self) -> float:
        """The longest single stretch at zero free slots inside the window.

        The ratio cannot distinguish ten brief squeezes from one long lockout,
        and only the second is an outage. An open stretch is measured to *now*
        rather than to the last change, so the number does not freeze at the
        exact moment the situation is at its worst.
        """
        return max((end - start for start, end in self._stretches()), default=0.0)

    def episodes(self) -> int:
        """Number of times the proxy filled up inside the window.

        Entries into saturation, not readings: the coordinator records every
        snapshot, so counting readings would measure the poll interval.
        """
        return len(self._stretches())

    # --------------------------------------------------------------- internals

    def _stretches(self) -> list[tuple[float, float]]:
        """Saturated intervals, clipped to the window. Oldest first."""
        now = self._clock()
        cutoff = now - self.window_s
        out: list[tuple[float, float]] = []
        for index, (start, saturated) in enumerate(self._transitions):
            if not saturated:
                continue
            # The last saturated entry has no successor: it is still running,
            # so it ends at `now`.
            is_open = index + 1 == len(self._transitions)
            end = now if is_open else self._transitions[index + 1][0]
            start = max(start, cutoff)
            # A *closed* stretch of zero length has aged fully out and is not
            # an episode any more. An open one of zero length is a proxy that
            # filled up on this very snapshot -- no duration yet, but it is
            # happening, and `episodes` would undercount by dropping it.
            if end > start or is_open:
                out.append((start, max(end, start)))
        return out

    def _saturated_s(self) -> float:
        return sum(end - start for start, end in self._stretches())
