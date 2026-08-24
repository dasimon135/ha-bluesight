# Adding a language

BlueSight's user-facing strings live in one flat JSON file per language:

```
custom_components/bluesight/frontend/www/locale/incidents.<lang>.json
```

To add a language: copy `incidents.en.json`, rename it to the two-letter base
code (`de`, `nl`, `pt`, …), translate the values, and open a PR. **No shipping
code changes** — the backend globs the directory at setup and the card fetches
`incidents.<lang>.json` by name. The drift tests do need your language added to
them, though; see [Checking your work](#checking-your-work).

The files sit under the card's `www` tree on purpose: the integration already
serves that directory over HTTP, so the backend reads the very same file the
browser fetches. One catalogue, both halves.

## What each language covers

Home Assistant hands out tags like `fr-CA`; BlueSight drops the region, so
`incidents.fr.json` serves every French variant. A key missing from your file
falls back to English individually, so a half-finished translation is usable
from the first key.

Two different languages can be in play at once:

| Surface | Language |
| --- | --- |
| Incident `detail`, persistent notifications | the installation's (`hass.config.language`) |
| The Lovelace card | the viewer's profile language |

## Key namespaces

| Prefix | Where it appears | Notes |
| --- | --- | --- |
| `incident.*` | the `detail` field on each incident, published as an attribute of `binary_sensor.bluesight_incident` | read by the card, by the native-card fallback, and by user automations |
| `notify.*` | persistent notification titles and messages | `.title` is a heading — keep it short |
| `card.*` | every string the Lovelace card draws itself: proxy status, badges, empty states | |

Keys are added and removed with features, so translate against the current
`incidents.en.json` rather than an older copy of another language.

## Placeholders

`{name}` placeholders are substituted at render time. **Every placeholder in
the English string must appear in yours, spelled identically.** They may be
reordered freely within the sentence — French usually requires it — but a
dropped `{address}` silently loses the device the incident is about, and an
invented `{foo}` renders as a literal `{foo}` to the user.

```json
"incident.deadlock.detail": "Held on {count} proxies simultaneously"
```

## Plurals

A counted string is split into two keys, `<key>.one` and `<key>.other`:

```json
"incident.storm.detail.one":   "{count} failure in {seconds}s",
"incident.storm.detail.other": "{count} failures in {seconds}s"
```

`.one` is used when the count is exactly 1, `.other` for everything else —
including 0. Ship **both** forms: the unsuffixed key does not exist once a key
is split, so a missing half renders as the bare key `incident.storm.detail`.

**Identical forms are legitimate.** French `fois` is invariable, so
`notify.proxy_reboot_storm.message.one` and `.other` are the same sentence
character for character — only the number `{count}` carries differs at render
time. `tests/test_catalogue_plurals.py` asserts that exact wording, so nobody
"corrects" it into `foises` later. If your language has no singular/plural
distinction for a noun, write the same sentence twice and move on.

BlueSight has no plural rule beyond 1-vs-rest. A language that needs more
categories (Polish, Russian, Arabic) needs the renderer taught about them
first — open an issue rather than approximating.

## Never translate `kind` values

Incident kinds — `deadlock`, `ghost_slot`, `storm`, `proxy_offline`,
`proxy_stalled`, `proxy_reboot_storm` — are machine identifiers. They are
published in the `incidents` attribute and users match on them in automation
templates, so translating one breaks somebody's automation silently.

What *is* translatable is the label the card puts on a kind: the `card.kind.*`
keys. Translate those; leave the values they are keyed by alone.

## Checking your work

```bash
python -m pytest tests/test_catalogue_files.py tests/test_catalogue_plurals.py
```

`tests/test_catalogue_files.py` is the file-level guard. It checks that:

| Test | Catches |
| --- | --- |
| `test_every_english_key_is_translated` | a key you have not translated yet |
| `test_no_french_key_is_orphaned` | a key you invented, or one left behind by a rename — it can never be reached, since English is the key set |
| `test_placeholders_match_across_languages` | a dropped or invented `{placeholder}` (order is not checked, deliberately) |
| `test_every_plural_key_has_both_forms` | `.one` without `.other`, or the reverse |
| `test_plural_forms_agree_on_placeholders` | a placeholder present in one form of a key but not the other — the singular rots unnoticed because it is the form seen least |
| `test_no_value_is_blank` | `""`, which renders as the bare key rather than as nothing |
| `test_the_catalogue_is_a_flat_map_of_strings` | a nested object or a non-string value |

These tests name their languages: the per-file checks are
`@pytest.mark.parametrize("language", ["en", "fr"])`, and the cross-language
ones take the `french` fixture directly. Adding a catalogue means widening both
to include it — otherwise none of the above runs against your file at all, and
CI stays green while your translation rots.

`tests/test_catalogue_plurals.py` pins the singular wording of every counted
string — it exists because "1 devices seen" shipped for a whole release. Add
your language's singulars there too.

If you also touched `card.*` strings in `incidents.en.json`, run
`tests/test_card_locale.py`: the card embeds a copy of the English `card.*`
half as a last resort for when the fetch fails, and that copy has to be updated
in lockstep.
