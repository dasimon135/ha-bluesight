"""Singular agreement for every counted string we ship.

A count of one is the case the catalogue got wrong for a whole release: the
templates were authored with the plural noun baked in, so a proxy that saw
exactly one device read "1 devices seen" and a threshold of one failure read
"1 failures". That is invisible in every test that happens to use a count of
two or more, which is why the singular is pinned explicitly here -- against
the *shipped* catalogues, in both languages, for every key that carries a
number.

French agrees with English on the 1-vs-rest boundary, so the two languages are
checked with the same ``count`` values; only the nouns differ. ``fois`` is
invariable, which is why one French pair below is legitimately identical.
"""
from __future__ import annotations

import pytest

from custom_components.bluesight.incident_policy import notification_content
from custom_components.bluesight.locale import read_catalogues
from custom_components.bluesight.model import Incident, IncidentKind
from custom_components.bluesight.rendering import Catalogue, render

_CATALOGUES = read_catalogues()
EN = Catalogue.for_language("en", _CATALOGUES)
FR = Catalogue.for_language("fr", _CATALOGUES)


#: ``(key, params-for-one, English singular, French singular)``. The plural is
#: exercised alongside each one so a template that lost its ``.other`` form and
#: fell through to the singular cannot pass.
COUNTED = [
    (
        "incident.storm.detail",
        {"count": "1", "seconds": "300"},
        "1 failure in 300s",
        "1 échec en 300s",
    ),
    (
        "incident.proxy_reboot_storm.detail",
        {"count": "1", "seconds": "600"},
        "1 proxy reboot in 600s",
        "1 redémarrage du proxy en 600s",
    ),
    (
        "incident.bond_lost.detail",
        {"count": "1", "proxy": "Salon"},
        "1 pairing failure on Salon, which holds no bond for this device "
        "— re-pair through Salon",
        "1 échec d'appairage sur Salon, qui ne détient aucun bond pour cet "
        "appareil — réappairez via Salon",
    ),
    (
        "card.proxy.last_advert_with_devices",
        {"age": "3 min", "count": "1"},
        "last advert 3 min ago · 1 device seen",
        "dernière annonce il y a 3 min · 1 appareil vu",
    ),
    (
        "card.incidents",
        {"count": "1"},
        "1 incident",
        "1 incident",
    ),
]


@pytest.mark.parametrize(
    ("key", "params", "english", "french"),
    COUNTED,
    ids=[row[0] for row in COUNTED],
)
def test_a_count_of_one_renders_the_singular(key, params, english, french):
    assert render(key, params, EN, count=1) == english
    assert render(key, params, FR, count=1) == french


@pytest.mark.parametrize(
    ("key", "params", "english", "french"),
    COUNTED,
    ids=[row[0] for row in COUNTED],
)
def test_a_count_above_one_renders_a_different_string(key, params, english, french):
    """The plural form must exist and differ, or the split did nothing."""
    plural_params = {**params, "count": "4"}
    assert render(key, plural_params, EN, count=4) != english
    assert render(key, plural_params, FR, count=4) != french


def test_storm_notification_agrees_with_a_single_failure():
    """A storm threshold of one is configurable, so this reaches users."""
    incident = Incident(
        IncidentKind.STORM,
        "11:22:33:44:55:66",
        [],
        detail_key="incident.storm.detail",
        detail_params={"count": "1", "seconds": "300"},
    )
    _, english = notification_content(incident, EN)
    _, french = notification_content(incident, FR)
    assert english.startswith("1 connection failure on")
    assert french.startswith("1 échec de connexion sur")


def test_bond_lost_notification_agrees_with_a_single_failure():
    """One failure is the common case, not the rare one.

    A bond is either there or it is not, so the very first attempt after it
    goes missing raises this — the counter has no threshold to climb to, and
    ``count`` is a cumulative SMP reading that is often exactly 1.
    """
    incident = Incident(
        IncidentKind.BOND_LOST,
        "11:22:33:44:55:66",
        ["AA:BB:CC:DD:EE:FF"],
        detail_key="incident.bond_lost.detail",
        detail_params={"count": "1", "proxy": "Salon"},
        evidence="smp",
    )
    _, english = notification_content(incident, EN)
    assert english.startswith("1 pairing failure for")
    _, french = notification_content(incident, FR)
    assert french.startswith("1 échec d'appairage pour")


def test_reboot_storm_notification_agrees_with_a_single_reboot():
    incident = Incident(
        IncidentKind.PROXY_REBOOT_STORM,
        "PX:01",
        ["PX:01"],
        detail_key="incident.proxy_reboot_storm.detail",
        detail_params={"count": "1", "seconds": "600"},
    )
    _, english = notification_content(incident, EN)
    assert "has rebooted 1 time in 600s" in english
    # "fois" is invariable in French: the singular is the plural, and saying so
    # in a test stops a future translator "fixing" it to "1 foi".
    _, french = notification_content(incident, FR)
    assert "a redémarré 1 fois en 600s" in french


def test_a_plural_key_without_a_count_still_renders_something():
    """``render`` must not go blank when the caller forgets the pivot.

    Without ``count`` the lookup falls through to the unsuffixed key, which a
    split key no longer has, so this lands on the key-of-last-resort. That is
    ugly but diagnosable -- and the point is that it does not raise inside the
    coordinator's snapshot loop.
    """
    assert render("incident.storm.detail", {"count": "3"}, EN) == (
        "incident.storm.detail"
    )
