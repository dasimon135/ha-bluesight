"""The joint between the Lovelace card and the shipped string catalogues.

The card is vanilla JS with no build step and no JS test harness, so nothing
in CI executes it. What CI *can* do is check the two static promises the card
makes about the catalogue, both of which rot silently:

* every key it asks for exists,
* its embedded last-resort English is still a copy of the shipped English, and
* the catalogue it fetches carries a cache-busting query, whose value is the
  real release version.

All three failures look like working code in review. A renamed key renders as a
literal ``card.proxy.offline`` in the badge; a stale embedded copy is invisible
until the day the catalogue fetch fails, which is exactly the day it matters;
and a catalogue served from a month-old browser cache answers this release's
keys with last release's file.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from custom_components.bluesight.model import IncidentKind

COMPONENT_DIR = (
    Path(__file__).resolve().parent.parent / "custom_components" / "bluesight"
)
WWW_DIR = COMPONENT_DIR / "frontend" / "www"
MANIFEST = COMPONENT_DIR / "manifest.json"
CARD = WWW_DIR / "bluesight-card.js"
LOCALE_DIR = WWW_DIR / "locale"

#: ``_t("key"`` and ``_lookup("key"`` with a literal key. The dynamic
#: ``card.kind.${kind}`` lookup is covered by
#: :func:`test_every_incident_kind_has_a_card_label` instead.
ASKED_FOR = re.compile(r'_(?:t|lookup)\(\s*"([\w.]+)"')


@pytest.fixture(scope="module")
def card_source() -> str:
    return CARD.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def english() -> dict[str, str]:
    return json.loads(
        (LOCALE_DIR / "incidents.en.json").read_text(encoding="utf-8")
    )


@pytest.fixture(scope="module")
def embedded(card_source: str) -> dict[str, str]:
    """The card's ``EMBEDDED_EN`` map, parsed as JSON.

    It is authored as a JSON-compatible object literal precisely so this test
    can read it without a JS parser; the only concession to JS is the trailing
    comma, which is stripped here.
    """
    start = card_source.index("const EMBEDDED_EN = {")
    body_start = card_source.index("{", start)
    end = card_source.index("\n};", body_start)
    literal = card_source[body_start:end + 2]
    literal = re.sub(r",(\s*})", r"\1", literal)
    return json.loads(literal)


def test_embedded_english_matches_the_shipped_catalogue(embedded, english):
    """The embedded map is a copy, not a second opinion.

    It is what a viewer sees when the catalogue is unreachable, so a drifted
    copy means the card silently renders last release's wording in exactly the
    situation where nobody is watching.
    """
    shipped = {k: v for k, v in english.items() if k.startswith("card.")}
    assert embedded == shipped


def test_every_key_the_card_asks_for_exists(card_source, english):
    """Including through the plural split: a counted key has no bare entry."""

    def resolvable(key: str) -> bool:
        return key in english or (
            f"{key}.one" in english and f"{key}.other" in english
        )

    asked = sorted(set(ASKED_FOR.findall(card_source)))
    assert asked, "the scanner found no keys -- it has drifted from the card"
    missing = [key for key in asked if not resolvable(key)]
    assert not missing, f"card asks for keys the catalogue lacks: {missing}"


def test_every_incident_kind_has_a_card_label(english):
    """``card.kind.${kind}`` is built from the backend's kind value.

    A detector added without its label falls back to ``card.kind.unknown``,
    which is legible but tells the user nothing -- so the fallback exists for
    version skew, not as a licence to skip the string.
    """
    missing = sorted(
        kind.value
        for kind in IncidentKind
        if f"card.kind.{kind.value}" not in english
    )
    assert not missing, f"incident kinds with no card label: {missing}"
    assert "card.kind.unknown" in english


def test_the_kind_label_is_not_title_cased_by_css(card_source):
    """``text-transform: capitalize`` mangles French.

    It existed because the card rendered ``kind.replace(/_/g, " ")`` in
    lowercase and needed the capitals back. English survives title-casing;
    "blocage de slot" becomes "Blocage De Slot". The catalogue supplies the
    casing now, so the rule must stay gone.
    """
    assert "text-transform: capitalize" not in card_source


def test_the_custom_element_is_still_defined_only_once(card_source):
    """An upgraded install can carry a stale `/local/` copy of the resource.

    A second `define()` for the same name throws, and that exception breaks the
    whole Lovelace view rather than just this card.
    """
    assert 'if (!customElements.get("bluesight-card"))' in card_source
    assert card_source.count("customElements.define(") == 1



def test_the_catalogue_fetch_is_cache_busted(card_source):
    """The locale URL must carry the version, not just the language.

    ``StaticPathConfig(URL_BASE, CARD_DIR, True)`` serves this directory with
    ``Cache-Control: public, max-age=2678400``. The card module escapes that
    through the ``?v=`` on its Lovelace resource URL; the catalogue is fetched
    by the card itself and has only what is written here.
    """
    match = re.search(
        r"const localeUrl = \(language\) =>\s*`([^`]+)`", card_source
    )
    assert match, "localeUrl is gone or no longer a template literal"
    url = match.group(1)
    assert "${language}" in url
    assert "?v=${CARD_VERSION}" in url


def test_the_card_version_matches_the_manifest(card_source):
    """The card's version constant is the integration's, or it is a lie.

    The card is plain JS served as-is, so there is no build step to stamp the
    real version into it; the constant is hand-maintained, and it had already
    been wrong for two releases when nothing was reading it. It is no longer
    only a console banner -- it is the catalogue's cache-buster -- so a stale
    value means the fetch URL does not change on upgrade and the month-long
    cache is never busted at all.
    """
    version = json.loads(MANIFEST.read_text(encoding="utf-8"))["version"]
    match = re.search(r'const CARD_VERSION = "([^"]+)"', card_source)
    assert match, "CARD_VERSION is gone or no longer a literal"
    assert match.group(1) == version, (
        f"card says {match.group(1)}, manifest says {version}"
    )


# --- the connected-device list ----------------------------------------------


def _code_only(source: str) -> str:
    """``source`` with its comment lines dropped.

    The card documents what it deliberately does *not* do, naming the API it
    avoids; a plain substring search would read that prose as the very thing it
    forbids.
    """
    return "\n".join(
        line
        for line in source.splitlines()
        if not line.lstrip().startswith(("//", "/*", "*"))
    )


def _method_body(source: str, name: str) -> str:
    """The source of one method, from its declaration to the next one.

    Crude on purpose: the point is to assert that a particular string appears
    in a particular method, which a whole-file grep cannot do.
    """
    start = source.index(f"  {name}(")
    end = source.index("\n  }\n", start)
    return source[start:end]


def test_the_card_draws_the_backends_resolved_names(card_source):
    """The names are resolved in Python, in `coordinator._build_device_index`,
    which settles which registry evidence may speak for a BLE address. The card
    reads the published `allocated_devices` and must not re-derive any of it
    from `hass.devices`.
    """
    body = _method_body(card_source, "_renderProxyTile")
    assert "allocated_devices" in body
    assert "hass.devices" not in _code_only(card_source)


def test_an_unresolved_address_is_marked_in_the_viewers_language(card_source):
    """An address Home Assistant knows nothing about, holding a slot on a
    saturated proxy, is the case this feature exists for -- so it gets a word,
    not a bare MAC that reads as a rendering defect.
    """
    assert '_t("card.proxy.unknown_device")' in card_source


def test_the_connected_devices_are_part_of_the_render_signature(card_source):
    """The card skips a rebuild when its signature is unchanged.

    `used/total` can stay put while *which* devices hold the slots changes --
    one device disconnects as another connects, or a device is renamed. Left
    out of the signature, the list silently shows the previous fleet.
    """
    assert "allocated_devices" in _method_body(card_source, "_computeSignature")
