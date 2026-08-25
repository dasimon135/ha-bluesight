"""Bond-lost detection: pairing attempted, failing, and no bond exists.

This is the one diagnosis that is impossible without the ESPHome telemetry
component: Home Assistant can see neither SMP failures nor a proxy's NVS bond
store, so every assertion here is about evidence only the firmware supplies.
"""
from __future__ import annotations

from custom_components.bluesight.detector import detect_bond_lost
from custom_components.bluesight.model import IncidentKind
from custom_components.bluesight.rendering import plural_count
from custom_components.bluesight.telemetry import ProxyTelemetry

ADDR = "D0:CF:13:0E:C9:2A"
OTHER = "AA:BB:CC:DD:EE:FF"


def _tel(failures=None, bonds=None, source="proxy1"):
    return ProxyTelemetry(source, smp_failures=failures, bonds=bonds)


def test_failures_with_no_bond_is_a_bond_lost():
    incidents = detect_bond_lost([_tel({ADDR: 3}, set())], {"proxy1": "Salon"})
    assert len(incidents) == 1
    assert incidents[0].kind is IncidentKind.BOND_LOST
    assert incidents[0].address == ADDR
    assert incidents[0].sources == ["proxy1"]
    assert incidents[0].evidence == "smp"


def test_failures_with_a_bond_present_is_not_bond_lost():
    """The bond exists, so the failure is something else -- range, interference,
    a busy peripheral. Not our call to make."""
    assert detect_bond_lost([_tel({ADDR: 3}, {ADDR})], {}) == []


def test_no_failures_is_never_bond_lost():
    """Never bonded and never attempted is just a device we do not talk to."""
    assert detect_bond_lost([_tel({}, set())], {}) == []


def test_absent_bond_list_yields_nothing():
    """Without the bond list we cannot tell 'no bond' from 'not reported'."""
    assert detect_bond_lost([_tel({ADDR: 3}, None)], {}) == []


def test_absent_failure_counts_yield_nothing():
    assert detect_bond_lost([_tel(None, set())], {}) == []


def test_no_telemetry_at_all_yields_nothing():
    assert detect_bond_lost([], {"proxy1": "Salon"}) == []


def test_only_addresses_that_failed_are_reported():
    incidents = detect_bond_lost([_tel({ADDR: 1}, {OTHER})], {})
    assert [i.address for i in incidents] == [ADDR]


def test_the_remedy_names_the_proxy_to_re_pair_through():
    """The whole point of the diagnosis: re-pair through *this* proxy.

    Detectors have emitted a key plus parameters rather than prose since
    0.5.0 -- ``detail`` is rendered later, in ``build_triage_data``, from the
    language catalogue -- so the proxy's friendly name is pinned where the
    detector actually puts it. It travels as a parameter because it is
    user-controlled text the renderer must substitute, never re-scan.
    """
    incidents = detect_bond_lost([_tel({ADDR: 2}, set())], {"proxy1": "Salon"})
    assert incidents[0].detail_key == "incident.bond_lost.detail"
    assert incidents[0].detail_params == {"count": "2", "proxy": "Salon"}
    assert incidents[0].detail_params["proxy"] == "Salon"
    # Unrendered at detector level: prose is the coordinator's job.
    assert incidents[0].detail == ""


def test_an_unnamed_proxy_falls_back_to_its_source_id():
    """An empty names map must still yield a usable remedy.

    ``names`` is built from the device registry, which can lag a freshly
    adopted proxy by a poll or two. Rendering "re-pair through {proxy}" with
    nothing in it would make the one actionable incident BlueSight has
    unactionable, so the raw source is the fallback.
    """
    incidents = detect_bond_lost([_tel({ADDR: 2}, set())], {})
    assert incidents[0].detail_params["proxy"] == "proxy1"


def test_a_zero_failure_count_is_not_an_incident():
    """The firmware can legitimately report ``MAC:0`` for an address it tracks
    and has never failed on: zero failures is the absence of the symptom."""
    assert detect_bond_lost([_tel({ADDR: 0}, set())], {}) == []


def test_a_negative_count_is_not_an_incident():
    """Unreachable through ``parse_counts`` (its regex is ``[0-9]+``), so this
    pins the detector's own guard rather than the parser's: a detector takes
    plain data and must not open an incident on a number nobody measured."""
    assert detect_bond_lost([_tel({ADDR: -1}, set())], {}) == []


def test_a_bond_on_a_different_proxy_does_not_excuse_the_failing_one():
    """Bonds are per-central: each proxy has its own NVS store.

    A device paired through the lounge proxy has no bond on the kitchen one,
    and Home Assistant will still route connections there. That is exactly the
    fault, and the remedy still names the proxy that is failing -- so the
    incident is raised for proxy1 and only proxy1.
    """
    telemetry = [
        _tel({ADDR: 4}, set(), source="proxy1"),
        _tel({}, {ADDR}, source="proxy2"),
    ]
    incidents = detect_bond_lost(telemetry, {})
    assert [(i.address, i.sources) for i in incidents] == [(ADDR, ["proxy1"])]


def test_each_failing_proxy_gets_its_own_incident():
    """Two proxies failing on one device is two remedies, not one."""
    telemetry = [
        _tel({ADDR: 2}, set(), source="proxy1"),
        _tel({ADDR: 5}, set(), source="proxy2"),
    ]
    incidents = detect_bond_lost(telemetry, {"proxy1": "Salon", "proxy2": "Cuisine"})
    assert [i.sources for i in incidents] == [["proxy1"], ["proxy2"]]
    # Distinct identities, so neither suppresses the other across snapshots.
    assert len({i.key for i in incidents}) == 2


def test_output_order_is_deterministic():
    """Addresses are sorted within a proxy and proxies keep input order.

    The incident list lands in an entity attribute; an order that shuffled
    between snapshots would churn that attribute for no change in the world.
    """
    telemetry = [
        _tel({OTHER: 1, ADDR: 1}, set(), source="proxy2"),
        _tel({OTHER: 1, ADDR: 1}, set(), source="proxy1"),
    ]
    incidents = detect_bond_lost(telemetry, {})
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
    assert detect_bond_lost([_tel({ADDR: 3}, {ADDR.lower()})], {}) == []


def test_the_reported_address_is_normalised():
    """The address is matched against the device registry downstream, and is
    half of the incident key, so it must be canonical however it arrived."""
    incidents = detect_bond_lost([_tel({ADDR.lower(): 3}, set())], {})
    assert incidents[0].address == ADDR


def test_the_count_is_a_string_that_selects_a_plural_form():
    """``plural_count`` reads the pivot off ``detail_params['count']``, so the
    number the user reads and the form it selects agree by construction."""
    single = detect_bond_lost([_tel({ADDR: 1}, set())], {})[0]
    many = detect_bond_lost([_tel({ADDR: 9}, set())], {})[0]
    assert single.detail_params["count"] == "1"
    assert plural_count(single.detail_params) == 1
    assert plural_count(many.detail_params) == 9


def test_two_spellings_of_one_address_are_one_incident():
    """Also unreachable through the parser, and also worth guarding: two rows
    for one device would share an incident key, so nothing downstream could
    tell them apart -- the card would draw the fault twice and the policy
    layer would treat one clearance as two."""
    incidents = detect_bond_lost([_tel({ADDR: 3, ADDR.lower(): 5}, set())], {})
    assert [(i.address, i.detail_params["count"]) for i in incidents] == [(ADDR, "5")]
