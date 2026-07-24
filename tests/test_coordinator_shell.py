"""Shell-logic tests for BleTriageCoordinator.

These exercise the coordinator's own (non-HA-runtime) helpers by bypassing
``__init__`` and injecting fakes, so no real ``hass`` fixture is required.
They still import Home Assistant core; guard with ``importorskip`` so the
default pure suite stays green even on a box where HA core cannot import.
The genuinely HA-runtime bits (``_handle_push``, ``async_setup``,
``_async_update_data``, the real ``_is_available`` bluetooth call, and
``async_shutdown``) are only verifiable on CI/Linux.
"""
import pytest

pytest.importorskip("homeassistant.helpers.update_coordinator")

from custom_components.ble_triage.coordinator import BleTriageCoordinator
from custom_components.ble_triage.model import IncidentKind
from custom_components.ble_triage.window import FailureWindow


def _bare_coordinator() -> BleTriageCoordinator:
    """A coordinator instance without HA wiring (no hass, no habluetooth)."""
    c = object.__new__(BleTriageCoordinator)
    c._window = FailureWindow(window_s=300, threshold=5, clock=lambda: 0.0)
    c._prev_availability = {}
    return c


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


def test_snapshot_flags_ghost_slot():
    c = _bare_coordinator()

    class _FakeAlloc:
        source, slots, free, allocated = "AA", 3, 2, ["11:22"]

    class _FakeMgr:
        def async_current_allocations(self, source=None):
            return [_FakeAlloc()]

    c._manager = _FakeMgr()
    c._is_available = lambda address: False   # device absent -> ghost slot

    data = c._snapshot()
    assert [p.name for p in data.proxies] == ["AA"]   # _name_for fallback
    assert any(i.kind is IncidentKind.GHOST_SLOT for i in data.incidents)
