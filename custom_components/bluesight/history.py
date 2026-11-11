"""How often each device took a slot, and through how many proxies, in a day.

Pure logic with an injected clock, like :mod:`.saturation`; no Home Assistant.

A device that keeps reconnecting is the early form of most of what BlueSight
detects. A storm is the same device failing faster; a deadlock usually starts
as a device handed between two proxies. The storm window counts *failures*
over a few minutes. Nothing counted the connections themselves, nor noticed a
device moved from proxy to proxy all day long.

**A connection is a slot appearing.** habluetooth allocates the slot as the
attempt starts, so an attempt that fails also counts: this is "how often did
this device go through the connection layer", which is the question. A slot
already held when BlueSight starts is not a connection -- it is new to us, not
new -- so the first snapshot is a baseline and nothing more.

**Published, not judged**, for the reason :mod:`.saturation` gives. A sensor
that wakes every five minutes to report a reading connects 288 times a day by
design, and a device that two proxies hear equally well may change route at
every connection without anything being wrong. The numbers say what happened;
the reader knows what the device is.

Snapshots arrive on every allocation change (debounced by a quarter of a
second) and on the poll, so a connection that opens and closes inside one
debounce is missed. That under-counts a storm, which the storm window covers;
it never invents a connection.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .model import normalize_address


@dataclass(frozen=True, slots=True)
class DeviceHistory:
    """One device's day at the connection layer."""

    #: Slots it took inside the window.
    connections: int
    #: How many of those went through a different proxy from the connection
    #: before -- including one that has aged out of the window, since that is
    #: still where the device was.
    proxy_changes: int
    #: The proxies it connected through inside the window, sorted.
    proxies: tuple[str, ...]


class ConnectionHistory:
    """Every slot appearance per device, over a rolling window."""

    def __init__(self, window_s: float, clock: Callable[[], float]) -> None:
        self.window_s = window_s
        self._clock = clock
        # Held (source, address) pairs at the last snapshot. None until the
        # first one, which is the baseline.
        self._held: set[tuple[str, str]] | None = None
        self._started: float | None = None
        # address -> (timestamp, source) per connection, oldest first.
        self._events: dict[str, deque[tuple[float, str]]] = {}
        # address -> the source of its newest connection that has aged out, so
        # the first connection still in the window is judged against it.
        self._before: dict[str, str] = {}

    def update(self, held: Iterable[tuple[str, str]]) -> None:
        """Advance one snapshot with the slots held now, as (source, address)."""
        now = self._clock()
        current = {
            (normalize_address(source), normalize_address(address))
            for source, address in held
        }
        if self._held is None:
            self._held = current
            self._started = now
            return
        for source, address in sorted(current - self._held):
            self._events.setdefault(address, deque()).append((now, source))
        self._held = current
        self._evict(now)

    def _evict(self, now: float) -> None:
        cutoff = now - self.window_s
        for address in list(self._events):
            events = self._events[address]
            while events and events[0][0] <= cutoff:
                self._before[address] = events.popleft()[1]
            if not events:
                # Nothing left to judge a change against: the device has been
                # quiet for a whole window, and its next connection is a fresh
                # start rather than a move.
                del self._events[address]
                self._before.pop(address, None)

    def for_address(self, address: str) -> DeviceHistory | None:
        """The device's day, or None if it took no slot inside the window."""
        address = normalize_address(address)
        events = self._events.get(address)
        if not events:
            return None
        previous = self._before.get(address)
        changes = 0
        for _, source in events:
            if previous is not None and source != previous:
                changes += 1
            previous = source
        return DeviceHistory(
            connections=len(events),
            proxy_changes=changes,
            proxies=tuple(sorted({source for _, source in events})),
        )

    def all(self) -> dict[str, DeviceHistory]:
        """Every device that took a slot inside the window."""
        out = {}
        for address in self._events:
            reading = self.for_address(address)
            if reading is not None:
                out[address] = reading
        return out

    def observed_s(self) -> float:
        """How much of the window is covered, for the reason saturation gives."""
        if self._started is None:
            return 0.0
        return min(self._clock() - self._started, self.window_s)


def history_attributes(history: DeviceHistory | None) -> dict[str, int] | None:
    """``history`` as published under each entry of ``allocated_devices``."""
    if history is None:
        return None
    return {
        "connections": history.connections,
        "proxy_changes": history.proxy_changes,
    }
