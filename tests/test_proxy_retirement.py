"""A Repair that offers to retire a proxy which has stayed offline.

`proxy_offline` has two causes with opposite remedies -- the proxy is down, or
it is gone for good -- and only the user knows which. The notification covers
the first. This covers the second: once a proxy has been offline for an hour, a
fixable Repair offers to stop tracking it and delete its device, which until now
meant finding its MAC and calling `bluesight.forget_proxy` by hand.

An hour, and not the 90 s offline grace: an OTA update, a router reboot or a
power cut must not put a "did you retire this?" question in front of anyone.
"""
from __future__ import annotations

from custom_components.bluesight import proxy_retirement as module
from custom_components.bluesight.incident_policy import retirement_candidates
from custom_components.bluesight.model import ProxyHealth
from custom_components.bluesight.proxy_retirement import (
    RETIRE_AFTER_S,
    RetirementIssues,
    issue_id_for,
    source_for_issue,
)

PROXY = "D8:3B:DA:11:22:35"
OTHER = "D0:CF:13:0F:05:5A"


def _health(source, *, online, age, name="Kitchen"):
    return ProxyHealth(source, name, True, online, age, 0)


# --- who is a candidate -----------------------------------------------------


def test_a_proxy_offline_for_long_enough_is_a_candidate():
    health = [_health(PROXY, online=False, age=1.0)]
    offline = {PROXY: RETIRE_AFTER_S}
    assert retirement_candidates(health, offline, RETIRE_AFTER_S) == health


def test_a_proxy_that_just_dropped_off_is_not():
    health = [_health(PROXY, online=False, age=1.0)]
    offline = {PROXY: RETIRE_AFTER_S - 1}
    assert retirement_candidates(health, offline, RETIRE_AFTER_S) == []


def test_the_clock_is_the_one_that_survives_a_restart():
    """`seconds_since_detection` is measured from this run's start, and a
    restart puts it back to nearly zero. The Repair asks whether a proxy is
    gone for good, and that question does not restart when Home Assistant
    does, so it reads the wall-clock map instead."""
    health = [_health(PROXY, online=False, age=2.0)]   # this run just began
    offline = {PROXY: RETIRE_AFTER_S * 24}             # gone since yesterday
    assert retirement_candidates(health, offline, RETIRE_AFTER_S) == health


def test_a_proxy_nothing_is_known_about_is_not_a_candidate():
    health = [_health(PROXY, online=False, age=RETIRE_AFTER_S * 10)]
    assert retirement_candidates(health, {}, RETIRE_AFTER_S) == []


def test_an_online_proxy_never_is_however_long_it_has_been_deaf():
    """`seconds_since_detection` is advertisement silence for an online proxy;
    that is PROXY_STALLED's business, and its remedy is a power cycle."""
    health = [_health(PROXY, online=True, age=RETIRE_AFTER_S * 10)]
    offline = {PROXY: RETIRE_AFTER_S * 10}
    assert retirement_candidates(health, offline, RETIRE_AFTER_S) == []


def test_the_issue_id_round_trips_to_the_source():
    assert source_for_issue(issue_id_for(PROXY)) == PROXY
    assert source_for_issue("something_else") is None


# --- the issues come and go -------------------------------------------------


class _FakeIssueRegistry:
    """Stands in for ``homeassistant.helpers.issue_registry``."""

    class IssueSeverity:
        WARNING = "warning"

    def __init__(self) -> None:
        self.created: dict[str, dict] = {}
        self.deleted: list[str] = []

    def async_create_issue(self, hass, domain, issue_id, **kwargs):
        self.created[issue_id] = {"domain": domain, **kwargs}

    def async_delete_issue(self, hass, domain, issue_id):
        self.deleted.append(issue_id)


def _issues(monkeypatch):
    fake = _FakeIssueRegistry()
    monkeypatch.setattr(module, "ir", fake)
    return RetirementIssues(hass=object()), fake


def test_a_candidate_gets_a_fixable_issue_named_after_the_proxy(monkeypatch):
    issues, fake = _issues(monkeypatch)
    issues.async_update(
        [_health(PROXY, online=False, age=1.0)], {}, {PROXY: RETIRE_AFTER_S}
    )
    issue = fake.created[issue_id_for(PROXY)]
    assert issue["domain"] == "bluesight"
    assert issue["is_fixable"] is True
    assert issue["translation_key"] == "proxy_retired"
    assert issue["translation_placeholders"] == {"proxy": "Kitchen", "source": PROXY}
    # The fix flow is handed what it needs, not asked to look it up again.
    assert issue["data"] == {"source": PROXY, "proxy": "Kitchen"}


def test_a_standing_issue_is_not_recreated_every_snapshot(monkeypatch):
    issues, fake = _issues(monkeypatch)
    health = [_health(PROXY, online=False, age=1.0)]
    issues.async_update(health, {}, {PROXY: RETIRE_AFTER_S})
    fake.created.clear()
    issues.async_update(
        [_health(PROXY, online=False, age=1.0)], {}, {PROXY: RETIRE_AFTER_S + 30}
    )
    assert fake.created == {}


def test_the_issue_goes_when_the_proxy_comes_back_or_is_forgotten(monkeypatch):
    issues, fake = _issues(monkeypatch)
    issues.async_update(
        [_health(PROXY, online=False, age=1.0)], {}, {PROXY: RETIRE_AFTER_S}
    )
    issues.async_update([_health(PROXY, online=True, age=1.0)], {}, {})
    assert fake.deleted == [issue_id_for(PROXY)]
    issues.async_update([], {}, {})              # forgotten: nothing left to say
    assert fake.deleted == [issue_id_for(PROXY)]  # and nothing deleted twice


def test_unloading_takes_its_issues_with_it(monkeypatch):
    issues, fake = _issues(monkeypatch)
    issues.async_update(
        [
            _health(PROXY, online=False, age=1.0),
            _health(OTHER, online=False, age=1.0),
        ],
        {},
        {PROXY: RETIRE_AFTER_S, OTHER: RETIRE_AFTER_S},
    )
    issues.async_shutdown()
    assert sorted(fake.deleted) == sorted([issue_id_for(PROXY), issue_id_for(OTHER)])


def test_the_repair_calls_the_proxy_what_the_user_calls_it(monkeypatch):
    """The scanner name is a node name with a MAC glued on; the card, the
    sensors and the incident text all use the name the user gave the proxy,
    and a Repair that named it differently would read as a different proxy."""
    issues, fake = _issues(monkeypatch)
    issues.async_update(
        [_health(PROXY, online=False, age=1.0, name=f"atom ({PROXY})")],
        {PROXY: "Proxy Buanderie"},
        {PROXY: RETIRE_AFTER_S},
    )
    issue = fake.created[issue_id_for(PROXY)]
    assert issue["translation_placeholders"]["proxy"] == "Proxy Buanderie"
    assert issue["data"]["proxy"] == "Proxy Buanderie"

