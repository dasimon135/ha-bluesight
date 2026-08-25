"""Tests for the pure snapshot assembly.

The ``catalogue`` tests read the *shipped* catalogues from disk, exactly as
``async_setup_entry`` does: ``Incident.detail`` is published in the
``incidents`` attribute of ``binary_sensor.bluesight_incident`` and real user
automations format their push notifications from it, so what matters is that
the strings users actually receive are non-empty and translated -- not that an
inline fake catalogue round-trips.
"""
from custom_components.bluesight.coordinator_data import (
    BlueSightData,
    build_triage_data,
)
from custom_components.bluesight.incident_policy import reconcile
from custom_components.bluesight.locale import read_catalogues
from custom_components.bluesight.model import (
    Incident,
    IncidentKind,
    ProxyHealth,
    ProxySlots,
)
from custom_components.bluesight.rendering import Catalogue
from custom_components.bluesight.storm_signal import ReleaseTracker
from custom_components.bluesight.telemetry import (
    CounterDeltas,
    ProxyTelemetry,
    parse_counts,
)
from custom_components.bluesight.window import FailureWindow

_CATALOGUES = read_catalogues()
FR = Catalogue.for_language("fr", _CATALOGUES)


def _empty_window():
    return FailureWindow(window_s=300, threshold=5, clock=lambda: 0.0)


def test_deadlock_across_two_proxies_surfaces():
    proxies = [
        ProxySlots("AA", "proxy-a", 3, 2, ["11:22"]),
        ProxySlots("BB", "proxy-b", 3, 2, ["11:22"]),
    ]
    data = build_triage_data(proxies, {}, _empty_window())
    kinds = [i.kind for i in data.incidents]
    assert IncidentKind.DEADLOCK in kinds


def test_ghost_slot_surfaces():
    proxies = [ProxySlots("AA", "proxy-a", 3, 2, ["11:22"])]
    data = build_triage_data(proxies, {"11:22": False}, _empty_window())
    kinds = [i.kind for i in data.incidents]
    assert IncidentKind.GHOST_SLOT in kinds


def test_storm_surfaces():
    now = [0.0]
    w = FailureWindow(window_s=300, threshold=5, clock=lambda: now[0])
    for _ in range(5):
        now[0] += 10
        w.record("11:22")
    data = build_triage_data([], {}, w)
    storms = [i for i in data.incidents if i.kind is IncidentKind.STORM]
    assert len(storms) == 1
    assert storms[0].address == "11:22"


def test_empty_inputs_produce_empty_data():
    data = build_triage_data([], {}, _empty_window())
    assert data == BlueSightData([], [])


def test_proxies_passed_through_unchanged():
    proxies = [ProxySlots("AA", "proxy-a", 3, 3, [])]
    data = build_triage_data(proxies, {}, _empty_window())
    assert data.proxies is proxies


def _win():
    return FailureWindow(600, 3, clock=lambda: 0.0)


def test_proxies_health_defaults_empty():
    d = build_triage_data([], {}, _win())
    assert d.proxies_health == []


def test_stalled_proxy_surfaces_in_incidents():
    health = [ProxyHealth("AA", "Salon", True, True, 300.0, 0)]
    d = build_triage_data([], {}, _win(), proxies_health=health,
                          known_sources={"AA"}, reboot_window=_win(),
                          stalled_threshold_s=180.0)
    assert IncidentKind.PROXY_STALLED in {i.kind for i in d.incidents}
    assert d.proxies_health == health


def test_offline_proxy_surfaces_in_incidents():
    # AA is known but not present in proxies_health -> offline
    d = build_triage_data([], {}, _win(), proxies_health=[],
                          known_sources={"AA"}, reboot_window=_win())
    assert IncidentKind.PROXY_OFFLINE in {i.kind for i in d.incidents}


# --- catalogue rendering of `detail` --------------------------------------

def _deadlock_proxies():
    return [
        ProxySlots("AA", "proxy-a", 3, 2, ["11:22"]),
        ProxySlots("BB", "proxy-b", 3, 2, ["11:22"]),
    ]


def test_detail_is_rendered_in_the_catalogues_language():
    data = build_triage_data(
        _deadlock_proxies(), {}, _empty_window(), catalogue=FR
    )
    deadlock = next(
        i for i in data.incidents if i.kind is IncidentKind.DEADLOCK
    )
    assert deadlock.detail == "Retenu simultanément sur 2 proxys"


def test_detail_renders_for_every_kind_a_snapshot_can_produce():
    now = [0.0]
    storm = FailureWindow(window_s=300, threshold=5, clock=lambda: now[0])
    for _ in range(5):
        now[0] += 10
        storm.record("11:22")
    reboots = FailureWindow(window_s=600, threshold=3, clock=lambda: now[0])
    for _ in range(3):
        reboots.record("CC")
    # Telemetry-fed kinds are part of "every kind a snapshot can produce":
    # BOND_LOST and the idle-slot GHOST_SLOT reach the user through this same
    # assembly step, so they belong under the same guard as the rest.
    deltas = CounterDeltas()
    deltas.update("EE", {"55:66": 0})
    telemetry = [
        ProxyTelemetry(
            "EE",
            smp_failures={"55:66": 4},
            bonds=set(),
            slot_idle_seconds={"77:88": 900.0},
        )
    ]
    data = build_triage_data(
        [
            ProxySlots("AA", "proxy-a", 3, 2, ["33:44"]),
            ProxySlots("FF", "proxy-f", 3, 2, ["33:44"]),
        ],
        {"33:44": False},
        storm,
        proxies_health=[ProxyHealth("BB", "Salon", True, True, 300.0, 0)],
        known_sources={"BB", "DD"},
        reboot_window=reboots,
        stalled_threshold_s=180.0,
        catalogue=FR,
        telemetry=telemetry,
        counter_deltas=deltas,
        idle_threshold_s=300.0,
        proxy_names={"EE": "Cuisine"},
    )
    # Every kind the assembly can emit is present, so the assertion below is
    # about all of them and not only the ones a v1 snapshot could reach.
    assert {i.kind for i in data.incidents} == set(IncidentKind)
    # No incident a detector can raise may reach the user with a blank detail:
    # an automation that formats `{{ i.detail }}` would lose its message body.
    assert data.incidents
    assert all(i.detail for i in data.incidents), [
        i.kind for i in data.incidents if not i.detail
    ]


def test_without_a_catalogue_incidents_are_untouched():
    data = build_triage_data(_deadlock_proxies(), {}, _empty_window())
    deadlock = next(
        i for i in data.incidents if i.kind is IncidentKind.DEADLOCK
    )
    assert deadlock.detail == ""
    assert deadlock.detail_key == "incident.deadlock.detail"


def test_an_incident_without_a_detail_key_keeps_its_detail(monkeypatch):
    # Nothing to render: an incident carrying prose but no key must be passed
    # through, not blanked (rendering an empty key would yield "").
    prose = Incident(
        kind=IncidentKind.STORM, address="11:22", detail="already worded"
    )
    monkeypatch.setattr(
        "custom_components.bluesight.coordinator_data.detect_deadlocks",
        lambda proxies: [prose],
    )
    data = build_triage_data([], {}, _empty_window(), catalogue=FR)
    assert data.incidents == [prose]


def test_an_unknown_key_degrades_to_the_key_not_a_blank_detail():
    empty = Catalogue()
    data = build_triage_data(
        _deadlock_proxies(), {}, _empty_window(), catalogue=empty
    )
    deadlock = next(
        i for i in data.incidents if i.kind is IncidentKind.DEADLOCK
    )
    assert deadlock.detail == "incident.deadlock.detail"


# --- measured SMP evidence, per proxy -------------------------------------

ADDR = "D0:CF:13:0E:C9:2A"


def test_telemetry_proxies_feed_the_storm_window_with_measured_failures():
    """SMP evidence replaces the heuristic for the proxies that report it."""
    window = FailureWindow(300.0, 2, clock=lambda: 0.0)
    tel = [ProxyTelemetry("p1", smp_failures={ADDR: 5}, bonds={ADDR})]
    deltas = CounterDeltas()
    deltas.update("p1", {ADDR: 3})  # baseline

    data = build_triage_data(
        [], {}, window, telemetry=tel, counter_deltas=deltas
    )
    storms = [i for i in data.incidents if i.kind is IncidentKind.STORM]
    assert len(storms) == 1
    assert storms[0].evidence == "smp"


def test_proxies_without_telemetry_keep_the_heuristic_evidence_label():
    window = FailureWindow(300.0, 1, clock=lambda: 0.0)
    window.record(ADDR)
    data = build_triage_data([], {}, window)
    storms = [i for i in data.incidents if i.kind is IncidentKind.STORM]
    assert storms[0].evidence == "heuristic"


def test_measured_addresses_are_reported_by_the_telemetry_source():
    window = FailureWindow(300.0, 1, clock=lambda: 0.0)
    tel = [ProxyTelemetry("p1", smp_failures={ADDR: 2}, bonds={ADDR})]
    deltas = CounterDeltas()
    deltas.update("p1", {ADDR: 1})
    data = build_triage_data([], {}, window, telemetry=tel, counter_deltas=deltas)
    storms = [i for i in data.incidents if i.kind is IncidentKind.STORM]
    assert storms[0].sources == ["p1"]


def test_a_huge_firmware_delta_does_not_spin_the_event_loop():
    """`count` is unbounded firmware data; replaying it verbatim would hang HA."""
    window = FailureWindow(300.0, 2, clock=lambda: 0.0)
    tel = [ProxyTelemetry("p1", smp_failures={ADDR: 4294967295}, bonds={ADDR})]
    deltas = CounterDeltas()
    deltas.update("p1", {ADDR: 0})  # baseline 0, so the delta is the whole count
    data = build_triage_data([], {}, window, telemetry=tel, counter_deltas=deltas)
    assert window.count(ADDR) == window.threshold
    storms = [i for i in data.incidents if i.kind is IncidentKind.STORM]
    assert len(storms) == 1


def test_one_snapshot_never_yields_two_incidents_with_the_same_key():
    """Measured and heuristic detectors can now reach the same address.

    `reconcile` maps incidents to notification ids by key, so two incidents
    sharing a key in one snapshot produce two creates and one notification --
    the second silently overwrites the first.
    """
    window = FailureWindow(300.0, 1, clock=lambda: 0.0)
    window.record(ADDR)
    tel = [ProxyTelemetry("p1", smp_failures={ADDR: 5}, bonds=set(),
                          slot_idle_seconds={ADDR: 9999.0})]
    deltas = CounterDeltas()
    deltas.update("p1", {ADDR: 0})
    data = build_triage_data([], {}, window, telemetry=tel, counter_deltas=deltas)
    keys = [i.key for i in data.incidents]
    assert len(keys) == len(set(keys)), f"duplicate incident keys: {keys}"


def test_an_unmanaged_idle_address_is_still_reported():
    """The managed set is registry-resolved addresses, not every allocated one."""
    window = FailureWindow(300.0, 99, clock=lambda: 0.0)
    tel = [ProxyTelemetry("p1", slot_idle_seconds={ADDR: 9999.0})]
    data = build_triage_data(
        [], {ADDR: True}, window, telemetry=tel, managed_addresses=set()
    )
    ghosts = [i for i in data.incidents if i.kind is IncidentKind.GHOST_SLOT]
    assert [i.address for i in ghosts] == [ADDR]


# --- the two routes an address takes into the storm window -----------------

def test_the_measured_and_heuristic_routes_agree_on_one_address():
    """One device, both routes, one bucket.

    The two addresses reach the window by entirely different paths -- the
    firmware string through `parse_counts`, the allocation list through
    `ReleaseTracker` -- and neither path is spelled the way the other is on
    the wire. If they canonicalised differently the window would open two
    buckets for one device, splitting a real storm into two counts below the
    threshold and reporting the measured half as unattributed.
    """
    window = FailureWindow(300.0, 3, clock=lambda: 0.0)
    # Heuristic route: habluetooth hands `allocated` through verbatim (see
    # adapter.current_proxy_slots, which normalises only `source`), so a
    # lower-case spelling is what ReleaseTracker has to absorb.
    tracker = ReleaseTracker()
    tracker.update([ADDR.lower()], lambda _a: True)
    for address in tracker.update([], lambda _a: False):
        window.record(address)
    # Measured route: compact lower-case hex off the firmware.
    deltas = CounterDeltas()
    deltas.update("p1", parse_counts("d0cf130ec92a:1"))
    tel = [ProxyTelemetry("p1", smp_failures=parse_counts("d0cf130ec92a:3"))]

    data = build_triage_data([], {}, window, telemetry=tel, counter_deltas=deltas)
    assert window.addresses() == [ADDR]
    storms = [i for i in data.incidents if i.kind is IncidentKind.STORM]
    assert len(storms) == 1
    assert storms[0].address == ADDR
    assert storms[0].evidence == "smp"


def test_a_measured_storm_stays_measured_while_the_window_holds_it():
    """The counter stops climbing long before the window drains.

    An SMP burst is over in seconds; the storm window holds it for minutes.
    Attribution read from this snapshot's *delta* would therefore be right for
    exactly one snapshot and then flip to `heuristic` with no sources, telling
    the user the firmware had stopped reporting a device it is still measuring
    -- and dropping the proxy name from the card and from diagnostics while the
    incident it belongs to is still open.

    The key is asserted too, but it no longer carries this test's weight:
    `Incident.key` ignores `sources` for `STORM`, so a storm's key is stable
    whatever happens to its attribution. What is pinned here is that the
    attribution itself survives as long as the evidence does.
    """
    window = FailureWindow(300.0, 2, clock=lambda: 0.0)
    deltas = CounterDeltas()
    deltas.update("p1", {ADDR: 0})
    tel = [ProxyTelemetry("p1", smp_failures={ADDR: 5}, bonds={ADDR})]

    first = build_triage_data([], {}, window, telemetry=tel, counter_deltas=deltas)
    # Same telemetry next poll: the counter has not moved, so there is no
    # delta -- the failures are still inside the window all the same.
    second = build_triage_data([], {}, window, telemetry=tel, counter_deltas=deltas)

    storm_a = next(i for i in first.incidents if i.kind is IncidentKind.STORM)
    storm_b = next(i for i in second.incidents if i.kind is IncidentKind.STORM)
    assert storm_b.evidence == "smp"
    assert storm_b.sources == ["p1"]
    assert storm_b.key == storm_a.key


def test_a_storm_does_not_re_alert_when_its_attribution_ages_out():
    """A storm that never stops must never notify twice.

    Attribution expires with the events it describes (that is the point of
    storing it in the window), but the storm itself does not: inferred
    failures can hold an address above threshold long after the measured ones
    that named a proxy have aged out. `sources` therefore empties underneath a
    standing incident -- so if `key` folded it in, the notification layer would
    see the fault resolve and a brand new one appear, for a device that has
    been failing continuously the whole time.
    """
    now = [0.0]
    window = FailureWindow(300.0, 2, clock=lambda: now[0])
    window.record(ADDR, source="p1")
    window.record(ADDR, source="p1")
    now[0] = 299.0
    window.record(ADDR)
    window.record(ADDR)

    measured = next(
        i
        for i in build_triage_data([], {}, window).incidents
        if i.kind is IncidentKind.STORM
    )
    now[0] = 301.0  # the measured events expire; the inferred ones do not
    inferred = next(
        i
        for i in build_triage_data([], {}, window).incidents
        if i.kind is IncidentKind.STORM
    )

    assert (measured.evidence, measured.sources) == ("smp", ["p1"])
    assert (inferred.evidence, inferred.sources) == ("heuristic", [])
    assert reconcile({measured.key}, [inferred]) == ([], [])


def test_one_address_failing_on_two_proxies_names_both():
    """Two proxies failing the same device is worse news, not a tie to break."""
    window = FailureWindow(300.0, 2, clock=lambda: 0.0)
    deltas = CounterDeltas()
    deltas.update("p1", {ADDR: 0})
    deltas.update("p2", {ADDR: 0})
    tel = [
        ProxyTelemetry("p1", smp_failures={ADDR: 2}),
        ProxyTelemetry("p2", smp_failures={ADDR: 2}),
    ]
    data = build_triage_data([], {}, window, telemetry=tel, counter_deltas=deltas)
    storm = next(i for i in data.incidents if i.kind is IncidentKind.STORM)
    assert storm.sources == ["p1", "p2"]


def test_a_storms_attribution_does_not_depend_on_the_order_proxies_are_polled():
    """`sources` is published, so an order-dependent answer churns.

    It reaches the `incidents` attribute of `binary_sensor.bluesight_incident`
    and the card renders it ("on {sources}") and folds it into the signature it
    redraws from, so two pollings of one unchanged fault must produce the same
    list. The key is compared as well for the kinds that still fold `sources`
    in -- a storm's does not, but the same window feeds them.
    """
    def _storm(order):
        window = FailureWindow(300.0, 2, clock=lambda: 0.0)
        deltas = CounterDeltas()
        deltas.update("p1", {ADDR: 0})
        deltas.update("p2", {ADDR: 0})
        tel = [ProxyTelemetry(src, smp_failures={ADDR: 2}) for src in order]
        data = build_triage_data(
            [], {}, window, telemetry=tel, counter_deltas=deltas
        )
        return next(
            i for i in data.incidents if i.kind is IncidentKind.STORM
        )

    first, second = _storm(["p1", "p2"]), _storm(["p2", "p1"])
    assert first.sources == second.sources == ["p1", "p2"]
    assert first.key == second.key


def test_the_snapshot_carries_the_telemetry_it_was_given():
    """Diagnostics and the card can only show what the snapshot holds."""
    tel = [ProxyTelemetry("p1", bonds={ADDR})]
    data = build_triage_data([], {}, _empty_window(), telemetry=tel)
    assert data.telemetry == tel
