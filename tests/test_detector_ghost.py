from custom_components.bluesight.detector import detect_ghost_slots
from custom_components.bluesight.model import IncidentKind, ProxySlots


def test_allocated_but_unavailable_is_ghost():
    proxies = [ProxySlots("AA", "P1", 2, 1, ["11:22"])]
    availability = {"11:22": False}
    incidents = detect_ghost_slots(proxies, availability)
    assert len(incidents) == 1
    assert incidents[0].kind is IncidentKind.GHOST_SLOT
    assert incidents[0].address == "11:22"


def test_allocated_and_available_is_fine():
    proxies = [ProxySlots("AA", "P1", 2, 1, ["11:22"])]
    assert detect_ghost_slots(proxies, {"11:22": True}) == []


def test_ghost_slot_case_insensitive_availability():
    # allocated is upper-case, availability keyed lower-case: must still match.
    proxies = [ProxySlots("AA", "P1", 2, 1, ["AA:BB"])]
    incidents = detect_ghost_slots(proxies, {"aa:bb": False})
    assert len(incidents) == 1
    assert incidents[0].kind is IncidentKind.GHOST_SLOT
    assert incidents[0].address == "AA:BB"
