from custom_components.bluesight.model import (
    Incident,
    IncidentKind,
    ProxyHealth,
    ProxySlots,
    normalize_address,
)


def test_normalize_address_uppercases_and_strips():
    assert normalize_address(" aa:bb:cc ") == "AA:BB:CC"
    assert normalize_address("AA:BB:CC") == "AA:BB:CC"


def test_proxyslots_used_derived():
    p = ProxySlots(source="AA:BB", name="Salon Proxy", slots=3, free=1,
                   allocated=["11:22", "33:44"])
    assert p.used == 2
    assert p.is_full is False
    assert p.is_connectable is True


def test_saturated_proxy_is_full():
    p = ProxySlots(source="AA:BB", name="Salon Proxy", slots=3, free=0,
                   allocated=["11:22", "33:44", "55:66"])
    assert p.is_full is True


def test_non_connectable_scanner_is_not_full():
    """habluetooth registers passive scanners with slots=0, free=0 — they hold
    no connections at all, which is not the same as being saturated."""
    p = ProxySlots(source="AA:BB", name="Passive", slots=0, free=0)
    assert p.is_full is False
    assert p.is_connectable is False
    assert p.used == 0


def test_incident_identity_is_stable():
    a = Incident(kind=IncidentKind.DEADLOCK, address="11:22", sources=["AA", "BB"])
    b = Incident(kind=IncidentKind.DEADLOCK, address="11:22", sources=["BB", "AA"])
    assert a.key == b.key   # order-independent identity


def test_proxyhealth_fields():
    p = ProxyHealth(source="AA", name="Salon", connectable=True,
                    online=True, seconds_since_detection=3.0, device_count=5)
    assert p.source == "AA" and p.online is True and p.device_count == 5


def test_new_incident_kinds_exist():
    assert IncidentKind.PROXY_OFFLINE.value == "proxy_offline"
    assert IncidentKind.PROXY_STALLED.value == "proxy_stalled"
    assert IncidentKind.PROXY_REBOOT_STORM.value == "proxy_reboot_storm"
