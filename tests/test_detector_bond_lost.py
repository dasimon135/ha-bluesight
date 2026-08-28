"""Bond-lost detection: pairing attempted, failing *now*, and no bond exists.

This is the one diagnosis that is impossible without the ESPHome telemetry
component: Home Assistant can see neither SMP failures nor a proxy's NVS bond
store, so every assertion here is about evidence only the firmware supplies.

The evidence is the rolling window, not the firmware's lifetime counter. That
counter is monotonic since the proxy booted, so reading it directly made one
refusal in a proxy's whole life open an incident that never closed -- a device
working perfectly through the proxy that holds its bond stayed flagged forever
because a different proxy had refused it once, months ago. The window is the
same one STORM already fills, from the same deltas, so the two halves of the
measured evidence finally run on one clock.
"""
from __future__ import annotations

from custom_components.bluesight.detector import detect_bond_lost
from custom_components.bluesight.model import IncidentKind
from custom_components.bluesight.rendering import plural_count
from custom_components.bluesight.telemetry import ProxyTelemetry
from custom_components.bluesight.window import FailureWindow

ADDR = "D0:CF:13:0E:C9:2A"
OTHER = "AA:BB:CC:DD:EE:FF"
THRESHOLD = 3


def _tel(bonds=None, source="proxy1", failures=None):
    return ProxyTelemetry(source, smp_failures=failures, bonds=bonds)


def _window(now=None, window_s=300):
    now = now if now is not None else [0.0]
    return FailureWindow(window_s=window_s, threshold=5, clock=lambda: now[0])


def _fill(window, address, count, source="proxy1"):
    for _ in range(count):
        window.record(address, source)
    return window


def test_failures_at_threshold_with_no_bond_is_a_bond_lost():
    w = _fill(_window(), ADDR, THRESHOLD)
    incidents = detect_bond_lost([_tel(set())], {"proxy1": "Salon"}, w, THRESHOLD)
    assert len(incidents) == 1
    assert incidents[0].kind is IncidentKind.BOND_LOST
    assert incidents[0].address == ADDR
    assert incidents[0].sources == ["proxy1"]
    assert incidents[0].evidence == "smp"


def test_below_threshold_is_not_yet_a_bond_lost():
    """A single refusal is not a diagnosis. The remedy sends someone to
    physically re-pair a device, so it needs more than one data point."""
    w = _fill(_window(), ADDR, THRESHOLD - 1)
    assert detect_bond_lost([_tel(set())], {}, w, THRESHOLD) == []


def test_failures_that_aged_out_of_the_window_clear_the_incident():
    """The defect this detector was rewritten for.

    Failures that stopped happening stop being evidence. Read off the lifetime
    counter, this device stayed flagged until the proxy rebooted.
    """
    now = [0.0]
    w = _fill(_window(now), ADDR, THRESHOLD + 5)
    now[0] += 400  # past the 300s window
    assert detect_bond_lost([_tel(set())], {}, w, THRESHOLD) == []


def test_failures_with_a_bond_present_is_not_bond_lost():
    """The bond exists, so the failure is something else -- range, interference,
    a busy peripheral. Not our call to make."""
    w = _fill(_window(), ADDR, THRESHOLD + 2)
    assert detect_bond_lost([_tel({ADDR})], {}, w, THRESHOLD) == []


def test_an_empty_window_is_never_bond_lost():
    """Never attempted is just a device we do not talk to."""
    assert detect_bond_lost([_tel(set())], {}, _window(), THRESHOLD) == []


def test_absent_bond_list_yields_nothing():
    """Without the bond list we cannot tell 'no bond' from 'not reported'.

    Asserting the first from the second would fire BOND_LOST across a whole
    fleet over a firmware formatting disagreement -- the exact failure
    ``telemetry.py`` maps a total parse rejection to ``None`` to avoid.
    """
    w = _fill(_window(), ADDR, THRESHOLD + 2)
    assert detect_bond_lost([_tel(None)], {}, w, THRESHOLD) == []


def test_absent_failure_counts_this_snapshot_do_not_close_an_open_incident():
    """The evidence lives in the window now, not in this snapshot.

    A proxy that briefly stops publishing its SMP counters while still
    publishing its bonds keeps its incident open -- the same reasoning that
    makes ``CounterDeltas`` hold a baseline through a blip rather than drop it.
    Reading ``smp_failures`` here would retract a live diagnosis over a gap.
    """
    w = _fill(_window(), ADDR, THRESHOLD)
    incidents = detect_bond_lost([_tel(set(), failures=None)], {}, w, THRESHOLD)
    assert [i.address for i in incidents] == [ADDR]


def test_inferred_failures_are_never_bond_lost():
    """The release heuristic cannot say which proxy dropped the slot, and the
    remedy names one proxy. Un-attributable evidence implicates nobody, at any
    count -- this is every proxy that does not run the ESPHome component."""
    w = _window()
    for _ in range(20):
        w.record(ADDR)
    assert detect_bond_lost([_tel(set())], {}, w, THRESHOLD) == []


def test_no_telemetry_at_all_yields_nothing():
    w = _fill(_window(), ADDR, THRESHOLD + 2)
    assert detect_bond_lost([], {"proxy1": "Salon"}, w, THRESHOLD) == []


def test_only_addresses_that_failed_are_reported():
    w = _fill(_window(), ADDR, THRESHOLD)
    incidents = detect_bond_lost([_tel({OTHER})], {}, w, THRESHOLD)
    assert [i.address for i in incidents] == [ADDR]


def test_the_remedy_names_the_proxy_to_re_pair_through():
    """The whole point of the diagnosis: re-pair through *this* proxy.

    Detectors have emitted a key plus parameters rather than prose since
    0.5.0 -- ``detail`` is rendered later, in ``build_triage_data``, from the
    language catalogue -- so the proxy's friendly name is pinned where the
    detector actually puts it. It travels as a parameter because it is
    user-controlled text the renderer must substitute, never re-scan.
    """
    w = _fill(_window(), ADDR, THRESHOLD)
    incidents = detect_bond_lost([_tel(set())], {"proxy1": "Salon"}, w, THRESHOLD)
    assert incidents[0].detail_key == "incident.bond_lost.detail"
    assert incidents[0].detail_params["proxy"] == "Salon"
    # Unrendered at detector level: prose is the coordinator's job.
    assert incidents[0].detail == ""


def test_the_detail_carries_the_window_the_count_was_measured_over():
    """The count means "in the last N seconds" now, not "ever", and the
    sentence a user reads has to say so. Without ``seconds`` the prose reads
    as a lifetime tally -- the defect v0.6.2 and v0.6.3 went to fix,
    reintroduced by the change that makes the number honest."""
    w = _fill(_window(window_s=600), ADDR, THRESHOLD)
    incidents = detect_bond_lost([_tel(set())], {}, w, THRESHOLD)
    assert incidents[0].detail_params == {
        "count": str(THRESHOLD),
        "proxy": "proxy1",
        "seconds": "600",
    }


def test_an_unnamed_proxy_falls_back_to_its_source_id():
    """An empty names map must still yield a usable remedy.

    ``names`` is built from the device registry, which can lag a freshly
    adopted proxy by a poll or two. Rendering "re-pair through {proxy}" with
    nothing in it would make the one actionable incident BlueSight has
    unactionable, so the raw source is the fallback.
    """
    w = _fill(_window(), ADDR, THRESHOLD)
    incidents = detect_bond_lost([_tel(set())], {}, w, THRESHOLD)
    assert incidents[0].detail_params["proxy"] == "proxy1"


def test_a_bond_on_a_different_proxy_does_not_excuse_the_failing_one():
    """Bonds are per-central: each proxy has its own NVS store.

    A device paired through the lounge proxy has no bond on the kitchen one,
    and Home Assistant will still route connections there. That is exactly the
    fault, and the remedy still names the proxy that is failing -- so the
    incident is raised for proxy1 and only proxy1.
    """
    w = _fill(_window(), ADDR, THRESHOLD, source="proxy1")
    telemetry = [_tel(set(), source="proxy1"), _tel({ADDR}, source="proxy2")]
    incidents = detect_bond_lost(telemetry, {}, w, THRESHOLD)
    assert [(i.address, i.sources) for i in incidents] == [(ADDR, ["proxy1"])]


def test_a_proxys_threshold_is_measured_on_its_own_failures():
    """Failures are counted per proxy, never pooled.

    Two proxies one refusal short of the threshold are two proxies with no
    diagnosis -- pooling them would implicate each on evidence gathered by the
    other, and the remedy would send the user to re-pair through a proxy that
    barely failed.
    """
    w = _window()
    _fill(w, ADDR, THRESHOLD - 1, source="proxy1")
    _fill(w, ADDR, THRESHOLD - 1, source="proxy2")
    telemetry = [_tel(set(), source="proxy1"), _tel(set(), source="proxy2")]
    assert detect_bond_lost(telemetry, {}, w, THRESHOLD) == []


def test_each_failing_proxy_gets_its_own_incident():
    """Two proxies failing on one device is two remedies, not one."""
    w = _window()
    _fill(w, ADDR, THRESHOLD, source="proxy1")
    _fill(w, ADDR, THRESHOLD + 2, source="proxy2")
    telemetry = [_tel(set(), source="proxy1"), _tel(set(), source="proxy2")]
    incidents = detect_bond_lost(
        telemetry, {"proxy1": "Salon", "proxy2": "Cuisine"}, w, THRESHOLD
    )
    assert [i.sources for i in incidents] == [["proxy1"], ["proxy2"]]
    # Each reports the failures *it* measured, not the pooled total.
    assert [i.detail_params["count"] for i in incidents] == [str(THRESHOLD),
                                                             str(THRESHOLD + 2)]
    # Distinct identities, so neither suppresses the other across snapshots.
    assert len({i.key for i in incidents}) == 2


def test_output_order_is_deterministic():
    """Addresses are sorted within a proxy and proxies keep input order.

    The incident list lands in an entity attribute; an order that shuffled
    between snapshots would churn that attribute for no change in the world.
    """
    w = _window()
    for source in ("proxy1", "proxy2"):
        _fill(w, ADDR, THRESHOLD, source=source)
        _fill(w, OTHER, THRESHOLD, source=source)
    telemetry = [_tel(set(), source="proxy2"), _tel(set(), source="proxy1")]
    incidents = detect_bond_lost(telemetry, {}, w, THRESHOLD)
    assert [(i.sources[0], i.address) for i in incidents] == [
        ("proxy2", OTHER),
        ("proxy2", ADDR),
        ("proxy1", OTHER),
        ("proxy1", ADDR),
    ]


def test_a_bond_recorded_in_another_case_still_counts_as_a_bond():
    """Correlation is case-insensitive, as everywhere else in BlueSight.

    Reading a bond as absent because it arrived lower-case would assert the
    opposite of what the proxy reported, and BOND_LOST would fire across a
    fleet -- the exact fleet-wide false positive ``telemetry.py`` goes out of
    its way to avoid when it maps a total parse failure to ``None``.
    """
    w = _fill(_window(), ADDR, THRESHOLD + 2)
    assert detect_bond_lost([_tel({ADDR.lower()})], {}, w, THRESHOLD) == []


def test_the_reported_address_is_normalised():
    """The address is matched against the device registry downstream, and is
    half of the incident key, so it must be canonical however it arrived."""
    w = _fill(_window(), ADDR.lower(), THRESHOLD)
    incidents = detect_bond_lost([_tel(set())], {}, w, THRESHOLD)
    assert incidents[0].address == ADDR


def test_the_count_is_a_string_that_selects_a_plural_form():
    """``plural_count`` reads the pivot off ``detail_params['count']``, so the
    number the user reads and the form it selects agree by construction."""
    single = detect_bond_lost(
        [_tel(set())], {}, _fill(_window(), ADDR, 1), 1
    )[0]
    many = detect_bond_lost(
        [_tel(set())], {}, _fill(_window(), ADDR, 9), THRESHOLD
    )[0]
    assert single.detail_params["count"] == "1"
    assert plural_count(single.detail_params) == 1
    assert plural_count(many.detail_params) == 9
