"""Logic tests for the global incident binary sensor.

Runs under plain pytest: the entity is built against a fake coordinator, so no
``hass`` fixture is needed. Exercises is_on and the attribute shape only.
"""
from __future__ import annotations

from custom_components.bluesight.binary_sensor import (
    IncidentBinarySensor,
    ProxyOnlineBinarySensor,
)
from custom_components.bluesight.coordinator_data import BlueSightData
from custom_components.bluesight.model import Incident, IncidentKind, ProxyHealth


class _FakeCoordinator:
    def __init__(self, data: BlueSightData) -> None:
        self.data = data
        self.last_update_success = True


def _coord(*incidents: Incident) -> _FakeCoordinator:
    return _FakeCoordinator(BlueSightData(proxies=[], incidents=list(incidents)))


def _coord_health(*health: ProxyHealth) -> _FakeCoordinator:
    return _FakeCoordinator(
        BlueSightData(proxies=[], incidents=[], proxies_health=list(health))
    )


def test_off_when_no_incidents() -> None:
    sensor = IncidentBinarySensor(_coord())
    assert sensor.is_on is False
    assert sensor.unique_id == "bluesight_incident"
    assert sensor.extra_state_attributes == {
        "incident_count": 0,
        "availability_degraded": False,
        "incidents": [],
    }


def test_degraded_availability_is_surfaced() -> None:
    """A broken ghost-slot signal must never read as a clean bill of health."""
    coordinator = _FakeCoordinator(BlueSightData(availability_degraded=True))
    sensor = IncidentBinarySensor(coordinator)
    assert sensor.is_on is False
    assert sensor.extra_state_attributes["availability_degraded"] is True


def test_on_when_incidents_present() -> None:
    incidents = (
        Incident(
            IncidentKind.DEADLOCK, "11:22", ["AA:BB", "CC:DD"], "on 2 proxies"
        ),
        Incident(IncidentKind.STORM, "33:44", [], "5 failures / 300s"),
    )
    sensor = IncidentBinarySensor(_coord(*incidents))

    assert sensor.is_on is True
    attrs = sensor.extra_state_attributes
    assert attrs["incident_count"] == 2
    assert attrs["incidents"] == [
        {
            "kind": "deadlock",
            "address": "11:22",
            "device_name": "",
            "sources": ["AA:BB", "CC:DD"],
            "source_names": ["AA:BB", "CC:DD"],
            "detail": "on 2 proxies",
        },
        {
            "kind": "storm",
            "address": "33:44",
            "device_name": "",
            "sources": [],
            "source_names": [],
            "detail": "5 failures / 300s",
        },
    ]


def test_device_info_is_service_device() -> None:
    sensor = IncidentBinarySensor(_coord())
    assert sensor.device_info["identifiers"] == {("bluesight", "service")}
    assert sensor.device_info["name"] == "BlueSight"


def test_proxy_online_is_on_when_online() -> None:
    health = ProxyHealth("AA:BB", "proxy-a", True, True, 3.0, 2)
    sensor = ProxyOnlineBinarySensor(_coord_health(health), "AA:BB")

    assert sensor.is_on is True
    assert sensor.available is True
    assert sensor.unique_id == "AA:BB_online"
    # Grouped under the same per-proxy device as the slot sensors.
    assert sensor.device_info["identifiers"] == {("bluesight", "AA:BB")}
    assert sensor.device_info["name"] == "proxy-a"


def test_proxy_online_off_when_offline() -> None:
    health = ProxyHealth("AA:BB", "proxy-a", True, False, 3.0, 2)
    sensor = ProxyOnlineBinarySensor(_coord_health(health), "AA:BB")

    assert sensor.is_on is False
    assert sensor.available is True


def test_proxy_online_unavailable_when_absent() -> None:
    # Source dropped out of the health snapshot entirely.
    sensor = ProxyOnlineBinarySensor(_coord_health(), "AA:BB")
    assert sensor.available is False
    assert sensor.is_on is None


# --- naming the device an incident is about ---------------------------------
#
# The card showed a bare MAC where it already knew how to name the proxy, so a
# user reading "9C:AC:6D:D4:F9:FC" had to translate it themselves to know which
# thermostat was involved. The address stays in the payload -- it is the
# correlation key and the only thing present for a device Home Assistant does
# not know -- and the name rides beside it.


def _named(*incidents: Incident, **names: str) -> _FakeCoordinator:
    return _FakeCoordinator(
        BlueSightData(
            incidents=list(incidents),
            device_names={k.replace("_", ":").upper(): v for k, v in names.items()},
        )
    )


def test_incident_carries_the_home_assistant_name_of_its_device() -> None:
    incident = Incident(IncidentKind.BOND_LOST, "9C:AC:6D:D4:F9:FC", ["AA:BB"])
    coordinator = _named(incident, **{"9c_ac_6d_d4_f9_fc": "Madoka Manon"})
    published = IncidentBinarySensor(coordinator).extra_state_attributes["incidents"]
    assert published[0]["address"] == "9C:AC:6D:D4:F9:FC"
    assert published[0]["device_name"] == "Madoka Manon"


def test_an_address_home_assistant_cannot_name_publishes_an_empty_name() -> None:
    """Absent, not guessed: an unmanaged peripheral holding a slot is itself a
    diagnostic, and a blank name lets the card fall back to the address rather
    than print something confident and wrong."""
    incident = Incident(IncidentKind.GHOST_SLOT, "C3:EB:49:65:67:55", ["AA:BB"])
    published = IncidentBinarySensor(_named(incident)).extra_state_attributes[
        "incidents"
    ]
    assert published[0]["device_name"] == ""


def test_the_name_lookup_is_case_insensitive() -> None:
    """Addresses are correlated on the canonical form everywhere else; a name
    map keyed in the other case must not silently miss."""
    incident = Incident(IncidentKind.BOND_LOST, "9c:ac:6d:d4:f9:fc", ["AA:BB"])
    coordinator = _named(incident, **{"9c_ac_6d_d4_f9_fc": "Madoka Manon"})
    published = IncidentBinarySensor(coordinator).extra_state_attributes["incidents"]
    assert published[0]["device_name"] == "Madoka Manon"


def test_a_proxy_the_names_map_knows_is_labelled_not_addressed() -> None:
    """The badge footer said "sur D0:CF:13:0F:05:5A" beside a sentence that
    already said "Proxy Buanderie" -- one proxy, named two ways, one of them
    unreadable."""
    incident = Incident(IncidentKind.BOND_LOST, "11:22", ["D0:CF:13:0F:05:5A"])
    coordinator = _FakeCoordinator(
        BlueSightData(
            incidents=[incident],
            proxy_display_names={"D0:CF:13:0F:05:5A": "Proxy Buanderie"},
        )
    )
    published = IncidentBinarySensor(coordinator).extra_state_attributes["incidents"]
    assert published[0]["source_names"] == ["Proxy Buanderie"]
    # The address is kept: it is what correlates this badge with the proxy's
    # own entities, and the card still needs it when no name is known.
    assert published[0]["sources"] == ["D0:CF:13:0F:05:5A"]
