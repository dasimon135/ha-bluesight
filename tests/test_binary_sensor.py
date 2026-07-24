"""Logic tests for the global incident binary sensor.

Runs under plain pytest: the entity is built against a fake coordinator, so no
``hass`` fixture is needed. Exercises is_on and the attribute shape only.
"""
from __future__ import annotations

from custom_components.ble_triage.binary_sensor import IncidentBinarySensor
from custom_components.ble_triage.coordinator_data import BleTriageData
from custom_components.ble_triage.model import Incident, IncidentKind


class _FakeCoordinator:
    def __init__(self, data: BleTriageData) -> None:
        self.data = data
        self.last_update_success = True


def _coord(*incidents: Incident) -> _FakeCoordinator:
    return _FakeCoordinator(BleTriageData(proxies=[], incidents=list(incidents)))


def test_off_when_no_incidents() -> None:
    sensor = IncidentBinarySensor(_coord())
    assert sensor.is_on is False
    assert sensor.unique_id == "ble_triage_incident"
    assert sensor.extra_state_attributes == {"incident_count": 0, "incidents": []}


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
    assert sensor.device_info["identifiers"] == {("ble_triage", "service")}
    assert sensor.device_info["name"] == "BLE Triage"
