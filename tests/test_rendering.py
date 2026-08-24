"""Tests for the pure catalogue renderer."""
from __future__ import annotations

from custom_components.bluesight.rendering import Catalogue, render

EN = {"incident.deadlock.detail": "Held on {count} proxies simultaneously"}
FR = {"incident.deadlock.detail": "Retenu sur {count} proxys simultanément"}


def test_renders_with_parameters():
    cat = Catalogue(primary=FR, fallback=EN)
    assert render("incident.deadlock.detail", {"count": "2"}, cat) == (
        "Retenu sur 2 proxys simultanément"
    )


def test_falls_back_to_english_for_a_missing_translation():
    """A half-translated catalogue must stay legible, not go blank."""
    cat = Catalogue(primary={}, fallback=EN)
    assert render("incident.deadlock.detail", {"count": "2"}, cat) == (
        "Held on 2 proxies simultaneously"
    )


def test_falls_back_to_the_key_when_nothing_matches():
    """Better a visible key than an empty badge: it is self-diagnosing."""
    cat = Catalogue(primary={}, fallback={})
    assert render("incident.mystery.detail", {}, cat) == "incident.mystery.detail"


def test_a_missing_parameter_leaves_the_placeholder_rather_than_raising():
    """Rendering runs inside the coordinator loop; it must never raise."""
    cat = Catalogue(primary=EN, fallback=EN)
    assert render("incident.deadlock.detail", {}, cat) == (
        "Held on {count} proxies simultaneously"
    )


def test_extra_parameters_are_ignored():
    cat = Catalogue(primary=EN, fallback=EN)
    out = render("incident.deadlock.detail", {"count": "2", "unused": "x"}, cat)
    assert out == "Held on 2 proxies simultaneously"


def test_plural_selects_the_matching_form():
    cat = Catalogue(
        primary={"card.incidents.one": "{n} incident", "card.incidents.other": "{n} incidents"},
        fallback={},
    )
    assert render("card.incidents", {"n": "1"}, cat, count=1) == "1 incident"
    assert render("card.incidents", {"n": "3"}, cat, count=3) == "3 incidents"


def test_catalogue_for_language_picks_the_base_language():
    """HA hands out tags like fr-CA; the catalogue is keyed by base language."""
    catalogues = {"fr": FR, "en": EN}
    cat = Catalogue.for_language("fr-CA", catalogues)
    assert cat.primary == FR
