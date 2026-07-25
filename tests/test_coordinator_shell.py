"""Shell-logic tests for BlueSightCoordinator.

These exercise the coordinator's own (non-HA-runtime) helpers by bypassing
``__init__`` and injecting fakes, so no real ``hass`` fixture is required.
They still import Home Assistant core; guard with ``importorskip`` so the
default pure suite stays green even on a box where HA core cannot import.
The genuinely HA-runtime bits (``_handle_push`` scheduling on the loop,
``_async_update_data`` and ``async_shutdown``) are only verifiable on
CI/Linux. The registry-backed availability path (``_build_mac_index`` and
``_device_is_alive``) is covered here with lightweight fakes for the device
registry, entity registry and state machine, so it stays Windows-runnable.
"""
import asyncio

import pytest

pytest.importorskip("homeassistant.helpers.update_coordinator")

from custom_components.bluesight import coordinator as coordinator_module
from custom_components.bluesight.coordinator import BlueSightCoordinator
from custom_components.bluesight.model import IncidentKind
from custom_components.bluesight.window import FailureWindow


def _bare_coordinator() -> BlueSightCoordinator:
    """A coordinator instance without HA wiring (no hass, no habluetooth)."""
    c = object.__new__(BlueSightCoordinator)
    c._window = FailureWindow(window_s=300, threshold=5, clock=lambda: 0.0)
    c._prev_availability = {}
    c._availability_degraded = False
    return c


class _FakeAdapter:
    """Records start()/stop() so subscription lifecycle is observable."""

    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True


def test_name_for_falls_back_to_source():
    assert _bare_coordinator()._name_for("AA:BB:CC") == "AA:BB:CC"


def test_record_flaps_records_present_to_absent():
    c = _bare_coordinator()
    c._record_flaps({"AA": True})
    assert c._window.count("AA") == 0
    c._record_flaps({"AA": False})   # present -> absent flap
    assert c._window.count("AA") == 1


def test_record_flaps_ignores_non_transitions():
    c = _bare_coordinator()
    c._record_flaps({"AA": False})   # first seen absent: no prior True
    c._record_flaps({"AA": False})
    assert c._window.count("AA") == 0
    c._record_flaps({"BB": True})
    c._record_flaps({"BB": True})    # stays present
    assert c._window.count("BB") == 0


def test_snapshot_flags_ghost_slot(monkeypatch):
    c = _bare_coordinator()

    class _FakeAlloc:
        source, slots, free, allocated = "AA", 3, 2, ["11:22"]

    class _FakeMgr:
        def async_current_allocations(self, source=None):
            return [_FakeAlloc()]

    c._manager = _FakeMgr()
    c.hass = object()
    # Isolate the ghost-detection wiring: stub the registry access and force
    # the device to read as dead so a ghost incident must be produced.
    c._build_mac_index = lambda: {}
    monkeypatch.setattr(coordinator_module.er, "async_get", lambda hass: None)
    c._device_is_alive = lambda address, mac_index, ent_reg: False

    data = c._snapshot()
    assert [p.name for p in data.proxies] == ["AA"]   # _name_for fallback
    assert any(i.kind is IncidentKind.GHOST_SLOT for i in data.incidents)


# --- Registry-backed availability: fakes for dr/er/states ---------------

class _FakeDevice:
    def __init__(self, device_id, *, connections=(), identifiers=()):
        self.id = device_id
        self.connections = set(connections)
        self.identifiers = set(identifiers)


class _FakeDeviceRegistry:
    def __init__(self, devices):
        # Mirrors dr.async_get(hass).devices.values().
        self.devices = {d.id: d for d in devices}


class _FakeEntry:
    def __init__(self, entity_id):
        self.entity_id = entity_id


class _FakeState:
    def __init__(self, state):
        self.state = state


class _FakeStates:
    def __init__(self, mapping):
        self._mapping = mapping

    def get(self, entity_id):
        return self._mapping.get(entity_id)


class _FakeHass:
    def __init__(self, states):
        self.states = _FakeStates(states)


def _wire_registries(monkeypatch, *, devices, entries_by_device, states):
    """Point coordinator_module's dr/er at fakes and return a hass fake."""
    dev_reg = _FakeDeviceRegistry(devices)
    monkeypatch.setattr(coordinator_module.dr, "async_get", lambda hass: dev_reg)
    monkeypatch.setattr(coordinator_module.er, "async_get", lambda hass: object())
    monkeypatch.setattr(
        coordinator_module.er,
        "async_entries_for_device",
        lambda reg, device_id, include_disabled_entities=False: entries_by_device.get(
            device_id, []
        ),
    )
    return _FakeHass(states)


def test_build_mac_index_scans_identifiers_and_connections(monkeypatch):
    c = _bare_coordinator()
    devices = [
        # madoka: MAC lives in identifiers, connections empty (real registry).
        _FakeDevice("dev_madoka", identifiers={("daikin_madoka", "1C:54:9E:8E:1D:2C")}),
        # other integration: MAC lives in a Bluetooth connection.
        _FakeDevice(
            "dev_other",
            connections={(coordinator_module.dr.CONNECTION_BLUETOOTH, "AA:BB:CC:DD:EE:FF")},
        ),
        # non-MAC identifier must be ignored (no pollution).
        _FakeDevice("dev_cloud", identifiers={("some_cloud", "account-12345")}),
    ]
    c.hass = _wire_registries(
        monkeypatch, devices=devices, entries_by_device={}, states={}
    )
    index = c._build_mac_index()
    # Both MAC shapes resolve; lookup is case-insensitive via normalize.
    assert index["1C:54:9E:8E:1D:2C"] == "dev_madoka"
    assert index["AA:BB:CC:DD:EE:FF"] == "dev_other"
    # The non-MAC identifier did not land in the index.
    assert "ACCOUNT-12345" not in index
    assert len(index) == 2


def test_build_mac_index_ignores_non_bluetooth_connections(monkeypatch):
    # A device with BOTH a Bluetooth MAC and a network (wifi) MAC connection:
    # only the Bluetooth one may be indexed, or a BLE allocation could collide
    # with some dead device's wifi MAC and be falsely flagged as a ghost.
    from homeassistant.helpers.device_registry import (
        CONNECTION_BLUETOOTH,
        CONNECTION_NETWORK_MAC,
    )

    c = _bare_coordinator()
    devices = [
        _FakeDevice(
            "dev_dual",
            connections={
                (CONNECTION_BLUETOOTH, "AA:BB:CC:DD:EE:FF"),
                (CONNECTION_NETWORK_MAC, "11:22:33:44:55:66"),
            },
        ),
    ]
    c.hass = _wire_registries(
        monkeypatch, devices=devices, entries_by_device={}, states={}
    )
    index = c._build_mac_index()
    assert index["AA:BB:CC:DD:EE:FF"] == "dev_dual"   # Bluetooth: indexed
    assert "11:22:33:44:55:66" not in index           # network MAC: ignored
    assert len(index) == 1


def test_device_is_alive_true_when_address_not_registered(monkeypatch):
    c = _bare_coordinator()
    c.hass = _wire_registries(
        monkeypatch, devices=[], entries_by_device={}, states={}
    )
    ent_reg = coordinator_module.er.async_get(c.hass)
    # Address maps to no device -> conservative alive (never flag unmanaged).
    assert c._device_is_alive("11:22:33:44:55:66", {}, ent_reg) is True
    assert c._availability_degraded is False


def test_device_is_alive_true_for_working_thermostat(monkeypatch):
    # The exact false-positive case: a working madoka whose climate entity is
    # 'off' (not advertising because it holds a slot) must read as ALIVE.
    c = _bare_coordinator()
    mac = "1C:54:9E:8E:1D:2C"
    devices = [_FakeDevice("dev_madoka", identifiers={("daikin_madoka", mac)})]
    c.hass = _wire_registries(
        monkeypatch,
        devices=devices,
        entries_by_device={"dev_madoka": [_FakeEntry("climate.madoka1")]},
        states={"climate.madoka1": _FakeState("off")},
    )
    ent_reg = coordinator_module.er.async_get(c.hass)
    index = c._build_mac_index()
    assert c._device_is_alive(mac, index, ent_reg) is True


def test_device_is_alive_false_when_all_entities_unavailable(monkeypatch):
    # A genuinely dead device: every entity is 'unavailable' -> ghost.
    c = _bare_coordinator()
    mac = "1C:54:9E:90:E3:0E"
    devices = [_FakeDevice("dev_dead", identifiers={("daikin_madoka", mac)})]
    c.hass = _wire_registries(
        monkeypatch,
        devices=devices,
        entries_by_device={
            "dev_dead": [_FakeEntry("climate.parents"), _FakeEntry("sensor.parents_rssi")]
        },
        states={
            "climate.parents": _FakeState("unavailable"),
            "sensor.parents_rssi": _FakeState("unavailable"),
        },
    )
    ent_reg = coordinator_module.er.async_get(c.hass)
    index = c._build_mac_index()
    assert c._device_is_alive(mac, index, ent_reg) is False


def test_device_is_alive_fails_toward_alive_and_flags_degraded(monkeypatch, caplog):
    c = _bare_coordinator()

    class _BoomReg:
        pass

    def _boom(reg, device_id, include_disabled_entities=False):
        raise RuntimeError("entity registry not yet loaded")

    monkeypatch.setattr(coordinator_module.er, "async_entries_for_device", _boom)
    c.hass = _FakeHass({})
    with caplog.at_level("WARNING"):
        result = c._device_is_alive("AA:BB:CC:DD:EE:FF", {"AA:BB:CC:DD:EE:FF": "dev"}, _BoomReg())
    assert result is True
    assert c._availability_degraded is True
    assert any(r.levelname == "WARNING" for r in caplog.records)


# --- I1: async_setup must not leak the subscription on refresh failure ----

def test_async_setup_starts_subscription_after_successful_refresh():
    c = _bare_coordinator()
    c._adapter = _FakeAdapter()
    order = []

    async def _ok_refresh():
        order.append("refresh")

    c.async_config_entry_first_refresh = _ok_refresh
    c._adapter.start = lambda: order.append("start")  # type: ignore[method-assign]

    asyncio.run(c.async_setup())
    assert order == ["refresh", "start"]   # refresh strictly before start


def test_async_setup_does_not_start_subscription_if_first_refresh_fails():
    c = _bare_coordinator()
    c._adapter = _FakeAdapter()

    async def _boom_refresh():
        raise RuntimeError("first refresh failed (ConfigEntryNotReady)")

    c.async_config_entry_first_refresh = _boom_refresh

    with pytest.raises(RuntimeError):
        asyncio.run(c.async_setup())
    # Refresh-before-start ordering means no callback was ever registered,
    # so there is nothing to leak.
    assert c._adapter.started is False
