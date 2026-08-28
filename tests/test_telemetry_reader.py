"""Reading telemetry entities off a proxy's Home Assistant device.

No Home Assistant here: :mod:`custom_components.bluesight.telemetry_reader`
takes its two lookups as callables, so the whole module is exercised with two
lambdas over plain dicts.
"""
from __future__ import annotations

import logging

from custom_components.bluesight.detector import detect_bond_lost
from custom_components.bluesight.telemetry_reader import (
    BONDS_NAME,
    SLOTS_NAME,
    SMP_NAME,
    read_fleet_telemetry,
    read_proxy_telemetry,
)
from custom_components.bluesight.window import FailureWindow

ADDR = "D0:CF:13:0E:C9:2A"

#: The reader logs under its own module name; pin it so a caplog assertion
#: cannot quietly pass by matching some other logger.
READER_LOGGER = "custom_components.bluesight.telemetry_reader"


class _Entry:
    """The two entity-registry attributes this module is allowed to read."""

    def __init__(self, entity_id, original_name):
        self.entity_id = entity_id
        self.original_name = original_name


def _reader(entries, states):
    """Build the two lookups read_proxy_telemetry takes."""
    return (
        lambda device_id: entries,
        lambda entity_id: states.get(entity_id),
    )


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------


def test_reads_the_three_sensors_by_original_name():
    entries = [
        _Entry("sensor.renamed_by_the_user", SMP_NAME),
        _Entry("sensor.whatever", BONDS_NAME),
        _Entry("sensor.anything", SLOTS_NAME),
    ]
    states = {
        "sensor.renamed_by_the_user": "d0cf130ec92a:4",
        "sensor.whatever": "d0cf130ec92a",
        "sensor.anything": "d0cf130ec92a:600",
    }
    tel = read_proxy_telemetry("src", "dev", *_reader(entries, states))
    assert tel.smp_failures == {ADDR: 4}
    assert tel.bonds == {ADDR}
    assert tel.slot_idle_seconds == {ADDR: 600.0}


def test_a_proxy_without_the_component_reports_no_signal():
    tel = read_proxy_telemetry("src", "dev", *_reader([], {}))
    assert tel.has_signal is False


def test_unrelated_entities_on_the_same_device_are_ignored():
    entries = [_Entry("sensor.uptime", "Uptime")]
    tel = read_proxy_telemetry("src", "dev", *_reader(entries, {"sensor.uptime": "42"}))
    assert tel.has_signal is False


def test_a_partially_flashed_proxy_reports_what_it_has():
    entries = [_Entry("sensor.a", SMP_NAME)]
    tel = read_proxy_telemetry("src", "dev", *_reader(entries, {"sensor.a": ""}))
    assert tel.smp_failures == {}
    assert tel.bonds is None


def test_the_lookup_is_asked_for_the_device_it_was_given():
    """Guards an argument swap between the two leading positional strings."""
    seen: list[str] = []

    def _entries_for(device_id):
        seen.append(device_id)
        return []

    read_proxy_telemetry("aa:bb:cc:dd:ee:ff", "dev-42", _entries_for, lambda e: None)
    assert seen == ["dev-42"]


# --------------------------------------------------------------------------
# The wire contract
# --------------------------------------------------------------------------


def test_the_sensor_names_are_the_wire_contract():
    """These strings are the contract with the firmware, not an implementation
    detail: Task 11's codegen must emit exactly them, and changing one takes
    every already-flashed proxy dark until it is re-flashed. Pinned here so
    that break is a failing test rather than a silent fleet-wide outage."""
    assert SMP_NAME == "BlueSight SMP failures"
    assert BONDS_NAME == "BlueSight bonds"
    assert SLOTS_NAME == "BlueSight slots"
    # Distinct, or a codegen copy-paste would map two signals onto one sensor.
    assert len({SMP_NAME, BONDS_NAME, SLOTS_NAME}) == 3


# --------------------------------------------------------------------------
# Degradation: every failure lands on "absent", never on a misleading zero
# --------------------------------------------------------------------------


def test_a_registry_failure_degrades_to_no_signal():
    """A broken lookup must not take the whole snapshot down."""

    def _explode(device_id):
        raise RuntimeError("registry is unhappy")

    tel = read_proxy_telemetry("src", "dev", _explode, lambda e: None)
    assert tel.has_signal is False


def test_an_empty_device_and_a_none_returning_lookup_read_the_same():
    """A registry helper returning None is as ordinary as one returning []."""
    tel = read_proxy_telemetry("src", "dev", lambda d: None, lambda e: None)
    assert tel.has_signal is False


def test_a_state_lookup_that_raises_costs_only_that_one_signal():
    """The state machine is a second surface that can fail, and it is read
    once per sensor. A raise there must degrade that sensor to absent, exactly
    as a registry failure does -- not propagate and blank every proxy in the
    snapshot, and not cost the two sensors that read fine."""
    entries = [
        _Entry("sensor.smp", SMP_NAME),
        _Entry("sensor.bonds", BONDS_NAME),
    ]

    def _state_of(entity_id):
        if entity_id == "sensor.smp":
            raise RuntimeError("state machine is unhappy")
        return "d0cf130ec92a"

    tel = read_proxy_telemetry("src", "dev", lambda d: entries, _state_of)
    assert tel.smp_failures is None
    assert tel.bonds == {ADDR}


def test_an_entry_without_an_entity_id_is_skipped():
    """The registry entry is another integration's object, read defensively
    exactly as adapter.py reads habluetooth's scanners: one unreadable entry
    is skipped, it does not abort the other two."""

    class _Broken:
        original_name = SMP_NAME  # no entity_id at all

    entries = [_Broken(), _Entry("sensor.bonds", BONDS_NAME)]
    tel = read_proxy_telemetry(
        "src", "dev", lambda d: entries, lambda e: "d0cf130ec92a"
    )
    assert tel.smp_failures is None
    assert tel.bonds == {ADDR}


def test_an_entity_registered_but_not_yet_in_the_state_machine_is_absent():
    """Setup order: the entity exists before its first state is written."""
    entries = [_Entry("sensor.a", BONDS_NAME)]
    tel = read_proxy_telemetry("src", "dev", *_reader(entries, {}))
    assert tel.bonds is None


def test_an_unavailable_sensor_is_absent_not_empty():
    """A rebooting proxy must not report "I have no bonds"."""
    entries = [_Entry("sensor.a", BONDS_NAME)]
    tel = read_proxy_telemetry(
        "src", "dev", *_reader(entries, {"sensor.a": "unavailable"})
    )
    assert tel.bonds is None


# --------------------------------------------------------------------------
# A device carrying two entities with the same name
# --------------------------------------------------------------------------


def test_a_stale_duplicate_entity_cannot_blank_a_live_reading():
    """Re-adopting or renaming an ESPHome node leaves the old entity behind on
    the same device, enabled and stuck at `unavailable`. Whichever order the
    registry hands the pair back, the one that is actually reporting has to
    win: letting the orphan overwrite it would report no bond list for a proxy
    that has one, which is precisely the absent/empty confusion telemetry.py
    exists to prevent -- and it would suppress BOND_LOST, since that detector
    needs both halves reported."""
    live = _Entry("sensor.bluesight_bonds", BONDS_NAME)
    orphan = _Entry("sensor.bluesight_bonds_2", BONDS_NAME)
    states = {
        "sensor.bluesight_bonds": "d0cf130ec92a",
        "sensor.bluesight_bonds_2": "unavailable",
    }
    for entries in ([live, orphan], [orphan, live]):
        tel = read_proxy_telemetry("src", "dev", *_reader(entries, states))
        assert tel.bonds == {ADDR}


def test_a_duplicate_is_reported_once_it_is_seen(caplog):
    """Two entities claiming one signal is a real misconfiguration; say so."""
    entries = [
        _Entry("sensor.a", SLOTS_NAME),
        _Entry("sensor.b", SLOTS_NAME),
    ]
    states = {"sensor.a": "d0cf130ec92a:600", "sensor.b": "unavailable"}
    with caplog.at_level(logging.DEBUG, logger=READER_LOGGER):
        read_proxy_telemetry("src", "dev", *_reader(entries, states))
    assert SLOTS_NAME in caplog.text


# --------------------------------------------------------------------------
# The source is a correlation key, so it arrives canonicalised
# --------------------------------------------------------------------------


def test_the_source_is_canonicalised_at_this_boundary():
    """adapter.py normalises ProxySlots.source at exactly this seam; its twin
    owes the rest of BlueSight the same guarantee."""
    tel = read_proxy_telemetry("d0:cf:13:0e:c9:2a", "dev", lambda d: [], lambda e: None)
    assert tel.source == ADDR


def test_a_lower_case_source_still_names_the_proxy_in_an_incident():
    """The consequence, end to end. `detect_bond_lost` resolves the friendly
    name with `names.get(tel.source, tel.source)`, and every proxy-name map in
    the integration (`coordinator._names`, ProxySlots.source, ProxyHealth.source)
    is keyed on the canonical form. A raw source here misses that lookup and
    every incident from this proxy names a MAC at the user instead."""
    entries = [
        _Entry("sensor.smp", SMP_NAME),
        _Entry("sensor.bonds", BONDS_NAME),
    ]
    states = {"sensor.smp": "d0cf130ec92a:3", "sensor.bonds": ""}
    tel = read_proxy_telemetry("d0:cf:13:0e:c9:2a", "dev", *_reader(entries, states))
    # The failures reach the detector through the window, so the canonical
    # source has to key the window events too -- which is the same seam.
    window = FailureWindow(300.0, 5, clock=lambda: 0.0)
    for _ in range(3):
        window.record(ADDR, tel.source)
    [incident] = detect_bond_lost([tel], {ADDR: "Kitchen proxy"}, window, 3)
    assert incident.detail_params["proxy"] == "Kitchen proxy"
    assert incident.sources == [ADDR]


# --------------------------------------------------------------------------
# The fleet-level read
# --------------------------------------------------------------------------

PROXY_A = "D8:3B:DA:11:22:33"
PROXY_B = "D8:3B:DA:44:55:66"


def _fleet(entries_by_device, states):
    """Build the three lookups read_fleet_telemetry takes, over plain dicts."""
    return (
        lambda device_id: entries_by_device.get(device_id, []),
        lambda entity_id: states.get(entity_id),
    )


def test_a_proxy_without_the_component_is_absent_from_the_fleet():
    """`BlueSightData.telemetry` means "the proxies running the component".

    An all-None entry would make the list say something it does not know, and
    would hand `CounterDeltas` a reading for a proxy that never reported.
    """
    entries, states = _fleet({"dev_a": [_Entry("sensor.uptime", "Uptime")]}, {})
    assert read_fleet_telemetry([PROXY_A], {PROXY_A: "dev_a"}.get, entries, states) == []


def test_a_proxy_with_no_home_assistant_device_is_skipped():
    """A local adapter, or a scanner whose integration registers no device."""
    entries, states = _fleet({}, {})
    assert read_fleet_telemetry([PROXY_A], {}.get, entries, states) == []


def test_a_reporting_proxy_comes_back_with_its_reading():
    entries, states = _fleet(
        {"dev_a": [_Entry("sensor.smp", SMP_NAME)]}, {"sensor.smp": "d0cf130ec92a:4"}
    )
    [tel] = read_fleet_telemetry([PROXY_A], {PROXY_A: "dev_a"}.get, entries, states)
    assert (tel.source, tel.smp_failures) == (PROXY_A, {ADDR: 4})


def test_sources_are_canonicalised_and_de_duplicated_preserving_order():
    """The caller feeds two overlapping lists (health, then allocations).

    Reading one proxy twice in a snapshot would hand `CounterDeltas` the same
    counter twice: the second read books a delta of zero over a baseline the
    first read has just advanced, so a genuine burst is silently halved. Order
    is preserved because it becomes the incident order.
    """
    entries, states = _fleet(
        {
            "dev_a": [_Entry("sensor.a", SMP_NAME)],
            "dev_b": [_Entry("sensor.b", SMP_NAME)],
        },
        {"sensor.a": "d0cf130ec92a:1", "sensor.b": "d0cf130ec92a:2"},
    )
    index = {PROXY_A: "dev_a", PROXY_B: "dev_b"}
    fleet = read_fleet_telemetry(
        # health order, then allocation order, with the case habluetooth is
        # not guaranteed to be consistent about.
        [PROXY_B, PROXY_A, PROXY_B.lower(), PROXY_A],
        index.get,
        entries,
        states,
    )
    assert [t.source for t in fleet] == [PROXY_B, PROXY_A]


def test_a_rebooting_proxy_drops_out_rather_than_reporting_zeroes():
    """Its ESPHome entities go `unavailable`, which parses to no signal.

    Dropping out is right: `CounterDeltas.update` keeps a baseline it is not
    told about, so the proxy resumes where it left off instead of replaying
    its whole counter on return.
    """
    entries, states = _fleet(
        {"dev_a": [_Entry("sensor.smp", SMP_NAME), _Entry("sensor.b", BONDS_NAME)]},
        {"sensor.smp": "unavailable", "sensor.b": "unavailable"},
    )
    assert read_fleet_telemetry([PROXY_A], {PROXY_A: "dev_a"}.get, entries, states) == []
