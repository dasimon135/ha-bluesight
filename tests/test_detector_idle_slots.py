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

from custom_components.bluesight.const import DEFAULT_IDLE_SLOT_THRESHOLD_S
from custom_components.bluesight.detector import detect_ghost_slots, detect_idle_slots
from custom_components.bluesight.model import IncidentKind, ProxySlots
from custom_components.bluesight.rendering import plural_count
from custom_components.bluesight.telemetry import ProxyTelemetry

ADDR = "D0:CF:13:0E:C9:2A"
OTHER = "AA:BB:CC:DD:EE:FF"
THRESHOLD = 300.0


def _tel(idle, source="proxy1"):
    return ProxyTelemetry(source, slot_idle_seconds=idle)


def _allocated(*addresses, source="proxy1"):
    """One proxy holding exactly these addresses as habluetooth reports them.

    Spelled verbatim, because that is what ``adapter.current_proxy_slots``
    produces: it canonicalises ``source`` and hands habluetooth's address list
    straight through.
    """
    return [ProxySlots(source, "Salon", 3, 3 - len(addresses), list(addresses))]


def test_idle_beyond_threshold_on_an_unmanaged_device_is_a_ghost_slot():
    incidents = detect_idle_slots([_tel({ADDR: 600.0})], _allocated(ADDR), set(), THRESHOLD, {})
    assert len(incidents) == 1
    assert incidents[0].kind is IncidentKind.GHOST_SLOT
    assert incidents[0].address == ADDR
    assert incidents[0].sources == ["proxy1"]
    assert incidents[0].evidence == "smp"


def test_idle_below_threshold_is_healthy():
    assert detect_idle_slots([_tel({ADDR: 30.0})], _allocated(ADDR), set(), THRESHOLD, {}) == []


def test_exactly_at_the_threshold_is_healthy():
    """Strictly greater, as in ``detect_stalled_proxies``.

    The two are the same shape -- a measured duration against a configured one
    -- so they must agree on the boundary, and the quiet direction is the right
    one for a threshold the user tunes downward until it fires.
    ``detect_storm`` uses ``>=`` because it counts events instead: the fifth
    failure of five is the storm, where the 300th second of a 300s budget is
    not yet an overrun.
    """
    assert detect_idle_slots([_tel({ADDR: THRESHOLD})], _allocated(ADDR), set(), THRESHOLD, {}) == []


def test_a_zero_idle_reading_is_healthy():
    """Traffic this instant: the liveliest reading the firmware can send."""
    assert detect_idle_slots([_tel({ADDR: 0.0})], _allocated(ADDR), set(), THRESHOLD, {}) == []


def test_a_managed_device_is_left_to_the_entity_based_detector():
    """Entity availability is the more semantic signal; two detectors firing on
    one address would double-report the same slot."""
    assert detect_idle_slots([_tel({ADDR: 600.0})], _allocated(ADDR), {ADDR}, THRESHOLD, {}) == []


def test_a_managed_address_in_another_case_is_still_managed():
    """Correlation is case-insensitive, as everywhere else in BlueSight.

    ``managed_addresses`` is assembled by the coordinator from the device
    registry, not by the telemetry parser, so the two sides of this membership
    test come from different places and only agree once both are canonicalised.
    Reading a managed device as unmanaged is the worst failure available here:
    it flags a device Home Assistant is perfectly able to judge -- and has
    judged alive -- and it does so for every such device at once.
    """
    assert detect_idle_slots([_tel({ADDR: 600.0})], _allocated(ADDR), {ADDR.lower()}, THRESHOLD, {}) == []


def test_the_reported_address_is_normalised():
    """The address is matched against the device registry downstream, and is
    half of the incident key, so it must be canonical however it arrived."""
    incidents = detect_idle_slots([_tel({ADDR.lower(): 600.0})], _allocated(ADDR), set(), THRESHOLD, {})
    assert incidents[0].address == ADDR


def test_absent_telemetry_yields_nothing():
    assert detect_idle_slots([_tel(None)], _allocated(ADDR), set(), THRESHOLD, {}) == []


def test_a_proxy_holding_no_connections_yields_nothing():
    """An empty reading is a proxy legitimately reporting zero connections --
    a different answer from ``None``, and equally not an incident."""
    assert detect_idle_slots([_tel({})], _allocated(ADDR), set(), THRESHOLD, {}) == []


def test_no_telemetry_at_all_yields_nothing():
    assert detect_idle_slots([], _allocated(ADDR), set(), THRESHOLD, {}) == []


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
    incidents = detect_idle_slots([_tel({ADDR: 600.0})], _allocated(ADDR), set(), THRESHOLD, {})
    assert incidents[0].detail_key == "incident.ghost_slot.idle_detail"
    assert incidents[0].detail_params == {"proxy": "proxy1", "seconds": "600"}
    # Unrendered at detector level: prose is the coordinator's job.
    assert incidents[0].detail == ""


def test_the_detail_names_the_proxy_holding_the_slot():
    """Which proxy to restart is the remedy, so the friendly name travels as a
    parameter -- user-controlled text the renderer substitutes, never rescans."""
    names = {"proxy1": "Salon"}
    incidents = detect_idle_slots([_tel({ADDR: 600.0})], _allocated(ADDR), set(), THRESHOLD, names)
    assert incidents[0].detail_params["proxy"] == "Salon"


def test_an_unnamed_proxy_falls_back_to_its_source_id():
    """``names`` is built from the device registry, which can lag a freshly
    adopted proxy by a poll or two; an empty ``{proxy}`` would leave the one
    actionable part of the incident blank."""
    incidents = detect_idle_slots([_tel({ADDR: 600.0})], _allocated(ADDR), set(), THRESHOLD, {})
    assert incidents[0].detail_params["proxy"] == "proxy1"


def test_the_measured_time_is_truncated_not_rounded():
    """int() truncates, exactly as ``detect_stalled_proxies`` does with the
    same kind of reading: a duration reported a second short is honest, and a
    fractional second in an entity attribute is churn."""
    incidents = detect_idle_slots([_tel({ADDR: 599.9})], _allocated(ADDR), set(), THRESHOLD, {})
    assert incidents[0].detail_params["seconds"] == "599"


def test_the_seconds_parameter_selects_no_plural_form():
    """The pivot ``plural_count`` reads is ``count``; this template counts
    nothing, so it renders from the unsuffixed key and needs no ``.one`` /
    ``.other`` pair. Pinned because renaming the parameter to ``count`` would
    silently start selecting forms that do not exist."""
    incidents = detect_idle_slots([_tel({ADDR: 600.0})], _allocated(ADDR), set(), THRESHOLD, {})
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
        [_tel({ADDR: 600.0, ADDR.lower(): 900.0})],
        _allocated(ADDR),
        set(),
        THRESHOLD,
        {},
    )
    assert [(i.address, i.detail_params["seconds"]) for i in incidents] == [
        (ADDR, "600")
    ]


def test_a_fresher_reading_under_the_threshold_clears_a_stale_duplicate():
    """The same merge at the boundary that matters: one of the two spellings
    says the slot saw traffic 30 seconds ago, so the slot is not stuck."""
    duplicated = _tel({ADDR: 900.0, ADDR.lower(): 30.0})
    assert detect_idle_slots([duplicated], _allocated(ADDR), set(), THRESHOLD, {}) == []


def test_each_proxy_reporting_an_idle_slot_gets_its_own_incident():
    """One address idle on two proxies is a deadlock in its own right, and each
    proxy still holds a slot of its own, so each gets its own incident -- and
    its own key, so neither suppresses the other across snapshots."""
    telemetry = [
        _tel({ADDR: 600.0}, source="proxy1"),
        _tel({ADDR: 900.0}, source="proxy2"),
    ]
    proxies = _allocated(ADDR, source="proxy1") + _allocated(ADDR, source="proxy2")
    incidents = detect_idle_slots(telemetry, proxies, set(), THRESHOLD, {})
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
    proxies = (
        _allocated(OTHER, ADDR, source="proxy2")
        + _allocated(OTHER, ADDR, source="proxy1")
    )
    incidents = detect_idle_slots(telemetry, proxies, set(), THRESHOLD, {})
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
    from_firmware = detect_idle_slots(
        [_tel({ADDR: 600.0})], proxies, {ADDR}, THRESHOLD, {}
    )
    assert len(from_entities) == 1
    assert from_firmware == []
    assert from_entities[0].key == f"ghost_slot:{ADDR}:proxy1"


# --- only Home Assistant's own slots ---------------------------------------
#
# The firmware watches the controller's GATTC event stream, so `BlueSight
# slots` reports every GATT client connection on the node -- including the
# ones `bluetooth_proxy` never opened. Those draw on `esp32_ble.max_connections`
# and not on the slots the proxy advertises to Home Assistant, so judging one
# as a ghost *slot* would be a true measurement under a false frame: the remedy
# says "restart that proxy to free the slot", and the restart frees no slot
# Home Assistant was waiting on.


def test_a_connection_that_is_not_an_allocated_slot_is_not_an_incident():
    """The `ble_client:` case, and the whole point of the allocated filter.

    Every other condition of the incident is met -- the peer is unknown to the
    registry and has been silent for hours -- so `allocated` is the only thing
    separating it from the case this detector exists for. Live on real
    hardware: a proxy carrying `ble_client:` pairing responders holds exactly
    such links, indefinitely and correctly.
    """
    telemetry = [_tel({OTHER: 600.0})]
    assert detect_idle_slots(telemetry, _allocated(ADDR), set(), THRESHOLD, {}) == []


def test_an_allocated_slot_for_a_registry_unknown_device_still_fires():
    """The load-bearing half: the filter must not cost the target case.

    habluetooth allocates per address and knows nothing of Home Assistant's
    device registry -- `HaBluetoothSlotAllocations.allocated` is "addresses of
    connected devices" -- so a connection Home Assistant opened for a device
    its registry cannot account for is allocated all the same. That device is
    unmanaged, and therefore unjudgeable by `detect_ghost_slots`, which is
    precisely why this detector exists.
    """
    incidents = detect_idle_slots(
        [_tel({ADDR: 600.0})], _allocated(ADDR), set(), THRESHOLD, {}
    )
    assert [i.address for i in incidents] == [ADDR]


def test_habluetooths_raw_spelling_of_an_allocated_address_still_matches():
    """`ProxySlots.allocated` is *not* canonicalised on the way in.

    `adapter.current_proxy_slots` normalises `source` and hands habluetooth's
    address list through verbatim, while a telemetry address is canonicalised
    by `telemetry.expand_compact_mac` before it ever reaches a detector. Two
    sides, two spellings, and a raw comparison would put *every* address
    outside `allocated` -- silencing the detector completely, with no symptom
    to follow but an absence.
    """
    incidents = detect_idle_slots(
        [_tel({ADDR: 600.0})], _allocated(ADDR.lower()), set(), THRESHOLD, {}
    )
    assert [i.address for i in incidents] == [ADDR]


def test_the_proxy_sources_are_matched_case_insensitively():
    """The second address comparison: which proxy's allocated set to read.

    `ProxySlots.source` is canonicalised by `adapter` and
    `ProxyTelemetry.source` by `telemetry_reader` -- two modules agreeing
    rather than one rule. A mismatch would empty the allocated set for a whole
    proxy rather than for one address, so every slot it holds would go
    unjudged.
    """
    telemetry = [_tel({ADDR: 600.0}, source="AA:BB:CC:00:11:22")]
    proxies = _allocated(ADDR, source="aa:bb:cc:00:11:22")
    incidents = detect_idle_slots(telemetry, proxies, set(), THRESHOLD, {})
    assert [i.address for i in incidents] == [ADDR]


def test_a_slot_allocated_on_another_proxy_does_not_license_this_one():
    """Allocation is per proxy, as the incident is: an address held on proxy2
    says nothing about a connection proxy1 reports. Reading the allocated sets
    as one pooled set would re-admit exactly the connections this filter
    removes, on any fleet where some other proxy happens to hold that peer."""
    telemetry = [_tel({ADDR: 600.0}, source="proxy1")]
    proxies = _allocated(ADDR, source="proxy2")
    assert detect_idle_slots(telemetry, proxies, set(), THRESHOLD, {}) == []


def test_a_proxy_habluetooth_reports_no_allocations_for_yields_nothing():
    """No allocation record means Home Assistant holds no slots there, so
    nothing the node reports can be a stuck one. Reached by a proxy absent from
    the allocation snapshot as well as by one holding an empty list."""
    telemetry = [_tel({ADDR: 600.0})]
    assert detect_idle_slots(telemetry, [], set(), THRESHOLD, {}) == []
    assert detect_idle_slots(telemetry, _allocated(), set(), THRESHOLD, {}) == []


def test_a_released_allocation_still_reported_by_the_firmware_is_silent():
    """The reverse mismatch, and the direction it resolves in.

    habluetooth has released the slot while the node still reports the link.
    Its ordinary cause is staleness -- the slots sensor publishes on change
    plus a tick, so it lags a disconnect Home Assistant has already booked --
    and one snapshot cannot tell that apart from a link genuinely stuck on the
    node. Even in the latter case the slot is one Home Assistant considers free
    and will hand to the next device, so `GHOST_SLOT` misdescribes it exactly
    as it misdescribes a `ble_client:` link. Silence is the honest answer, and
    it is what falling outside `allocated` already gives.
    """
    telemetry = [_tel({ADDR: 9999.0})]
    proxies = _allocated(OTHER)
    assert detect_idle_slots(telemetry, proxies, set(), THRESHOLD, {}) == []


def test_both_filters_apply_and_neither_substitutes_for_the_other():
    """An allocated slot for a *managed* device is still left to
    `detect_ghost_slots`, and an unallocated connection for an unmanaged device
    is still nobody's incident. The two conditions are conjunctive."""
    telemetry = [_tel({ADDR: 600.0, OTHER: 600.0})]
    proxies = _allocated(ADDR, OTHER)
    incidents = detect_idle_slots(telemetry, proxies, {ADDR}, THRESHOLD, {})
    assert [i.address for i in incidents] == [OTHER]


def test_the_shipped_default_does_not_flag_a_measured_healthy_device():
    """The one reading that set `DEFAULT_IDLE_SLOT_THRESHOLD_S`, kept executable.

    A Daikin Madoka BRC1H thermostat, working normally on a live proxy,
    reported 430.7s of GATT silence. It went unflagged only because it is in
    Home Assistant's device registry, so this detector stands down for it --
    the same device *absent* from the registry, which is the whole population
    judged here, would have been a ghost slot at the 300s default this
    replaced, while perfectly healthy.

    So this asserts the default against the measurement, not against a number.
    Lowering the constant back under 430 fails here with the reason attached,
    which a bare `== 1800.0` could never say. It deliberately does not use this
    module's `THRESHOLD`: that one is a fixture for the boundary tests and has
    nothing to do with what ships.
    """
    telemetry = [_tel({ADDR: 430.7})]
    incidents = detect_idle_slots(
        telemetry, _allocated(ADDR), set(), DEFAULT_IDLE_SLOT_THRESHOLD_S, {}
    )
    assert incidents == []
