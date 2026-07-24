from custom_components.ble_triage.coordinator_data import (
    BleTriageData,
    build_triage_data,
)
from custom_components.ble_triage.model import IncidentKind, ProxySlots
from custom_components.ble_triage.window import FailureWindow


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
    assert data == BleTriageData([], [])


def test_proxies_passed_through_unchanged():
    proxies = [ProxySlots("AA", "proxy-a", 3, 3, [])]
    data = build_triage_data(proxies, {}, _empty_window())
    assert data.proxies is proxies
