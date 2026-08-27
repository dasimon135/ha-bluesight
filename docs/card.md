# BlueSight dashboard

BlueSight ships an optional Lovelace custom card, plus a **native-card
fallback** you can paste with zero custom JavaScript. Both read the same
entities the integration creates:

- `sensor.<proxy>_slots_used` — state = used count; attributes `total`, `free`,
  `allocated` (list of MACs), `allocated_devices` (the same slots in the same
  order, each `{address, name, device_id}`; `name` is `""` and `device_id` is
  `null` for an address Home Assistant's device registry does not know),
  `source`.
- `sensor.<proxy>_slots_free` — state = free count.
- `binary_sensor.<proxy>_online` — `on` while the proxy is a registered scanner.
- `sensor.<proxy>_last_device_seen` — seconds since that proxy last heard any
  advertisement; attributes `device_count`, `connectable`, `online`.
- `binary_sensor.bluesight_incident` — `on` when incidents exist; attributes
  `incident_count` (int), `availability_degraded` (bool), and `incidents` (list
  of `{kind, address, device_name, sources, source_names, detail}` — where
  `device_name` is what Home Assistant calls the peripheral, `""` when it
  cannot name one, and `source_names` the proxies named the way the `detail`
  sentence names them — `kind` ∈ `deadlock` / `ghost_slot` /
  `storm` / `bond_lost` / `proxy_offline` / `proxy_stalled` /
  `proxy_reboot_storm`).

---

## Option A — the custom card (`custom:bluesight-card`)

The custom card auto-discovers every proxy from `hass` (any
`sensor.*_slots_used` that carries a `total` attribute) and renders, per proxy:

- a **`used/total` count** beside the proxy's name,
- a **slot rack** — one row per slot, a pip on the left (filled = used, empty =
  free) and, beside it, the Home Assistant device holding that slot. Free slots
  keep their row, so the rack reads as a gauge at a glance without reading the
  numbers, and each name sits on its own slot's row rather than in a list whose
  order you have to trust:

  ```
  Proxy Buanderie              2/3
  ▣  C3:EB:49:65:67:55
     unknown to Home Assistant
  ▣  Madoka parents
  □
  ```

  An address the device registry cannot account for shows as its raw MAC marked
  **`unknown to Home Assistant`** on the line under it — that is the diagnostic,
  not a rendering defect: something Home Assistant knows nothing about is
  spending one of a handful of connection slots. A device the registry knows but
  nobody has named shows its MAC unmarked. A backend older than 0.6.0 publishes
  no names and the rack draws bare pips,
- a **health line** — how long since that proxy last heard an advertisement and
  how many devices it currently sees,
- **`offline`** in place of the rack when the proxy's online sensor says it is
  gone (its last known occupants are exactly what you must not believe), and
  **`scan only — no connection slots`** for a passive scanner, which habluetooth
  reports with zero slots and which therefore has no rack — English wording
  shown here; the card is translated, see [Languages](#languages).

The rack costs one row per slot, so a saturated 3-slot proxy is four rows tall
and a large fleet makes a tall card. That is a deliberate trade: free slots keep
their row so the rack reads as a gauge at a glance, and a rack that collapsed its
empty rows would lose exactly the thing you look at it for.

#### `show_devices: false` — the squares, on one line

If the fixed row per slot is the wrong trade for your fleet, `show_devices:
false` puts the pips back on a single horizontal line and draws no names at
all:

```
Proxy Buanderie              2/3
▣ ▣ □
```

Everything else is unchanged — the count, the health line, `offline`, `scan
only — no connection slots`, the incident feed. What you lose is exactly the
names: the tile still says *how many* slots are spent, never *who* is spending
them, so a saturated proxy tells you to go look rather than telling you where.

Worth turning off when the height is the problem and not the answer: eight
proxies of eight slots is 64 rack rows and a card taller than the screen, while
the same fleet is eight lines of squares. In that layout a proxy's tile is a
fixed height whatever its slot count, and the card's masonry size shrinks with
it.

One thing survives on purpose. A proxy reporting **more occupants than it has
slots** — the two numbers reach the sensor from the same habluetooth snapshot
but not from the same field, so they can briefly disagree — draws the extra
squares anyway, in amber, past the end of the row. Dropping them would make the
card silently under-report how many devices hold a connection, which is the one
thing it exists not to do; and with the names off, the amber square is the only
thing left on screen saying the two numbers disagree.

Default: `true` (the rack).

Below that it draws a **coloured incident feed**: red for `deadlock` and
`ghost_slot`, amber for everything else — including `bond_lost` — each badge
carrying the incident kind, the device address, the detail, and the proxies
involved. The rule behind the colours, rather than the list: red is for an
incident that **wastes a scarce resource**, a connection slot held for nothing. A
lost bond holds no slot; it is a device that cannot connect, which is bad and
differently bad. A pairing problem also tends to hit several devices at once, and
a card that turns entirely red stops meaning anything.

It is a single vanilla-JS file — no build step, no dependencies.

### Languages

The card is translated: English and French ship, anything else falls back to
English. It renders in the **viewer's** language — the one set on their Home
Assistant user profile — not the installation's, so two people looking at the
same dashboard each read it in their own. Nothing to configure; the card reads
`hass.language` and fetches the matching catalogue.

The one part that does *not* follow the viewer is the incident **detail** line.
That text is a published attribute of `binary_sensor.bluesight_incident`,
rendered once in the installation's language because user automations build
push notifications from it; the card prints it as it arrives. Adding a language
is described in [translations.md](translations.md).

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
show_devices: true                                # false = one row of pips,
                                                  # no names (see above)
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
- `sensor.<proxy>_slots_used` carries `total`, `free`, `allocated` and
  `allocated_devices` as attributes, so a tile on it already tells you the whole
  slot story; the separate `_slots_free` sensor is there for templates and
  history. A markdown card can iterate `allocated_devices` to name the devices
  holding the slots, exactly as the custom card does — the resolution is done in
  the backend, so the names are simply there to read.
- The markdown card iterates the `incidents` attribute with a Jinja `for` loop
  and guards the empty case with `or []`, so it renders cleanly even mid-update.
- `conditional` cards hide themselves entirely when their condition is false, so
  the incident panel only appears when something is actually wrong.
- The fallback prints the raw `kind` (`ghost slot`) rather than a translated
  label, because a Jinja template has no access to BlueSight's catalogue. The
  `detail` line beside it *is* translated — it arrives already rendered in the
  installation's language.
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

**A proxy shows `scan only — no connection slots`** (or its translation).
That is correct, not a bug: habluetooth registers non-connectable scanners with
zero slots. They can
see advertisements but never hold a connection.

---

## Roadmap

Animated live-migration transitions (showing a slot moving between proxies) are
intentionally out of scope and may arrive in a later release.
