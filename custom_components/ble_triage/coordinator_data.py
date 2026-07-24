"""Pure snapshot container + assembly for BLE Triage.

No Home Assistant dependency: the whole correlation/assembly step lives here
so it is fully unit-testable with plain pytest. The ``DataUpdateCoordinator``
subclass stays a thin shell that only feeds this function a snapshot.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .detector import detect_deadlocks, detect_ghost_slots, detect_storm
from .model import Incident, ProxySlots
from .window import FailureWindow


@dataclass(frozen=True, slots=True)
class BleTriageData:
    proxies: list[ProxySlots] = field(default_factory=list)
    incidents: list[Incident] = field(default_factory=list)


def build_triage_data(
    proxies: list[ProxySlots],
    availability: dict[str, bool],
    storm_window: FailureWindow,
) -> BleTriageData:
    """Pure assembly: run all three detectors over a snapshot + the rolling
    failure window and return the combined incident list. No HA, no I/O.

    Incidents are emitted independently: one address may surface as several
    kinds at once (deadlock + ghost + storm). Any dedup/precedence policy is
    the notification layer's job (Task 10), not this assembly step's.
    """
    incidents: list[Incident] = []
    incidents += detect_deadlocks(proxies)
    incidents += detect_ghost_slots(proxies, availability)
    for addr in storm_window.addresses():
        inc = detect_storm(addr, storm_window)
        if inc is not None:
            incidents.append(inc)
    return BleTriageData(proxies=proxies, incidents=incidents)
