# community.home-assistant.io announcement — launch (English)

> **Launch post, current as of v0.3.1.** The project had never been announced
> before this. The French HACF version is in `post-hacf.md`.

---

## 🔍 BlueSight — see what's actually holding your Bluetooth proxy slots

Hi everyone,

I've published a custom integration for a problem that cost me a lot of time and for which I could not find any existing tool.

**Repo:** https://github.com/dasimon135/ha-bluesight

### The blind spot

Home Assistant's Bluetooth stack has two layers, and you can only see one of them.

The **visibility** layer — which devices each proxy can *hear* — is well served. HA 2025.2 added the [Advertisement Monitor](https://www.home-assistant.io/integrations/bluetooth/) (Settings → Devices → Bluetooth → Configure), and it does its job.

The **connection** layer is invisible. An ESP32 proxy holds **three** GATT connections by default. Those slots are a shared pool, and nothing in Home Assistant tells you who is occupying them.

That is where things break. When a slot is held by a device that stopped responding, it is never released. Your other Bluetooth devices go `unavailable` — no error, no warning, nothing. You restart a proxy at random, it works for a while, it happens again.

### What BlueSight does

It reads the per-proxy slot allocations Home Assistant already tracks internally, and turns them into six diagnostics.

**Device-side:**

| Incident | Fires when |
| --- | --- |
| **Deadlock** | the same address is allocated on **two or more distinct** proxies at once. A BLE peripheral can only be connected to one central, so the extras are stale allocations that will never be released. This is a real, documented core bug — [issue #176516](https://github.com/home-assistant/core/issues/176516) — and the only method offered in that thread is "enable debug logs and read them by hand". |
| **Ghost slot** | a proxy still holds a slot for a device whose Home Assistant entities have **all** gone `unavailable`. The slot is spent on a connection that does nothing. |
| **Pairing storm** | a device produces repeated connection failures in a burst, churning slots and destabilising the proxy. |

**Proxy-side:**

| Incident | Fires when |
| --- | --- |
| **Proxy offline** | a proxy that was online dropped off the bus. |
| **Proxy stalled** | still online, but has heard no advertisement for a while — up but deaf. |
| **Proxy reboot storm** | it registers and unregisters over and over. Usually a failing power supply. |

Everything surfaces as ordinary entities, plus a readable persistent notification when an incident opens — with the physical action that clears it, not just an error code. The notification dismisses itself when the incident resolves.

You get:

- per proxy: `sensor.<proxy>_slots_used` / `_slots_free`, with `total`, `free` and the list of allocated addresses as attributes
- per proxy: `binary_sensor.<proxy>_online` and `sensor.<proxy>_last_device_seen`
- a global `binary_sensor.bluesight_incident` carrying every open incident in its attributes
- a full **diagnostics dump** to attach to a bug report
- an optional Lovelace card drawing each proxy's slots as pips, with a live incident feed

The slot data comes from `habluetooth`'s structured allocation API, so **slot visibility is exact, not inferred** — precisely the data #176516 lacks — and the deadlock detector is a plain intersection of allocated lists across proxies.

**Everything is read-only.** BlueSight never frees a slot, forces an unbond, or reflashes anything. It observes and advises.

### What it found on my own setup

The day I finished 0.3.0, it surfaced — within minutes, on my own install — a ghost slot on a Daikin BRC1H thermostat and two pairing storms across two of my four thermostats. Those entities had been `unavailable` for a while and I had no idea why.

One telling detail: the ghost slot moved to a different proxy between two snapshots. The device was bouncing from proxy to proxy as it was retried in a loop — the classic storm signature, and exactly the kind of thing that is invisible without this.

### Requirements

- **Home Assistant ≥ 2025.7** — the slot-allocation API arrived in 2025.2, but the proxy-health layer also needs habluetooth's scanner-registration callbacks, which landed later.
- **One or more ESPHome Bluetooth proxies** (or local adapters). With a single one you still get slot visibility plus ghost and storm detection; deadlock detection only means something across two or more.

No configuration — it reads what HA already knows. Nothing to change on the ESPHome side, no reflash.

### Installing

Not in the HACS default store yet — add `https://github.com/dasimon135/ha-bluesight` as a **custom repository, category Integration**, then restart and add the integration from Settings → Devices & Services.

⚠️ **The Lovelace card is NOT installed by HACS.** HACS only handles `www/` assets for repositories in the *Lovelace/plugin* category, and BlueSight is an integration — so you get `custom_components/bluesight/` and nothing else. Copying the card and registering its resource are two manual steps, documented in [docs/card.md](https://github.com/dasimon135/ha-bluesight/blob/main/docs/card.md). Worth flagging because the failure is silent: nothing errors, the card simply never appears. I caught myself out with it.

If you would rather not run custom JavaScript, the same page has a native-card equivalent you can paste.

### Known limits

Being upfront about the edges:

- **Storm detection is a best-effort heuristic.** HA exposes no raw SMP-failure counters, so a failed connection is inferred from the only observable thing: a slot **released while the device it belonged to is unavailable**. A healthy poll cycle also releases its slot, but leaves its entities available, so it is not counted. Useful early warning, not a precise tally.
- **Ghost detection only judges HA-managed devices.** A slot held for a peripheral Home Assistant does not track cannot be judged, so it is treated as alive rather than flagged.
- **Read-only by design.** Guided remediation comes later.

### Roadmap

Next up is an optional ESPHome component for the proxies themselves, exposing what the HA API cannot: real SMP-failure counts, connection rejects, BLE RAM, bond state. That upgrades storm detection from the current heuristic to actual evidence. After that, guided remediation — "free this slot" and assisted re-pair.

### Feedback wanted

I would especially like to hear about:

- what it detects on your setup — **including and especially false positives**, which are the main risk with this kind of tool
- larger multi-proxy installations, which I cannot test myself
- which Bluetooth devices hold slots hostage for you; I suspect this is not just a Daikin story

If you have Bluetooth devices going `unavailable` with no explanation, this is worth a look. Feedback and issues very welcome — here or on [GitHub Issues](https://github.com/dasimon135/ha-bluesight/issues). 🔵
