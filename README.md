# BlueSight — Home Assistant integration

[![Release](https://img.shields.io/github/v/release/dasimon135/ha-bluesight)](https://github.com/dasimon135/ha-bluesight/releases)
[![Validate](https://github.com/dasimon135/ha-bluesight/actions/workflows/validate.yml/badge.svg)](https://github.com/dasimon135/ha-bluesight/actions/workflows/validate.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![License](https://img.shields.io/github/license/dasimon135/ha-bluesight)](LICENSE)

When a Bluetooth device in Home Assistant goes `unavailable` for no visible
reason, this tells you why.

Every ESPHome Bluetooth proxy can hold only a handful of Bluetooth connections
at once — three, usually. Home Assistant does not show you how many are in use
or which device is holding each one, so when they run out, devices simply stop
working and nothing says so. **BlueSight shows you those connections**, names
the device holding each one, and flags the three ways they go wrong.

> **What it does not do:** anything. BlueSight only reads. It never touches a
> proxy, a connection or a pairing, and it needs no firmware reflash — it
> installs through HACS like any other integration.

![The BlueSight card: four Bluetooth proxies, twelve connection slots, and the device holding each one](https://raw.githubusercontent.com/dasimon135/ha-bluesight/main/docs/images/card_en.png)

*Four proxies, twelve slots, and what is actually in them — every held slot
named by the device holding it, not by a MAC address. One proxy is full while
two sit empty. "No incidents" is the normal reading: the card stays quiet until
something is genuinely wrong.*

## Will this be useful to me?

**If you run ESPHome Bluetooth proxies**, and especially several of them, yes.
The more Bluetooth devices you have, the more often you meet the problems below.

**If your Bluetooth devices never misbehave**, it is a dashboard that says
everything is fine. That is a reasonable thing to own, but it is not urgent.

**It requires nothing new.** No hardware, no reflash, no configuration file. It
reads what Home Assistant and your proxies already know.

## The three failures it catches

Home Assistant already shows you what each proxy can *hear* — the Advertisement
Monitor added in 2025.2 does that well. What no tool shows is what each proxy is
*connected to*, and that is where these live:

- **A device that hogs every proxy.** One misbehaving device takes a connection
  on every proxy and never gives it back. The shared pool runs dry and unrelated
  devices go `unavailable` with no error anywhere. This is a real, documented
  failure — Home Assistant core issue
  [#176516](https://github.com/home-assistant/core/issues/176516) — and the only
  method offered in that thread is to turn on debug logs and read them by hand.
- **A connection held by something already dead.** The proxy still counts a
  device as connected while every one of that device's entities has gone
  `unavailable`. The slot is spent on nothing.
- **A device that keeps failing to pair.** It retries over and over in a tight
  burst, churning connections and destabilising the proxy for everything else.

## What it shows

Three detectors, running over the exact per-proxy slot allocations Home
Assistant already tracks internally:

| Detector | Fires when |
| --- | --- |
| **Deadlock** (`#176516`) | the same device address is allocated on **two or more distinct** proxies at once — a BLE peripheral can only be connected to one central, so the extra allocations are stale duplicates spending slots across the pool. |
| **Ghost slot** | an address is in a proxy's allocated list while its Home Assistant device is dead — the device is found in the registry (by MAC in `connections` or `identifiers`) and **all** its entities are `unavailable`. Availability is judged from entity state, not advertising: a connected device stops advertising, so advertisement presence would false-positive every healthy persistent connection. A device with no registry entry cannot be judged this way and is treated as alive — unless the proxy holding it runs the optional [ESPHome component](#measured-evidence-optional-esphome-component), which measures the connection's idle time directly instead. See [Known limitations](#known-limitations). |
| **Pairing storm** | a device's slot is released, over and over, while its Home Assistant device is unavailable — beyond the configured threshold inside the storm window. A best-effort heuristic on its own; on a proxy running the optional [ESPHome component](#measured-evidence-optional-esphome-component) the same window is fed real SMP-failure counts instead — see [Known limitations](#known-limitations). |

It surfaces the state as:

- **Per-proxy sensors** — `sensor.<proxy>_slots_used` and
  `sensor.<proxy>_slots_free`, with the total, free count, the list of
  allocated device addresses, and those same addresses resolved to Home
  Assistant devices as attributes.
- **A global incident binary sensor** — `binary_sensor.bluesight_incident`
  (device class `problem`), `on` whenever any incident is open, with the full
  incident list in its attributes.
- **Persistent notifications** — a human-readable alert is raised (and cleared)
  as incidents open and resolve.
- **A Lovelace card** — an optional custom card that draws each proxy's slots as
  pips and shows a live incident feed, plus a zero-JavaScript native-card
  fallback. See [Dashboard](#dashboard).

Everything is **read-only**. BlueSight never frees a slot, forces an unbond, or
reflashes anything. It observes and reports.

## Proxy health

The v1 detectors watch what flows *through* the proxies. v1.2 adds a layer that
watches the **proxies themselves**. BlueSight reads the `habluetooth` scanner
registry Home Assistant already maintains — so this needs **zero user config** —
and raises three more incidents:

| Incident | Fires when |
| --- | --- |
| **Proxy offline** | a proxy that was online is no longer a registered scanner — it dropped off the bus. Check its power and Wi-Fi. |
| **Proxy stalled** | a proxy is still online but has not seen any Bluetooth advertisement for a while. It is up but deaf; power-cycle it. |
| **Proxy reboot storm** | a proxy registers and unregisters over and over inside the reboot window. Check its power supply or brownouts. |

It surfaces per-proxy health as two extra entities on each proxy device:

- `binary_sensor.<proxy>_online` (device class `connectivity`) — `on` while the
  proxy is a registered scanner, `off` the moment it is not. There is no grace
  period here: the sensor reports the fact, the **proxy offline** incident
  applies the patience — so an OTA update is a brief `off` and no alert.
- `sensor.<proxy>_last_device_seen` — seconds since the last advertisement that
  proxy heard, the signal behind the stalled detector.

Three new options tune it: the **stalled threshold** (how long a proxy may go
without an advertisement before it is flagged), the **reboot window**, and the
**reboot threshold** (register/unregister cycles within that window that trip a
reboot storm).

RAM, Wi-Fi signal, and uptime telemetry are **not** part of BlueSight at all.
They need per-proxy instrumentation, and when that instrumentation arrived in
0.6.0 it deliberately carried none of them: they describe the health of a *node*,
not of the connection layer, nothing in BlueSight would consume them, and ESPHome
already exposes all three directly if you want them on a dashboard.

## Measured evidence (optional ESPHome component)

Everything above runs on what Home Assistant already knows. Two things it does
not know, and cannot: **why a pairing failed** — the reason is raised as a
`BleakError` to whichever integration owns the connection and never becomes
state — and **what is in a proxy's bond store**, which lives in that proxy's NVS
flash and is not exposed over the ESPHome API at all.

An optional ESPHome component closes both gaps. It is a passive observer on the
proxy's BLE event stream that opens no connection and writes no bond — the
read-only invariant holds into the firmware — and it publishes three text
sensors of raw facts: SMP failure counts, the bond list, and per-connection idle
time. It reaches no verdicts; the integration does, which is why a retune is an
options change and not a reflash. Two diagnoses become possible:

| Incident | Fires when |
| --- | --- |
| **Bond lost** | a device's pairing keeps failing on a proxy whose own bond store holds no entry for it. The remedy is exact, and is the whole reason this diagnosis is worth firmware: **re-pair through that specific proxy**. Bonds are stored per proxy, so pairing through whichever proxy Home Assistant happens to pick next will not fix it. |
| **Ghost slot, by idle time** | a slot **Home Assistant holds on that proxy** goes without GATT traffic for longer than the idle threshold, for a device Home Assistant does not manage. The entity-based ghost detector cannot judge such a device and treats it as alive; measured silence is the only way to see it at all. Only addresses habluetooth reports as allocated are judged, so a connection the node opened for itself — an ESPHome `ble_client:` link — is reported by the sensor and never mistaken for a stuck slot. It raises an ordinary `ghost_slot` incident — a new source of evidence, not a new kind. |

It also upgrades storm detection from the heuristic to counted SMP failures —
**per proxy, never globally**. A real fleet is mixed, so it degrades proxy by
proxy: a flashed proxy contributes measurements while an unflashed one keeps
contributing inferred failures, into the same window and the same storm concept.
`Incident.evidence` records which was used.

**You do not need any of this.** BlueSight works with no firmware change, every
detector above keeps exactly its previous behaviour on a proxy that does not run
the component, and adding it to one proxy does not change how the others are
judged. Installation, the wire format, the two new diagnoses and how to verify
it is working are in **[docs/esphome-component.md](docs/esphome-component.md)**.

## Requirements

- **Home Assistant ≥ 2025.7** — the slot-allocation API arrived in 2025.2, but
  the proxy-health layer also needs `habluetooth`'s scanner-registration
  callbacks, which landed later. On an older release the integration fails to
  set up rather than degrading.
- **One or more ESPHome Bluetooth proxies** (or local adapters) that expose
  connection slots. With a single adapter you still get slot visibility and
  ghost/storm detection; the deadlock detector is most meaningful across
  multiple proxies.

## Installation

BlueSight is a HACS custom repository.

1. In HACS, open the three-dot menu → **Custom repositories**.
2. Add `https://github.com/dasimon135/ha-bluesight` with category
   **Integration**.
3. Install **BlueSight**, then restart Home Assistant.
4. Go to **Settings → Devices & Services → Add Integration** and add
   **BlueSight**. It is single-instance and needs no configuration to start.

### Options

Open the integration's **Configure** dialog to tune:

| Option | Default | Meaning |
| --- | --- | --- |
| Storm window | 300 s | sliding window over which failed connections are counted (min 30 s). |
| Storm threshold | 5 | failed connections within the window that trip a storm incident (min 2). |
| Poll interval | 30 s | how often the coordinator refreshes its slot snapshot (min 5 s). |
| Stalled threshold | 180 s | how long a proxy may go without seeing any advertisement before it is flagged as stalled. |
| Reboot window | 600 s | sliding window over which proxy register/unregister cycles are counted. |
| Reboot threshold | 3 | reboots within the window that trip a reboot-storm incident. |
| Offline grace | 90 s | how long a proxy may be missing before it is reported offline. A proxy drops off the bus for ~20-30 s on every OTA update; 0 reports the first missing snapshot. |
| Idle-slot threshold | 1800 s | how long a held GATT connection may go without traffic before the slot is reported stuck (min 60 s). Needs the [BlueSight ESPHome component](docs/esphome-component.md) on the proxy. Set it above your quietest device: a working Daikin BRC1H thermostat here measured 430 s of GATT silence, and a lighting mesh nobody touches overnight is silent for hours. No one value fits every network — see [choosing a value](docs/esphome-component.md#choosing-idle_threshold_s). |

### Actions

| Action | What it does |
| --- | --- |
| `bluesight.forget_proxy` | Stops tracking a proxy that is gone (field: `source`, its MAC): clears any open **proxy offline** incident and deletes the proxy's BlueSight device. A proxy seen online once is remembered for good — across restarts, from that device — so this is how you retire or replace one without leaving a permanent alert. Deleting the device from its page does the same. A proxy that is still a registered scanner cannot be retired either way. |

### Repairs

**Proxy offline** has two causes with opposite remedies: the proxy is down, or
it is gone for good. The notification speaks to the first. Once a proxy has
stayed offline for **an hour**, a Repair asks about the second — *was it
retired?* — and its one button does what `bluesight.forget_proxy` does, by
name, without you having to find a MAC address. Do nothing and it clears itself
the moment the proxy comes back. An hour, and not the offline grace period,
so that an OTA update or a router reboot never asks anyone that question.

### Events

`bluesight_incident` is fired once when an incident opens and once when it
resolves, so an automation is told instead of having to watch
`binary_sensor.bluesight_incident` and work out what changed in its attributes.

| Field | |
| --- | --- |
| `action` | `opened` or `resolved` |
| `kind` | `deadlock`, `ghost_slot`, `storm`, `bond_lost`, `proxy_offline`, `proxy_stalled`, `proxy_reboot_storm` |
| `address` | the device's address — or the proxy's, for the three `proxy_*` kinds |
| `device_name` | what Home Assistant calls that device, `""` if it cannot name it |
| `sources`, `source_names` | the proxies involved, as addresses and as the names you gave them |
| `detail` | the same sentence the incident sensor publishes, in the installation's language |
| `evidence` | `smp` when a proxy measured it, `heuristic` when it was inferred |
| `key` | the incident's identity: it stays the same while the numbers in `detail` move |

```yaml
triggers:
  - trigger: event
    event_type: bluesight_incident
    event_data:
      action: opened
      kind: bond_lost
actions:
  - action: notify.mobile_app_phone
    data:
      title: "{{ trigger.event.data.device_name or trigger.event.data.address }}"
      message: "{{ trigger.event.data.detail }}"
```

The precedence rules apply first, exactly as for the notifications: a missing
pairing key is one event, not a `bond_lost` and the `storm` it causes. A
`resolved` event carries the incident as it was last seen. Reloading the
integration resolves nothing and fires nothing; the incidents still open
afterwards are reported as `opened` again, because to the new run they are.

### Diagnostics

The integration's **⋮ → Download diagnostics** dumps the exact slot
allocations, per-proxy health, every open incident, and the current contents of
the storm and reboot windows. Attach it to a bug report. BLE addresses are *not*
redacted — they are the subject of the report, and a redacted dump cannot show
that the same address is held on two proxies.

If you run the [ESPHome component](docs/esphome-component.md), the dump's
`telemetry` section is where you check that it is actually being read:
`reporting` holds each proxy's raw reading (`absent` and `reporting` are stated
per signal, so "no telemetry" never looks like "nothing to report"), and
`silent_sources` names the proxies that sent nothing at all.

## Entities

| Entity | Type | State | Key attributes |
| --- | --- | --- | --- |
| `sensor.<proxy>_slots_used` | sensor | slots allocated on that proxy | `total`, `free`, `allocated` (list of MACs), `allocated_devices` (list of `{address, name, device_id}`, same slots in the same order), `source` |
| `sensor.<proxy>_slots_free` | sensor | slots still free on that proxy | — |
| `binary_sensor.<proxy>_online` | binary_sensor (`connectivity`) | `on` while the proxy is a registered scanner | — |
| `sensor.<proxy>_last_device_seen` | sensor (`duration`, seconds) | seconds since that proxy last heard an advertisement | `device_count`, `connectable`, `online` |
| `sensor.<proxy>_saturation_24h` | sensor (`%`) | share of the last 24h that proxy spent with **no free slot** | `longest_saturated_s`, `episodes`, `observed_s`, `window_s`, `source` |
| `binary_sensor.bluesight_incident` | binary_sensor (`problem`) | `on` when any incident is open | `incident_count`, `availability_degraded`, `incidents` (list of `{kind, address, device_name, sources, source_names, detail}`, where `device_name` is what Home Assistant calls the peripheral — `""` when it cannot name it — and `source_names` the proxies named the way the detail sentence names them; `kind` ∈ `deadlock` / `ghost_slot` / `storm` / `bond_lost` / `proxy_offline` / `proxy_stalled` / `proxy_reboot_storm`) |

### Saturation is measured, not judged

`sensor.<proxy>_saturation_24h` raises no incident and never will on its own.
A proxy dedicated to three permanent connections is saturated *by design*, and
the point where busy becomes too busy is not knowable from one fleet — so this
publishes the measurement rather than a verdict, the way `idle_threshold_s`
went from an argued 300s to a measured 1800s.

It is still the most useful number here when nothing is wrong. A proxy sitting
near 100% while its neighbours idle is why a device will go `unavailable` with
no error days from now: the next connection that needs that proxy will not get
in. Nothing else in Home Assistant can see this, because it needs per-proxy
slot accounting.

Read the attributes alongside the percentage. Ten one-second squeezes and one
ten-minute lockout give the same ratio and mean nothing alike —
`longest_saturated_s` and `episodes` are what separate them, and `observed_s`
says how much evidence any of it rests on (the window is in memory, so it
restarts with Home Assistant; the sensor's own recorder history does not).

`availability_degraded` turns `true` if the device/entity registry lookup behind
ghost-slot detection ever fails. Ghost verdicts are biased toward "alive", so a
degraded signal means *absence of ghost incidents proves nothing* — it is
surfaced here rather than only in the log.

Each proxy is registered as its own Home Assistant device carrying its two slot
sensors; the incident binary sensor lives on a single **BlueSight** service
device.

## Dashboard

BlueSight ships an optional custom Lovelace card plus a native-card fallback
you can paste with no custom JavaScript. Both read the entities above. Full
setup — resource registration, the `custom:bluesight-card` config, and the
native fallback YAML — is in **[docs/card.md](docs/card.md)**.

> The card draws one row per connection slot, naming the device that holds it.
> On a large fleet that gets tall: `show_devices: false` puts the pips back on
> a single line and drops the names. See
> [docs/card.md](docs/card.md#show_devices-false--the-squares-on-one-line).

> **`layout: tile` is the whole fleet on one line** — a coloured dot, the slots
> in use, the open incidents — and tapping it opens the full card in a popup.
> Green means nothing is wrong, amber and red are the incident feed's own
> colours, and grey means the diagnostic itself is not answering. For a
> dashboard where BlueSight is something you check rather than something you
> watch. See [docs/card.md](docs/card.md#layout-tile--the-whole-fleet-on-one-line).

> Since 0.4.0 the card ships **inside the integration**, so HACS delivers it
> along with everything else and the integration serves and registers it
> itself — no copying, no resource to declare. YAML-mode dashboards still
> declare the resource by hand, since Home Assistant does not let an
> integration write to a YAML-managed resource list; that one line is in
> [docs/card.md](docs/card.md).

## Languages

Incident details, persistent notifications, the custom Lovelace card and the
entity names are translated. **English and French** ship today; anything else falls back to
English, key by key, so a partial translation is never worse than no
translation. Adding a language is one JSON file and no code — see
**[docs/translations.md](docs/translations.md)**.

Which language a surface speaks depends on *whose* it is:

| Surface | Language it uses |
| --- | --- |
| The `detail` field on each incident, persistent notifications, and entity names (`Slots Used`, `Online`, …) | the **installation's** — `hass.config.language` |
| The custom Lovelace card | the **viewer's** — their Home Assistant profile language |

So two people can read one dashboard in two languages at the same time. The
card is the only surface that varies per person, and deliberately so: `detail`
is a published attribute that user automations format their own push
notifications from, so it is rendered once, in the installation's language, and
the card prints it as it arrives rather than re-translating it.

Incident `kind` values (`deadlock`, `ghost_slot`, …) are machine identifiers and
are never translated — only the labels the card puts on them.

One consequence of translated entity names is Home Assistant's, not BlueSight's:
on an install in one of some forty languages, French among them, a **newly
created** entity gets its id from the translated name —
`sensor.proxy_salon_slots_utilises` rather than `..._slots_used`. Entities that
already exist keep the id they have. The card does not care either way: it finds
a proxy's entities through the entity registry, by what they are, and only falls
back to the `_slots_used` suffix on a Home Assistant too old to offer one. If you
list entities by hand with `proxies:`, use whatever ids your install gave them.

## How it works

BlueSight is deliberately split into pure logic and a thin Home Assistant
shell:

- **Pure logic** (`model.py`, `detector.py`, `window.py`, `storm_signal.py`,
  `availability.py`, `incident_policy.py`, `rendering.py`) takes plain snapshots
  of proxy slot state plus the rolling event windows and returns incidents,
  and turns the keys and parameters those incidents carry into prose. It imports
  no Home Assistant code and is unit-tested on its own.
- **`locale.py`** is the only other Home Assistant-free module, and the only one
  that touches the filesystem: it reads the string catalogues that
  `rendering.py` renders from. They live under the card's `www` tree, so the
  backend reads the very same files the card fetches over HTTP — one source of
  truth for both halves.
- **`adapter.py`** is the *only* module that touches the `habluetooth` manager
  (`async_current_allocations()` / `async_register_allocation_callback()` for
  slots, `async_current_scanners()` /
  `async_register_scanner_registration_callback()` for proxy health). All
  version-sensitive, semi-internal access is isolated here behind a stable
  interface, so the rest of the code never sees HA internals. This is the
  isolation the design calls out as `_internals`/adapter containment.
- **The coordinator** (`coordinator.py`) subscribes to the `habluetooth`
  allocation callback, maintains the sliding window, runs the detectors, and
  drives the entities and notifications.

Because the slot data comes from `habluetooth`'s structured allocation API, slot
visibility is **exact, not inferred** — precisely the data `#176516` lacks — and
the deadlock detector is an exact intersection of allocated lists across
proxies.

## Roadmap

- **v1 — detect + advise (shipped).** Read-only. Slots, deadlock, ghost, and
  storm detection; entities, notifications, and the card.
- **v1.2 — proxy health (shipped, 0.2.0).** Offline, stalled, and reboot-storm
  detection from the scanner registry; per-proxy online / last-seen entities.
- **0.3.0 — audit pass (shipped).** A rebuilt storm signal, an offline grace
  period and a `forget_proxy` action, diagnostics, and stable proxy naming.
- **0.4.0 — the card ships with the integration (shipped).** HACS delivers the
  Lovelace card, and the integration serves it and registers its dashboard
  resource itself — replacing two manual steps that failed silently.
- **0.5.0 — internationalisation (shipped).** Incident details, persistent
  notifications and the card follow the user's language; English and French
  ship. The backend renders in the installation's language, the card in the
  viewer's profile language.
- **0.6.0 — measured evidence, and who holds each slot (shipped).** An optional
  ESPHome component publishes what the Home Assistant API cannot see — SMP
  (pairing) failures and the proxy's own bond store — which upgrades storm
  detection from heuristic to measurement and adds two diagnoses: **bond lost**,
  and ghost slots judged by measured idle time for devices Home Assistant does
  not manage. Evidence is replaced **per proxy**, so a mixed fleet improves proxy
  by proxy and an unflashed proxy is judged exactly as it was before. The card
  gained a slot rack that names the device holding each slot. Two items from the
  v1.5 sketch did **not** ship: connection-reject counts, which remain a possible
  addition, and BLE RAM, which is node health rather than connection-layer
  evidence and which ESPHome already exposes on its own.
- **v2 — self-healing.** Guided, then automatic remediation: "free this slot" and
  guided re-pair, built on the proven v1 base.

## Known limitations

BlueSight is honest about its edges:

- **Storm detection is a best-effort heuristic on any proxy that does not
  measure it.** With HA-only data there are no raw SMP-failure counters, so
  BlueSight infers a failed connection from the only thing it can observe: a slot
  **released** while the device it belonged to is unavailable. A healthy poll
  cycle also releases its slot, but leaves its entities available, so it is not
  counted. It is a useful early warning, not a precise SMP tally. The optional
  [ESPHome component](docs/esphome-component.md) replaces it with a counted one —
  but **per proxy**, so on a mixed fleet the heuristic and every edge below are
  still live on each proxy that does not run it, and the integration keeps
  working with no proxy running it at all. Wherever the heuristic is what is
  available, it also inherits the ghost-slot limitation below: a device Home
  Assistant does not manage can never be judged as failing.
- **Ghost detection judges HA-managed devices from entity state, and everything
  else only where a proxy measures it.** Availability comes from the device's
  Home Assistant entities, so a slot held for a device that has no registry entry
  (an unmanaged BLE peripheral HA does not track) is treated as alive rather than
  flagged. This is deliberate — the alternative, advertisement presence,
  false-positives every healthy persistent connection. The optional
  [ESPHome component](docs/esphome-component.md) removes that blind spot on the
  proxies that run it, because it sees the connection itself and can measure how
  long it has been silent — for the slots Home Assistant holds there, which is
  the subset it judges; on a proxy without it, an unmanaged device is still
  never flagged. Measured silence is not proof either — a legitimately quiet link
  looks identical to a stuck one — which is why the idle threshold is a tunable
  with a floor rather than a constant. See
  [docs/esphome-component.md](docs/esphome-component.md).
- **The measured path has edges of its own.** On a proxy that counts SMP
  failures, those counts are the *only* thing feeding the storm window for the
  devices it holds — a released slot is no longer read as a failure there,
  because the same failed pairing would otherwise be counted twice. The price is
  that a connection which fails before pairing is ever attempted (a timeout, a
  device out of range) is not a storm event on that proxy: it is a **pairing**
  storm that is measured, and only that. A device using a resolvable
  private address is reported by the bond store, and by the pairing-failure
  event, under its *identity* address — which may not be the address habluetooth
  knows it by. The two then fail to correlate and **bond lost** simply does not
  fire: a false negative, never a false positive. And a proxy whose bond store is
  too large to fit in a Home Assistant entity state leaves the list unpublished
  rather than truncating it, because a truncated list reads as "unbonded", which
  is exactly what **bond lost** fires on. Both are detailed in
  [docs/esphome-component.md](docs/esphome-component.md).
- **The custom card needs a browser to eyeball.** The entities and notifications
  work headless, but the pip/feed visualisation is a dashboard card you have to
  look at.
- **Internal-API coupling is contained, not eliminated.** BlueSight rides the
  semi-public `habluetooth` allocation API. That coupling is isolated to
  `adapter.py`, so an upstream change touches one module — but a sufficiently
  large `habluetooth` change could still require an adapter update.
- **Plurals only distinguish one from many.** The string catalogue has a
  singular and a plural form per counted message, which is all English and
  French need. Languages with richer plural rules — Polish, Russian, Arabic and
  others — cannot be translated correctly until the renderer learns their
  categories. Adding such a language is a code change, not just a catalogue.

## Support

Open an issue here for anything about this integration — a bug, a question, or a
feature request. Forum threads are for general discussion and user-to-user help;
nothing raised there is tracked, and it can be lost. An issue cannot.

Before you open one, read [Known limitations](#known-limitations). Several of the behaviours
reported as bugs are heuristics working as designed, and the section says which.

To get a useful answer on the first exchange, include:

- your Home Assistant version and the version of this integration;
- the adapters and ESPHome proxies involved — board, ESPHome version, and how
  many of each;
- the diagnostics download (Settings → Devices & services → BlueSight → **⋮** →
  *Download diagnostics*);
- a debug log, plus what you did, what you expected, and what happened instead.

### Staying informed

New versions are announced here and nowhere else. To hear about one:

- **HACS already offers you the update**, release notes included — nothing to do;
- subscribe to `https://github.com/dasimon135/ha-bluesight/releases.atom` in any
  RSS reader, or inside Home Assistant through the `feedreader` integration;
- or use **Watch → Custom → Releases** on this repository.

## License

[MIT](LICENSE) © 2026 David Simon.
