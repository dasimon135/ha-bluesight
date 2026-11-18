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

import math
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from .model import normalize_address

#: Width of one bucket in :meth:`ConnectionHistory.hourly`.
HOUR_S = 3600.0

#: Most stays kept per device. A device that reconnects every few minutes would
#: otherwise add hundreds of entries a day to the saved file, for a timeline
#: nobody can read at that density.
MAX_STAYS = 96

#: Two stays through the same proxy this close are one in the timeline: a flap
#: of a few seconds says nothing the stay around it does not.
MERGE_GAP_S = 60.0


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


@dataclass(frozen=True, slots=True)
class Timeline:
    """Which proxy held one device, when, over the window."""

    #: ``(seconds ago it began, seconds ago it ended, proxy)``, oldest first.
    #: Ended ``0.0`` means it is still held.
    stays: tuple[tuple[float, float, str], ...]
    #: True when the device reconnects so often that the oldest stays were
    #: dropped to keep the history small: the window is then not fully covered.
    truncated: bool


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
        # address -> [start, end | None, source] per stay, oldest first. A stay
        # is a slot held from the moment it appears to the moment it is
        # released, which the connection events above do not record. Kept apart
        # from them so counting connections means what it always did: a slot
        # already held when BlueSight starts opens a stay (it is being held)
        # but is not a connection (it is not new).
        self._stays: dict[str, deque[list]] = {}
        # The stay each held (source, address) pair has open right now.
        self._open: dict[tuple[str, str], list] = {}

    def update(self, held: Iterable[tuple[str, str]]) -> None:
        """Advance one snapshot with the slots held now, as (source, address)."""
        now = self._clock()
        current = {
            (normalize_address(source), normalize_address(address))
            for source, address in held
        }
        if self._held is None:
            self._held = current
            # Kept if a restore already set it: the observation then continues
            # from where the saved history stopped instead of starting over.
            if self._started is None:
                self._started = now
            for pair in sorted(current):
                self._open_stay(pair, now)
            # A restored history can hold events that aged out while Home
            # Assistant was down; this is the first moment to drop them.
            self._evict(now)
            return
        for source, address in sorted(current - self._held):
            self._events.setdefault(address, deque()).append((now, source))
            self._open_stay((source, address), now)
        for pair in sorted(self._held - current):
            stay = self._open.pop(pair, None)
            if stay is not None:
                stay[1] = now
        self._held = current
        self._evict(now)

    def _open_stay(self, pair: tuple[str, str], now: float) -> None:
        source, address = pair
        if pair in self._open:
            return
        stay: list = [now, None, source]
        self._stays.setdefault(address, deque(maxlen=MAX_STAYS)).append(stay)
        self._open[pair] = stay

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
        for address in list(self._stays):
            stays = self._stays[address]
            while stays and stays[0][1] is not None and stays[0][1] <= cutoff:
                stays.popleft()
            if not stays:
                del self._stays[address]

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

    def hourly(self, address: str) -> tuple[int, ...]:
        """Connections per hour over the window, oldest hour first.

        The last bucket is the hour ending now, so the series reads left to
        right as time passes. It is derived from the events the history already
        keeps and restores, which is why a restart does not empty it. A window
        that is not a whole number of hours rounds up, so nothing inside it is
        dropped. Empty for a device that took no slot inside the window.
        """
        events = self._events.get(normalize_address(address))
        if not events:
            return ()
        buckets = max(1, math.ceil(self.window_s / HOUR_S))
        now = self._clock()
        counts = [0] * buckets
        for at, _ in events:
            # A clock that stepped back would give a negative age; the event is
            # then "just now", not a bucket past the end.
            index = buckets - 1 - int(max(0.0, now - at) // HOUR_S)
            if 0 <= index < buckets:
                counts[index] += 1
        return tuple(counts)

    def timeline(self, address: str) -> Timeline | None:
        """Which proxy held the device, and when, or None if none did.

        Ages are measured from now and clipped to the window. Two stays through
        the same proxy less than :data:`MERGE_GAP_S` apart are joined; a stay
        through another proxy never is, since that gap is the story.
        """
        stays = self._stays.get(normalize_address(address))
        if not stays:
            return None
        now = self._clock()
        out: list[tuple[float, float, str]] = []
        for start, end, source in stays:
            to_age = 0.0 if end is None else max(0.0, now - end)
            if to_age >= self.window_s:
                continue
            from_age = min(self.window_s, max(to_age, now - start))
            if out and out[-1][2] == source and out[-1][1] - from_age <= MERGE_GAP_S:
                out[-1] = (out[-1][0], to_age, source)
            else:
                out.append((from_age, to_age, source))
        if not out:
            return None
        return Timeline(stays=tuple(out), truncated=len(stays) >= MAX_STAYS)

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

    # ---------------------------------------------------------- persistence

    def export(self) -> dict[str, Any]:
        """The history as plain JSON, for the store.

        Events are written as *ages*, seconds before now, and never as
        timestamps: this class runs on an injected monotonic clock that starts
        again with every process, so a timestamp from one run means nothing in
        the next. The caller records when it was written, and tells
        :meth:`restore` how long ago that was.
        """
        now = self._clock()
        return {
            "observed_s": self.observed_s(),
            "events": {
                address: [[now - at, source] for at, source in events]
                for address, events in self._events.items()
            },
            "before": dict(self._before),
            "stays": {
                address: [
                    [now - start, None if end is None else now - end, source]
                    for start, end, source in stays
                ]
                for address, stays in self._stays.items()
            },
        }

    def restore(self, data: dict[str, Any], downtime_s: float) -> None:
        """Take a saved history back, ``downtime_s`` after it was written.

        Only a history that has not started watching takes it: one that has
        would have its own, newer past overwritten by a file that is older.

        The events are older by the downtime, which is what ages some of them
        out of the window. The downtime itself is **not** counted as observed:
        nothing was watching while Home Assistant was down, and ``observed_s``
        is the claim of how much of the day the numbers rest on.

        ``_held`` stays unset, so the first snapshot after a restore is still a
        baseline. A slot held when Home Assistant comes back is not a
        connection: the device may or may not have reconnected meanwhile, and
        counting it would give every restart one connection per device.
        """
        if (
            self._held is not None
            or self._started is not None
            or self._events
            or self._stays
        ):
            return
        downtime_s = max(0.0, downtime_s)
        # What the saved history watched, and the window still reaches back to.
        # Down for longer than the window, none of it does, and claiming the
        # coverage the file recorded would be claiming a day nobody watched.
        observed = min(float(data["observed_s"]), self.window_s - downtime_s)
        if observed <= 0:
            return
        now = self._clock()
        self._started = now - observed
        for address, events in data["events"].items():
            self._events[address] = deque(
                (now - float(age) - downtime_s, str(source)) for age, source in events
            )
        self._before = {
            address: str(source) for address, source in data["before"].items()
        }
        # A file from before stays were recorded has none, and the timeline
        # starts from the first snapshot. One that was open when it was written
        # ends where it was written: that is the last moment it was known to be
        # held, and Home Assistant going down drops the links anyway.
        for address, stays in data.get("stays", {}).items():
            restored: deque[list] = deque(maxlen=MAX_STAYS)
            for start_age, end_age, source in stays:
                start = now - float(start_age) - downtime_s
                end = (
                    now - downtime_s
                    if end_age is None
                    else now - float(end_age) - downtime_s
                )
                restored.append([start, end, str(source)])
            self._stays[address] = restored


def history_attributes(history: DeviceHistory | None) -> dict[str, int] | None:
    """``history`` as published under each entry of ``allocated_devices``."""
    if history is None:
        return None
    return {
        "connections": history.connections,
        "proxy_changes": history.proxy_changes,
    }
