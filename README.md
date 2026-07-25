# BlueSight

Make the **connection layer** of Home Assistant's Bluetooth visible. BlueSight
shows how many GATT connection slots each ESPHome Bluetooth proxy is using, who
is holding them, and — the point — flags the failure modes that today leave you
staring at `unavailable` devices with no explanation: slot-leak **deadlocks**,
**ghost slots**, and **pairing storms**.

> **Status:** v1 — read-only diagnostics. BlueSight detects and advises; it
> never touches your proxies or bonds. Installs through HACS with no reflash.

## The problem

Home Assistant's Bluetooth stack has two layers. The **visibility** layer —
which devices each proxy can *see* — is well served: HA 2025.2 added an
[Advertisement Monitor](https://www.home-assistant.io/integrations/bluetooth/)
(Settings → Devices → Bluetooth → Configure) that lists what each proxy hears.

The **connection** layer — the finite pool of GATT slots a proxy can actually
*connect* through — is invisible. And that is where things break:

- **Slot-leak deadlock** — one misbehaving device grabs a slot on *every* proxy
  and never releases it. The shared pool deadlocks, and unrelated devices go
  `unavailable` with no error. This is a real, documented failure: core issue
  [home-assistant/core#176516](https://github.com/home-assistant/core/issues/176516).
  There is no diagnostic tool for it — the only method offered in the thread is
  "enable debug logs" and read them by hand.
- **Ghost slot** — a proxy still reports a device as holding a slot while that
  device is dead: every one of its Home Assistant entities has gone
  `unavailable`. The slot is spent on a connection that is no longer doing
  anything.
- **Pairing storm** — a device fails to bond over and over (SMP failures /
  connection rejects) in a tight burst, churning slots and destabilising the
  proxy.

The Advertisement Monitor cannot show any of this, because it covers *what a
proxy sees*, not *what a proxy is connected to*. BlueSight fills exactly that
gap.

## What it does (v1)

Three detectors, running over the exact per-proxy slot allocations Home
Assistant already tracks internally:

| Detector | Fires when |
| --- | --- |
| **Deadlock** (`#176516`) | the same device address is allocated on **N or more** proxies at once — a stale duplicate allocation across the pool. |
| **Ghost slot** | an address is in a proxy's allocated list while its Home Assistant device is dead — the device is found in the registry (by MAC in `connections` or `identifiers`) and **all** its entities are `unavailable`. Availability is judged from entity state, not advertising: a connected device stops advertising, so advertisement presence would false-positive every healthy persistent connection. Unmanaged devices (no registry entry) are never flagged — see [Limitations](#limitations). |
| **Pairing storm** | a device produces a burst of availability flaps beyond the configured threshold inside the storm window (best-effort heuristic — see [Limitations](#limitations)). |

It surfaces the state as:

- **Per-proxy sensors** — `sensor.<proxy>_slots_used` and
  `sensor.<proxy>_slots_free`, with the total, free count, and the list of
  allocated device addresses as attributes.
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

## Requirements

- **Home Assistant ≥ 2025.2** — this is when the `habluetooth` slot-allocation
  API BlueSight rides became available.
- **One or more ESPHome Bluetooth proxies** (or local adapters) that expose
  connection slots. With a single adapter you still get slot visibility and
  ghost/storm detection; the deadlock detector is most meaningful across
  multiple proxies.

## Install

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
| Storm window | 300 s | sliding window over which storm flaps are counted (min 30 s). |
| Storm threshold | 5 | flaps within the window that trip a storm incident (min 2). |
| Poll interval | 30 s | how often the coordinator refreshes its slot snapshot (min 5 s). |

## Entities

| Entity | Type | State | Key attributes |
| --- | --- | --- | --- |
| `sensor.<proxy>_slots_used` | sensor | slots allocated on that proxy | `total`, `free`, `allocated` (list of MACs), `source` |
| `sensor.<proxy>_slots_free` | sensor | slots still free on that proxy | — |
| `binary_sensor.bluesight_incident` | binary_sensor (`problem`) | `on` when any incident is open | `incident_count`, `incidents` (list of `{kind, address, sources, detail}`; `kind` ∈ `deadlock` / `ghost_slot` / `storm`) |

Each proxy is registered as its own Home Assistant device carrying its two slot
sensors; the incident binary sensor lives on a single **BlueSight** service
device.

## Dashboard

BlueSight ships an optional custom Lovelace card plus a native-card fallback
you can paste with no custom JavaScript. Both read the entities above. Full
setup — resource registration, the `custom:bluesight-card` config, and the
native fallback YAML — is in **[docs/card.md](docs/card.md)**.

## How it works

BlueSight is deliberately split into pure logic and a thin Home Assistant
shell:

- **Pure detectors** (`model.py`, `detector.py`, `window.py`,
  `incident_policy.py`) take plain snapshots of proxy slot state plus a rolling
  event window and return incidents. They import no Home Assistant code and are
  unit-tested on their own.
- **`adapter.py`** is the *only* module that touches the `habluetooth` manager
  (`get_manager().async_current_allocations()` /
  `async_register_allocation_callback()`). All version-sensitive, semi-internal
  access is isolated here behind a stable interface, so the rest of the code
  never sees HA internals. This is the isolation the design calls out as
  `_internals`/adapter containment.
- **The coordinator** (`coordinator.py`) subscribes to the `habluetooth`
  allocation callback, maintains the sliding window, runs the detectors, and
  drives the entities and notifications.

Because the slot data comes from `habluetooth`'s structured allocation API, slot
visibility is **exact, not inferred** — precisely the data `#176516` lacks — and
the deadlock detector is an exact intersection of allocated lists across
proxies.

## Roadmap

- **v1 — detect + advise (this release).** Read-only. Slots, deadlock, ghost,
  and storm detection; entities, notifications, and the card.
- **v1.5 — optional ESPHome component.** An auto-detected custom component on the
  proxy exposing raw telemetry the HA API cannot: NimBLE SMP-fail counts,
  connection rejects, BLE RAM, and bond state. This upgrades storm detection from
  the v1 heuristic to real SMP evidence. Additive — v1 keeps working without it.
- **v2 — self-healing.** Guided, then automatic remediation: "free this slot" and
  guided re-pair, built on the proven v1 base.

## Limitations

BlueSight v1 is honest about its edges:

- **Storm detection is a best-effort heuristic.** With HA-only data there are no
  raw SMP-failure counters, so v1 infers storms from availability flaps within
  the window. It is a useful early warning, not a precise SMP tally; the v1.5
  ESPHome component is what upgrades it to real bond-failure telemetry.
- **Ghost detection only judges HA-managed devices.** Availability comes from
  the device's Home Assistant entities, so a slot held for a device that has no
  registry entry (an unmanaged BLE peripheral HA does not track) is treated as
  alive rather than flagged. This is deliberate — the alternative, advertisement
  presence, false-positives every healthy persistent connection. Robust
  stale-slot detection for unmanaged devices lands with the v1.5 ESPHome
  component, which sees the connection directly.
- **The custom card needs a browser to eyeball.** The entities and notifications
  work headless, but the pip/feed visualisation is a dashboard card you have to
  look at.
- **Internal-API coupling is contained, not eliminated.** BlueSight rides the
  semi-public `habluetooth` allocation API. That coupling is isolated to
  `adapter.py`, so an upstream change touches one module — but a sufficiently
  large `habluetooth` change could still require an adapter update.

## License

[MIT](LICENSE) © 2026 David Simon.
