from dataclasses import fields

from custom_components.bluesight.model import (
    DeviceRef,
    Incident,
    IncidentKind,
    ProxyHealth,
    ProxySlots,
    normalize_address,
)


def test_normalize_address_uppercases_and_strips():
    assert normalize_address(" aa:bb:cc ") == "AA:BB:CC"
    assert normalize_address("AA:BB:CC") == "AA:BB:CC"


def test_proxyslots_used_derived():
    p = ProxySlots(source="AA:BB", name="Salon Proxy", slots=3, free=1,
                   allocated=["11:22", "33:44"])
    assert p.used == 2
    assert p.is_full is False
    assert p.is_connectable is True


def test_saturated_proxy_is_full():
    p = ProxySlots(source="AA:BB", name="Salon Proxy", slots=3, free=0,
                   allocated=["11:22", "33:44", "55:66"])
    assert p.is_full is True


def test_non_connectable_scanner_is_not_full():
    """habluetooth registers passive scanners with slots=0, free=0 — they hold
    no connections at all, which is not the same as being saturated."""
    p = ProxySlots(source="AA:BB", name="Passive", slots=0, free=0)
    assert p.is_full is False
    assert p.is_connectable is False
    assert p.used == 0


def test_incident_identity_is_stable():
    a = Incident(kind=IncidentKind.DEADLOCK, address="11:22", sources=["AA", "BB"])
    b = Incident(kind=IncidentKind.DEADLOCK, address="11:22", sources=["BB", "AA"])
    assert a.key == b.key   # order-independent identity


def test_proxyhealth_fields():
    p = ProxyHealth(source="AA", name="Salon", connectable=True,
                    online=True, seconds_since_detection=3.0, device_count=5)
    assert p.source == "AA" and p.online is True and p.device_count == 5


def test_new_incident_kinds_exist():
    assert IncidentKind.PROXY_OFFLINE.value == "proxy_offline"
    assert IncidentKind.PROXY_STALLED.value == "proxy_stalled"
    assert IncidentKind.PROXY_REBOOT_STORM.value == "proxy_reboot_storm"


def test_incident_translation_fields_default_empty():
    """A hand-built incident carries no key and no parameters.

    Task 6 renders `detail` from these; until every producer sets them, the
    empty defaults must keep positional construction working unchanged.
    """
    inc = Incident(IncidentKind.DEADLOCK, "11:22", ["AA", "BB"], "held twice")
    assert inc.detail == "held twice"
    assert inc.detail_key == ""
    assert inc.detail_params == {}


def test_incident_identity_ignores_translation_fields():
    """`key` is the incident's identity across snapshots.

    If the key or its parameters entered it, an incident whose parameters
    shift -- a rising failure count -- would look like a brand new incident
    every snapshot and re-alert forever.
    """
    a = Incident(IncidentKind.STORM, "11:22", ["AA"],
                 detail_key="incident.storm.detail",
                 detail_params={"count": "5", "seconds": "300"})
    b = Incident(IncidentKind.STORM, "11:22", ["AA"],
                 detail_key="incident.something.else",
                 detail_params={"count": "9", "seconds": "300"})
    assert a.key == b.key


def test_evidence_defaults_to_heuristic():
    inc = Incident(IncidentKind.STORM, "AA:BB:CC:DD:EE:FF", [])
    assert inc.evidence == "heuristic"


def test_evidence_is_not_part_of_the_key():
    """The same fault is the same incident however it was observed.

    A proxy that gains or loses telemetry would otherwise emit a duplicate
    alert for a storm that never stopped.
    """
    heuristic = Incident(IncidentKind.STORM, "AA:BB:CC:DD:EE:FF", ["p1"])
    measured = Incident(
        IncidentKind.STORM, "AA:BB:CC:DD:EE:FF", ["p1"], evidence="smp"
    )
    assert heuristic.key == measured.key


def test_bond_lost_is_an_incident_kind():
    assert IncidentKind.BOND_LOST.value == "bond_lost"


def test_evidence_is_the_last_field():
    """Field order is a contract: `Incident` is built positionally.

    The plan for this change said to put `evidence` "after `detail`", which was
    written when `detail` was the final field. Doing that now would shift
    `detail_key` and `detail_params` one place right and silently mis-assign
    every positional construction of them. Pinning the order makes that a test
    failure rather than a rendering bug.
    """
    assert [f.name for f in fields(Incident)] == [
        "kind",
        "address",
        "sources",
        "detail",
        "detail_key",
        "detail_params",
        "evidence",
    ]


def test_full_positional_construction_still_binds_the_translation_fields():
    """The concrete form of the guard above, as a caller writes it."""
    inc = Incident(
        IncidentKind.STORM,
        "11:22",
        ["AA"],
        "5 failures / 300s",
        "incident.storm.detail",
        {"count": "5", "seconds": "300"},
    )
    assert inc.detail == "5 failures / 300s"
    assert inc.detail_key == "incident.storm.detail"
    assert inc.detail_params == {"count": "5", "seconds": "300"}
    assert inc.evidence == "heuristic"


def test_evidence_does_not_leak_between_instances():
    """A mutable default here would make one detector's evidence global."""
    a = Incident(IncidentKind.STORM, "11:22", ["AA"], evidence="smp")
    b = Incident(IncidentKind.STORM, "33:44", ["AA"])
    assert a.evidence == "smp"
    assert b.evidence == "heuristic"


def test_bond_lost_key_is_shaped_like_every_other_kind():
    """A new kind must not need special handling downstream.

    `notification_id_for_key` and the notified-key store both treat the key as
    an opaque slug, so the only requirement is that it is built the same way.
    """
    inc = Incident(IncidentKind.BOND_LOST, "11:22", ["BB", "AA"], evidence="smp")
    assert inc.key == "bond_lost:11:22:AA,BB"


# --- sources: identity for some kinds, evidence for others ------------------

def test_a_storms_key_ignores_which_proxy_measured_it():
    """For a storm, `sources` is evidence, not identity.

    The fault is "this device keeps failing to connect". Which proxy measured
    it is how we know, not what broke -- the same argument that already keeps
    `evidence` out of the key. Folding it in re-keys a storm that never
    stopped: the measured events age out of the failure window while inferred
    ones keep it above threshold, `sources` empties, and the user gets a
    second notification for one continuous fault.

    The literal is pinned because it is the pre-0.6.0 storm key, which
    `detect_storm` produced for every storm when it always built `sources=[]`.
    Excluding `sources` restores it exactly.
    """
    unattributed = Incident(IncidentKind.STORM, "11:22")
    measured = Incident(IncidentKind.STORM, "11:22", ["p1"], evidence="smp")
    assert measured.key == unattributed.key == "storm:11:22:"
    # The attribution is still carried -- only `key` ignores it. The card
    # renders it ("on {sources}") and diagnostics publish it.
    assert measured.sources == ["p1"]


def test_only_a_storms_key_ignores_its_sources():
    """The other half of the rule, pinned for every kind at once.

    A ghost slot is a slot stuck *on a proxy* and a lost bond is a missing
    entry in *a proxy's* own bond store, so one address on two proxies is two
    distinct faults that must notify separately -- `sources` is identity there
    and stays in the key. The `PROXY_*` kinds carry `sources == [address]`, so
    the question never arises for them and either answer is correct; they are
    swept in here only so that adding a kind forces a deliberate choice rather
    than inheriting one.
    """
    for kind in IncidentKind:
        one = Incident(kind, "11:22", ["p1"])
        other = Incident(kind, "11:22", ["p2"])
        assert (one.key == other.key) is (kind is IncidentKind.STORM), kind


# --- allocated_devices: who holds each slot ---------------------------------
#
# `allocated` (raw MACs, published since 0.1) and `allocated_devices` (the same
# slots, named) describe the same thing, so they must not be able to disagree.
# `allocated` stays the stored field and `allocated_devices` is DERIVED from
# it: the resolved names live in a side map keyed by normalized address, and
# the property walks `allocated` to build one entry per occupied slot. Nothing
# can add, drop or reorder an entry without doing the same to `allocated`.


def test_devices_defaults_empty_and_yields_a_bare_entry_per_slot():
    """A ProxySlots built without a resolver still answers the question."""
    p = ProxySlots("AA:BB", "Salon", 3, 1, ["11:22", "33:44"])
    assert p.devices == {}
    assert p.allocated_devices == [
        {"address": "11:22", "name": "", "device_id": None},
        {"address": "33:44", "name": "", "device_id": None},
    ]


def test_allocated_devices_is_present_and_empty_for_an_idle_proxy():
    """Empty, never absent: `allocated` is already `[]` for an idle proxy, and
    an attribute that appears and disappears is worse to template against."""
    p = ProxySlots("AA:BB", "Salon", 3, 3)
    assert p.allocated_devices == []


def test_allocated_devices_resolves_through_the_normalized_address():
    """habluetooth's spelling of an address and the registry index's need not
    match byte for byte; the side map is keyed by `normalize_address`."""
    p = ProxySlots(
        "AA:BB", "Salon", 3, 2, ["c3:eb:49:65:67:aa"],
        {"C3:EB:49:65:67:AA": DeviceRef("Madoka salon", "dev_1")},
    )
    assert p.allocated_devices == [
        {
            # Verbatim from `allocated`: the two attributes name the same
            # slots with the same strings.
            "address": "c3:eb:49:65:67:aa",
            "name": "Madoka salon",
            "device_id": "dev_1",
        }
    ]


def test_an_address_the_registry_does_not_know_keeps_its_raw_mac():
    """The interesting case: an unknown address holding a slot. The backend
    leaves the name empty and the device_id null; the card supplies the
    translated marker, because only the card knows the viewer's language."""
    p = ProxySlots(
        "AA:BB", "Salon", 3, 2, ["C3:EB:49:65:67:55"],
        {"11:22:33:44:55:66": DeviceRef("Elsewhere", "dev_9")},
    )
    assert p.allocated_devices == [
        {"address": "C3:EB:49:65:67:55", "name": "", "device_id": None}
    ]


def test_allocated_devices_cannot_diverge_from_allocated():
    """The lockstep guarantee, stated directly: same length, same addresses,
    same order, whatever the side map happens to contain."""
    for allocated, devices in (
        ([], {}),
        (["11:22"], {}),
        (["11:22", "33:44"], {"11:22": DeviceRef("A", "dev_a")}),
        # A resolver map holding addresses this proxy does not hold cannot
        # inject an entry.
        (["11:22"], {"55:66": DeviceRef("Stranger", "dev_x")}),
        # The same address twice on one proxy is two slots and two entries.
        (["11:22", "11:22"], {"11:22": DeviceRef("A", "dev_a")}),
    ):
        p = ProxySlots("AA:BB", "Salon", 3, 0, allocated, devices)
        entries = p.allocated_devices
        assert [e["address"] for e in entries] == list(p.allocated)


def test_resolved_names_do_not_enter_proxyslots_identity():
    """Two snapshots that differ only in a resolved name hold the same slots.

    The names are decoration read out of the device registry; the allocation
    state is the connection-layer fact. Tests that care about the names assert
    on `allocated_devices` directly rather than on ProxySlots equality.
    """
    bare = ProxySlots("AA:BB", "Salon", 3, 2, ["11:22"])
    named = ProxySlots(
        "AA:BB", "Salon", 3, 2, ["11:22"], {"11:22": DeviceRef("Madoka", "dev_1")}
    )
    assert bare == named
    assert bare.allocated_devices != named.allocated_devices


def test_device_ref_defaults_to_an_unknown_device():
    ref = DeviceRef()
    assert ref.name == ""
    assert ref.device_id is None
