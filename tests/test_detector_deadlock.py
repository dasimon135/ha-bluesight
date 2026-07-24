from custom_components.bluesight.model import ProxySlots, IncidentKind
from custom_components.bluesight.detector import detect_deadlocks


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


def test_duplicate_address_within_one_proxy_is_not_deadlock():
    # The same address listed twice on a SINGLE proxy is not a deadlock:
    # a deadlock (#176516) needs the address held on >=2 DISTINCT proxies.
    proxies = [ProxySlots("AA", "P1", 2, 1, ["11:22", "11:22"])]
    assert detect_deadlocks(proxies) == []


def test_three_proxies_sharing_address():
    proxies = [
        ProxySlots("AA", "P1", 2, 1, ["11:22"]),
        ProxySlots("BB", "P2", 2, 1, ["11:22"]),
        ProxySlots("CC", "P3", 2, 1, ["11:22"]),
    ]
    incidents = detect_deadlocks(proxies)
    assert len(incidents) == 1
    inc = incidents[0]
    assert inc.kind is IncidentKind.DEADLOCK
    assert inc.address == "11:22"
    assert sorted(inc.sources) == ["AA", "BB", "CC"]
    assert "3 proxies" in inc.detail


def test_deadlock_correlates_across_case():
    # Same device reported in different case on two proxies must correlate.
    proxies = [
        ProxySlots("AA", "P1", 2, 1, ["AA:BB:CC:DD:EE:FF"]),
        ProxySlots("BB", "P2", 2, 1, ["aa:bb:cc:dd:ee:ff"]),
    ]
    incidents = detect_deadlocks(proxies)
    assert len(incidents) == 1
    assert incidents[0].address == "AA:BB:CC:DD:EE:FF"
    assert sorted(incidents[0].sources) == ["AA", "BB"]
