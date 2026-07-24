"""Logic tests for the per-proxy slot sensors.

These run under plain pytest on any platform: the entities are constructed
against a fake coordinator (a bare object exposing ``.data``), so no ``hass``
fixture and no HA test plugin are required. Only entity *logic* is exercised
here (native_value / attributes / availability); wiring is CI-only.
"""
from __future__ import annotations

from custom_components.bluesight.coordinator_data import BlueSightData
from custom_components.bluesight.model import ProxySlots
from custom_components.bluesight.sensor import SlotsFreeSensor, SlotsUsedSensor


class _FakeCoordinator:
    def __init__(self, data: BlueSightData) -> None:
        self.data = data
        self.last_update_success = True


def _coord(*proxies: ProxySlots) -> _FakeCoordinator:
    return _FakeCoordinator(BlueSightData(proxies=list(proxies), incidents=[]))


def test_slots_used_native_value_and_attributes() -> None:
    proxy = ProxySlots("AA:BB", "proxy-a", 3, 1, ["11:22", "33:44"])
    sensor = SlotsUsedSensor(_coord(proxy), "AA:BB")

    assert sensor.native_value == 2  # used = slots - free = 3 - 1
    assert sensor.unique_id == "AA:BB_slots_used"
    assert sensor.extra_state_attributes == {
        "total": 3,
        "free": 1,
        "allocated": ["11:22", "33:44"],
        "source": "AA:BB",
    }


def test_slots_free_native_value() -> None:
    proxy = ProxySlots("AA:BB", "proxy-a", 3, 1, ["11:22", "33:44"])
    sensor = SlotsFreeSensor(_coord(proxy), "AA:BB")

    assert sensor.native_value == 1
    assert sensor.unique_id == "AA:BB_slots_free"


def test_available_true_when_proxy_present() -> None:
    proxy = ProxySlots("AA:BB", "proxy-a", 3, 3, [])
    sensor = SlotsUsedSensor(_coord(proxy), "AA:BB")
    assert sensor.available is True


def test_unavailable_when_proxy_absent() -> None:
    # Coordinator no longer reports this source: sensor goes unavailable and
    # its value/attributes degrade to None rather than raising.
    sensor = SlotsUsedSensor(_coord(), "AA:BB")
    assert sensor.available is False
    assert sensor.native_value is None
    assert sensor.extra_state_attributes is None


def test_free_sensor_unavailable_when_proxy_absent() -> None:
    sensor = SlotsFreeSensor(_coord(), "AA:BB")
    assert sensor.available is False
    assert sensor.native_value is None


def test_device_info_groups_by_source() -> None:
    proxy = ProxySlots("AA:BB", "proxy-a", 3, 3, [])
    used = SlotsUsedSensor(_coord(proxy), "AA:BB")
    free = SlotsFreeSensor(_coord(proxy), "AA:BB")

    assert used.device_info == free.device_info
    assert used.device_info["identifiers"] == {("bluesight", "AA:BB")}
    assert used.device_info["name"] == "proxy-a"
