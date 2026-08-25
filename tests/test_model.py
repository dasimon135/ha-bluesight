from dataclasses import fields

from custom_components.bluesight.model import (
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
