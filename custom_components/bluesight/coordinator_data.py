"""Pure snapshot container + assembly for BlueSight.

No Home Assistant dependency: the whole correlation/assembly step lives here
so it is fully unit-testable with plain pytest. The ``DataUpdateCoordinator``
subclass stays a thin shell that only feeds this function a snapshot.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from .detector import (
    detect_deadlocks,
    detect_ghost_slots,
    detect_offline_proxies,
    detect_reboot_storm,
    detect_stalled_proxies,
    detect_storm,
)
from .model import Incident, ProxyHealth, ProxySlots
from .rendering import Catalogue, plural_count, render
from .window import FailureWindow


@dataclass(frozen=True, slots=True)
class BlueSightData:
    proxies: list[ProxySlots] = field(default_factory=list)
    incidents: list[Incident] = field(default_factory=list)
    proxies_health: list[ProxyHealth] = field(default_factory=list)
    # True once an availability lookup has failed: ghost-slot detection is then
    # biased toward "alive" and its verdicts must be read with that in mind.
    # Surfaced on the incident sensor and in diagnostics so a broken signal is
    # observable instead of silently reading as "nothing wrong".
    availability_degraded: bool = False


def build_triage_data(
    proxies: list[ProxySlots],
    availability: dict[str, bool],
    storm_window: FailureWindow,
    *,
    proxies_health: list[ProxyHealth] | None = None,
    known_sources: set[str] | None = None,
    reboot_window: FailureWindow | None = None,
    stalled_threshold_s: float = 180.0,
    offline_for: dict[str, float] | None = None,
    offline_grace_s: float = 0.0,
    availability_degraded: bool = False,
    catalogue: Catalogue | None = None,
) -> BlueSightData:
    """Pure assembly: run all detectors over a snapshot + the rolling failure
    windows and return the combined incident list. No HA, no I/O.

    Incidents are emitted independently: one address may surface as several
    kinds at once (deadlock + ghost + storm). Any dedup/precedence policy is
    the notification layer's job (Task 10), not this assembly step's.

    Detectors emit a translation key and parameters, never prose. With a
    ``catalogue`` the incidents come back with ``detail`` rendered in that
    language; without one they are returned exactly as the detectors built
    them, which is the honest default for a pure function and keeps every
    detector test independent of any catalogue.
    """
    proxies_health = proxies_health or []
    known_sources = known_sources or set()
    incidents: list[Incident] = []
    # Slot-layer incidents
    incidents += detect_deadlocks(proxies)
    incidents += detect_ghost_slots(proxies, availability)
    for addr in storm_window.addresses():
        inc = detect_storm(addr, storm_window)
        if inc is not None:
            incidents.append(inc)
    # Proxy-health incidents
    incidents += detect_offline_proxies(
        proxies_health, known_sources, offline_for, offline_grace_s
    )
    incidents += detect_stalled_proxies(proxies_health, stalled_threshold_s)
    if reboot_window is not None:
        for src in reboot_window.addresses():
            inc = detect_reboot_storm(src, reboot_window)
            if inc is not None:
                incidents.append(inc)
    # `detail` is a published contract: it lands in the `incidents` attribute
    # of `binary_sensor.bluesight_incident`, and user automations format push
    # notifications from it. Rendering it here -- once, where the incident
    # list is assembled -- is what keeps that automation producing prose.
    if catalogue is not None:
        incidents = [
            replace(
                i,
                detail=render(
                    i.detail_key,
                    i.detail_params,
                    catalogue,
                    count=plural_count(i.detail_params),
                ),
            )
            if i.detail_key
            else i
            for i in incidents
        ]
    return BlueSightData(
        proxies=proxies,
        incidents=incidents,
        proxies_health=proxies_health,
        availability_degraded=availability_degraded,
    )
