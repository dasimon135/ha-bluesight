"""Rolling time-window bookkeeping for connection failures.

Pure logic with an injected clock for deterministic testing; no Home
Assistant or wall-clock dependency.
"""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Callable


class FailureWindow:
    def __init__(self, window_s: float, threshold: int, clock: Callable[[], float]):
        self.window_s = window_s
        self.threshold = threshold
        self._clock = clock
        self._events: dict[str, deque[float]] = defaultdict(deque)

    def record(self, address: str) -> None:
        self._events[address].append(self._clock())
        self._evict(address)

    def count(self, address: str) -> int:
        # Do not auto-create a key for a never-seen address on read.
        if address not in self._events:
            return 0
        self._evict(address)
        return len(self._events.get(address, ()))

    def _evict(self, address: str) -> None:
        q = self._events.get(address)
        if q is None:
            return
        cutoff = self._clock() - self.window_s
        while q and q[0] < cutoff:
            q.popleft()
        # Self-clean: drop the address entirely once its window is empty so the
        # store does not accumulate stale deques in the 24/7 coordinator loop.
        if not q:
            del self._events[address]
