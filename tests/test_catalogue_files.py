"""Drift guards for the shipped incident string catalogues.

The catalogues live under the directory the integration already serves over
HTTP, so the backend reads the same files the card will fetch: one source of
truth for both sides. That only holds if the languages stay in step, which is
what these tests pin.

Nothing here imports the integration -- the catalogues are data, and checking
them must not depend on Home Assistant being importable.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

#: Resolved from this file, not the working directory: the suite must pass
#: from anywhere.
LOCALE_DIR = (
    Path(__file__).resolve().parent.parent
    / "custom_components"
    / "bluesight"
    / "frontend"
    / "www"
    / "locale"
)

#: Mirrors ``rendering._PLACEHOLDER``, deliberately duplicated: this test is a
#: check on the files as data, so it should fail if the renderer's idea of a
#: placeholder ever drifts from the catalogue's.
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _load(language: str) -> dict[str, str]:
    path = LOCALE_DIR / f"incidents.{language}.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def english() -> dict[str, str]:
    return _load("en")


@pytest.fixture(scope="module")
def french() -> dict[str, str]:
    return _load("fr")


def test_every_english_key_is_translated(english, french):
    missing = sorted(set(english) - set(french))
    assert not missing, f"untranslated keys: {missing}"


def test_no_french_key_is_orphaned(english, french):
    """A key French has and English does not can never be reached.

    English is the fallback every language degrades to, so it is the key set;
    an extra French key is either a typo or a leftover from a rename.
    """
    orphans = sorted(set(french) - set(english))
    assert not orphans, f"French keys absent from English: {orphans}"


def test_placeholders_match_across_languages(english, french):
    """Same names, in any order.

    Reordering placeholders within a sentence is normal and often required in
    French; dropping one silently loses a proxy name or an address, and adding
    one renders a literal ``{foo}`` to the user.
    """
    mismatched = {
        key: (sorted(PLACEHOLDER.findall(value)), sorted(PLACEHOLDER.findall(french[key])))
        for key, value in english.items()
        if key in french
        and set(PLACEHOLDER.findall(value)) != set(PLACEHOLDER.findall(french[key]))
    }
    assert not mismatched, f"placeholder drift: {mismatched}"


@pytest.mark.parametrize("language", ["en", "fr"])
def test_every_plural_key_has_both_forms(language):
    """``.one`` without ``.other`` (or the reverse) is a half-done split.

    ``render`` falls back to the unsuffixed key when the form it wants is
    missing, and the unsuffixed key is exactly what a split removes -- so the
    missing half renders as a bare ``incident.storm.detail`` to the user.
    """
    catalogue = _load(language)
    stems = {
        key.rsplit(".", 1)[0]
        for key in catalogue
        if key.rsplit(".", 1)[1] in ("one", "other")
    }
    incomplete = sorted(
        stem
        for stem in stems
        if f"{stem}.one" not in catalogue or f"{stem}.other" not in catalogue
    )
    assert not incomplete, f"half-split plural keys in {language}: {incomplete}"


@pytest.mark.parametrize("language", ["en", "fr"])
def test_plural_forms_agree_on_placeholders(language):
    """The two forms of one key must interpolate the same names.

    ``test_placeholders_match_across_languages`` compares ``.one`` to ``.one``
    and ``.other`` to ``.other``, so a placeholder dropped from the singular in
    *every* language slips past it. This closes that: the singular is the form
    users see least often, which is precisely why it rots unnoticed.
    """
    catalogue = _load(language)
    stems = sorted(
        key.rsplit(".", 1)[0] for key in catalogue if key.endswith(".one")
    )
    mismatched = {
        stem: (
            sorted(PLACEHOLDER.findall(catalogue[f"{stem}.one"])),
            sorted(PLACEHOLDER.findall(catalogue[f"{stem}.other"])),
        )
        for stem in stems
        if f"{stem}.other" in catalogue
        and set(PLACEHOLDER.findall(catalogue[f"{stem}.one"]))
        != set(PLACEHOLDER.findall(catalogue[f"{stem}.other"]))
    }
    assert not mismatched, f"plural placeholder drift in {language}: {mismatched}"


@pytest.mark.parametrize("language", ["en", "fr"])
def test_no_value_is_blank(language):
    """A blank entry renders as the bare key, which is not a translation."""
    catalogue = _load(language)
    blank = sorted(key for key, value in catalogue.items() if not value.strip())
    assert not blank, f"blank values in {language}: {blank}"


@pytest.mark.parametrize("language", ["en", "fr"])
def test_the_catalogue_is_a_flat_map_of_strings(language):
    catalogue = _load(language)
    assert catalogue, f"{language} catalogue is empty"
    non_strings = sorted(
        key for key, value in catalogue.items() if not isinstance(value, str)
    )
    assert not non_strings, f"non-string values in {language}: {non_strings}"
