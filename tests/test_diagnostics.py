"""Shape tests for the diagnostics dump.

Built against fakes so no ``hass`` fixture is needed; the point is that the
dump is JSON-serialisable and actually carries the triage state a bug report
needs.
"""
import asyncio
import json

import pytest

pytest.importorskip("homeassistant.core")

from custom_components.bluesight.const import DEFAULT_BOND_THRESHOLD, OPTION_DEFAULTS
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
from custom_components.bluesight.telemetry import CounterDeltas, ProxyTelemetry
from custom_components.bluesight.window import FailureWindow


class _FakeCoordinator:
    def __init__(self, data):
        self.data = data
        self.storm_window = FailureWindow(300.0, 5, clock=lambda: 0.0)
        self.reboot_window = FailureWindow(600.0, 3, clock=lambda: 0.0)
        self.tracked_sources = {"AA:BB:CC:DD:EE:FF"}
        self.deltas = CounterDeltas()

    @property
    def counter_baselines(self):
        return self.deltas.baselines


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


def _minimal_data():
    """An empty snapshot: these tests are about the options block alone."""
    return BlueSightData(proxies=[], incidents=[], proxies_health=[])


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

    # Every tunable, not just the persisted ones: see the dedicated tests below.
    assert dump["options"]["storm_threshold"] == 4
    assert dump["options"]["bond_threshold"] == DEFAULT_BOND_THRESHOLD
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


def test_dump_carries_the_esphome_telemetry():
    """The reason this section exists: every way the telemetry can fail to
    arrive is silent, so a dump without it cannot tell "nothing is wrong" from
    "the reader has never seen anything".

    The ``set`` in ``ProxyTelemetry.bonds`` is why a bare ``asdict()`` will not
    do -- ``json.dumps`` below is the real assertion. See
    ``tests/test_diagnostics_data.py`` for the shape rules; they are tested
    there because that module needs no Home Assistant.
    """
    data = BlueSightData(
        proxies_health=[
            ProxyHealth("AA:BB:CC:DD:EE:FF", "salon", True, True, 2.0, 7),
            ProxyHealth("D0:CF:13:0E:C9:2A", "garage", True, True, 1.0, 3),
        ],
        telemetry=[
            ProxyTelemetry(
                "D0:CF:13:0E:C9:2A",
                smp_failures={"11:22:33:44:55:66": 2},
                bonds={"11:22:33:44:55:66"},
            )
        ],
    )
    dump = _dump(data)
    json.dumps(dump)  # must not raise: `bonds` is a set on the dataclass

    entry = dump["telemetry"]["reporting"][0]
    assert entry["source"] == "D0:CF:13:0E:C9:2A"
    assert entry["bonds"] == ["11:22:33:44:55:66"]
    assert entry["signals"]["slot_idle_seconds"] == "absent"
    assert entry["slot_idle_seconds"] is None
    # The proxy that reported nothing is named rather than silently missing.
    assert dump["telemetry"]["silent_sources"] == ["AA:BB:CC:DD:EE:FF"]


def test_dump_carries_the_counter_baselines():
    """A baseline captured too high swallows real failures with nothing to
    show for it on any entity, so the dump is the only place it can be seen."""
    coordinator = _FakeCoordinator(BlueSightData())
    coordinator.deltas.update("D0:CF:13:0E:C9:2A", {"11:22:33:44:55:66": 9})
    dump = asyncio.run(
        async_get_config_entry_diagnostics(None, _FakeEntry(coordinator))
    )
    assert dump["telemetry"]["counter_baselines"] == {
        "D0:CF:13:0E:C9:2A": {"11:22:33:44:55:66": 9}
    }


def test_the_dump_reports_a_tunable_running_at_its_default():
    """A threshold in force but never submitted must still appear.

    ``options`` held only what the user had saved, so a tunable running at its
    constant was simply absent from the dump -- and every option is absent
    until the day someone opens the dialog and presses Submit. That makes the
    one artifact attached to a bug report silent about the thresholds that
    produced the behaviour being reported, which is the opposite of its job.
    """
    dump = _dump(_minimal_data())

    assert dump["options"] == {**OPTION_DEFAULTS, "storm_threshold": 4}
    # The value the user actually saved still wins over the constant.
    assert dump["options"]["storm_threshold"] == 4


def test_the_dump_reports_every_tunable_the_options_flow_offers():
    """No tunable may be missing from the dump.

    Pinned against the defaults table rather than a literal list, so an option
    added later cannot quietly stop being reported -- the failure mode here is
    silent, and only shows up as a support thread that cannot be answered.
    """
    dump = _dump(_minimal_data())

    assert set(OPTION_DEFAULTS) <= set(dump["options"])
