from custom_components.ble_triage.model import ProxySlots, IncidentKind
from custom_components.ble_triage.detector import detect_deadlocks


def test_address_on_two_proxies_is_deadlock():
    proxies = [
        ProxySlots("AA", "P1", 2, 1, ["11:22"]),
        ProxySlots("BB", "P2", 2, 1, ["11:22"]),
        ProxySlots("CC", "P3", 2, 2, []),
    ]
    incidents = detect_deadlocks(proxies)
    assert len(incidents) == 1
    inc = incidents[0]
    assert inc.kind is IncidentKind.DEADLOCK
    assert inc.address == "11:22"
    assert sorted(inc.sources) == ["AA", "BB"]


def test_address_on_one_proxy_is_not_deadlock():
    proxies = [ProxySlots("AA", "P1", 2, 1, ["11:22"])]
    assert detect_deadlocks(proxies) == []
