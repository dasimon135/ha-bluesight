"""Shape tests for the telemetry section of the diagnostics dump.

Pure: :mod:`custom_components.bluesight.diagnostics_data` imports no Home
Assistant symbol, so unlike ``tests/test_diagnostics.py`` (which exercises the
HA-facing entry point and skips without a real Home Assistant) every test here
runs on a bare dev box.

The question this section exists to answer is "is this proxy reporting, and
what did it say?" -- so most of what is asserted below is about *absence*:
that a signal nobody sent stays distinguishable from a signal that reported
zero entries, and that a proxy which reported nothing at all is still named.
"""
import json
from dataclasses import asdict

import pytest

from custom_components.bluesight.diagnostics_data import telemetry_report
from custom_components.bluesight.telemetry import CounterDeltas, ProxyTelemetry

PROXY = "D0:CF:13:0E:C9:2A"
OTHER = "AA:BB:CC:DD:EE:FF"
DEVICE = "11:22:33:44:55:66"


def test_a_raw_asdict_of_the_telemetry_is_not_json_serialisable():
    """Why this module exists, pinned.

    ``bonds`` is a ``set``. Dropping ``asdict(ProxyTelemetry(...))`` straight
    into the dump raises at download time, in Home Assistant, on the one
    artefact a user reaches for when something is already wrong. If
    ``ProxyTelemetry`` ever changes ``bonds`` to a JSON-native container this
    test fails, which is the signal that the conversion below can be dropped.
    """
    tel = ProxyTelemetry(PROXY, bonds={DEVICE})
    with pytest.raises(TypeError, match="set"):
        json.dumps(asdict(tel))


def test_the_report_round_trips_through_json():
    report = telemetry_report(
        [
            ProxyTelemetry(
                PROXY,
                smp_failures={DEVICE: 3},
                bonds={DEVICE, OTHER},
                slot_idle_seconds={DEVICE: 12.5},
            )
        ],
        known_sources=[PROXY, OTHER],
        baselines={PROXY: {DEVICE: 3}},
    )
    assert json.loads(json.dumps(report)) == report


def test_a_bond_set_becomes_a_sorted_list():
    report = telemetry_report(
        [ProxyTelemetry(PROXY, bonds={OTHER, DEVICE})], [PROXY], {}
    )
    assert report["reporting"][0]["bonds"] == [DEVICE, OTHER]


def test_absent_and_empty_are_distinguishable():
    """The whole wire contract rests on this difference.

    ``None`` means "no telemetry"; an empty container means "reporting, zero
    entries". A dump that rendered both the same way would destroy exactly the
    distinction the operator opened it for -- so the values differ (``null``
    vs ``[]``/``{}``) *and* a status word says which is which in words.
    """
    absent, empty = telemetry_report(
        [
            ProxyTelemetry(PROXY),
            ProxyTelemetry(OTHER, smp_failures={}, bonds=set(), slot_idle_seconds={}),
        ],
        [PROXY, OTHER],
        {},
    )["reporting"]

    assert absent["smp_failures"] is None
    assert absent["bonds"] is None
    assert absent["slot_idle_seconds"] is None
    assert absent["signals"] == {
        "smp_failures": "absent",
        "bonds": "absent",
        "slot_idle_seconds": "absent",
    }

    assert empty["smp_failures"] == {}
    assert empty["bonds"] == []
    assert empty["slot_idle_seconds"] == {}
    assert empty["signals"] == {
        "smp_failures": "reporting",
        "bonds": "reporting",
        "slot_idle_seconds": "reporting",
    }

    # And they survive the trip as different JSON, not merely as different
    # Python objects.
    assert '"bonds": null' in json.dumps(absent, indent=1)
    assert '"bonds": []' in json.dumps(empty, indent=1)


def test_a_partial_reading_reports_each_signal_separately():
    """One sensor missing must not read as "this proxy is silent"."""
    report = telemetry_report([ProxyTelemetry(PROXY, bonds={DEVICE})], [PROXY], {})
    entry = report["reporting"][0]
    assert entry["signals"] == {
        "smp_failures": "absent",
        "bonds": "reporting",
        "slot_idle_seconds": "absent",
    }
    assert entry["bonds"] == [DEVICE]
    assert report["silent_sources"] == []


def test_proxies_that_reported_nothing_are_named_not_omitted():
    """``BlueSightData.telemetry`` omits a proxy with no signal entirely.

    That is right for the field, and useless for the operator asking why a
    flashed proxy is not showing up: absence with no comment reads the same as
    "everything is fine". The dump names them.
    """
    report = telemetry_report(
        [ProxyTelemetry(PROXY, bonds=set())], [OTHER, PROXY, DEVICE], {}
    )
    assert report["silent_sources"] == [DEVICE, OTHER]
    assert report["note"]


def test_silent_sources_are_normalised_and_deduplicated():
    """Sources arrive from two snapshots that overlap almost entirely."""
    report = telemetry_report(
        [ProxyTelemetry(PROXY)], [OTHER.lower(), OTHER, PROXY.lower()], {}
    )
    assert report["silent_sources"] == [OTHER]


def test_no_proxies_at_all_is_empty_rather_than_absent():
    report = telemetry_report([], [], {})
    assert report["reporting"] == []
    assert report["silent_sources"] == []
    assert report["counter_baselines"] == {}


def test_counter_baselines_are_reported():
    """The state that decides whether a rising counter becomes an incident.

    Invisible from outside otherwise: a baseline captured too high silently
    swallows failures, and nothing in the entity surface would show it.
    """
    deltas = CounterDeltas()
    deltas.update(PROXY, {DEVICE: 7})
    report = telemetry_report([], [], deltas.baselines)
    assert report["counter_baselines"] == {PROXY: {DEVICE: 7}}


def test_the_report_does_not_alias_the_live_baselines():
    """A diagnostics download must not hand out the coordinator's state."""
    deltas = CounterDeltas()
    deltas.update(PROXY, {DEVICE: 7})
    report = telemetry_report([], [], deltas.baselines)
    report["counter_baselines"][PROXY][DEVICE] = 999
    deltas.update(PROXY, {DEVICE: 9})
    assert deltas.update(PROXY, {DEVICE: 10}) == {DEVICE: 1}


def test_keys_are_sorted_so_two_dumps_diff_cleanly():
    report = telemetry_report(
        [
            ProxyTelemetry(
                PROXY,
                smp_failures={OTHER: 1, DEVICE: 2},
                slot_idle_seconds={OTHER: 1.0, DEVICE: 2.0},
            )
        ],
        [PROXY],
        {},
    )
    entry = report["reporting"][0]
    assert list(entry["smp_failures"]) == [DEVICE, OTHER]
    assert list(entry["slot_idle_seconds"]) == [DEVICE, OTHER]
