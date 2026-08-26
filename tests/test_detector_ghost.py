from custom_components.bluesight.detector import detect_ghost_slots
from custom_components.bluesight.model import IncidentKind, ProxySlots


def test_allocated_but_unavailable_is_ghost():
    proxies = [ProxySlots("AA", "P1", 2, 1, ["11:22"])]
    availability = {"11:22": False}
    incidents = detect_ghost_slots(proxies, availability)
    assert len(incidents) == 1
    assert incidents[0].kind is IncidentKind.GHOST_SLOT
    assert incidents[0].address == "11:22"
    assert incidents[0].detail_key == "incident.ghost_slot.detail"
    # The proxy's friendly name is user-controlled text; it travels as a
    # parameter and is substituted by the renderer, never re-scanned.
    assert incidents[0].detail_params == {"proxy": "P1"}


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


# --- naming the proxy -------------------------------------------------------
#
# `{proxy}` is what the user reads in the incident detail and in the
# notification built beside it, so it has to be the name the user gave the
# proxy. That name is resolved once, by the coordinator, and handed to every
# detector that names a proxy -- `detect_bond_lost` and `detect_idle_slots`
# already take exactly this map. `ProxySlots.name` stays the habluetooth
# scanner name because it is also what the proxy's *device* is created with.


def test_the_users_name_for_the_proxy_is_what_the_detail_says():
    proxies = [
        ProxySlots("D8:3B:DA:11:22:33", "atomebuanderie (D8:3B:DA:11:22:33)", 2, 1, ["11:22"])
    ]
    incidents = detect_ghost_slots(
        proxies, {"11:22": False}, {"D8:3B:DA:11:22:33": "Proxy Buanderie"}
    )
    assert incidents[0].detail_params == {"proxy": "Proxy Buanderie"}


def test_a_proxy_the_names_map_does_not_know_keeps_the_scanner_name():
    proxies = [ProxySlots("AA", "P1", 2, 1, ["11:22"])]
    incidents = detect_ghost_slots(proxies, {"11:22": False}, {"BB": "Elsewhere"})
    assert incidents[0].detail_params == {"proxy": "P1"}


def test_the_proxy_parameter_is_always_present_even_with_no_name_anywhere():
    """`incident_policy._ghost_proxy` falls back to `sources[0]` and then to a
    catalogued "an unspecified proxy". Neither becomes reachable here: the
    parameter is emitted for every ghost slot, exactly as before."""
    proxies = [ProxySlots("AA:BB:CC:DD:EE:FF", "", 2, 1, ["11:22"])]
    incidents = detect_ghost_slots(proxies, {"11:22": False}, {})
    assert incidents[0].detail_params == {"proxy": ""}
    assert incidents[0].sources == ["AA:BB:CC:DD:EE:FF"]


def test_the_names_map_is_matched_on_the_canonical_source():
    proxies = [ProxySlots("d8:3b:da:11:22:33", "scanner", 2, 1, ["11:22"])]
    incidents = detect_ghost_slots(
        proxies, {"11:22": False}, {"D8:3B:DA:11:22:33": "Proxy Buanderie"}
    )
    assert incidents[0].detail_params == {"proxy": "Proxy Buanderie"}
