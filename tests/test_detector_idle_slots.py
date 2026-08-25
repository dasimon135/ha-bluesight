"""Idle-slot detection -- the ghost-slot case Home Assistant cannot judge.

``detect_ghost_slots`` reaches its verdict through entity availability, which
exists only for devices in Home Assistant's registry; ``availability.py``
deliberately calls an unknown device alive rather than flag one it cannot
judge. That leaves a real hole: a peripheral Home Assistant does not manage can
hold a proxy slot forever and nothing observable changes. The firmware sees the
connection itself, so it can time the silence directly -- which is the one way
to judge a device Home Assistant knows nothing about.
"""
from __future__ import annotations

from custom_components.bluesight.detector import detect_ghost_slots, detect_idle_slots
from custom_components.bluesight.model import IncidentKind, ProxySlots
from custom_components.bluesight.rendering import plural_count
from custom_components.bluesight.telemetry import ProxyTelemetry

ADDR = "D0:CF:13:0E:C9:2A"
OTHER = "AA:BB:CC:DD:EE:FF"
THRESHOLD = 300.0


def _tel(idle, source="proxy1"):
    return ProxyTelemetry(source, slot_idle_seconds=idle)


def test_idle_beyond_threshold_on_an_unmanaged_device_is_a_ghost_slot():
    incidents = detect_idle_slots([_tel({ADDR: 600.0})], set(), THRESHOLD, {})
    assert len(incidents) == 1
    assert incidents[0].kind is IncidentKind.GHOST_SLOT
    assert incidents[0].address == ADDR
    assert incidents[0].sources == ["proxy1"]
    assert incidents[0].evidence == "smp"


def test_idle_below_threshold_is_healthy():
    assert detect_idle_slots([_tel({ADDR: 30.0})], set(), THRESHOLD, {}) == []


def test_exactly_at_the_threshold_is_healthy():
    """Strictly greater, as in ``detect_stalled_proxies``.

    The two are the same shape -- a measured duration against a configured one
    -- so they must agree on the boundary, and the quiet direction is the right
    one for a threshold the user tunes downward until it fires.
    ``detect_storm`` uses ``>=`` because it counts events instead: the fifth
    failure of five is the storm, where the 300th second of a 300s budget is
    not yet an overrun.
    """
    assert detect_idle_slots([_tel({ADDR: THRESHOLD})], set(), THRESHOLD, {}) == []


def test_a_zero_idle_reading_is_healthy():
    """Traffic this instant: the liveliest reading the firmware can send."""
    assert detect_idle_slots([_tel({ADDR: 0.0})], set(), THRESHOLD, {}) == []


def test_a_managed_device_is_left_to_the_entity_based_detector():
    """Entity availability is the more semantic signal; two detectors firing on
    one address would double-report the same slot."""
    assert detect_idle_slots([_tel({ADDR: 600.0})], {ADDR}, THRESHOLD, {}) == []


def test_a_managed_address_in_another_case_is_still_managed():
    """Correlation is case-insensitive, as everywhere else in BlueSight.

    ``managed_addresses`` is assembled by the coordinator from the device
    registry, not by the telemetry parser, so the two sides of this membership
    test come from different places and only agree once both are canonicalised.
    Reading a managed device as unmanaged is the worst failure available here:
    it flags a device Home Assistant is perfectly able to judge -- and has
    judged alive -- and it does so for every such device at once.
    """
    assert detect_idle_slots([_tel({ADDR: 600.0})], {ADDR.lower()}, THRESHOLD, {}) == []


def test_the_reported_address_is_normalised():
    """The address is matched against the device registry downstream, and is
    half of the incident key, so it must be canonical however it arrived."""
    incidents = detect_idle_slots([_tel({ADDR.lower(): 600.0})], set(), THRESHOLD, {})
    assert incidents[0].address == ADDR


def test_absent_telemetry_yields_nothing():
    assert detect_idle_slots([_tel(None)], set(), THRESHOLD, {}) == []


def test_a_proxy_holding_no_connections_yields_nothing():
    """An empty reading is a proxy legitimately reporting zero connections --
    a different answer from ``None``, and equally not an incident."""
    assert detect_idle_slots([_tel({})], set(), THRESHOLD, {}) == []


def test_no_telemetry_at_all_yields_nothing():
    assert detect_idle_slots([], set(), THRESHOLD, {}) == []


def test_detail_reports_the_measured_idle_time():
    """The measured silence is what makes this incident actionable.

    Detectors have emitted a key plus parameters rather than prose since 0.5.0
    -- ``detail`` is rendered later, in ``build_triage_data``, from the language
    catalogue -- so the number is pinned where the detector actually puts it.
    ``incident.ghost_slot.idle_detail`` is a *second* detail for an existing
    kind and must not displace ``incident.ghost_slot.detail``: a slot idle on an
    unmanaged device and a slot held for a device Home Assistant has lost are
    different findings that happen to share a kind.
    """
    incidents = detect_idle_slots([_tel({ADDR: 600.0})], set(), THRESHOLD, {})
    assert incidents[0].detail_key == "incident.ghost_slot.idle_detail"
    assert incidents[0].detail_params == {"proxy": "proxy1", "seconds": "600"}
    # Unrendered at detector level: prose is the coordinator's job.
    assert incidents[0].detail == ""


def test_the_detail_names_the_proxy_holding_the_slot():
    """Which proxy to restart is the remedy, so the friendly name travels as a
    parameter -- user-controlled text the renderer substitutes, never rescans."""
    names = {"proxy1": "Salon"}
    incidents = detect_idle_slots([_tel({ADDR: 600.0})], set(), THRESHOLD, names)
    assert incidents[0].detail_params["proxy"] == "Salon"


def test_an_unnamed_proxy_falls_back_to_its_source_id():
    """``names`` is built from the device registry, which can lag a freshly
    adopted proxy by a poll or two; an empty ``{proxy}`` would leave the one
    actionable part of the incident blank."""
    incidents = detect_idle_slots([_tel({ADDR: 600.0})], set(), THRESHOLD, {})
    assert incidents[0].detail_params["proxy"] == "proxy1"


def test_the_measured_time_is_truncated_not_rounded():
    """int() truncates, exactly as ``detect_stalled_proxies`` does with the
    same kind of reading: a duration reported a second short is honest, and a
    fractional second in an entity attribute is churn."""
    incidents = detect_idle_slots([_tel({ADDR: 599.9})], set(), THRESHOLD, {})
    assert incidents[0].detail_params["seconds"] == "599"


def test_the_seconds_parameter_selects_no_plural_form():
    """The pivot ``plural_count`` reads is ``count``; this template counts
    nothing, so it renders from the unsuffixed key and needs no ``.one`` /
    ``.other`` pair. Pinned because renaming the parameter to ``count`` would
    silently start selecting forms that do not exist."""
    incidents = detect_idle_slots([_tel({ADDR: 600.0})], set(), THRESHOLD, {})
    assert plural_count(incidents[0].detail_params) is None


def test_two_spellings_of_one_address_are_one_incident():
    """Unreachable through ``parse_idle_seconds`` -- it canonicalises its keys,
    so the two collapse before they arrive -- and still worth guarding: two rows
    for one slot would share an incident key, so nothing downstream could tell
    them apart. The card would draw the fault twice and the policy layer would
    treat one clearance as two.

    The *lower* reading wins, unlike the merge in ``detect_bond_lost``, for a
    reason particular to this signal: an SMP counter only climbs, so the higher
    reading there is the later one. An idle timer resets to zero on traffic, so
    the lower reading here is the fresher observation -- and the one that says
    the slot is alive.
    """
    incidents = detect_idle_slots(
        [_tel({ADDR: 600.0, ADDR.lower(): 900.0})], set(), THRESHOLD, {}
    )
    assert [(i.address, i.detail_params["seconds"]) for i in incidents] == [
        (ADDR, "600")
    ]


def test_a_fresher_reading_under_the_threshold_clears_a_stale_duplicate():
    """The same merge at the boundary that matters: one of the two spellings
    says the slot saw traffic 30 seconds ago, so the slot is not stuck."""
    duplicated = _tel({ADDR: 900.0, ADDR.lower(): 30.0})
    assert detect_idle_slots([duplicated], set(), THRESHOLD, {}) == []


def test_each_proxy_reporting_an_idle_slot_gets_its_own_incident():
    """One address idle on two proxies is a deadlock in its own right, and each
    proxy still holds a slot of its own, so each gets its own incident -- and
    its own key, so neither suppresses the other across snapshots."""
    telemetry = [
        _tel({ADDR: 600.0}, source="proxy1"),
        _tel({ADDR: 900.0}, source="proxy2"),
    ]
    incidents = detect_idle_slots(telemetry, set(), THRESHOLD, {})
    assert [i.sources for i in incidents] == [["proxy1"], ["proxy2"]]
    assert len({i.key for i in incidents}) == 2


def test_output_order_is_deterministic():
    """Addresses are sorted within a proxy and proxies keep input order. The
    incident list lands in an entity attribute; an order that shuffled between
    snapshots would churn that attribute for no change in the world."""
    telemetry = [
        _tel({OTHER: 600.0, ADDR: 600.0}, source="proxy2"),
        _tel({OTHER: 600.0, ADDR: 600.0}, source="proxy1"),
    ]
    incidents = detect_idle_slots(telemetry, set(), THRESHOLD, {})
    assert [(i.sources[0], i.address) for i in incidents] == [
        ("proxy2", OTHER),
        ("proxy2", ADDR),
        ("proxy1", OTHER),
        ("proxy1", ADDR),
    ]


def test_the_two_ghost_detectors_never_both_fire_for_one_address():
    """The ``managed_addresses`` guard is load-bearing, not tidiness.

    Both detectors raise ``GHOST_SLOT`` for the same address from the same
    proxy, so their incidents would be identical under ``Incident.key`` -- one
    slot reported twice, indistinguishable downstream. An address whose
    entities read "unavailable" is by construction one Home Assistant manages,
    which is precisely the set this detector stands down for.
    """
    proxies = [ProxySlots("proxy1", "Salon", 3, 2, [ADDR])]
    from_entities = detect_ghost_slots(proxies, {ADDR: False})
    from_firmware = detect_idle_slots([_tel({ADDR: 600.0})], {ADDR}, THRESHOLD, {})
    assert len(from_entities) == 1
    assert from_firmware == []
    assert from_entities[0].key == f"ghost_slot:{ADDR}:proxy1"
