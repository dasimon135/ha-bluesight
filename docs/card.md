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

### 1. Nothing to install

The card ships **inside the integration**. HACS copies
`custom_components/bluesight/`, the card lives at
`custom_components/bluesight/frontend/www/bluesight-card.js`, so installing or
updating BlueSight delivers the card too.

On setup the integration serves it at `/bluesight/bluesight-card.js` and — on a
storage-mode dashboard, which is the default — registers the dashboard resource
for you. There is nothing to copy and nothing to declare.

Hard-refresh the browser (Ctrl/Cmd+Shift+R) once after installing, then skip to
step 3.

> **Upgrading from 0.3.x?** You no longer need your hand-placed
> `config/www/bluesight-card.js`. BlueSight rewrites an existing
> `/local/bluesight-card.js` resource to the served path rather than adding a
> second one, so the card keeps working across the upgrade; the leftover file in
> `config/www/` is then dead weight you can delete.

### 2. YAML-mode dashboards only

Home Assistant does not let an integration write to a YAML-managed resource
list, so if your Lovelace is in YAML mode, declare the resource yourself:

```yaml
resources:
  - url: /bluesight/bluesight-card.js
    type: module
```

The file is served either way — only the registration differs. On storage mode
(the default) this step does not apply.

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
First check the file is being served: browse to
`http://<your-ha>:8123/bluesight/bluesight-card.js`. You should get the file,
not a 404 — if it 404s, the integration did not finish setting up.

If it is served, the resource is missing. On a YAML-mode dashboard that is
expected: declare it yourself (step 2). On storage mode, check the Home
Assistant log for a `bluesight` warning naming the URL to add by hand, and
verify under Settings → Dashboards → ⋮ → *Resources* (the menu only appears
with *Advanced Mode* enabled in your user profile).

Otherwise the browser cached a failed load — hard-refresh with
Ctrl/Cmd+Shift+R.

**The card renders but an old version of it.** The served URL carries the
integration version as a cache-buster, so an update normally busts it by
itself; hard-refresh if it does not. If you still have a hand-registered
`/local/bluesight-card.js` from 0.3.x pointing at a stale copy, delete that
resource — the integration serves its own.

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
