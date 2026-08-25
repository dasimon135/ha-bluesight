"""Turning monotonic firmware counters into failure events."""
from __future__ import annotations

from custom_components.bluesight.telemetry import CounterDeltas, parse_counts

ADDR = "D0:CF:13:0E:C9:2A"
OTHER = "AA:BB:CC:DD:EE:FF"


def test_first_reading_establishes_a_baseline_and_counts_nothing():
    """We joined mid-life; the counter's history is not ours to replay."""
    deltas = CounterDeltas()
    assert deltas.update("proxy", {ADDR: 7}) == {}


def test_increase_is_reported_as_that_many_failures():
    deltas = CounterDeltas()
    deltas.update("proxy", {ADDR: 7})
    assert deltas.update("proxy", {ADDR: 10}) == {ADDR: 3}


def test_unchanged_counter_reports_nothing():
    deltas = CounterDeltas()
    deltas.update("proxy", {ADDR: 7})
    assert deltas.update("proxy", {ADDR: 7}) == {}


def test_a_decrease_is_a_reboot_and_rearms_without_counting():
    """Counters reset to 0 on reboot. That is not a recovery, and the climb
    back up must not be counted twice."""
    deltas = CounterDeltas()
    deltas.update("proxy", {ADDR: 7})
    assert deltas.update("proxy", {ADDR: 2}) == {}
    assert deltas.update("proxy", {ADDR: 5}) == {ADDR: 3}


def test_absent_telemetry_holds_the_baseline_rather_than_dropping_it():
    """A proxy that goes briefly unavailable must not replay its whole
    counter as new failures when it comes back."""
    deltas = CounterDeltas()
    deltas.update("proxy", {ADDR: 7})
    assert deltas.update("proxy", None) == {}
    assert deltas.update("proxy", {ADDR: 9}) == {ADDR: 2}


def test_baselines_are_per_proxy():
    deltas = CounterDeltas()
    deltas.update("a", {ADDR: 7})
    deltas.update("b", {ADDR: 100})
    assert deltas.update("a", {ADDR: 8}) == {ADDR: 1}


def test_forget_drops_a_retired_proxy():
    deltas = CounterDeltas()
    deltas.update("a", {ADDR: 7})
    deltas.forget("a")
    assert deltas.update("a", {ADDR: 9}) == {}


# --- reboot sequences a real fleet produces ---------------------------------


def test_a_second_reboot_rearms_again():
    """A proxy stuck in a boot loop reboots between consecutive polls. Each
    decrease must rearm, or the first one would leave a baseline the later
    climbs are measured against and over-report."""
    deltas = CounterDeltas()
    deltas.update("proxy", {ADDR: 7})
    assert deltas.update("proxy", {ADDR: 3}) == {}
    assert deltas.update("proxy", {ADDR: 1}) == {}
    assert deltas.update("proxy", {ADDR: 4}) == {ADDR: 3}


def test_a_reboot_to_exactly_zero_counts_the_whole_climb_back():
    """Zero is the value a fresh boot reports, so the next reading is entirely
    post-reboot and every unit of it is a real failure."""
    deltas = CounterDeltas()
    deltas.update("proxy", {ADDR: 7})
    assert deltas.update("proxy", {ADDR: 0}) == {}
    assert deltas.update("proxy", {ADDR: 4}) == {ADDR: 4}


def test_a_first_reading_of_zero_is_still_only_a_baseline():
    """Zero is a real measurement, not a missing one: the proxy booted and
    nothing has failed yet, so the next climb is fully ours to count."""
    deltas = CounterDeltas()
    assert deltas.update("proxy", {ADDR: 0}) == {}
    assert deltas.update("proxy", {ADDR: 3}) == {ADDR: 3}


# --- addresses coming and going ---------------------------------------------


def test_an_address_missing_from_one_reading_keeps_its_baseline():
    """parse_counts drops malformed fields one at a time, so an address can
    vanish from a reading and return in the next. Holding the baseline defers
    those failures to the next good reading; dropping it would rearm and count
    the address's whole counter as new."""
    deltas = CounterDeltas()
    deltas.update("proxy", {ADDR: 7, OTHER: 1})
    assert deltas.update("proxy", {OTHER: 2}) == {OTHER: 1}
    assert deltas.update("proxy", {ADDR: 10, OTHER: 2}) == {ADDR: 3}


def test_a_proxy_reporting_zero_entries_keeps_its_baselines():
    """An empty reading is a proxy legitimately saying it has nothing to
    report, not a reason to forget what it said last time."""
    deltas = CounterDeltas()
    deltas.update("proxy", {ADDR: 7})
    assert deltas.update("proxy", {}) == {}
    assert deltas.update("proxy", {ADDR: 9}) == {ADDR: 2}


def test_an_address_that_returns_lower_rearms_rather_than_double_counting():
    """The proxy rebooted while that address was unreadable. The held baseline
    is stale, so it rearms and the climb back up is counted once."""
    deltas = CounterDeltas()
    deltas.update("proxy", {ADDR: 7})
    deltas.update("proxy", {})
    assert deltas.update("proxy", {ADDR: 2}) == {}
    assert deltas.update("proxy", {ADDR: 6}) == {ADDR: 4}


def test_an_address_new_to_a_known_proxy_only_baselines():
    """Absent last snapshot does not mean new: it may be an address whose
    field has never yet parsed, whose counter has climbed since boot. Counting
    it in full would be a storm incident invented out of a formatting bug."""
    deltas = CounterDeltas()
    deltas.update("proxy", {ADDR: 7})
    assert deltas.update("proxy", {ADDR: 7, OTHER: 40}) == {}
    assert deltas.update("proxy", {ADDR: 7, OTHER: 42}) == {OTHER: 2}


def test_a_mixed_snapshot_counts_only_the_addresses_that_climbed():
    deltas = CounterDeltas()
    deltas.update("proxy", {ADDR: 7, OTHER: 5})
    assert deltas.update("proxy", {ADDR: 9, OTHER: 2, "11:22:33:44:55:66": 4}) == {
        ADDR: 2
    }


# --- key canonicalisation ---------------------------------------------------


def test_proxy_case_does_not_split_a_baseline():
    """Everything downstream correlates on the normalised form. A second
    baseline under a different case would make the first reading of each case
    count nothing, and interleaved cases would report the same climb twice."""
    deltas = CounterDeltas()
    deltas.update(OTHER, {ADDR: 7})
    assert deltas.update(OTHER.lower(), {ADDR: 10}) == {ADDR: 3}


def test_forget_matches_whatever_case_the_service_call_used():
    """forget_proxy hands through the MAC the user typed in Developer Tools.
    A case-sensitive drop would report success and leave the baseline behind,
    so a replacement proxy reusing the MAC inherits a stranger's counter."""
    deltas = CounterDeltas()
    deltas.update(OTHER, {ADDR: 7})
    deltas.forget(OTHER.lower())
    assert deltas.update(OTHER, {ADDR: 9}) == {}


def test_address_case_does_not_split_a_baseline():
    """The storm window is keyed verbatim and an incident address is matched
    against the device registry, so a lower-case address would open a second
    bucket and split one real storm into two counts below the threshold."""
    deltas = CounterDeltas()
    deltas.update("proxy", {ADDR: 7})
    assert deltas.update("proxy", {ADDR.lower(): 10}) == {ADDR: 3}


# --- state hygiene ----------------------------------------------------------


def test_the_stored_baseline_is_a_copy_not_a_view_of_the_reading():
    """Storing the caller's dict would alias it -- and would silently prune,
    because the next snapshot's dict lacks the addresses this one dropped."""
    deltas = CounterDeltas()
    counts = {ADDR: 7}
    deltas.update("proxy", counts)
    counts[ADDR] = 99
    counts[OTHER] = 50
    assert deltas.update("proxy", {ADDR: 8, OTHER: 3}) == {ADDR: 1}


def test_update_does_not_mutate_the_reading_it_was_given():
    """The reading belongs to a frozen ProxyTelemetry the caller still holds."""
    deltas = CounterDeltas()
    deltas.update("proxy", {ADDR: 7})
    counts = {ADDR: 10}
    deltas.update("proxy", counts)
    assert counts == {ADDR: 10}


def test_the_returned_events_are_not_internal_state():
    deltas = CounterDeltas()
    deltas.update("proxy", {ADDR: 7})
    events = deltas.update("proxy", {ADDR: 10})
    events[ADDR] = 9999
    events[OTHER] = 1
    assert deltas.update("proxy", {ADDR: 12}) == {ADDR: 2}


def test_forget_is_quiet_about_a_proxy_it_never_saw():
    """The service accepts any MAC; an unknown one must not raise."""
    CounterDeltas().forget("never-seen")


def test_absent_telemetry_for_an_unknown_proxy_leaves_no_baseline():
    """A proxy first seen while unavailable has told us nothing, so its first
    real reading is still only a baseline."""
    deltas = CounterDeltas()
    assert deltas.update("proxy", None) == {}
    assert deltas.update("proxy", {ADDR: 7}) == {}
    assert deltas.update("proxy", {ADDR: 8}) == {ADDR: 1}


def test_the_parsers_output_flows_straight_in():
    """The one producer of these readings is parse_counts, including its None
    for a proxy that has gone unavailable."""
    deltas = CounterDeltas()
    deltas.update("proxy", parse_counts("d0cf130ec92a:4"))
    assert deltas.update("proxy", parse_counts("d0cf130ec92a:9")) == {ADDR: 5}
    assert deltas.update("proxy", parse_counts("unavailable")) == {}
    assert deltas.update("proxy", parse_counts("d0cf130ec92a:11")) == {ADDR: 2}
