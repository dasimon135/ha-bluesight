"""Pure detectors for BLE connection-layer incidents.

No Home Assistant dependency; fully unit-testable with plain pytest.
"""
from __future__ import annotations

from collections import defaultdict

from .model import Incident, IncidentKind, ProxySlots
from .window import FailureWindow


def detect_deadlocks(proxies: list[ProxySlots]) -> list[Incident]:
    """A single BLE peripheral can be connected to one central at a time.
    An address held by >=2 DISTINCT proxies is a stale duplicate allocation
    (core issue #176516). We correlate over distinct proxy sources so a
    single proxy that lists the same address twice does not fabricate a
    deadlock."""
    by_addr: dict[str, set[str]] = defaultdict(set)
    for p in proxies:
        for addr in p.allocated:
            by_addr[addr].add(p.source)
    return [
        Incident(IncidentKind.DEADLOCK, addr, sorted(sources),
                 detail=f"Held on {len(sources)} proxies simultaneously")
        for addr, sources in by_addr.items() if len(sources) >= 2
    ]


def detect_ghost_slots(
    proxies: list[ProxySlots], availability: dict[str, bool]
) -> list[Incident]:
    """A slot held for a device whose entity is unavailable is likely stale."""
    out: list[Incident] = []
    for p in proxies:
        for addr in p.allocated:
            if availability.get(addr, True) is False:
                out.append(Incident(
                    IncidentKind.GHOST_SLOT, addr, [p.source],
                    detail=f"Slot held on {p.name} while device unavailable"))
    return out


def detect_storm(address: str, window: FailureWindow) -> Incident | None:
    count = window.count(address)
    if count >= window.threshold:
        return Incident(IncidentKind.STORM, address, [],
                        detail=f"{count} failures in {int(window.window_s)}s")
    return None
