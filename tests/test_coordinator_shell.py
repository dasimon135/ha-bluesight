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
from custom_components.bluesight.storm_signal import ReleaseTracker
from custom_components.bluesight.window import FailureWindow


def _bare_coordinator() -> BlueSightCoordinator:
    """A coordinator instance without HA wiring (no hass, no habluetooth)."""
    c = object.__new__(BlueSightCoordinator)
    c._window = FailureWindow(window_s=300, threshold=5, clock=lambda: 0.0)
    c._release_tracker = ReleaseTracker()
    c._availability_degraded = False
    c._stopped = False
    # Proxy-health wiring (Task 7).
    c._stalled_threshold_s = 180.0
    c._reboot_window = FailureWindow(window_s=600, threshold=3, clock=lambda: 0.0)
    c._last_online = {}
    c._offline_grace_s = 0.0
    c._names = {}
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


def test_name_for_uses_the_last_seen_scanner_name():
    """Every entity on a proxy device must report the same device name, so the
    slot sensors have to see the friendly name the health snapshot carries."""
    c = _bare_coordinator()
    c._names = {"AA:BB:CC": "proxy-salon"}
    assert c._name_for("aa:bb:cc") == "proxy-salon"


def test_forget_proxy_drops_a_tracked_source():
    c = _bare_coordinator()
    c._last_online = {"AA:BB:CC": 0.0}
    c._names = {"AA:BB:CC": "proxy-salon"}
    assert c.forget_proxy("aa:bb:cc") is True
    assert c.tracked_sources == set()
    assert c._names == {}
    assert c.forget_proxy("AA:BB:CC") is False


def test_snapshot_flags_ghost_slot(monkeypatch):
    c = _bare_coordinator()

    class _FakeAlloc:
        source, slots, free, allocated = "AA", 3, 2, ["11:22"]

    class _FakeMgr:
        def async_current_allocations(self, source=None):
            return [_FakeAlloc()]

        def async_current_scanners(self):
            return []

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
    c._scanner_adapter = _FakeAdapter()
    order = []

    async def _ok_refresh():
        order.append("refresh")

    c.async_config_entry_first_refresh = _ok_refresh
    c._adapter.start = lambda: order.append("start")  # type: ignore[method-assign]

    asyncio.run(c.async_setup())
    assert order == ["refresh", "start"]   # refresh strictly before start
    assert c._scanner_adapter.started is True   # scanner subscription started too


def test_async_setup_does_not_start_subscription_if_first_refresh_fails():
    c = _bare_coordinator()
    c._adapter = _FakeAdapter()
    c._scanner_adapter = _FakeAdapter()

    async def _boom_refresh():
        raise RuntimeError("first refresh failed (ConfigEntryNotReady)")

    c.async_config_entry_first_refresh = _boom_refresh

    with pytest.raises(RuntimeError):
        asyncio.run(c.async_setup())
    # Refresh-before-start ordering means no callback was ever registered,
    # so there is nothing to leak.
    assert c._adapter.started is False
    assert c._scanner_adapter.started is False


# --- Task 7: proxy-health wiring -----------------------------------------

class _ImmediateLoopHass:
    """hass fake whose loop runs call_soon_threadsafe synchronously, so the
    marshalled reboot record is observable without a real event loop."""

    class _Loop:
        def call_soon_threadsafe(self, fn, *args):
            fn(*args)

    def __init__(self, is_stopping=False):
        self.loop = self._Loop()
        self.is_stopping = is_stopping


def test_record_reboot_schedules_normalized_window_record():
    # The scanner-registration callback may fire from an executor thread, so
    # _record_reboot must marshal onto the loop rather than mutate the window
    # directly. With a synchronous loop fake we can assert the record landed,
    # and that the source was normalized (lowercase in -> uppercase key).
    c = _bare_coordinator()
    c.hass = _ImmediateLoopHass()
    source = "aa:bb:cc:dd:ee:ff"
    assert c._reboot_window.count("AA:BB:CC:DD:EE:FF") == 0
    c._record_reboot(source)
    assert c._reboot_window.count("AA:BB:CC:DD:EE:FF") == 1
    # The lowercase form is NOT a separate key.
    assert c._reboot_window.count(source) == 0


def test_snapshot_flags_stalled_proxy_and_records_known_source(monkeypatch):
    c = _bare_coordinator()

    class _FakeScanner:
        # Lowercase source to prove _snapshot normalizes it.
        source = "aa:bb:cc:dd:ee:ff"
        name = "Kitchen proxy"
        connectable = True
        discovered_devices = []

        def time_since_last_detection(self):
            return 500.0   # > 180s stalled threshold

    class _FakeMgr:
        def async_current_allocations(self, source=None):
            return []

        def async_current_scanners(self):
            return [_FakeScanner()]

    c._manager = _FakeMgr()
    c.hass = object()
    c._build_mac_index = lambda: {}
    monkeypatch.setattr(coordinator_module.er, "async_get", lambda hass: None)

    data = c._snapshot()
    assert any(i.kind is IncidentKind.PROXY_STALLED for i in data.incidents)
    # The stalled proxy surfaces in proxies_health and is remembered (normalized)
    # as a known source for future offline detection.
    assert "AA:BB:CC:DD:EE:FF" in c.tracked_sources
    assert [h.source for h in data.proxies_health] == ["AA:BB:CC:DD:EE:FF"]
    # Its friendly name is captured for the slot sensors' DeviceInfo.
    assert c._name_for("AA:BB:CC:DD:EE:FF") == "Kitchen proxy"


def test_record_reboot_is_skipped_while_home_assistant_stops():
    """HA shutdown unregisters every scanner; those are not reboots."""
    c = _bare_coordinator()
    c.hass = _ImmediateLoopHass(is_stopping=True)
    c._record_reboot("AA:BB:CC:DD:EE:FF")
    assert c._reboot_window.count("AA:BB:CC:DD:EE:FF") == 0


def test_push_snapshot_no_ops_once_stopped():
    """A push already queued on the loop must not snapshot a torn-down
    coordinator."""
    c = _bare_coordinator()
    c._stopped = True
    c._snapshot = lambda: pytest.fail("snapshot taken after shutdown")
    c.async_set_updated_data = lambda data: pytest.fail("published after shutdown")
    c._push_snapshot()


def _offline_coordinator(monkeypatch, *, grace_s, elapsed):
    """A coordinator whose only known proxy went away `elapsed` seconds ago."""
    c = _bare_coordinator()
    c._offline_grace_s = grace_s

    class _FakeMgr:
        def async_current_allocations(self, source=None):
            return []

        def async_current_scanners(self):
            return []

    c._manager = _FakeMgr()
    c.hass = object()
    c._build_mac_index = lambda: {}
    monkeypatch.setattr(coordinator_module.er, "async_get", lambda hass: None)
    monkeypatch.setattr(coordinator_module.time, "monotonic", lambda: elapsed)
    c._last_online = {"AA:BB:CC:DD:EE:FF": 0.0}
    return c


def test_offline_incident_waits_out_the_grace_period(monkeypatch):
    """An ESPHome proxy drops off the bus for ~20-30s on every OTA update."""
    data = _offline_coordinator(monkeypatch, grace_s=90.0, elapsed=30.0)._snapshot()
    assert not any(i.kind is IncidentKind.PROXY_OFFLINE for i in data.incidents)


def test_offline_incident_fires_after_the_grace_period(monkeypatch):
    data = _offline_coordinator(monkeypatch, grace_s=90.0, elapsed=120.0)._snapshot()
    assert [i.address for i in data.incidents if i.kind is IncidentKind.PROXY_OFFLINE] \
        == ["AA:BB:CC:DD:EE:FF"]


def test_forgetting_a_proxy_clears_its_offline_incident(monkeypatch):
    c = _offline_coordinator(monkeypatch, grace_s=90.0, elapsed=120.0)
    assert c._snapshot().incidents != []
    assert c.forget_proxy("AA:BB:CC:DD:EE:FF") is True
    assert c._snapshot().incidents == []


def test_snapshot_records_a_release_of_a_dead_device_as_a_failure(monkeypatch):
    """The storm signal: a failed connection releases its slot, so the failure
    is only observable at the release, not as an availability flap."""
    c = _bare_coordinator()
    allocated = ["11:22:33:44:55:66"]

    class _FakeAlloc:
        source, slots, free = "AA:BB:CC:DD:EE:FF", 3, 2

        @property
        def allocated(self):
            return allocated

    class _FakeMgr:
        def async_current_allocations(self, source=None):
            return [_FakeAlloc()]

        def async_current_scanners(self):
            return []

    c._manager = _FakeMgr()
    c.hass = object()
    c._build_mac_index = lambda: {}
    monkeypatch.setattr(coordinator_module.er, "async_get", lambda hass: None)
    c._device_is_alive = lambda address, mac_index, ent_reg: False

    c._snapshot()
    assert c.storm_window.count("11:22:33:44:55:66") == 0
    allocated.clear()               # connection failed, slot released
    c._snapshot()
    assert c.storm_window.count("11:22:33:44:55:66") == 1
