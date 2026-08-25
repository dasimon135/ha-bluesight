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
from custom_components.bluesight.locale import read_catalogues
from custom_components.bluesight.model import (
    Incident,
    IncidentKind,
    ProxyHealth,
    ProxySlots,
)
from custom_components.bluesight.rendering import Catalogue
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
    data = build_triage_data(
        [ProxySlots("AA", "proxy-a", 3, 2, ["33:44"])],
        {"33:44": False},
        storm,
        proxies_health=[ProxyHealth("BB", "Salon", True, True, 300.0, 0)],
        known_sources={"BB", "DD"},
        reboot_window=reboots,
        stalled_threshold_s=180.0,
        catalogue=FR,
    )
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
