"""Shell-logic tests for BlueSightCoordinator.

These exercise the coordinator's own (non-HA-runtime) helpers by bypassing
``__init__`` and injecting fakes, so no real ``hass`` fixture is required.
They still import Home Assistant core; guard with ``importorskip`` so the
default pure suite stays green even on a box where HA core cannot import.
The genuinely HA-runtime bits (``_handle_push`` scheduling on the loop,
``_async_update_data`` and ``async_shutdown``) are only verifiable on
CI/Linux. The registry-backed availability path (``_build_device_index`` and
``_device_is_alive``) is covered here with lightweight fakes for the device
registry, entity registry and state machine, so it stays Windows-runnable.
"""
import asyncio

import pytest

pytest.importorskip("homeassistant.helpers.update_coordinator")

from custom_components.bluesight import coordinator as coordinator_module
from custom_components.bluesight.coordinator import BlueSightCoordinator
from custom_components.bluesight.device_index import DeviceIndex
from custom_components.bluesight.model import IncidentKind
from custom_components.bluesight.storm_signal import ReleaseTracker
from custom_components.bluesight.telemetry import CounterDeltas
from custom_components.bluesight.telemetry_reader import (
    BONDS_NAME,
    SLOTS_NAME,
    SMP_NAME,
)
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
    # Proxy telemetry (v1.5). The deltas object outlives the snapshot, so a
    # bare coordinator needs its own rather than sharing one.
    c._counter_deltas = CounterDeltas()
    c._idle_threshold_s = 300.0
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
    c._build_device_index = lambda: DeviceIndex({}, {})
    monkeypatch.setattr(coordinator_module.er, "async_get", lambda hass: None)
    c._device_is_alive = lambda address, mac_index, ent_reg: False

    data = c._snapshot()
    assert [p.name for p in data.proxies] == ["AA"]   # _name_for fallback
    assert any(i.kind is IncidentKind.GHOST_SLOT for i in data.incidents)


# --- Registry-backed availability: fakes for dr/er/states ---------------

class _FakeDevice:
    def __init__(
        self,
        device_id,
        *,
        connections=(),
        identifiers=(),
        name=None,
        name_by_user=None,
    ):
        self.id = device_id
        self.connections = set(connections)
        self.identifiers = set(identifiers)
        # What the card shows for an allocated address. `name_by_user` wins.
        self.name = name
        self.name_by_user = name_by_user


class _FakeDeviceRegistry:
    def __init__(self, devices):
        # Mirrors dr.async_get(hass).devices.values().
        self.devices = {d.id: d for d in devices}


class _FakeEntry:
    def __init__(self, entity_id, original_name=None):
        self.entity_id = entity_id
        # The telemetry reader discovers sensors by `original_name`; the
        # availability path never looks at it.
        self.original_name = original_name


class _FakeState:
    def __init__(self, state):
        self.state = state


class _FakeStates:
    def __init__(self, mapping):
        self._mapping = mapping

    def get(self, entity_id):
        return self._mapping.get(entity_id)


class _FakeConfigEntry:
    """The one config-entry attribute the coordinator reads."""

    def __init__(self, data):
        self.data = data


class _FakeConfigEntries:
    def __init__(self, entries):
        self._entries = [_FakeConfigEntry(d) for d in entries]

    def async_entries(self, domain=None):
        return list(self._entries)


class _FakeHass:
    def __init__(self, states, scanner_entries=()):
        self.states = _FakeStates(states)
        self.config_entries = _FakeConfigEntries(scanner_entries)


def _wire_registries(
    monkeypatch, *, devices, entries_by_device, states, scanner_entries=()
):
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
    return _FakeHass(states, scanner_entries)


def test_build_device_index_scans_identifiers_and_connections(monkeypatch):
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
    index = c._build_device_index().peripherals
    # Both MAC shapes resolve; lookup is case-insensitive via normalize.
    assert index["1C:54:9E:8E:1D:2C"] == "dev_madoka"
    assert index["AA:BB:CC:DD:EE:FF"] == "dev_other"
    # The non-MAC identifier did not land in the index.
    assert "ACCOUNT-12345" not in index
    assert len(index) == 2


def test_build_device_index_ignores_non_bluetooth_connections(monkeypatch):
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
    index = c._build_device_index().peripherals
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
    index = c._build_device_index().peripherals
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
    index = c._build_device_index().peripherals
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
    c._build_device_index = lambda: DeviceIndex({}, {})
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
    c._build_device_index = lambda: DeviceIndex({}, {})
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
    c._build_device_index = lambda: DeviceIndex({}, {})
    monkeypatch.setattr(coordinator_module.er, "async_get", lambda hass: None)
    c._device_is_alive = lambda address, mac_index, ent_reg: False

    c._snapshot()
    assert c.storm_window.count("11:22:33:44:55:66") == 0
    allocated.clear()               # connection failed, slot released
    c._snapshot()
    assert c.storm_window.count("11:22:33:44:55:66") == 1


# --- Task 8: ESPHome proxy telemetry -------------------------------------
#
# The seam these cover is an address-canonicalisation one, and not the obvious
# one. A Bluetooth proxy is not a BLE peripheral: Home Assistant's ESPHome
# integration registers it with (CONNECTION_NETWORK_MAC, mac_address), and that
# same mac_address string is what habluetooth reports as the remote scanner's
# source. The availability index deliberately refuses network MACs (a wifi MAC
# must never judge a BLE address), so resolving a proxy through it would find
# nothing at all -- silently, with every proxy reading as having no telemetry.
# device_index therefore keeps two indexes; these check the coordinator asks
# each of them the right question.

PROXY = "D8:3B:DA:11:22:35"
PERIPHERAL = "D0:CF:13:0E:C9:2A"


def _telemetry_manager(allocated):
    """A manager fake with one proxy: a scanner, plus optional allocations."""

    class _FakeScanner:
        source = PROXY
        name = "Kitchen proxy"
        connectable = True
        discovered_devices = []

        def time_since_last_detection(self):
            return 1.0

    class _FakeAlloc:
        # `allocated` is bound in __init__, not in the class body: a class
        # body is not a closure over the enclosing function's locals.
        source, slots, free = PROXY, 3, 2

        def __init__(self, addresses):
            self.allocated = list(addresses)

    class _FakeMgr:
        def async_current_allocations(self, source=None):
            return [_FakeAlloc(allocated)] if allocated else []

        def async_current_scanners(self):
            return [_FakeScanner()]

    return _FakeMgr()


#: What Home Assistant's ESPHome integration puts in the device registry: the
#: Wi-Fi MAC, deliberately NOT equal to PROXY. On ESP32 the Bluetooth MAC is
#: the base MAC + 2, and `bleak_esphome` uses the Bluetooth MAC as the scanner
#: source when the firmware reports one. Making them differ here is the point:
#: it means every test below fails if proxy resolution ever falls back to
#: matching the source against a device-registry connection.
PROXY_WIFI_MAC = "D8:3B:DA:11:22:33"

#: The record Home Assistant keeps for exactly this: one `bluetooth` config
#: entry per external scanner, keyed by the scanner's own source string.
SCANNER_ENTRY = {"source": PROXY, "source_device_id": "dev_proxy"}


def _telemetry_coordinator(
    monkeypatch,
    *,
    entries,
    states,
    allocated=(),
    extra_devices=(),
    proxy_connection_mac=PROXY_WIFI_MAC,
    scanner_entries=(SCANNER_ENTRY,),
):
    """A coordinator whose one proxy is registered the way ESPHome does it."""
    c = _bare_coordinator()
    c._manager = _telemetry_manager(allocated)
    devices = [
        _FakeDevice(
            "dev_proxy",
            connections={
                (coordinator_module.dr.CONNECTION_NETWORK_MAC, proxy_connection_mac)
            },
        ),
        *extra_devices,
    ]
    c.hass = _wire_registries(
        monkeypatch,
        devices=devices,
        entries_by_device=entries,
        states=states,
        scanner_entries=scanner_entries,
    )
    return c


def test_a_proxy_with_telemetry_entities_raises_bond_lost(monkeypatch):
    """End to end: registry -> reader -> parser -> detector -> incident."""
    c = _telemetry_coordinator(
        monkeypatch,
        entries={
            "dev_proxy": [
                _FakeEntry("sensor.smp", SMP_NAME),
                _FakeEntry("sensor.bonds", BONDS_NAME),
            ]
        },
        states={
            "sensor.smp": _FakeState("d0cf130ec92a:3"),
            # Reporting zero bonds, which is a reading and not an absence.
            "sensor.bonds": _FakeState(""),
        },
    )
    data = c._snapshot()
    # The proxy resolved through its NETWORK MAC and reported.
    assert [t.source for t in data.telemetry] == [PROXY]
    [incident] = [i for i in data.incidents if i.kind is IncidentKind.BOND_LOST]
    assert incident.address == PERIPHERAL
    assert incident.sources == [PROXY]
    assert incident.evidence == "smp"
    # Named from the health snapshot, which is keyed on the canonical source.
    assert incident.detail_params["proxy"] == "Kitchen proxy"


def test_a_proxy_without_the_component_is_absent_from_the_snapshot(monkeypatch):
    c = _telemetry_coordinator(
        monkeypatch,
        entries={"dev_proxy": [_FakeEntry("sensor.uptime", "Uptime")]},
        states={"sensor.uptime": _FakeState("42")},
    )
    data = c._snapshot()
    assert data.telemetry == []
    assert data.incidents == []


def test_forget_proxy_clears_the_counter_baseline(monkeypatch):
    """A retired proxy must not keep a baseline: a replacement reusing the MAC
    would inherit a stranger's counter and replay the gap as a burst."""
    smp = _FakeState("d0cf130ec92a:3")
    c = _telemetry_coordinator(
        monkeypatch,
        entries={"dev_proxy": [_FakeEntry("sensor.smp", SMP_NAME)]},
        states={"sensor.smp": smp},
    )
    c._snapshot()                                    # first reading: baseline
    assert c.storm_window.count(PERIPHERAL) == 0
    smp.state = "d0cf130ec92a:5"
    c._snapshot()
    assert c.storm_window.count(PERIPHERAL) == 2     # two measured failures
    assert c.forget_proxy(PROXY) is True
    smp.state = "d0cf130ec92a:9"
    c._snapshot()
    # Re-baselined at 9 rather than counting 5->9 against a stale baseline.
    assert c.storm_window.count(PERIPHERAL) == 2


def _idle_slot_data(monkeypatch, *, entity_state=None, allocated=(PERIPHERAL,)):
    """A snapshot where the firmware reports a 600s-idle slot (threshold 300).

    ``entity_state`` None means Home Assistant has no device for the address at
    all -- the unmanaged case detect_idle_slots exists to cover.
    """
    extra_devices = []
    entries = {"dev_proxy": [_FakeEntry("sensor.slots", SLOTS_NAME)]}
    states = {"sensor.slots": _FakeState("d0cf130ec92a:600")}
    if entity_state is not None:
        extra_devices = [
            _FakeDevice("dev_madoka", identifiers={("daikin_madoka", PERIPHERAL)})
        ]
        entries["dev_madoka"] = [_FakeEntry("climate.madoka")]
        states["climate.madoka"] = _FakeState(entity_state)
    c = _telemetry_coordinator(
        monkeypatch,
        entries=entries,
        states=states,
        allocated=allocated,
        extra_devices=extra_devices,
    )
    return c._snapshot()


def _ghosts(data):
    return [i for i in data.incidents if i.kind is IncidentKind.GHOST_SLOT]


def test_an_unmanaged_idle_slot_is_still_flagged(monkeypatch):
    """managed_addresses must not stand the detector down everywhere.

    Passing set(availability) -- every allocated address, unmanaged ones
    included -- would silently disable exactly this case, and nothing would
    look wrong.
    """
    [ghost] = _ghosts(_idle_slot_data(monkeypatch))
    assert (ghost.address, ghost.sources, ghost.evidence) == (PERIPHERAL, [PROXY], "smp")


def test_a_connection_that_is_not_a_home_assistant_slot_is_not_flagged(monkeypatch):
    """The `ble_client:` case, end to end, on the shape of the user's own node.

    `allocated=()` with no registry device: the firmware reports a connection
    idle for 600 s whose peer Home Assistant has never heard of, and
    habluetooth holds no slot for it. That is an ESPHome `ble_client:` link --
    a Madoka pairing responder on `atomesalon`, say -- doing exactly its job.
    It draws on the node's `max_connections` pool, not on the slots the proxy
    advertises, so `GHOST_SLOT` would be a true measurement under a false
    frame: its remedy says restart the proxy to free the slot, and the restart
    would free no slot Home Assistant was waiting on.
    """
    assert _ghosts(_idle_slot_data(monkeypatch, allocated=())) == []


def test_a_managed_idle_slot_is_reported_once_not_twice(monkeypatch):
    """Both detectors raise GHOST_SLOT for this address from this proxy, so a
    slot judged by both would produce two incidents identical under
    Incident.key: one fault drawn twice, one clearance counted as two."""
    [ghost] = _ghosts(_idle_slot_data(monkeypatch, entity_state="unavailable"))
    # The entity verdict won, as the more semantic of the two.
    assert ghost.evidence == "heuristic"


def test_a_managed_device_judged_alive_is_not_flagged_idle(monkeypatch):
    """A working thermostat whose climate entity is 'off' holds its connection
    quietly. Home Assistant can judge it and says it is fine; the firmware's
    idle timer must not overrule that."""
    assert _ghosts(_idle_slot_data(monkeypatch, entity_state="off")) == []


def test_a_registry_known_address_is_not_flagged_from_a_stale_reading(monkeypatch):
    """allocated=(): habluetooth does not list the address, so it is absent
    from availability. Deriving "managed" from availability's keys would call
    this address unmanaged and flag a device Home Assistant judges alive -- the
    ordinary cause being a firmware text sensor that has not published since
    the disconnect. The index, not the allocation list, is the authority on
    what Home Assistant can judge."""
    assert _ghosts(_idle_slot_data(monkeypatch, entity_state="off", allocated=())) == []


def _smp_only(states_value="d0cf130ec92a:3", **kwargs):
    """A proxy publishing just the SMP counter; enough to prove it resolved."""
    return dict(
        entries={"dev_proxy": [_FakeEntry("sensor.smp", SMP_NAME)]},
        states={"sensor.smp": _FakeState(states_value)},
        **kwargs,
    )


def test_a_proxy_resolves_through_home_assistants_scanner_record(monkeypatch):
    """The Bluetooth MAC habluetooth reports as `source` is in no device
    registry connection -- the ESPHome integration registers the Wi-Fi MAC.
    Resolution therefore goes through the `bluetooth` integration's own
    per-scanner config entry, which is keyed by that same source string."""
    c = _telemetry_coordinator(monkeypatch, **_smp_only())
    assert [t.source for t in c._snapshot().telemetry] == [PROXY]


def test_without_that_record_the_wifi_mac_cannot_stand_in_for_it(monkeypatch):
    """The failure this guards against is silent: no crash, no warning, just
    every proxy reading as having no telemetry."""
    c = _telemetry_coordinator(monkeypatch, **_smp_only(scanner_entries=()))
    assert c._snapshot().telemetry == []


def test_a_legacy_proxy_is_still_resolved_by_its_network_mac(monkeypatch):
    """Firmware too old to report a Bluetooth MAC makes `bleak_esphome` fall
    back to the Wi-Fi MAC as the scanner source, and an older Home Assistant
    may have no `source_device_id` on the entry. MAC correlation still covers
    that pair, which is why it is kept as the fallback."""
    c = _telemetry_coordinator(
        monkeypatch, **_smp_only(scanner_entries=(), proxy_connection_mac=PROXY)
    )
    assert [t.source for t in c._snapshot().telemetry] == [PROXY]


def test_an_unreadable_config_entry_list_falls_back_instead_of_degrading(monkeypatch):
    """Losing this record costs telemetry, not ghost detection, so it must not
    raise and must not flag the availability signal as degraded."""
    c = _telemetry_coordinator(
        monkeypatch, **_smp_only(proxy_connection_mac=PROXY)
    )

    def _boom(domain=None):
        raise RuntimeError("config entries not loaded")

    c.hass.config_entries.async_entries = _boom
    assert [t.source for t in c._snapshot().telemetry] == [PROXY]
    assert c._availability_degraded is False


# --- naming the devices that hold the slots ---------------------------------
#
# The resolution is done here, in Python, and never in the card: the peripheral
# index settles which registry evidence may speak for a BLE address, and
# reimplementing that rule in JavaScript would be a correctness risk for no
# benefit.

SALON = "1C:54:9E:8E:1D:2C"
UNKNOWN = "C3:EB:49:65:67:55"


class _AllocOnly:
    """A manager that reports one saturated proxy and no scanners."""

    def __init__(self, source, slots, free, allocated):
        self._alloc = type(
            "_A",
            (),
            {"source": source, "slots": slots, "free": free, "allocated": allocated},
        )()

    def async_current_allocations(self, source=None):
        return [self._alloc]

    def async_current_scanners(self):
        return []


def _saturated_proxy_coordinator(monkeypatch, devices, entries_by_device, states):
    c = _bare_coordinator()
    c._manager = _AllocOnly("D8:3B:DA:11:22:33", 3, 1, [UNKNOWN, SALON])
    c.hass = _wire_registries(
        monkeypatch,
        devices=devices,
        entries_by_device=entries_by_device,
        states=states,
    )
    return c


def test_snapshot_names_the_device_holding_each_slot(monkeypatch):
    """The whole feature, end to end through the shell: a saturated proxy whose
    slots are named, and the one address the registry cannot account for left
    as a raw MAC for the card to mark."""
    c = _saturated_proxy_coordinator(
        monkeypatch,
        devices=[
            _FakeDevice(
                "dev_salon",
                identifiers={("daikin_madoka", SALON)},
                name="Madoka BRC1H",
                name_by_user="Madoka salon",
            )
        ],
        entries_by_device={"dev_salon": [_FakeEntry("climate.salon")]},
        states={"climate.salon": _FakeState("off")},
    )
    assert c._snapshot().proxies[0].allocated_devices == [
        {"address": UNKNOWN, "name": "", "device_id": None},
        {"address": SALON, "name": "Madoka salon", "device_id": "dev_salon"},
    ]


def test_a_slot_named_through_a_wifi_mac_is_not_named_at_all(monkeypatch):
    """The asymmetry the peripheral index exists for, applied to naming.

    A network MAC is not evidence about a BLE peripheral. If it were allowed
    to name one, an allocated BLE address colliding with some device's Wi-Fi
    MAC would be labelled with that device's name -- a confident lie, which is
    worse here than the raw MAC the card would otherwise show.
    """
    c = _saturated_proxy_coordinator(
        monkeypatch,
        devices=[
            _FakeDevice(
                "dev_router",
                connections={(coordinator_module.dr.CONNECTION_NETWORK_MAC, UNKNOWN)},
                name="Some router",
            )
        ],
        entries_by_device={},
        states={},
    )
    named = c._snapshot().proxies[0].allocated_devices
    assert named[0] == {"address": UNKNOWN, "name": "", "device_id": None}


def test_naming_walks_the_device_registry_once_per_snapshot(monkeypatch):
    """Naming is one more lookup in an index that is already built, not a
    registry scan per allocated address."""
    c = _saturated_proxy_coordinator(
        monkeypatch,
        devices=[
            _FakeDevice("dev_salon", identifiers={("daikin_madoka", SALON)}, name="Salon")
        ],
        entries_by_device={},
        states={},
    )
    fetched = coordinator_module.dr.async_get
    calls = []

    def _counted(hass):
        calls.append(hass)
        return fetched(hass)

    monkeypatch.setattr(coordinator_module.dr, "async_get", _counted)
    c._snapshot()
    assert len(calls) == 1


def test_a_broken_registry_costs_the_names_and_nothing_else(monkeypatch):
    """`_build_device_index` fails toward empty indexes. Every slot then reads
    as unresolved -- honest, since nothing could be looked up -- and the slot
    counts, which come from habluetooth, are untouched."""
    c = _saturated_proxy_coordinator(monkeypatch, devices=[], entries_by_device={}, states={})
    c._build_device_index = lambda: DeviceIndex({}, {})

    proxy = c._snapshot().proxies[0]
    assert proxy.used == 2
    assert proxy.allocated == [UNKNOWN, SALON]
    assert [e["name"] for e in proxy.allocated_devices] == ["", ""]
