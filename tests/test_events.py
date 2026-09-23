"""`bluesight_incident` events: an incident opening or resolving, on the bus.

Until now an automation that wanted to react to one incident had to watch
`binary_sensor.bluesight_incident`, read its `incidents` attribute, find the
entry it cared about and notice by itself whether it was new. The event says
it once, when it happens, with the fields already apart.

The payload builder is pure; the manager is exercised with a fake bus, the same
way `test_notify` swaps `persistent_notification` for a recorder.
"""
from __future__ import annotations

from custom_components.bluesight.events import EVENT_INCIDENT, IncidentEvents
from custom_components.bluesight.incident_policy import event_payload
from custom_components.bluesight.model import Incident, IncidentKind

PROXY = "D8:3B:DA:11:22:35"
ADDR = "D0:CF:13:0E:C9:2A"


def _ghost(address=ADDR, source=PROXY, detail="Slot held on Kitchen") -> Incident:
    return Incident(
        IncidentKind.GHOST_SLOT, address, [source], detail=detail,
        detail_key="incident.ghost_slot.detail", detail_params={"proxy": "Kitchen"},
    )


def _storm(address=ADDR, sources=(), count="5") -> Incident:
    return Incident(
        IncidentKind.STORM, address, list(sources),
        detail_key="incident.storm.detail",
        detail_params={"count": count, "seconds": "300"},
    )


# --- the payload ------------------------------------------------------------


def test_the_payload_carries_the_fields_already_apart():
    payload = event_payload(
        "opened", _ghost(), {ADDR: "Madoka salon"}, {PROXY: "Proxy Buanderie"}
    )
    assert payload == {
        "action": "opened",
        "kind": "ghost_slot",
        "address": ADDR,
        "device_name": "Madoka salon",
        "sources": [PROXY],
        "source_names": ["Proxy Buanderie"],
        "detail": "Slot held on Kitchen",
        "evidence": "heuristic",
        "key": f"ghost_slot:{ADDR}:{PROXY}",
        # False unless the incident was already open when BlueSight started
        # looking; see the tests at the end of this module.
        "initial": False,
    }


def test_an_unnamed_device_and_proxy_fall_back_as_the_sensor_does():
    """The same rule `binary_sensor` publishes by: "" for a device Home
    Assistant cannot name, the source address for a proxy it cannot."""
    payload = event_payload("opened", _ghost(), {}, {})
    assert payload["device_name"] == ""
    assert payload["source_names"] == [PROXY]


# --- the manager ------------------------------------------------------------


class _FakeBus:
    def __init__(self) -> None:
        self.fired: list[tuple[str, dict]] = []

    def async_fire(self, event_type, event_data=None):
        self.fired.append((event_type, event_data))


class _FakeHass:
    def __init__(self) -> None:
        self.bus = _FakeBus()


def _events():
    hass = _FakeHass()
    return IncidentEvents(hass), hass.bus.fired


def test_an_incident_that_appears_fires_opened_once():
    events, fired = _events()
    events.async_update([_ghost()], {}, {})
    events.async_update([_ghost()], {}, {})
    assert [(t, d["action"], d["kind"]) for t, d in fired] == [
        (EVENT_INCIDENT, "opened", "ghost_slot")
    ]


def test_an_incident_that_goes_fires_resolved_with_what_it_was():
    """The incident is gone from the snapshot by then, so the manager has to
    have kept it: a bare key would make every listener parse it back apart."""
    events, fired = _events()
    events.async_update([_ghost()], {ADDR: "Madoka salon"}, {})
    events.async_update([], {}, {})
    assert [d["action"] for _t, d in fired] == ["opened", "resolved"]
    resolved = fired[1][1]
    assert (resolved["kind"], resolved["address"]) == ("ghost_slot", ADDR)
    # As it was last seen, not re-resolved against a registry that moved on.
    assert resolved["device_name"] == "Madoka salon"


def test_a_storm_whose_attribution_shifts_is_still_one_incident():
    """`Incident.key` ignores a storm's sources, so the proxies that measured
    it coming and going must not read as a new storm."""
    events, fired = _events()
    events.async_update([_storm(sources=[PROXY])], {}, {})
    events.async_update([_storm(sources=[], count="9")], {}, {})
    assert [d["action"] for _t, d in fired] == ["opened"]


def test_precedence_is_applied_before_anything_fires():
    """One physical fault, one event -- the same rule as the notifications."""
    deadlock = Incident(IncidentKind.DEADLOCK, ADDR, ["AA", "BB"])
    events, fired = _events()
    events.async_update([_ghost(), deadlock], {}, {})
    assert [d["kind"] for _t, d in fired] == ["deadlock"]


def test_unloading_resolves_nothing():
    """An entry being reloaded is not a fault going away."""
    events, fired = _events()
    events.async_update([_ghost()], {}, {})
    events.async_shutdown()
    assert [d["action"] for _t, d in fired] == ["opened"]


# --- what is new, and what was merely already there -------------------------


def test_incidents_already_open_at_the_first_update_are_marked():
    """Setup publishes before anything is known, so every open incident fires
    `opened`. An automation that notifies on it would notify again on every
    restart and on every options edit, for a fault that never stopped. The
    event says which it is instead of leaving the automation to guess."""
    events, fired = _events()
    events.async_update([_ghost()], {}, {})
    assert fired[0][1]["initial"] is True


def test_an_incident_that_opens_later_is_not_initial():
    events, fired = _events()
    events.async_update([], {}, {})
    events.async_update([_ghost()], {}, {})
    assert fired[0][1]["initial"] is False


def test_the_first_update_marks_only_what_it_found():
    """A second incident arriving in the same snapshot as the first update is
    still part of that first picture; one arriving later is not."""
    events, fired = _events()
    events.async_update([_ghost(), _storm()], {}, {})
    assert [d["initial"] for _t, d in fired] == [True, True]


def test_a_resolved_event_keeps_the_flag_it_opened_with():
    events, fired = _events()
    events.async_update([_ghost()], {}, {})
    events.async_update([], {}, {})
    assert [(d["action"], d["initial"]) for _t, d in fired] == [
        ("opened", True),
        ("resolved", True),
    ]
