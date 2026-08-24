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

from dataclasses import dataclass, field


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
        return self.primary.get(key) or self.fallback.get(key)


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
    for name, value in (params or {}).items():
        template = template.replace("{" + name + "}", str(value))
    return template
