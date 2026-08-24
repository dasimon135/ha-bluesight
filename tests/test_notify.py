"""Tests for the BlueSight notification layer.

The policy functions in ``incident_policy`` are HA-free and always run. The
``NotificationManager`` is exercised on any platform by injecting a fake
``hass`` and monkeypatching the module-level ``persistent_notification`` with a
recorder, so the full create/dismiss/resolve cycle is verified without a
running Home Assistant.

Wording is asserted against the *shipped* catalogues, read from disk exactly
as ``async_setup_entry`` reads them. A test written against an inline fake
catalogue would still pass if the real files were empty or malformed.
"""
from dataclasses import replace

import pytest

from custom_components.bluesight.incident_policy import (
    dedupe_incidents,
    notification_content,
    notification_id_for_key,
    reconcile,
)
from custom_components.bluesight.locale import read_catalogues
from custom_components.bluesight.model import Incident, IncidentKind
from custom_components.bluesight.rendering import Catalogue

_CATALOGUES = read_catalogues()
EN = Catalogue.for_language("en", _CATALOGUES)
FR = Catalogue.for_language("fr", _CATALOGUES)


def _deadlock(address: str, sources=("AA", "BB")) -> Incident:
    return Incident(kind=IncidentKind.DEADLOCK, address=address,
                    sources=list(sources))


def _ghost(address: str, sources=("AA",)) -> Incident:
    return Incident(kind=IncidentKind.GHOST_SLOT, address=address,
                    sources=list(sources))


def _storm(address: str, count="7", seconds="300", detail="") -> Incident:
    return Incident(
        kind=IncidentKind.STORM, address=address, detail=detail,
        detail_key="incident.storm.detail",
        detail_params={"count": count, "seconds": seconds},
    )


def _proxy_offline(source: str) -> Incident:
    return Incident(kind=IncidentKind.PROXY_OFFLINE, address=source,
                    sources=[source])


def _proxy_stalled(source: str, seconds="742", detail="") -> Incident:
    return Incident(
        kind=IncidentKind.PROXY_STALLED, address=source, sources=[source],
        detail=detail, detail_key="incident.proxy_stalled.detail",
        detail_params={"seconds": seconds},
    )


def _proxy_reboot_storm(
    source: str, count="4", seconds="600", detail=""
) -> Incident:
    return Incident(
        kind=IncidentKind.PROXY_REBOOT_STORM, address=source, sources=[source],
        detail=detail, detail_key="incident.proxy_reboot_storm.detail",
        detail_params={"count": count, "seconds": seconds},
    )


# --- dedupe_incidents / precedence ---------------------------------------

def test_dedupe_deadlock_supersedes_ghost_for_same_address():
    incidents = [_ghost("11:22"), _deadlock("11:22")]
    result = dedupe_incidents(incidents)
    assert [i.kind for i in result] == [IncidentKind.DEADLOCK]


def test_dedupe_keeps_lone_ghost_slot():
    incidents = [_ghost("11:22")]
    assert dedupe_incidents(incidents) == incidents


def test_dedupe_keeps_storm_alongside_deadlock():
    incidents = [_storm("11:22"), _deadlock("11:22")]
    result = dedupe_incidents(incidents)
    assert {i.kind for i in result} == {IncidentKind.STORM, IncidentKind.DEADLOCK}


def test_dedupe_ghost_kept_when_deadlock_is_a_different_address():
    incidents = [_deadlock("11:22"), _ghost("33:44")]
    assert dedupe_incidents(incidents) == incidents


def test_dedupe_precedence_uses_normalized_address():
    # Different casing must still collapse to one physical fault.
    incidents = [_ghost("aa:bb"), _deadlock("AA:BB")]
    result = dedupe_incidents(incidents)
    assert [i.kind for i in result] == [IncidentKind.DEADLOCK]


def test_dedupe_preserves_input_order():
    a = _storm("11:22")
    b = _deadlock("33:44")
    c = _ghost("55:66")
    assert dedupe_incidents([a, b, c]) == [a, b, c]


# --- dedupe_incidents / proxy precedence ---------------------------------

def test_dedupe_proxy_offline_supersedes_stalled_for_same_source():
    incidents = [_proxy_stalled("PX:01"), _proxy_offline("PX:01")]
    result = dedupe_incidents(incidents)
    assert [i.kind for i in result] == [IncidentKind.PROXY_OFFLINE]


def test_dedupe_proxy_offline_and_stalled_kept_for_different_sources():
    incidents = [_proxy_offline("PX:01"), _proxy_stalled("PX:02")]
    assert dedupe_incidents(incidents) == incidents


def test_dedupe_keeps_lone_proxy_stalled():
    incidents = [_proxy_stalled("PX:01")]
    assert dedupe_incidents(incidents) == incidents


def test_dedupe_proxy_reboot_storm_coexists_with_offline():
    incidents = [_proxy_offline("PX:01"), _proxy_reboot_storm("PX:01")]
    result = dedupe_incidents(incidents)
    assert {i.kind for i in result} == {
        IncidentKind.PROXY_OFFLINE, IncidentKind.PROXY_REBOOT_STORM
    }


def test_dedupe_proxy_precedence_uses_normalized_source():
    incidents = [_proxy_stalled("px:01"), _proxy_offline("PX:01")]
    result = dedupe_incidents(incidents)
    assert [i.kind for i in result] == [IncidentKind.PROXY_OFFLINE]


# --- reconcile ------------------------------------------------------------

def test_reconcile_new_incident_is_created():
    inc = _deadlock("11:22")
    to_create, to_dismiss = reconcile(set(), [inc])
    assert to_create == [inc]
    assert to_dismiss == []


def test_reconcile_unchanged_incident_not_recreated():
    inc = _deadlock("11:22")
    to_create, _ = reconcile({inc.key}, [inc])
    assert to_create == []


def test_reconcile_disappeared_incident_is_dismissed():
    inc = _deadlock("11:22")
    to_create, to_dismiss = reconcile({inc.key}, [])
    assert to_create == []
    assert to_dismiss == [inc.key]


def test_reconcile_empty_is_empty():
    assert reconcile(set(), []) == ([], [])


# --- notification_content -------------------------------------------------

def test_content_storm_is_actionable():
    title, message = notification_content(
        _storm("11:22", count="7", seconds="300"), EN
    )
    assert title == "BlueSight: pairing storm"
    assert "11:22" in message
    assert "7" in message and "300" in message
    assert "reconnect" in message.lower()


def test_content_deadlock_references_issue_and_sources():
    title, message = notification_content(_deadlock("11:22", sources=["AA", "BB"]), EN)
    assert title == "BlueSight: proxy slot deadlock"
    assert "11:22" in message
    assert "176516" in message
    assert "AA" in message and "BB" in message


def test_content_ghost_names_the_proxy():
    title, message = notification_content(_ghost("11:22", sources=["PROXY1"]), EN)
    assert title == "BlueSight: ghost slot"
    assert "11:22" in message
    assert "PROXY1" in message
    assert "restart" in message.lower()


def test_content_ghost_without_sources_does_not_crash():
    title, message = notification_content(
        Incident(kind=IncidentKind.GHOST_SLOT, address="11:22", sources=[]), EN
    )
    assert "11:22" in message


def test_content_ghost_prefers_the_friendly_name_over_the_source_mac():
    # The detail and the notification name the same proxy side by side; the
    # friendly name is what the user sees everywhere else in Home Assistant,
    # so it wins over the MAC in `sources`.
    incident = Incident(
        kind=IncidentKind.GHOST_SLOT,
        address="11:22",
        sources=["AA:BB:CC:DD:EE:FF"],
        detail_key="incident.ghost_slot.detail",
        detail_params={"proxy": "Proxy Cuisine"},
    )
    _, message = notification_content(incident, EN)
    assert "Proxy Cuisine" in message
    assert "AA:BB:CC:DD:EE:FF" not in message


def test_content_ghost_falls_back_to_the_source_when_no_friendly_name():
    _, message = notification_content(_ghost("11:22", sources=["PROXY1"]), EN)
    assert "PROXY1" in message


def test_content_ghost_without_a_proxy_names_the_unknown_proxy_in_english():
    _, message = notification_content(
        Incident(kind=IncidentKind.GHOST_SLOT, address="11:22", sources=[]), EN
    )
    assert "an unspecified proxy" in message


def test_content_ghost_without_a_proxy_names_the_unknown_proxy_in_french():
    # The one English literal that survived the extraction sweep: a French
    # user with a sourceless ghost slot must not be told "a proxy".
    _, message = notification_content(
        Incident(kind=IncidentKind.GHOST_SLOT, address="11:22", sources=[]), FR
    )
    assert "un proxy non identifié" in message
    assert "a proxy" not in message


def test_content_proxy_offline_names_source_and_is_actionable():
    title, message = notification_content(_proxy_offline("PX:01"), EN)
    assert title == "BlueSight: proxy offline"
    assert "PX:01" in message
    assert "offline" in message.lower()


def test_content_proxy_stalled_names_source_measurement_and_action():
    title, message = notification_content(_proxy_stalled("PX:01", seconds="742"), EN)
    assert title == "BlueSight: proxy stalled"
    assert "PX:01" in message
    assert "742" in message
    assert "power-cycle" in message.lower()


def test_content_proxy_reboot_storm_names_source_measurement_and_action():
    title, message = notification_content(
        _proxy_reboot_storm("PX:01", count="4", seconds="600"), EN
    )
    assert title == "BlueSight: proxy rebooting"
    assert "PX:01" in message
    assert "4" in message and "600" in message
    assert "power" in message.lower()


def test_content_is_rendered_in_home_assistants_language():
    # The whole point of the release: no wording is hard-coded in Python, so
    # the same incident notifies in French with nothing else changed.
    title, message = notification_content(
        _deadlock("11:22", sources=["AA", "BB"]), FR
    )
    assert title == "BlueSight : blocage de slot proxy"
    assert "occupe un slot de connexion sur 2 proxys" in message
    assert "11:22" in message
    assert "AA, BB" in message


def test_content_french_storm_carries_the_measured_numbers():
    title, message = notification_content(
        _storm("11:22", count="7", seconds="300"), FR
    )
    assert title == "BlueSight : tempête d'appairage"
    assert "7 échecs de connexion" in message
    assert "300s" in message


def test_content_unknown_kind_degrades_to_the_generic_wording():
    # A detector added without catalogue strings must still notify: this runs
    # inside the coordinator's update callback, where raising kills the
    # snapshot for every other incident too.
    unknown = Incident(kind="mystery", address="11:22")
    assert notification_content(unknown, EN) == (
        "BlueSight: incident",
        "An incident was detected on 11:22.",
    )
    assert notification_content(unknown, FR) == (
        "BlueSight : incident",
        "Un incident a été détecté sur 11:22.",
    )


def test_content_with_an_empty_catalogue_shows_the_key_not_a_blank():
    # Catalogues unreadable on disk: a visible key is diagnosable, a blank
    # notification is not.
    title, message = notification_content(_deadlock("11:22"), Catalogue())
    assert title == "notify.deadlock.title"
    assert message == "notify.deadlock.message"


#: One incident per kind, each with `detail` deliberately blank and its
#: structured parameters populated -- exactly the shape a caller gets from a
#: freshly detected incident, before `build_triage_data` has rendered
#: anything.
_UNRENDERED = [
    _storm("11:22", count="7", seconds="300"),
    _deadlock("11:22", sources=["AA", "BB"]),
    Incident(
        kind=IncidentKind.GHOST_SLOT, address="11:22", sources=["AA"],
        detail_key="incident.ghost_slot.detail",
        detail_params={"proxy": "Proxy Cuisine"},
    ),
    _proxy_offline("PX:01"),
    _proxy_stalled("PX:01", seconds="742"),
    _proxy_reboot_storm("PX:01", count="4", seconds="600"),
]


@pytest.mark.parametrize("catalogue", [EN, FR], ids=["en", "fr"])
@pytest.mark.parametrize(
    "incident", _UNRENDERED, ids=[i.kind.value for i in _UNRENDERED]
)
def test_content_needs_no_rendered_detail(incident, catalogue):
    """Notification text must not depend on `build_triage_data` running first.

    Nothing enforces that ordering, so a notification built straight off a
    freshly detected incident used to get an empty parenthetical.
    """
    assert incident.detail == ""
    title, message = notification_content(incident, catalogue)
    assert title and message
    assert "{" not in message, f"unresolved placeholder: {message}"
    assert "()" not in message, f"empty parenthetical: {message}"


@pytest.mark.parametrize("catalogue", [EN, FR], ids=["en", "fr"])
def test_content_carries_the_numbers_without_a_rendered_detail(catalogue):
    for incident, expected in (
        (_storm("11:22", count="7", seconds="300"), ("7", "300")),
        (_proxy_stalled("PX:01", seconds="742"), ("742",)),
        (_proxy_reboot_storm("PX:01", count="4", seconds="600"), ("4", "600")),
    ):
        _, message = notification_content(incident, catalogue)
        for number in expected:
            assert number in message, (incident.kind, number, message)


@pytest.mark.parametrize("catalogue", [EN, FR], ids=["en", "fr"])
@pytest.mark.parametrize(
    "incident", _UNRENDERED, ids=[i.kind.value for i in _UNRENDERED]
)
def test_content_never_interpolates_the_rendered_detail(incident, catalogue):
    """Mechanical proof that `notification_content` ignores `incident.detail`.

    A sentinel in the one field the function must not read: if any template
    ever reinstates `{detail}`, it lands in the output verbatim.
    """
    sentinel = "SENTINEL-DETAIL-MUST-NOT-BE-RENDERED"
    title, message = notification_content(
        replace(incident, detail=sentinel), catalogue
    )
    assert sentinel not in title
    assert sentinel not in message


# --- notification_id_for_key ---------------------------------------------

def test_notification_id_is_a_safe_slug():
    inc = _deadlock("11:22", sources=["AA", "BB"])
    nid = notification_id_for_key(inc.key)
    assert ":" not in nid
    assert "," not in nid
    assert nid.startswith("bluesight_")


def test_notification_id_matches_for_create_and_dismiss():
    # The create-time id and the dismiss-time id for the same key MUST match,
    # or the dismiss silently no-ops and the notification is orphaned.
    inc = _deadlock("11:22", sources=["AA", "BB"])
    assert notification_id_for_key(inc.key) == notification_id_for_key(inc.key)


def test_notification_id_distinct_for_distinct_keys():
    a = notification_id_for_key(_deadlock("11:22").key)
    b = notification_id_for_key(_deadlock("33:44").key)
    assert a != b


# --- NotificationManager create/dismiss/resolve cycle --------------------

class _FakePersistentNotification:
    """Records create/dismiss calls in place of the HA component."""

    def __init__(self) -> None:
        self.created: dict[str, tuple[str, str]] = {}
        self.dismissed: list[str] = []

    def async_create(self, hass, message, title=None, notification_id=None):
        self.created[notification_id] = (title, message)

    def async_dismiss(self, hass, notification_id):
        self.dismissed.append(notification_id)


def _manager(monkeypatch):
    from custom_components.bluesight import notify as notify_module

    fake = _FakePersistentNotification()
    monkeypatch.setattr(notify_module, "persistent_notification", fake)
    return notify_module.NotificationManager(hass=object(), catalogue=EN), fake


def test_manager_creates_notification_for_new_incident(monkeypatch):
    manager, fake = _manager(monkeypatch)
    inc = _deadlock("11:22")
    manager.async_update([inc])
    nid = notification_id_for_key(inc.key)
    assert nid in fake.created
    assert fake.created[nid][0] == "BlueSight: proxy slot deadlock"


def test_manager_does_not_recreate_stable_incident(monkeypatch):
    manager, fake = _manager(monkeypatch)
    inc = _deadlock("11:22")
    manager.async_update([inc])
    fake.created.clear()
    manager.async_update([inc])  # same incident, second update
    assert fake.created == {}   # not re-created
    assert fake.dismissed == []


def test_manager_dismisses_resolved_incident(monkeypatch):
    manager, fake = _manager(monkeypatch)
    inc = _deadlock("11:22")
    manager.async_update([inc])
    manager.async_update([])   # incident resolved
    assert fake.dismissed == [notification_id_for_key(inc.key)]


def test_manager_applies_precedence_before_notifying(monkeypatch):
    manager, fake = _manager(monkeypatch)
    # Ghost + deadlock for the same address -> only the deadlock notifies.
    manager.async_update([_ghost("11:22"), _deadlock("11:22")])
    titles = {t for t, _ in fake.created.values()}
    assert titles == {"BlueSight: proxy slot deadlock"}


def test_manager_shutdown_dismisses_all_active(monkeypatch):
    manager, fake = _manager(monkeypatch)
    inc_a = _deadlock("11:22")
    inc_b = _storm("33:44")
    manager.async_update([inc_a, inc_b])
    manager.async_shutdown()
    assert set(fake.dismissed) == {
        notification_id_for_key(inc_a.key),
        notification_id_for_key(inc_b.key),
    }


def test_manager_shutdown_is_idempotent(monkeypatch):
    manager, fake = _manager(monkeypatch)
    manager.async_update([_deadlock("11:22")])
    manager.async_shutdown()
    fake.dismissed.clear()
    manager.async_shutdown()   # nothing active now
    assert fake.dismissed == []
