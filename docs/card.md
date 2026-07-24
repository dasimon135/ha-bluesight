# BlueSight dashboard

BlueSight ships an optional Lovelace custom card, plus a **native-card
fallback** you can paste with zero custom JavaScript. Both read the same
entities the integration creates:

- `sensor.<proxy>_slots_used` — state = used count; attributes `total`, `free`,
  `allocated` (list of MACs), `source`.
- `sensor.<proxy>_slots_free` — state = free count.
- `binary_sensor.bluesight_incident` — `on` when incidents exist; attributes
  `incident_count` (int) and `incidents` (list of
  `{kind, address, sources, detail}`, `kind` ∈ `deadlock` / `ghost_slot` /
  `storm`).

---

## Option A — the custom card (`custom:bluesight-card`)

The custom card auto-discovers every proxy from `hass` (any
`sensor.*_slots_used` that carries a `total` attribute), draws a slot-pip row
per proxy, and renders a coloured incident banner. It is a single vanilla-JS
file — no build step, no dependencies.

### 1. Make the JS file available to Home Assistant

The file lives at `www/bluesight-card.js` in this repository. Home Assistant
serves anything under its own `config/www/` directory at the URL `/local/`.

**If you installed via HACS:** HACS copies the file for you and can register
the resource automatically — you usually only need step 2 to confirm it. If
HACS did not register it, add it manually as in step 2.

**Manual install:** copy `www/bluesight-card.js` into your Home Assistant
`config/www/` folder, e.g. `config/www/bluesight-card.js`.

### 2. Register the dashboard resource

Add it as a **module** resource so the browser loads it.

**Via the UI** (Settings → Dashboards → ⋮ → *Resources* → *Add resource*):

- **URL:** `/local/bluesight-card.js`
- **Resource type:** `JavaScript Module`

> The *Resources* menu only appears when dashboards are in *Advanced Mode*
> (enable *Advanced Mode* in your user profile). On HA 2026.x the entry lives
> under **Settings → Dashboards → ⋮ (top-right) → Resources**.

**Via YAML mode** (if your Lovelace is YAML-managed) add:

```yaml
resources:
  - url: /local/bluesight-card.js
    type: module
```

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
the custom card. It gives you most of the value: a per-proxy slot glance and a
conditional incident panel.

Paste this as a **manual card** (or into raw-config YAML). Replace the example
`sensor.*` entity ids with your own proxy entities (see them under
Developer Tools → States, filter `slots_`).

```yaml
type: vertical-stack
cards:
  # --- Per-proxy slot usage -------------------------------------------------
  - type: glance
    title: BLE proxy slots
    show_state: true
    columns: 2
    entities:
      - entity: sensor.living_room_proxy_slots_used
        name: Living room · used
      - entity: sensor.living_room_proxy_slots_free
        name: Living room · free
      - entity: sensor.garage_proxy_slots_used
        name: Garage · used
      - entity: sensor.garage_proxy_slots_free
        name: Garage · free

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

- The `entities`/`glance` list is static, so add one `_slots_used` /
  `_slots_free` pair per proxy you have. (The custom card in Option A discovers
  these automatically; the native fallback cannot, by design.)
- The markdown card iterates the `incidents` attribute with a Jinja `for` loop
  and guards the empty case with `or []`, so it renders cleanly even mid-update.
- `conditional` cards hide themselves entirely when their condition is false, so
  the incident panel only appears when something is actually wrong.

---

## Roadmap

Animated live-migration transitions (showing a slot moving between proxies) are
intentionally out of scope for v1 and may arrive in a later release.
