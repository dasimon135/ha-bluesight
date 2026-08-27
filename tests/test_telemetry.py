"""Tests for the pure telemetry parser."""
from __future__ import annotations

import logging

import pytest

from custom_components.bluesight.telemetry import (
    ProxyTelemetry,
    expand_compact_mac,
    parse_addresses,
    parse_counts,
    parse_idle_seconds,
)

#: The parser logs under its own module name; pin it here so a caplog
#: assertion cannot quietly pass by matching some other logger.
TELEMETRY_LOGGER = "custom_components.bluesight.telemetry"


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


def test_duplicate_addresses_keep_the_most_recent_traffic():
    """One address can occupy several slot records; the freshest one wins.

    A proxy reports one field per tracked connection, and the same device can
    legitimately appear more than once -- several GATT client interfaces can
    hold a record for a single physical link, and only the interface carrying
    the traffic has its idle timer reset. Reducing with "last field wins"
    would let a record that no traffic ever touches decide the age, reporting
    a device that spoke seconds ago as idle for hours. That is a manufactured
    ghost slot: the one verdict this signal exists to reach.
    """
    raw = "9cac6dd4f9fc:7.3,9cac6dd4f9fc:29831.7,9cac6dd4f9fc:29831.7"
    assert parse_idle_seconds(raw) == {"9C:AC:6D:D4:F9:FC": 7.3}


def test_duplicate_addresses_keep_the_highest_failure_count():
    """Counters are monotonic, so the largest reading is the current one.

    The firmware keys its SMP table by address and so emits no duplicates
    today. This pins the reduction anyway: an under-count silently disarms
    the storm detector, and "last field wins" would pick arbitrarily.
    """
    # Highest last and highest first: "last field wins" passes the first
    # ordering by luck, so both are pinned.
    assert parse_counts("d0cf130ec92a:3,d0cf130ec92a:11") == {"D0:CF:13:0E:C9:2A": 11}
    assert parse_counts("d0cf130ec92a:11,d0cf130ec92a:3") == {"D0:CF:13:0E:C9:2A": 11}


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
#
# A single-field input parsing to None *is* the assertion that the field was
# rejected: a reading in which every field was rejected is not an empty
# reading, it is an absent one -- see
# `test_a_wholly_rejected_reading_is_none_not_empty`.


def test_a_twelve_character_non_hex_field_is_not_an_address():
    """Length alone does not make a MAC.

    Any 12-character field would otherwise be reshaped into a plausible-looking
    address and enter the bond set or the SMP map, where it can only correlate
    with nothing -- or, on the SMP side, raise an incident naming a device that
    does not exist.
    """
    assert parse_addresses("hello world!") is None
    assert parse_addresses("zzzzzzzzzzzz") is None
    assert parse_counts("hello world!:3") is None


def test_a_colon_field_that_is_not_an_address_is_rejected():
    assert parse_addresses("no:pe") is None
    assert parse_addresses("D0:CF:13:0E:C9") is None


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
    assert parse_idle_seconds("d0cf130ec92a:nan") is None
    assert parse_idle_seconds("d0cf130ec92a:inf") is None
    assert parse_idle_seconds("d0cf130ec92a:-inf") is None


def test_negative_values_are_rejected():
    """Counts are monotonic since boot and idle time runs forwards.

    A negative reading is corruption, and feeding it to the delta logic (which
    reads a decrease as a reboot) would rearm the baseline for no reason.
    """
    assert parse_counts("d0cf130ec92a:-5") is None
    assert parse_idle_seconds("d0cf130ec92a:-1") is None


def test_python_numeric_literal_syntax_is_not_accepted():
    """``int('3_0')`` is 30 and ``float('1e3')`` is 1000.0 in Python.

    The firmware prints plain decimals; anything else is a corrupt field, and
    silently reinterpreting it invents a count nobody measured.
    """
    assert parse_counts("d0cf130ec92a:3_0") is None
    assert parse_counts("d0cf130ec92a:+3") is None
    assert parse_idle_seconds("d0cf130ec92a:1e3") is None


def test_fractional_idle_seconds_still_parse():
    assert parse_idle_seconds("d0cf130ec92a:240.5") == {"D0:CF:13:0E:C9:2A": 240.5}


# --- absent vs. empty, when nothing survives ---------------------------------


def test_a_wholly_rejected_reading_is_none_not_empty():
    """A reading this parser could not read at all is absent, not empty.

    `esphome::format_hex_pretty()` emits `D0.CF.13.0E.C9.2A` -- the obvious
    helper for the firmware to reach for, and a shape this parser refuses. If a
    firmware built that way yielded an empty *set* rather than None, a full bond
    list would arrive as a confident "this proxy has no bonds", and BOND_LOST
    would fire across the whole fleet on a formatting mismatch. The module would
    not merely lose the reading; it would assert its opposite.
    """
    hex_pretty = "D0.CF.13.0E.C9.2A,AA.BB.CC.DD.EE.FF"
    assert parse_addresses(hex_pretty) is None
    assert parse_counts(f"{hex_pretty}:3") is None
    assert parse_idle_seconds(f"{hex_pretty}:240") is None


def test_an_empty_reading_is_still_empty_not_none():
    """The other half of the rule: nothing to reject is not a rejection.

    A proxy with zero bonds legitimately publishes an empty string, and that has
    to stay distinguishable from a proxy whose payload we could not read.
    """
    assert parse_addresses("") == set()
    assert parse_counts("") == {}
    assert parse_idle_seconds("") == {}
    assert parse_addresses("  ,  ,") == set()


def test_one_survivor_is_enough_to_keep_the_reading():
    """Partial survival is a reading; only total rejection is absence."""
    assert parse_counts("garbage,d0cf130ec92a:3,nope:x") == {"D0:CF:13:0E:C9:2A": 3}
    assert parse_addresses("D0.CF.13.0E.C9.2A,aabbccddeeff") == {"AA:BB:CC:DD:EE:FF"}


# --- observability on the drop path ------------------------------------------


def test_a_dropped_field_is_logged(caplog):
    """Silent disappearance is the failure mode we are most worried about, and
    the firmware is the part CI cannot test: a debug line is the only thing that
    tells "the proxy reported nothing" apart from "we threw it away".
    """
    with caplog.at_level(logging.DEBUG, logger=TELEMETRY_LOGGER):
        parse_counts("garbage,d0cf130ec92a:3,nope:x")
    assert "nope" in caplog.text


def test_a_wholly_rejected_reading_warns(caplog):
    """Total rejection means the firmware and this parser disagree about the
    wire format. That is a bug in one of them, not a debug detail.
    """
    with caplog.at_level(logging.DEBUG, logger=TELEMETRY_LOGGER):
        assert parse_addresses("D0.CF.13.0E.C9.2A") is None
    assert any(record.levelno >= logging.WARNING for record in caplog.records)


# --- the wire contract Task 11's firmware has to satisfy ----------------------
# These pin what the parser *accepts*, not only what it rejects: the executable
# form of the firmware<->integration contract. If Task 11 prints an address or a
# duration in any other shape, one of these fails.


@pytest.mark.parametrize(
    "address",
    [
        "d0cf130ec92a",       # sprintf("%02x") -- the documented form
        "D0CF130EC92A",       # sprintf("%02X")
        "D0:CF:13:0E:C9:2A",  # a build that decides to send colons
        "d0:cf:13:0e:c9:2a",
    ],
)
def test_wire_contract_accepts_every_address_form(address):
    assert parse_addresses(address) == {"D0:CF:13:0E:C9:2A"}
    assert parse_counts(f"{address}:3") == {"D0:CF:13:0E:C9:2A": 3}
    assert parse_idle_seconds(f"{address}:240") == {"D0:CF:13:0E:C9:2A": 240.0}


@pytest.mark.parametrize(
    ("printed", "expected"),
    [
        ("240", 240.0),         # snprintf("%.0f")
        ("240.0", 240.0),       # snprintf("%.1f")
        ("240.00", 240.0),      # Arduino String(float)
        ("240.000000", 240.0),  # std::to_string(float)
        ("0", 0.0),             # a slot that just saw traffic
        ("0.0", 0.0),
    ],
)
def test_wire_contract_accepts_the_durations_firmware_prints(printed, expected):
    assert parse_idle_seconds(f"d0cf130ec92a:{printed}") == {
        "D0:CF:13:0E:C9:2A": expected
    }


@pytest.mark.parametrize("printed", ["0", "3", "11", "4294967295"])
def test_wire_contract_accepts_the_counts_firmware_prints(printed):
    assert parse_counts(f"d0cf130ec92a:{printed}") == {
        "D0:CF:13:0E:C9:2A": int(printed)
    }


@pytest.mark.parametrize(
    "printed",
    [
        "1e+06",  # snprintf("%g") switches to exponent form at >=1e6 seconds,
        "1e6",    # which is 11.6 days idle -- reachable for a bonded slot
        "-0.0",   # signed rollover in the idle calculation
        "3_0",
        "+3",
        "nan",
        "inf",
    ],
)
def test_wire_contract_rejects_what_firmware_must_not_print(printed):
    assert parse_idle_seconds(f"d0cf130ec92a:{printed}") is None


@pytest.mark.parametrize(
    "printed", ["D0.CF.13.0E.C9.2A", "d0cf130ec92", "d0cf130ec92aa", "0x d0cf130e"]
)
def test_wire_contract_rejects_address_shapes_firmware_must_not_print(printed):
    assert parse_addresses(f"{printed},{printed}") is None


# --- the frozen/unhashable trap -----------------------------------------------


def test_telemetry_compares_by_value_and_is_deliberately_unhashable():
    """`frozen=True` on a dataclass holding a dict and a set reads as a promise
    of hashability that Python cannot keep.

    Value equality is kept because snapshot diffing needs it; hashing raises
    loudly rather than silently degrading to identity, which is what dropping
    `eq` would do. Key on `source` when a set member or dict key is wanted.
    """
    a = ProxyTelemetry("AA", smp_failures={"X": 1})
    b = ProxyTelemetry("AA", smp_failures={"X": 1})
    assert a == b
    with pytest.raises(TypeError):
        hash(a)
