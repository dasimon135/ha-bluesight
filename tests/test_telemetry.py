"""Tests for the pure telemetry parser."""
from __future__ import annotations

from custom_components.bluesight.telemetry import (
    ProxyTelemetry,
    expand_compact_mac,
    parse_addresses,
    parse_counts,
    parse_idle_seconds,
)


def test_expand_compact_mac_matches_habluetooth_form():
    assert expand_compact_mac("d0cf130ec92a") == "D0:CF:13:0E:C9:2A"


def test_expand_compact_mac_passes_through_an_already_expanded_address():
    assert expand_compact_mac("D0:CF:13:0E:C9:2A") == "D0:CF:13:0E:C9:2A"


def test_absent_state_is_none_not_empty():
    """None means 'no telemetry'; it must never read as 'nothing wrong'."""
    assert parse_counts(None) is None
    assert parse_addresses(None) is None
    assert parse_idle_seconds(None) is None


def test_unavailable_and_unknown_are_none():
    """A rebooting proxy drops its entities; zero failures would be a lie."""
    for raw in ("unavailable", "unknown"):
        assert parse_counts(raw) is None
        assert parse_addresses(raw) is None
        assert parse_idle_seconds(raw) is None


def test_empty_string_is_empty_not_none():
    """A proxy with zero bonds legitimately publishes an empty string."""
    assert parse_counts("") == {}
    assert parse_addresses("") == set()
    assert parse_idle_seconds("") == {}


def test_parse_counts_expands_and_normalises():
    assert parse_counts("d0cf130ec92a:3,aabbccddeeff:11") == {
        "D0:CF:13:0E:C9:2A": 3,
        "AA:BB:CC:DD:EE:FF": 11,
    }


def test_parse_addresses_expands_and_normalises():
    assert parse_addresses("d0cf130ec92a,aabbccddeeff") == {
        "D0:CF:13:0E:C9:2A",
        "AA:BB:CC:DD:EE:FF",
    }


def test_parse_idle_seconds_reads_floats():
    assert parse_idle_seconds("d0cf130ec92a:240") == {"D0:CF:13:0E:C9:2A": 240.0}


def test_malformed_entries_are_skipped_not_fatal():
    """Firmware is the least trustworthy input we have; never crash on it."""
    assert parse_counts("garbage,d0cf130ec92a:3,nope:x") == {"D0:CF:13:0E:C9:2A": 3}


def test_telemetry_reports_whether_it_has_any_signal():
    absent = ProxyTelemetry("AA", smp_failures=None, bonds=None, slot_idle_seconds=None)
    present = ProxyTelemetry("AA", smp_failures={}, bonds=None, slot_idle_seconds=None)
    assert absent.has_signal is False
    assert present.has_signal is True


# --- edge cases beyond the plan's list ---------------------------------------
# Everything below is input the firmware can physically emit and that the
# parser above would otherwise wave through into the detectors.


def test_a_twelve_character_non_hex_field_is_not_an_address():
    """Length alone does not make a MAC.

    Any 12-character field would otherwise be reshaped into a plausible-looking
    address and enter the bond set or the SMP map, where it can only correlate
    with nothing -- or, on the SMP side, raise an incident naming a device that
    does not exist.
    """
    assert parse_addresses("hello world!") == set()
    assert parse_addresses("zzzzzzzzzzzz") == set()
    assert parse_counts("hello world!:3") == {}


def test_a_colon_field_that_is_not_an_address_is_rejected():
    assert parse_addresses("no:pe") == set()
    assert parse_addresses("D0:CF:13:0E:C9") == set()


def test_mappings_tolerate_an_already_expanded_address():
    """``expand_compact_mac`` promises colon tolerance; the pair parsers must
    honour it too, or a firmware build that sends colons loses every reading
    while the bond list keeps working -- the worst kind of partial failure.
    """
    assert parse_counts("D0:CF:13:0E:C9:2A:3") == {"D0:CF:13:0E:C9:2A": 3}
    assert parse_idle_seconds("D0:CF:13:0E:C9:2A:240") == {"D0:CF:13:0E:C9:2A": 240.0}


def test_non_finite_idle_seconds_are_rejected():
    """``float()`` accepts 'nan' and 'inf'.

    A NaN idle time compares False against every threshold, so a stuck slot
    would be silently missed; an infinite one trips every threshold and cries
    wolf forever. Neither is a reading -- treat them as absent.
    """
    assert parse_idle_seconds("d0cf130ec92a:nan") == {}
    assert parse_idle_seconds("d0cf130ec92a:inf") == {}
    assert parse_idle_seconds("d0cf130ec92a:-inf") == {}


def test_negative_values_are_rejected():
    """Counts are monotonic since boot and idle time runs forwards.

    A negative reading is corruption, and feeding it to the delta logic (which
    reads a decrease as a reboot) would rearm the baseline for no reason.
    """
    assert parse_counts("d0cf130ec92a:-5") == {}
    assert parse_idle_seconds("d0cf130ec92a:-1") == {}


def test_python_numeric_literal_syntax_is_not_accepted():
    """``int('3_0')`` is 30 and ``float('1e3')`` is 1000.0 in Python.

    The firmware prints plain decimals; anything else is a corrupt field, and
    silently reinterpreting it invents a count nobody measured.
    """
    assert parse_counts("d0cf130ec92a:3_0") == {}
    assert parse_counts("d0cf130ec92a:+3") == {}
    assert parse_idle_seconds("d0cf130ec92a:1e3") == {}


def test_fractional_idle_seconds_still_parse():
    assert parse_idle_seconds("d0cf130ec92a:240.5") == {"D0:CF:13:0E:C9:2A": 240.5}
