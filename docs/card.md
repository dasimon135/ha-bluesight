# BlueSight dashboard

BlueSight ships an optional Lovelace custom card, plus a **native-card
fallback** you can paste with zero custom JavaScript. Both read the same
entities the integration creates:

- `sensor.<proxy>_slots_used` — state = used count; attributes `total`, `free`,
  `allocated` (list of MACs), `source`.
- `sensor.<proxy>_slots_free` — state = free count.
- `binary_sensor.<proxy>_online` — `on` while the proxy is a registered scanner.
- `sensor.<proxy>_last_device_seen` — seconds since that proxy last heard any
  advertisement; attributes `device_count`, `connectable`, `online`.
- `binary_sensor.bluesight_incident` — `on` when incidents exist; attributes
  `incident_count` (int), `availability_degraded` (bool), and `incidents` (list
  of `{kind, address, sources, detail}`, `kind` ∈ `deadlock` / `ghost_slot` /
  `storm` / `proxy_offline` / `proxy_stalled` / `proxy_reboot_storm`).

---

## Option A — the custom card (`custom:bluesight-card`)

The custom card auto-discovers every proxy from `hass` (any
`sensor.*_slots_used` that carries a `total` attribute) and renders, per proxy:

- a **slot-pip row** (filled = used, empty = free) with a `used/total` count,
- a **health line** — how long since that proxy last heard an advertisement and
  how many devices it currently sees,
- **`offline`** in place of the pips when the proxy's online sensor says it is
  gone, and **`scan only — no connection slots`** for a passive scanner (which
  habluetooth reports with zero slots).

Below that it draws a **coloured incident feed**: red for `deadlock` and
`ghost_slot`, amber for everything else, each badge carrying the incident kind,
the device address, the detail, and the proxies involved.

It is a single vanilla-JS file — no build step, no dependencies.

### 1. Copy the JS file into `config/www/`

> **BlueSight is a HACS *integration*, so HACS does NOT install this card.**
> HACS only copies `www/` assets and registers dashboard resources for
> repositories in the **Lovelace/plugin** category. Installing BlueSight gives
> you `custom_components/bluesight/` and nothing else — the card is yours to
> place. Both steps below are mandatory.

The file lives at [`www/bluesight-card.js`](../www/bluesight-card.js) in this
repository. Home Assistant serves anything under its own `config/www/`
directory at the URL `/local/`, so copy it to
`config/www/bluesight-card.js` (create the `www` folder if it does not exist).

Check it is being served before going further — browse to
`http://<your-ha>:8123/local/bluesight-card.js`. You should get the file, not a
404.

### 2. Register the dashboard resource

Add it as a **module** resource so the browser loads it.

**Via the UI** (Settings → Dashboards → ⋮ → *Resources* → *Add resource*):

- **URL:** `/local/bluesight-card.js?v=0.3.0`
- **Resource type:** `JavaScript Module`

> The *Resources* menu only appears when dashboards are in *Advanced Mode*
> (enable *Advanced Mode* in your user profile). On HA 2026.x the entry lives
> under **Settings → Dashboards → ⋮ (top-right) → Resources**.

**Via YAML mode** (if your Lovelace is YAML-managed) add:

```yaml
resources:
  - url: /local/bluesight-card.js?v=0.3.0
    type: module
```

The `?v=` suffix is a cache-buster, and it matters: browsers cache ES modules
aggressively, so **after replacing the file with a newer version you must bump
that number** — otherwise the old card keeps rendering and it looks like the
update did nothing.

After adding the resource, hard-refresh the browser (Ctrl/Cmd+Shift+R) so the
new module is picked up.

### 3. Add the card

Minimal config — everything is auto-discovered:

```yaml
type: custom:bluesight-card
```

Override form (all keys optional):

```yaml
type: custom:bluesight-card
title: BlueSight                                  # card header text
incident_entity: binary_sensor.bluesight_incident # default shown
proxies:                                            # skip auto-discovery
  - sensor.living_room_proxy_slots_used
  - sensor.garage_proxy_slots_used
```

If no proxies are found the card shows a helpful hint instead of failing.

### Screenshot

> _Screenshot placeholder — add `docs/card.png` once the card has been rendered
> in a browser and reference it here._

---

## Option B — native-card fallback (no custom JavaScript)

This uses only built-in Lovelace cards, so it works even if you never install
the custom card. It gives you most of the value: per-proxy slot and health
tiles, plus a conditional incident panel.

Paste this as a **manual card** (or into raw-config YAML). Replace the example
`sensor.*` entity ids with your own proxy entities (see them under
Developer Tools → States, filter `slots_`).

```yaml
type: vertical-stack
cards:
  # --- Per-proxy slot usage + health ---------------------------------------
  - type: grid
    columns: 2
    square: false
    cards:
      - type: tile
        entity: sensor.living_room_proxy_slots_used
        name: Living room · slots
      - type: tile
        entity: sensor.living_room_proxy_last_device_seen
        name: Living room · last advert
      - type: tile
        entity: sensor.garage_proxy_slots_used
        name: Garage · slots
      - type: tile
        entity: sensor.garage_proxy_last_device_seen
        name: Garage · last advert

  # --- Incident panel: only shown when an incident is active ----------------
  - type: conditional
    conditions:
      - entity: binary_sensor.bluesight_incident
        state: "on"
    card:
      type: markdown
      content: >
        ## ⚠️ BLE incidents:
        {{ state_attr('binary_sensor.bluesight_incident', 'incident_count') }}

        {% for inc in state_attr('binary_sensor.bluesight_incident',
        'incidents') or [] %}
        - **{{ inc.kind | replace('_', ' ') }}** — `{{ inc.address }}`{% if
        inc.detail %} — {{ inc.detail }}{% endif %}
        {% endfor %}

  # --- Optional: a subtle "all clear" panel when there are no incidents -----
  - type: conditional
    conditions:
      - entity: binary_sensor.bluesight_incident
        state: "off"
    card:
      type: markdown
      content: >
        ✅ No BLE incidents.
```

Notes:

- The card list is static, so add one row per proxy you have. (The custom card
  in Option A discovers them automatically; the native fallback cannot, by
  design.) Find your entity ids under Developer Tools → States, filter `slots_`.
- `sensor.<proxy>_slots_used` carries `total`, `free` and `allocated` as
  attributes, so a tile on it already tells you the whole slot story; the
  separate `_slots_free` sensor is there for templates and history.
- The markdown card iterates the `incidents` attribute with a Jinja `for` loop
  and guards the empty case with `or []`, so it renders cleanly even mid-update.
- `conditional` cards hide themselves entirely when their condition is false, so
  the incident panel only appears when something is actually wrong.
- On a `sections` dashboard, drop the `vertical-stack` and put each group in its
  own section behind a `heading` card — that is the current Home Assistant
  layout idiom and it reflows better on phones.

---

## Troubleshooting

**"Custom element doesn't exist: bluesight-card".** The module was not loaded.
In order of likelihood: the resource is not registered (Option A step 2); the
file is not actually at `config/www/bluesight-card.js` (check
`/local/bluesight-card.js` in a browser); or the browser cached a failed load —
hard-refresh with Ctrl/Cmd+Shift+R.

**The card renders but an old version of it.** Bump the `?v=` suffix on the
resource URL and hard-refresh. The browser will not re-fetch a module at an
unchanged URL.

**The card is empty / "No BlueSight proxies found".** The card looks for
`sensor.*_slots_used` entities carrying a `total` attribute. If your proxies are
named unusually, list them explicitly with the `proxies:` config key rather than
relying on discovery.

**A proxy shows `scan only — no connection slots`.** That is correct, not a
bug: habluetooth registers non-connectable scanners with zero slots. They can
see advertisements but never hold a connection.

---

## Roadmap

Animated live-migration transitions (showing a slot moving between proxies) are
intentionally out of scope and may arrive in a later release.
