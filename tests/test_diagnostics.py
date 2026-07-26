"""Shape tests for the diagnostics dump.

Built against fakes so no ``hass`` fixture is needed; the point is that the
dump is JSON-serialisable and actually carries the triage state a bug report
needs.
"""
import asyncio
import json

import pytest

pytest.importorskip("homeassistant.core")

from custom_components.bluesight.coordinator_data import BlueSightData
from custom_components.bluesight.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.bluesight.model import (
    Incident,
    IncidentKind,
    ProxyHealth,
    ProxySlots,
)
from custom_components.bluesight.window import FailureWindow


class _FakeCoordinator:
    def __init__(self, data):
        self.data = data
        self.storm_window = FailureWindow(300.0, 5, clock=lambda: 0.0)
        self.reboot_window = FailureWindow(600.0, 3, clock=lambda: 0.0)
        self.tracked_sources = {"AA:BB:CC:DD:EE:FF"}


class _FakeEntry:
    data = {}
    options = {"storm_threshold": 4}

    def __init__(self, coordinator):
        self.runtime_data = coordinator


def _dump(data):
    coordinator = _FakeCoordinator(data)
    coordinator.storm_window.record("11:22:33:44:55:66")
    return asyncio.run(
        async_get_config_entry_diagnostics(None, _FakeEntry(coordinator))
    )


def test_dump_is_json_serialisable_and_complete():
    data = BlueSightData(
        proxies=[ProxySlots("AA:BB:CC:DD:EE:FF", "salon", 3, 1, ["11:22:33:44:55:66"])],
        incidents=[
            Incident(IncidentKind.DEADLOCK, "11:22:33:44:55:66", ["AA", "BB"], "x"),
            Incident(IncidentKind.GHOST_SLOT, "11:22:33:44:55:66", ["AA"], "y"),
        ],
        proxies_health=[ProxyHealth("AA:BB:CC:DD:EE:FF", "salon", True, True, 2.0, 7)],
        availability_degraded=True,
    )
    dump = _dump(data)
    json.dumps(dump)   # must not raise

    assert dump["options"] == {"storm_threshold": 4}
    assert dump["availability_degraded"] is True
    assert dump["proxies"][0]["allocated"] == ["11:22:33:44:55:66"]
    assert dump["proxies_health"][0]["device_count"] == 7
    assert dump["tracked_sources"] == ["AA:BB:CC:DD:EE:FF"]
    assert dump["storm_window"]["counts"] == {"11:22:33:44:55:66": 1}
    assert dump["reboot_window"]["counts"] == {}


def test_dump_reports_which_incidents_were_actually_notified():
    """The ghost slot is suppressed by the deadlock on the same address, so the
    dump must show the deduped set alongside the raw one."""
    data = BlueSightData(
        incidents=[
            Incident(IncidentKind.DEADLOCK, "11:22", ["AA", "BB"], "x"),
            Incident(IncidentKind.GHOST_SLOT, "11:22", ["AA"], "y"),
        ]
    )
    dump = _dump(data)
    assert len(dump["incidents"]) == 2
    assert dump["notified_incidents"] == ["deadlock:11:22:AA,BB"]


def test_addresses_are_not_redacted():
    """A redacted dump cannot show the same address held on two proxies, which
    is the failure mode this integration exists to diagnose."""
    data = BlueSightData(
        proxies=[ProxySlots("AA:BB:CC:DD:EE:FF", "salon", 3, 2, ["11:22:33:44:55:66"])]
    )
    assert "11:22:33:44:55:66" in json.dumps(_dump(data))
