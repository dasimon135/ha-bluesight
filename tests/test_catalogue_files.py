"""Drift guards for the shipped incident string catalogues.

The catalogues live under the directory the integration already serves over
HTTP, so the backend reads the same files the card will fetch: one source of
truth for both sides. That only holds if the languages stay in step, which is
what these tests pin.

The languages are discovered from disk rather than listed here, so dropping an
``incidents.<lang>.json`` into the locale directory is genuinely all a
translator has to do: every guard below picks the new catalogue up, and each
one names its language in the test ID so a failure says which file is wrong.

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

#: English is the fallback every other language degrades to, key by key, so it
#: is the reference the rest are measured against rather than one more
#: catalogue in the list.
REFERENCE = "en"

#: Mirrors ``rendering._PLACEHOLDER``, deliberately duplicated: this test is a
#: check on the files as data, so it should fail if the renderer's idea of a
#: placeholder ever drifts from the catalogue's.
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _discover_languages() -> list[str]:
    """Every language with a catalogue on disk, reference included."""
    return sorted(path.name.split(".")[1] for path in LOCALE_DIR.glob("incidents.*.json"))


#: Every shipped language, and every shipped language *except* the reference.
#: Parametrising over these is what makes a third catalogue guarded the moment
#: it lands, with no edit to this file.
LANGUAGES = _discover_languages()
TRANSLATIONS = [language for language in LANGUAGES if language != REFERENCE]


def _load(language: str) -> dict[str, str]:
    path = LOCALE_DIR / f"incidents.{language}.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def english() -> dict[str, str]:
    return _load(REFERENCE)


def test_the_reference_catalogue_is_present_and_readable():
    """Nothing below is meaningful without English.

    The cross-language guards are parametrised over the *other* languages, so
    an English catalogue that went missing would leave them comparing against
    nothing -- and a guard that passes vacuously when its input disappears is
    worse than no guard. This is the one check that has to fail loudly on its
    own.
    """
    path = LOCALE_DIR / f"incidents.{REFERENCE}.json"
    assert path.is_file(), f"reference catalogue missing: {path}"
    catalogue = json.loads(path.read_text(encoding="utf-8"))
    assert catalogue, f"reference catalogue is empty: {path}"


def test_at_least_one_translation_is_shipped():
    """Guards the parametrisation itself, for the same reason as above."""
    assert TRANSLATIONS, f"no non-{REFERENCE} catalogues found under {LOCALE_DIR}"


@pytest.mark.parametrize("language", TRANSLATIONS)
def test_every_english_key_is_translated(language, english):
    missing = sorted(set(english) - set(_load(language)))
    assert not missing, f"untranslated keys in {language}: {missing}"


@pytest.mark.parametrize("language", TRANSLATIONS)
def test_no_translated_key_is_orphaned(language, english):
    """A key a translation has and English does not can never be reached.

    English is the fallback every language degrades to, so it is the key set;
    an extra key is either a typo or a leftover from a rename.
    """
    orphans = sorted(set(_load(language)) - set(english))
    assert not orphans, f"{language} keys absent from {REFERENCE}: {orphans}"


@pytest.mark.parametrize("language", TRANSLATIONS)
def test_placeholders_match_across_languages(language, english):
    """Same names, in any order.

    Reordering placeholders within a sentence is normal and often required in
    French; dropping one silently loses a proxy name or an address, and adding
    one renders a literal ``{foo}`` to the user.
    """
    catalogue = _load(language)
    mismatched = {
        key: (sorted(PLACEHOLDER.findall(value)), sorted(PLACEHOLDER.findall(catalogue[key])))
        for key, value in english.items()
        if key in catalogue
        and set(PLACEHOLDER.findall(value)) != set(PLACEHOLDER.findall(catalogue[key]))
    }
    assert not mismatched, f"placeholder drift in {language}: {mismatched}"


@pytest.mark.parametrize("language", LANGUAGES)
def test_every_plural_key_has_both_forms(language):
    """``.one`` without ``.other`` (or the reverse) is a half-done split.

    ``render`` falls back to the unsuffixed key when the form it wants is
    missing, and the unsuffixed key is exactly what a split removes -- so the
    missing half renders as a bare ``incident.storm.detail`` to the user.
    """
    catalogue = _load(language)
    stems = {
        key.rpartition(".")[0]
        for key in catalogue
        if key.rpartition(".")[2] in ("one", "other")
    }
    incomplete = sorted(
        stem
        for stem in stems
        if f"{stem}.one" not in catalogue or f"{stem}.other" not in catalogue
    )
    assert not incomplete, f"half-split plural keys in {language}: {incomplete}"


@pytest.mark.parametrize("language", LANGUAGES)
def test_plural_forms_agree_on_placeholders(language):
    """The two forms of one key must interpolate the same names.

    ``test_placeholders_match_across_languages`` compares ``.one`` to ``.one``
    and ``.other`` to ``.other``, so a placeholder dropped from the singular in
    *every* language slips past it. This closes that: the singular is the form
    users see least often, which is precisely why it rots unnoticed.
    """
    catalogue = _load(language)
    stems = sorted(
        key.rpartition(".")[0] for key in catalogue if key.endswith(".one")
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


@pytest.mark.parametrize("language", LANGUAGES)
def test_no_value_is_blank(language):
    """A blank entry renders as the bare key, which is not a translation."""
    catalogue = _load(language)
    blank = sorted(
        key for key, value in catalogue.items() if isinstance(value, str) and not value.strip()
    )
    assert not blank, f"blank values in {language}: {blank}"


@pytest.mark.parametrize("language", LANGUAGES)
def test_the_catalogue_is_a_flat_map_of_strings(language):
    catalogue = _load(language)
    assert catalogue, f"{language} catalogue is empty"
    non_strings = sorted(
        key for key, value in catalogue.items() if not isinstance(value, str)
    )
    assert not non_strings, f"non-string values in {language}: {non_strings}"
