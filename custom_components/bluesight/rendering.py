"""Pure rendering of catalogued, parameterised strings.

No Home Assistant dependency; unit-testable with plain pytest.

Detectors emit a key and parameters instead of prose so the same incident can
be rendered in Home Assistant's language on the backend and in the viewer's
language in the card, from one catalogue.

Nothing here raises. Rendering runs inside the coordinator's snapshot loop, and
a missing key or parameter must degrade to something legible rather than take
the snapshot down.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

#: One ``{placeholder}`` in a catalogue template. Names are authored by us, so
#: the character class is deliberately narrow: anything else stays literal.
_PLACEHOLDER = re.compile(r"\{(\w+)\}")


@dataclass(frozen=True, slots=True)
class Catalogue:
    """A requested language plus the English safety net behind it."""

    primary: dict[str, str] = field(default_factory=dict)
    fallback: dict[str, str] = field(default_factory=dict)

    @classmethod
    def for_language(
        cls, language: str | None, catalogues: dict[str, dict[str, str]]
    ) -> Catalogue:
        """Pick the catalogue for ``language``, falling back to English.

        Home Assistant hands out tags like ``fr-CA`` and ``pt-BR``; catalogues
        are keyed by base language, so the region is dropped.
        """
        english = catalogues.get("en", {})
        if not language:
            return cls(primary=english, fallback=english)
        base = language.replace("_", "-").split("-")[0].lower()
        return cls(primary=catalogues.get(base, english), fallback=english)

    def lookup(self, key: str) -> str | None:
        """Return the first usable string for ``key``, or ``None``.

        A blank value counts as missing at *both* levels. An entry a
        translator left empty is an untranslated one, not a translation to
        nothing, so it must fall through to English — and an empty English
        entry must fall through to :func:`render`'s key-of-last-resort rather
        than surface as an empty badge.
        """
        return self.primary.get(key) or self.fallback.get(key) or None


def plural_count(params: dict[str, str] | None, name: str = "count") -> int | None:
    """The grammatical count for :func:`render`, read off the parameters.

    The plural pivot is deliberately taken from the very parameter the
    template substitutes rather than carried alongside it. A second copy could
    drift from the number the user actually reads, and the drift is invisible
    in review -- it only shows up as "1 failures" in production. Reading the
    substituted value makes the two agree by construction.

    A parameter that is absent or is not an integer selects no form, so
    rendering falls back to the unsuffixed key: a template with no plural
    forms is unaffected by this being called for every key.
    """
    if not params:
        return None
    try:
        return int(params[name])
    except (KeyError, TypeError, ValueError):
        return None


def render(
    key: str,
    params: dict[str, str] | None,
    catalogue: Catalogue,
    *,
    count: int | None = None,
) -> str:
    """Render ``key`` against ``catalogue``.

    ``count`` selects a plural form: the key is tried as ``<key>.one`` or
    ``<key>.other`` first. English and French agree on the boundary (1 vs the
    rest); a language that does not can add its own forms later without
    changing callers.
    """
    template: str | None = None
    if count is not None:
        suffix = "one" if abs(count) == 1 else "other"
        template = catalogue.lookup(f"{key}.{suffix}")
    if template is None:
        template = catalogue.lookup(key)
    if template is None:
        return key  # self-diagnosing: a visible key beats an empty badge
    values = params or {}

    # One pass over the template, so every placeholder is resolved exactly
    # once against the original text and a substituted value is never
    # rescanned. Parameters carry user-controlled proxy and device names: a
    # name containing "{count}" must stay literal instead of being replaced in
    # turn, and the result must not depend on the order of ``params``. An
    # unknown name keeps its placeholder — visible, but legible.
    def _substitute(match: re.Match[str]) -> str:
        name = match.group(1)
        return str(values[name]) if name in values else match.group(0)

    return _PLACEHOLDER.sub(_substitute, template)
