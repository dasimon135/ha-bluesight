"""Logic tests for the per-proxy slot sensors.

These run under plain pytest on any platform: the entities are constructed
against a fake coordinator (a bare object exposing ``.data``), so no ``hass``
fixture and no HA test plugin are required. Only entity *logic* is exercised
here (native_value / attributes / availability); wiring is CI-only.
"""
from __future__ import annotations

from custom_components.bluesight.coordinator_data import BlueSightData
from custom_components.bluesight.model import DeviceRef, ProxyHealth, ProxySlots
from custom_components.bluesight.sensor import (
    LastDeviceSeenSensor,
    SlotsFreeSensor,
    SlotsUsedSensor,
)


class _FakeCoordinator:
    def __init__(self, data: BlueSightData) -> None:
        self.data = data
        self.last_update_success = True


def _coord(*proxies: ProxySlots) -> _FakeCoordinator:
    return _FakeCoordinator(BlueSightData(proxies=list(proxies), incidents=[]))


def _coord_health(*health: ProxyHealth) -> _FakeCoordinator:
    return _FakeCoordinator(
        BlueSightData(proxies=[], incidents=[], proxies_health=list(health))
    )


def test_slots_used_native_value_and_attributes() -> None:
    proxy = ProxySlots("AA:BB", "proxy-a", 3, 1, ["11:22", "33:44"])
    sensor = SlotsUsedSensor(_coord(proxy), "AA:BB")

    assert sensor.native_value == 2  # used = slots - free = 3 - 1
    assert sensor.unique_id == "AA:BB_slots_used"
    assert sensor.extra_state_attributes == {
        "total": 3,
        "free": 1,
        "allocated": ["11:22", "33:44"],
        "allocated_devices": [
            {"address": "11:22", "name": "", "device_id": None},
            {"address": "33:44", "name": "", "device_id": None},
        ],
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


def test_last_device_seen_native_value_and_attributes() -> None:
    health = ProxyHealth("AA:BB", "proxy-a", True, True, 12.6, 4)
    sensor = LastDeviceSeenSensor(_coord_health(health), "AA:BB")

    assert sensor.native_value == 13  # round(12.6)
    assert sensor.unique_id == "AA:BB_last_device_seen"
    assert sensor.available is True
    assert sensor.extra_state_attributes == {
        "device_count": 4,
        "connectable": True,
        "online": True,
    }
    # Grouped under the same per-proxy device as the slot sensors.
    assert sensor.device_info["identifiers"] == {("bluesight", "AA:BB")}
    assert sensor.device_info["name"] == "proxy-a"


def test_last_device_seen_offline_proxy_still_reported() -> None:
    # A proxy present in health but flagged offline still reports its value.
    health = ProxyHealth("AA:BB", "proxy-a", False, False, 200.4, 0)
    sensor = LastDeviceSeenSensor(_coord_health(health), "AA:BB")

    assert sensor.native_value == 200
    assert sensor.extra_state_attributes == {
        "device_count": 0,
        "connectable": False,
        "online": False,
    }


def test_last_device_seen_unavailable_when_absent() -> None:
    # Source dropped out of the health snapshot entirely.
    sensor = LastDeviceSeenSensor(_coord_health(), "AA:BB")
    assert sensor.available is False
    assert sensor.native_value is None
    assert sensor.extra_state_attributes is None


# --- the published slot attributes ------------------------------------------


def _slots_used_attrs(proxy: ProxySlots) -> dict:
    return SlotsUsedSensor(_coord(proxy), proxy.source).extra_state_attributes


def test_allocated_is_still_exactly_what_0_5_0_published() -> None:
    """A regression guard on a contract, not a description of the code.

    `allocated` has been published since 0.1 and user automations read it, so
    adding `allocated_devices` beside it must leave it byte-identical: a plain
    list of the raw address strings habluetooth reported, in habluetooth's
    order and habluetooth's spelling, and a copy rather than the live list.
    """
    proxy = ProxySlots(
        "AA:BB", "proxy-a", 3, 1, ["11:22", "33:44"],
        {"11:22": DeviceRef("Madoka salon", "dev_1")},
    )
    allocated = _slots_used_attrs(proxy)["allocated"]

    assert allocated == ["11:22", "33:44"]
    assert type(allocated) is list
    assert [type(a) for a in allocated] == [str, str]
    assert allocated is not proxy.allocated   # a copy, not the live list


def test_allocated_and_allocated_devices_describe_the_same_slots() -> None:
    """The two attributes are two views of one fact and must never disagree.

    `allocated_devices` is derived from `allocated` in the model, so this is a
    guard on the published surface rather than on arithmetic: if the two ever
    came apart, an automation reading one and a dashboard reading the other
    would show different fleets.
    """
    proxy = ProxySlots(
        "AA:BB", "proxy-a", 3, 0, ["11:22", "33:44", "55:66"],
        {"33:44": DeviceRef("Madoka salon", "dev_1")},
    )
    attrs = _slots_used_attrs(proxy)
    assert [e["address"] for e in attrs["allocated_devices"]] == attrs["allocated"]


def test_allocated_devices_names_the_slots_it_can_and_says_so_when_it_cannot():
    proxy = ProxySlots(
        "AA:BB", "proxy-a", 3, 0,
        ["C3:EB:49:65:67:55", "1C:54:9E:8E:1D:2C", "1C:54:9E:90:E3:0E"],
        {
            "1C:54:9E:8E:1D:2C": DeviceRef("Madoka salon", "dev_salon"),
            "1C:54:9E:90:E3:0E": DeviceRef("Madoka parents", "dev_parents"),
        },
    )
    assert _slots_used_attrs(proxy)["allocated_devices"] == [
        # The saturated proxy from the field report: one of the three slots is
        # held by an address Home Assistant knows nothing about.
        {"address": "C3:EB:49:65:67:55", "name": "", "device_id": None},
        {"address": "1C:54:9E:8E:1D:2C", "name": "Madoka salon", "device_id": "dev_salon"},
        {"address": "1C:54:9E:90:E3:0E", "name": "Madoka parents", "device_id": "dev_parents"},
    ]


def test_allocated_devices_is_present_and_empty_on_an_idle_proxy() -> None:
    """Present, not absent: `state_attr()` returning None instead of an empty
    list is a different shape for templates to handle, and the card iterates
    it unconditionally."""
    attrs = _slots_used_attrs(ProxySlots("AA:BB", "proxy-a", 3, 3))
    assert attrs["allocated"] == []
    assert attrs["allocated_devices"] == []


def test_the_published_attributes_are_json_shaped() -> None:
    """They go into the recorder and over the websocket, so every value has to
    survive `json.dumps` -- a dataclass would not."""
    import json

    proxy = ProxySlots(
        "AA:BB", "proxy-a", 3, 2, ["11:22"], {"11:22": DeviceRef("Madoka", "dev_1")}
    )
    assert json.loads(json.dumps(_slots_used_attrs(proxy)))["allocated_devices"] == [
        {"address": "11:22", "name": "Madoka", "device_id": "dev_1"}
    ]
