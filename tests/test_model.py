from custom_components.bluesight.model import (
    ProxySlots, Incident, IncidentKind, normalize_address,
)


def test_normalize_address_uppercases_and_strips():
    assert normalize_address(" aa:bb:cc ") == "AA:BB:CC"
    assert normalize_address("AA:BB:CC") == "AA:BB:CC"


def test_proxyslots_used_derived():
    p = ProxySlots(source="AA:BB", name="Salon Proxy", slots=3, free=1,
                   allocated=["11:22", "33:44"])
    assert p.used == 2
    assert p.is_full is False


def test_incident_identity_is_stable():
    a = Incident(kind=IncidentKind.DEADLOCK, address="11:22", sources=["AA", "BB"])
    b = Incident(kind=IncidentKind.DEADLOCK, address="11:22", sources=["BB", "AA"])
    assert a.key == b.key   # order-independent identity
