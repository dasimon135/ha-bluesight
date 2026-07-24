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
        self._evict(address)
        return len(self._events[address])

    def _evict(self, address: str) -> None:
        cutoff = self._clock() - self.window_s
        q = self._events[address]
        while q and q[0] < cutoff:
            q.popleft()
