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
            "sources": ["AA:BB", "CC:DD"],
            "detail": "on 2 proxies",
        },
        {
            "kind": "storm",
            "address": "33:44",
            "sources": [],
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
