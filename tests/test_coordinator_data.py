from custom_components.bluesight.coordinator_data import (
    BlueSightData,
    build_triage_data,
)
from custom_components.bluesight.model import IncidentKind, ProxyHealth, ProxySlots
from custom_components.bluesight.window import FailureWindow


def _empty_window():
    return FailureWindow(window_s=300, threshold=5, clock=lambda: 0.0)


def test_deadlock_across_two_proxies_surfaces():
    proxies = [
        ProxySlots("AA", "proxy-a", 3, 2, ["11:22"]),
        ProxySlots("BB", "proxy-b", 3, 2, ["11:22"]),
    ]
    data = build_triage_data(proxies, {}, _empty_window())
    kinds = [i.kind for i in data.incidents]
    assert IncidentKind.DEADLOCK in kinds


def test_ghost_slot_surfaces():
    proxies = [ProxySlots("AA", "proxy-a", 3, 2, ["11:22"])]
    data = build_triage_data(proxies, {"11:22": False}, _empty_window())
    kinds = [i.kind for i in data.incidents]
    assert IncidentKind.GHOST_SLOT in kinds


def test_storm_surfaces():
    now = [0.0]
    w = FailureWindow(window_s=300, threshold=5, clock=lambda: now[0])
    for _ in range(5):
        now[0] += 10
        w.record("11:22")
    data = build_triage_data([], {}, w)
    storms = [i for i in data.incidents if i.kind is IncidentKind.STORM]
    assert len(storms) == 1
    assert storms[0].address == "11:22"


def test_empty_inputs_produce_empty_data():
    data = build_triage_data([], {}, _empty_window())
    assert data == BlueSightData([], [])


def test_proxies_passed_through_unchanged():
    proxies = [ProxySlots("AA", "proxy-a", 3, 3, [])]
    data = build_triage_data(proxies, {}, _empty_window())
    assert data.proxies is proxies


def _win():
    return FailureWindow(600, 3, clock=lambda: 0.0)


def test_proxies_health_defaults_empty():
    d = build_triage_data([], {}, _win())
    assert d.proxies_health == []


def test_stalled_proxy_surfaces_in_incidents():
    health = [ProxyHealth("AA", "Salon", True, True, 300.0, 0)]
    d = build_triage_data([], {}, _win(), proxies_health=health,
                          known_sources={"AA"}, reboot_window=_win(),
                          stalled_threshold_s=180.0)
    assert IncidentKind.PROXY_STALLED in {i.kind for i in d.incidents}
    assert d.proxies_health == health


def test_offline_proxy_surfaces_in_incidents():
    # AA is known but not present in proxies_health -> offline
    d = build_triage_data([], {}, _win(), proxies_health=[],
                          known_sources={"AA"}, reboot_window=_win())
    assert IncidentKind.PROXY_OFFLINE in {i.kind for i in d.incidents}
