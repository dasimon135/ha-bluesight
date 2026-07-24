"""Shell-logic tests for BlueSightCoordinator.

These exercise the coordinator's own (non-HA-runtime) helpers by bypassing
``__init__`` and injecting fakes, so no real ``hass`` fixture is required.
They still import Home Assistant core; guard with ``importorskip`` so the
default pure suite stays green even on a box where HA core cannot import.
The genuinely HA-runtime bits (``_handle_push`` scheduling on the loop,
``_async_update_data``, the real ``async_address_present`` call inside
``_is_available``, and ``async_shutdown``) are only verifiable on CI/Linux.
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


# --- I2: _is_available degrades loudly instead of silently ---------------

def test_is_available_returns_underlying_result_when_ok(monkeypatch):
    c = _bare_coordinator()
    c.hass = object()
    monkeypatch.setattr(
        coordinator_module, "async_address_present",
        lambda hass, address, connectable=True: False,
    )
    assert c._is_available("AA") is False
    assert c._availability_degraded is False


def test_is_available_fails_toward_present_and_flags_degraded(monkeypatch, caplog):
    c = _bare_coordinator()
    c.hass = object()

    def _boom(hass, address, connectable=True):
        raise RuntimeError("bluetooth integration not yet loaded")

    monkeypatch.setattr(coordinator_module, "async_address_present", _boom)
    with caplog.at_level("WARNING"):
        result = c._is_available("AA")
    # Fail toward present (avoid fabricating false ghosts) but record degradation.
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
