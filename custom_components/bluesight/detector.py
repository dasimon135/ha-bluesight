"""Pure detectors for BLE connection-layer incidents.

No Home Assistant dependency; fully unit-testable with plain pytest.
"""
from __future__ import annotations

from collections import defaultdict

from .model import Incident, IncidentKind, ProxyHealth, ProxySlots, normalize_address
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
            by_addr[normalize_address(addr)].add(p.source)
    return [
        Incident(IncidentKind.DEADLOCK, addr, sorted(sources),
                 detail=f"Held on {len(sources)} proxies simultaneously")
        for addr, sources in by_addr.items() if len(sources) >= 2
    ]


def detect_ghost_slots(
    proxies: list[ProxySlots], availability: dict[str, bool]
) -> list[Incident]:
    """A slot held for a device whose entity is unavailable is likely stale."""
    avail = {normalize_address(k): v for k, v in availability.items()}
    out: list[Incident] = []
    for p in proxies:
        for addr in p.allocated:
            norm = normalize_address(addr)
            if avail.get(norm, True) is False:
                out.append(Incident(
                    IncidentKind.GHOST_SLOT, norm, [p.source],
                    detail=f"Slot held on {p.name} while device unavailable"))
    return out


def detect_offline_proxies(
    proxies: list[ProxyHealth],
    known_sources: set[str],
    offline_for: dict[str, float] | None = None,
    grace_s: float = 0.0,
) -> list[Incident]:
    """A source we have seen online before, now absent from the scanners.

    ``offline_for`` maps a source to how many seconds it has been missing, and
    ``grace_s`` is how long a source may be missing before it is reported. An
    ESPHome proxy unregisters and re-registers on every OTA update and on every
    reload of its config entry, so reporting the very first absent snapshot
    raises (and immediately clears) an alert on entirely routine events. A
    source with no entry in ``offline_for`` is treated as freshly missing.

    The detail string deliberately carries no elapsed time: it lands in entity
    attributes that would otherwise churn on every single snapshot.
    """
    online = {p.source for p in proxies if p.online}
    offline_for = offline_for or {}
    return [
        Incident(IncidentKind.PROXY_OFFLINE, src, [src],
                 detail="Proxy offline (no longer a registered scanner)")
        for src in sorted(known_sources - online)
        if offline_for.get(src, 0.0) >= grace_s
    ]


def detect_stalled_proxies(
    proxies: list[ProxyHealth], threshold_s: float
) -> list[Incident]:
    """Online scanner that has not seen any advertisement for too long."""
    return [
        Incident(IncidentKind.PROXY_STALLED, p.source, [p.source],
                 detail=f"Online but no BLE advertisement seen for "
                        f"{int(p.seconds_since_detection)}s")
        for p in proxies
        if p.online and p.seconds_since_detection > threshold_s
    ]


def detect_storm(address: str, window: FailureWindow) -> Incident | None:
    count = window.count(address)
    if count >= window.threshold:
        return Incident(IncidentKind.STORM, address, [],
                        detail=f"{count} failures in {int(window.window_s)}s")
    return None


def detect_reboot_storm(source: str, window: FailureWindow) -> Incident | None:
    count = window.count(source)
    if count >= window.threshold:
        return Incident(IncidentKind.PROXY_REBOOT_STORM, source, [source],
                        detail=f"{count} proxy reboots in "
                               f"{int(window.window_s)}s")
    return None
