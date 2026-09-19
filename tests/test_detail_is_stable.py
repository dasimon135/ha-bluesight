"""A published ``detail`` must not move while the fault it describes stands still.

``detail`` lands in the ``incidents`` attribute of the incident sensor, so a
number in it that changes every snapshot rewrites that attribute -- and a
recorder row, and the card's render signature -- for as long as the incident is
open. ``detect_offline_proxies`` has said so since it was written, and carries
no elapsed time for that reason; the stalled and idle-slot details did, and
were the two that ran longest.

They now state the threshold that was crossed, which is true for the whole life
of the incident. The measured value still travels in ``detail_params``, where
the notification -- written once, when the incident opens -- reads it.
"""
from __future__ import annotations

import pytest

from custom_components.bluesight.coordinator_data import build_triage_data
from custom_components.bluesight.incident_policy import notification_content
from custom_components.bluesight.locale import read_catalogues
from custom_components.bluesight.model import IncidentKind, ProxyHealth, ProxySlots
from custom_components.bluesight.rendering import Catalogue
from custom_components.bluesight.telemetry import ProxyTelemetry
from custom_components.bluesight.window import FailureWindow

_CATALOGUES = read_catalogues()
LANGUAGES = [Catalogue.for_language(lang, _CATALOGUES) for lang in ("en", "fr")]

PROXY = "D8:3B:DA:11:22:35"
ADDR = "D0:CF:13:0E:C9:2A"


def _window() -> FailureWindow:
    return FailureWindow(300.0, 5, clock=lambda: 0.0)


def _stalled(age: float, catalogue: Catalogue):
    data = build_triage_data(
        [],
        {},
        _window(),
        proxies_health=[ProxyHealth(PROXY, "Kitchen", True, True, age, 0)],
        stalled_threshold_s=180.0,
        catalogue=catalogue,
    )
    [incident] = [i for i in data.incidents if i.kind is IncidentKind.PROXY_STALLED]
    return incident


def _idle(idle_s: float, catalogue: Catalogue):
    data = build_triage_data(
        [ProxySlots(PROXY, "Kitchen", 3, 2, [ADDR])],
        {ADDR: True},
        _window(),
        telemetry=[ProxyTelemetry(PROXY, slot_idle_seconds={ADDR: idle_s})],
        idle_threshold_s=1800.0,
        catalogue=catalogue,
    )
    [incident] = [i for i in data.incidents if i.kind is IncidentKind.GHOST_SLOT]
    return incident


@pytest.mark.parametrize("catalogue", LANGUAGES)
def test_a_stalled_proxys_detail_does_not_move_as_the_silence_grows(catalogue):
    early, late = _stalled(200.0, catalogue), _stalled(7200.0, catalogue)
    assert early.detail == late.detail
    assert "180" in early.detail          # the threshold that was crossed


@pytest.mark.parametrize("catalogue", LANGUAGES)
def test_an_idle_slots_detail_does_not_move_as_the_silence_grows(catalogue):
    early, late = _idle(1900.0, catalogue), _idle(90000.0, catalogue)
    assert early.detail == late.detail
    assert "1800" in early.detail


@pytest.mark.parametrize("catalogue", LANGUAGES)
def test_the_notification_still_carries_what_was_measured(catalogue):
    """Written once, when the incident opens, so a live number costs nothing
    there -- and it is the number that makes the alert worth reading."""
    _title, message = notification_content(_stalled(213.0, catalogue), catalogue)
    assert "213" in message
    _title, message = notification_content(_idle(1900.0, catalogue), catalogue)
    assert "1900" in message
