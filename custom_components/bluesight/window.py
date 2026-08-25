"""Rolling time-window bookkeeping for connection failures.

Pure logic with an injected clock for deterministic testing; no Home
Assistant or wall-clock dependency.
"""
from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Callable


class FailureWindow:
    def __init__(self, window_s: float, threshold: int, clock: Callable[[], float]):
        self.window_s = window_s
        self.threshold = threshold
        self._clock = clock
        # (timestamp, source) per event. ``source`` is who *observed* it, and
        # is None for an inferred event -- see ``record``.
        self._events: dict[str, deque[tuple[float, str | None]]] = defaultdict(
            deque
        )

    def record(self, address: str, source: str | None = None) -> None:
        """Book one event, optionally naming the proxy that measured it.

        ``source`` carries provenance *with the event* rather than beside it,
        and that placement is the point. A caller holding provenance in its
        own dict would have to rebuild that dict every snapshot from whatever
        it learned in that snapshot -- but a burst of failures is over in
        seconds while this window keeps it for minutes, so the attribution
        would evaporate one snapshot after the events it describes, while the
        incident they raise is still open. Stored here, provenance expires
        exactly when the evidence does.

        It also removes a correlation seam: the alternative is a side lookup
        keyed by address, and the measured and inferred addresses arrive by
        different routes, so a spelling disagreement between them would
        silently drop the attribution rather than fail.

        ``None`` means "inferred, not measured" (the release heuristic in
        :mod:`.storm_signal`, which cannot say which proxy dropped the slot).
        """
        self._events[address].append((self._clock(), source))
        self._evict(address)

    def sources(self, address: str) -> list[str]:
        """Proxies that measured a still-live event for ``address``, sorted.

        Empty when every live event was inferred. Sorted, and therefore
        independent of the order proxies were polled in: this list lands in
        ``Incident.sources``, which is published in the incident attribute and
        rendered on the card, so an order-dependent answer would churn the
        published attribute and redraw the card on a fault that never changed.

        It no longer re-*alerts*: ``Incident.key`` ignores ``sources`` for
        ``STORM`` precisely because this attribution expires with the events it
        describes, while inferred failures can keep the incident open past
        them. Sorting stays a requirement all the same -- the card and
        diagnostics read this list directly.
        """
        if address not in self._events:
            return []
        self._evict(address)
        return sorted(
            {src for _ts, src in self._events.get(address, ()) if src is not None}
        )

    def count(self, address: str) -> int:
        # Do not auto-create a key for a never-seen address on read.
        if address not in self._events:
            return 0
        self._evict(address)
        return len(self._events.get(address, ()))

    def addresses(self) -> list[str]:
        """Return a snapshot of currently-tracked addresses.

        Evicts stale events first so a fully-expired address is neither
        reported nor left lingering in the store. Iterates over a copy of the
        keys because ``_evict`` may delete entries. Never creates a key.
        """
        for address in list(self._events):
            self._evict(address)
        return list(self._events)

    def _evict(self, address: str) -> None:
        q = self._events.get(address)
        if q is None:
            return
        cutoff = self._clock() - self.window_s
        while q and q[0][0] < cutoff:
            q.popleft()
        # Self-clean: drop the address entirely once its window is empty so the
        # store does not accumulate stale deques in the 24/7 coordinator loop.
        if not q:
            del self._events[address]
