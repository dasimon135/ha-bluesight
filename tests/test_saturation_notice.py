"""The "this proxy is often full" Repair.

A Repair, not an incident: nothing is pushed and the incident sensor stays
quiet, because a proxy that is full all day may be full on purpose -- and Home
Assistant lets the user ignore a Repair for good, which is the right answer
for that proxy and one only the user can give.
"""
from __future__ import annotations

from custom_components.bluesight import saturation_notice as module
from custom_components.bluesight.saturation_notice import (
    SaturationIssues,
    issue_id_for,
)

PROXY = "D0:CF:13:0E:D1:9E"
DAY = 86400.0


class _FakeIssueRegistry:
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
    return SaturationIssues(hass=object()), fake


def test_a_saturated_proxy_gets_an_unfixable_repair_named_as_the_user_names_it(
    monkeypatch,
):
    issues, fake = _issues(monkeypatch)
    issues.async_update({PROXY: (0.61, DAY)}, {PROXY: "Proxy Valentine"})
    issue = fake.created[issue_id_for(PROXY)]
    assert issue["domain"] == "bluesight"
    assert issue["is_fixable"] is False
    assert issue["translation_key"] == "proxy_saturated"
    assert issue["translation_placeholders"] == {
        "proxy": "Proxy Valentine",
        "source": PROXY,
        "percent": "61",
        "hours": "24",
    }
    assert issues.raised == {PROXY}


def test_the_percentage_never_reads_above_one_hundred(monkeypatch):
    issues, fake = _issues(monkeypatch)
    issues.async_update({PROXY: (1.02, DAY)}, {})
    placeholders = fake.created[issue_id_for(PROXY)]["translation_placeholders"]
    assert placeholders["percent"] == "100"
    assert placeholders["proxy"] == PROXY


def test_the_repair_clears_below_the_lower_line_and_not_before(monkeypatch):
    issues, fake = _issues(monkeypatch)
    issues.async_update({PROXY: (0.6, DAY)}, {})
    issues.async_update({PROXY: (0.4, DAY)}, {})
    assert fake.deleted == []
    issues.async_update({PROXY: (0.1, DAY)}, {})
    assert fake.deleted == [issue_id_for(PROXY)]
    assert issues.raised == set()


def test_a_proxy_that_goes_offline_drops_its_repair(monkeypatch):
    """The coordinator only hands in online proxies; a missing one is cleared."""
    issues, fake = _issues(monkeypatch)
    issues.async_update({PROXY: (0.9, DAY)}, {})
    issues.async_update({}, {})
    assert fake.deleted == [issue_id_for(PROXY)]


def test_unloading_takes_its_repairs_with_it(monkeypatch):
    issues, fake = _issues(monkeypatch)
    issues.async_update({PROXY: (0.9, DAY)}, {})
    issues.async_shutdown()
    assert fake.deleted == [issue_id_for(PROXY)]
    assert issues.raised == set()


def test_the_id_does_not_collide_with_the_retirement_repair():
    """Both are per proxy. The fix flow is reached only for fixable issues, but
    an id the retirement module could parse would still be one id for two
    Repairs."""
    from custom_components.bluesight.proxy_retirement import source_for_issue

    assert source_for_issue(issue_id_for(PROXY)) is None
