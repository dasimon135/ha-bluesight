# BlueSight 0.5.0 — Internationalisation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make every user-facing string BlueSight produces — incident details, persistent notifications, and the Lovelace card — available in the user's language, starting with English and French.

**Architecture:** Detectors stop authoring prose and emit a translation key plus parameters. A single catalogue file per language, shipped inside the integration and served over the static path 0.4.0 already registers, is the one source of truth for both sides: the backend renders `detail` and notification text in Home Assistant's configured language, the card fetches the same catalogue and renders its own chrome in the *viewer's* language.

**Tech Stack:** Python 3.14, Home Assistant 2026.8, vanilla JS (no build step), pytest.

---

## Why this comes before v1.5

v1.5 adds two incident kinds and a batch of new notification wording. Writing that prose in English first and migrating it a week later is wasted work, so the structure lands first and v1.5 writes its strings already keyed.

## The compatibility constraint that shapes everything

`binary_sensor.bluesight_incident` publishes an `incidents` attribute list, and real automations consume it. A live example from the maintainer's own install:

```jinja
{% for i in state_attr('binary_sensor.bluesight_incident','incidents') or [] %}
• {{ i.kind }} — {{ i.address }}{% if i.detail %} ({{ i.detail }}){% endif %}
{% endfor %}
```

**`detail` is therefore a published contract.** It must stay, and it must stay a human-readable string. What changes is that it becomes *rendered* rather than *authored* — so this automation starts producing French with no edit, which is the point.

`kind` is likewise consumed and must keep its current machine values (`deadlock`, `ghost_slot`, …). Translate the *label*, never the value.

## Design

- `Incident` gains `detail_key: str` and `detail_params: dict[str, str]`. `detail` remains, populated by rendering.
- Catalogue files live at `custom_components/bluesight/frontend/www/locale/incidents.<lang>.json`. That directory is already served at `/bluesight/locale/…` by the static path registered in 0.4.0, so the card reaches the same file the backend reads. No second registration, no second copy.
- `rendering.py` is pure: key + params + catalogue → string. No HA, no I/O, unit-tested on Windows.
- The backend renders in `hass.config.language` (one language per installation). The card renders its own chrome in `hass.language` (**per user profile** — two people can view the same dashboard in different languages).
- Fallbacks cascade: requested language → English → the key itself. A missing translation must degrade to something legible, never to a blank badge.

---

## Task 1: `rendering.py` — the pure renderer

**Files:**
- Create: `custom_components/bluesight/rendering.py`
- Test: `tests/test_rendering.py`

**Step 1: Write the failing tests**

```python
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
```

**Step 2: Run to verify they fail**

Run: `py -3.14 -m pytest tests/test_rendering.py -v`
Expected: FAIL — module does not exist

**Step 3: Write the implementation**

```python
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
```

**Step 4: Run to verify they pass**

Run: `py -3.14 -m pytest tests/test_rendering.py -v`
Expected: PASS (7 tests)

**Step 5: Commit**

```bash
git add custom_components/bluesight/rendering.py tests/test_rendering.py
git commit -m "feat: pure catalogue renderer with language and plural fallbacks"
```

---

## Task 2: the catalogue files

**Files:**
- Create: `custom_components/bluesight/frontend/www/locale/incidents.en.json`
- Create: `custom_components/bluesight/frontend/www/locale/incidents.fr.json`
- Test: `tests/test_catalogue_files.py`

Flat key→string maps. Three namespaces: `incident.*` (details), `notify.*` (titles and bodies), `card.*` (chrome and kind labels).

Extract every current string verbatim into `incidents.en.json` — this task must not reword anything, so any behaviour change later is visible in isolation. Then translate into French.

Keys to create, one per existing string:

```
incident.deadlock.detail          incident.ghost_slot.detail
incident.storm.detail             incident.proxy_offline.detail
incident.proxy_stalled.detail     incident.proxy_reboot_storm.detail
notify.deadlock.title             notify.deadlock.message
notify.ghost_slot.title           notify.ghost_slot.message
notify.storm.title                notify.storm.message
notify.proxy_offline.title        notify.proxy_offline.message
notify.proxy_stalled.title        notify.proxy_stalled.message
notify.proxy_reboot_storm.title   notify.proxy_reboot_storm.message
card.kind.deadlock                card.kind.ghost_slot
card.kind.storm                   card.kind.proxy_offline
card.kind.proxy_stalled           card.kind.proxy_reboot_storm
card.offline                      card.missing
card.no_incidents                 card.scan_only
card.incidents.one                card.incidents.other
card.last_advert                  card.last_advert_with_devices
card.empty                        card.incident_no_detail
card.incident_sensor_missing      card.sources
card.age.seconds  card.age.minutes  card.age.hours  card.age.days
```

**Test — this is the one that stops the two files drifting:**

```python
"""The catalogues must stay in step with each other."""
from __future__ import annotations

import json
from pathlib import Path

LOCALE = (
    Path(__file__).resolve().parents[1]
    / "custom_components" / "bluesight" / "frontend" / "www" / "locale"
)


def _load(name):
    return json.loads((LOCALE / name).read_text(encoding="utf-8"))


def test_french_covers_every_english_key():
    """A key added to English and forgotten in French silently ships English."""
    en, fr = _load("incidents.en.json"), _load("incidents.fr.json")
    assert set(en) - set(fr) == set()


def test_french_has_no_keys_english_lacks():
    en, fr = _load("incidents.en.json"), _load("incidents.fr.json")
    assert set(fr) - set(en) == set()


def test_placeholders_match_between_languages():
    """A translated string that drops {count} renders a lie, not a typo."""
    import re

    en, fr = _load("incidents.en.json"), _load("incidents.fr.json")
    for key, text in en.items():
        assert set(re.findall(r"\{(\w+)\}", text)) == set(
            re.findall(r"\{(\w+)\}", fr[key])
        ), key


def test_no_value_is_empty():
    for name in ("incidents.en.json", "incidents.fr.json"):
        assert all(v.strip() for v in _load(name).values()), name
```

**Commit**

```bash
git add custom_components/bluesight/frontend/www/locale tests/test_catalogue_files.py
git commit -m "feat: English and French incident string catalogues"
```

---

## Task 3: `Incident` carries key + params

**Files:** Modify `model.py`; Test `tests/test_model.py`

Add two fields after `detail`, leaving `detail` and `key` untouched:

```python
    #: Translation key and parameters for `detail`. `detail` itself stays a
    #: rendered human string: it is published in the incident attribute and
    #: real automations format notifications from it, so it is a contract.
    detail_key: str = ""
    detail_params: dict[str, str] = field(default_factory=dict)
```

Tests: `detail_key` defaults empty; `key` is unchanged by either new field (same argument as `evidence` in the v1.5 plan — one fault, one incident).

Note `dict` in a `slots=True` frozen dataclass needs `field(default_factory=dict)`, which the class already uses for `sources`.

```bash
git commit -am "feat: incidents carry a translation key and parameters"
```

---

## Task 4: detectors emit keys, not prose

**Files:** Modify `detector.py`; Test the six `tests/test_detector_*.py`

For each of the six detectors, replace the f-string `detail=` with `detail_key=` plus `detail_params=`, and leave `detail` **unset** — the coordinator fills it in Task 6.

Worked example for `detect_deadlocks`:

```python
        Incident(
            IncidentKind.DEADLOCK, addr, sorted(sources),
            detail_key="incident.deadlock.detail",
            detail_params={"count": str(len(sources))},
        )
```

Keep `detect_offline_proxies`'s existing note about the detail string carrying no elapsed time — that reasoning (attribute churn on every snapshot) still applies to the *parameters* now.

Update each detector test to assert on `detail_key` and `detail_params` instead of substring-matching prose. This is stricter than what it replaces: `assert "2 proxies" in detail` passed for several wordings, `detail_params == {"count": "2"}` passes for exactly one.

```bash
git commit -am "refactor: detectors emit translation keys instead of prose"
```

---

## Task 5: notifications render from the catalogue

**Files:** Modify `incident_policy.py`, `notify.py`; Test `tests/test_notify.py`

`notification_content(incident)` becomes `notification_content(incident, catalogue)` and returns `(render(f"notify.{kind}.title", …), render(f"notify.{kind}.message", …))`.

The message parameters differ per kind — `address`, `count`, `sources`, `proxy` — so build the parameter dict per kind exactly as the current f-strings do, then render once. Do not invent new wording; Task 2 already captured it verbatim.

`notify.py` passes the catalogue the coordinator loaded (Task 6).

Tests: assert the French catalogue produces French, that an unknown kind degrades to the key rather than raising, and keep the existing precedence tests untouched.

```bash
git commit -am "feat: render notifications from the catalogue"
```

---

## Task 6: load the catalogue and render `detail`

**Files:** Modify `__init__.py`, `coordinator.py`, `coordinator_data.py`; Test `tests/test_coordinator_shell.py`

1. Add a small loader — reading JSON is blocking, so it goes through the executor and runs **once** at setup, not per snapshot:

```python
async def _async_load_catalogues(hass: HomeAssistant) -> dict[str, dict[str, str]]:
    """Read the shipped catalogues off disk (executor: this is blocking I/O)."""
    def _read() -> dict[str, dict[str, str]]:
        out: dict[str, dict[str, str]] = {}
        for path in (LOCALE_DIR).glob("incidents.*.json"):
            lang = path.name.split(".")[1]
            try:
                out[lang] = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                _LOGGER.warning("Unreadable catalogue %s", path, exc_info=True)
        return out

    return await hass.async_add_executor_job(_read)
```

2. Build `Catalogue.for_language(hass.config.language, catalogues)` at setup and hand it to the coordinator.
3. In `build_triage_data`, after assembling incidents, render each one's `detail` from its key. Add a keyword-only `catalogue: Catalogue | None = None`; when it is `None`, leave `detail` as-is so every existing pure test keeps working unchanged.

```python
    if catalogue is not None:
        incidents = [
            replace(i, detail=render(i.detail_key, i.detail_params, catalogue))
            if i.detail_key else i
            for i in incidents
        ]
```

4. Test: a French catalogue yields French `detail` on the incident attribute; no catalogue yields the untouched incident.

```bash
git commit -am "feat: render incident detail in Home Assistant's language"
```

---

## Task 7: the card

**Files:** Modify `custom_components/bluesight/frontend/www/bluesight-card.js`; manual browser check

Every hardcoded string listed below moves behind a `this._t(key, params)` helper:

| Line | String |
| --- | --- |
| 264-266 | the three-line "No BlueSight proxies found…" empty state |
| 290 | `"missing"` |
| 313 | `"offline"` |
| 334-335 | `` `last advert ${age} ago · ${seen} devices seen` `` and its shorter form |
| 356 | `"scan only — no connection slots"` |
| 372 | `` `Incident sensor ${incidentEntity} not found.` `` |
| 381 | `"No incidents"` |
| 395 | `` `${count} incident${count === 1 ? "" : "s"}` `` |
| 402 | `"Incident active (no detail available)"` |
| 423 | `kind.replace(/_/g, " ")` → `card.kind.<kind>` |
| 445 | `` `on ${sources.join(", ")}` `` |

Implementation notes:

- Fetch `/bluesight/locale/incidents.<lang>.json` once, from `this.hass.language` — **per user**, not the installation language. Two people can view one dashboard in different languages, and the card is the only surface where that is true.
- Keep a small embedded English map as the fallback, so a failed fetch renders English rather than raw keys. The card must never render worse than it does today.
- Render synchronously from whatever is loaded and re-render when the fetch resolves; never block the first paint on the network.
- Line 395's pluralisation must go through `card.incidents.one` / `card.incidents.other`. The current `+ "s"` is English-only and French needs the same split anyway.
- The relative age units (`s`/`m`/`h`/`d`) come from `card.age.*`.
- `detail` arrives already localised from the backend — do not translate it in the card.

**Verify manually** — this is the part with no automated coverage:

1. `robocopy` the integration to the HA config share, restart, hard-refresh.
2. With the HA profile language set to French, confirm the card chrome and incident kind labels are French.
3. Switch the profile to English, hard-refresh, confirm it flips.
4. Break the fetch (rename the locale dir) and confirm English still renders, not raw keys.

```bash
git commit -am "feat: translate the Lovelace card from the shared catalogue"
```

---

## Task 8: docs + version

**Files:** `manifest.json`, `README.md`, `docs/card.md`, new `docs/translations.md`

- Bump to `0.5.0`.
- README: state that incidents, notifications, and the card follow the user's language, with English and French shipped.
- `docs/translations.md`: how to add a language — copy `incidents.en.json`, translate, drop it in `locale/`, done. Point at `tests/test_catalogue_files.py` as the check a contributor runs.

```bash
git commit -am "docs: internationalisation guide and 0.5.0 bump"
```

---

## Definition of done

- [ ] `py -3.14 -m pytest tests/` green, roughly 25 new tests
- [ ] `ruff check .` clean; Hassfest and HACS green
- [ ] The existing incident automation still formats correctly — and now in French
- [ ] `kind` values unchanged (`deadlock`, `ghost_slot`, …); only labels translate
- [ ] Card renders French for a French profile, English for an English one
- [ ] A missing catalogue file degrades to English, never to raw keys

## Out of scope (YAGNI)

- Languages beyond English and French — the mechanism is what matters; contributors add the rest
- Translating `strings.json` beyond what already exists
- Any change to detection behaviour: this release must be text-only
