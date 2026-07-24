"""Pure detectors for BLE connection-layer incidents.

No Home Assistant dependency; fully unit-testable with plain pytest.
"""
from __future__ import annotations

from collections import defaultdict

from .model import Incident, IncidentKind, ProxySlots


def detect_deadlocks(proxies: list[ProxySlots]) -> list[Incident]:
    """A single BLE peripheral can be connected to one central at a time.
    An address in the `allocated` list of >=2 proxies is a stale duplicate
    allocation (core issue #176516)."""
    by_addr: dict[str, list[str]] = defaultdict(list)
    for p in proxies:
        for addr in p.allocated:
            by_addr[addr].append(p.source)
    return [
        Incident(IncidentKind.DEADLOCK, addr, sources,
                 detail=f"Held on {len(sources)} proxies simultaneously")
        for addr, sources in by_addr.items() if len(sources) >= 2
    ]
