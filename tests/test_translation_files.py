"""Drift guards for Home Assistant's own config/options-flow translations.

These are the ``strings.json`` + ``translations/<lang>.json`` files HA renders
the config and options dialogs from -- a **different** mechanism from the
incident catalogues under ``frontend/www/locale`` guarded by
``test_catalogue_files``. A key present in the schema but missing from a
language's file does not fail loudly: Home Assistant falls back to showing the
raw option key ("idle_threshold_s") as the field label, which looks like a bug
in the integration rather than a missing translation.

``strings.json`` is the source HA extracts from, and ``translations/en.json``
is the copy it actually serves for English, so the two must be byte-equal in
content. Every other language must carry the same key *structure*, with
different values.

Nothing here imports the integration -- these files are data, and checking them
must not depend on Home Assistant being importable, so the guard runs on every
dev machine and not only on CI.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

#: Resolved from this file, not the working directory: the suite must pass
#: from anywhere.
COMPONENT_DIR = (
    Path(__file__).resolve().parent.parent / "custom_components" / "bluesight"
)
TRANSLATIONS_DIR = COMPONENT_DIR / "translations"

#: English is the source language: ``strings.json`` is authored in it and
#: every other file is a translation of it.
REFERENCE = "en"


def _discover_languages() -> list[str]:
    """Every language with a translation file on disk, reference included."""
    return sorted(path.stem for path in TRANSLATIONS_DIR.glob("*.json"))


LANGUAGES = _discover_languages()
TRANSLATIONS = [language for language in LANGUAGES if language != REFERENCE]


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _key_paths(node, prefix: str = "") -> set[str]:
    """Every leaf path in a nested dict, as dotted strings."""
    if not isinstance(node, dict):
        return {prefix}
    return {
        path
        for key, value in node.items()
        for path in _key_paths(value, f"{prefix}.{key}" if prefix else key)
    }


def test_reference_translation_matches_strings_json():
    """``translations/en.json`` is the served copy of ``strings.json``."""
    assert _load(TRANSLATIONS_DIR / f"{REFERENCE}.json") == _load(
        COMPONENT_DIR / "strings.json"
    )


@pytest.mark.parametrize("language", TRANSLATIONS)
def test_translation_has_every_reference_key(language):
    """A missing key renders as the raw option key in the options dialog."""
    reference = _key_paths(_load(COMPONENT_DIR / "strings.json"))
    translated = _key_paths(_load(TRANSLATIONS_DIR / f"{language}.json"))
    assert reference - translated == set()


@pytest.mark.parametrize("language", TRANSLATIONS)
def test_translation_has_no_extra_keys(language):
    """An extra key is a typo or a stale entry: HA would never read it."""
    reference = _key_paths(_load(COMPONENT_DIR / "strings.json"))
    translated = _key_paths(_load(TRANSLATIONS_DIR / f"{language}.json"))
    assert translated - reference == set()


@pytest.mark.parametrize("language", LANGUAGES)
def test_every_option_field_has_a_label_and_a_description(language):
    """Both halves must be present: HA shows the label above the field and the
    description under it, and each falls back to the raw key on its own."""
    init = _load(TRANSLATIONS_DIR / f"{language}.json")["options"]["step"]["init"]
    assert set(init["data"]) == set(init["data_description"])


@pytest.mark.parametrize("language", LANGUAGES)
def test_no_option_string_is_empty(language):
    """An empty string is worse than a missing one: it renders as a blank
    label with no hint that a translation is owed."""
    init = _load(TRANSLATIONS_DIR / f"{language}.json")["options"]["step"]["init"]
    for section in ("data", "data_description"):
        for key, value in init[section].items():
            assert value.strip(), f"{language}: empty {section}.{key}"
