# community.home-assistant.io announcement — launch (English)

> **Body of the live thread, current as of v0.6.5.**
>
> The thread is already posted and this is its opening post:
> https://community.home-assistant.io/t/bluesight-see-whats-actually-holding-your-bluetooth-proxy-slots/1022461
> (posted 2026-08-24). Update it by **editing that post**, not by starting a new
> thread — a reader lands on the opening post, so a correction added as a reply
> is a correction almost nobody sees. That already happened once: the card
> section below was wrong from 0.4.0 and was fixed in a reply two days later,
> while the opening post kept saying it.
>
> The French HACF version is in `post-hacf.md`.

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

It reads the per-proxy slot allocations Home Assistant already tracks internally, and turns them into seven diagnostics.

**Device-side:**

| Incident | Fires when |
| --- | --- |
| **Deadlock** | the same address is allocated on **two or more distinct** proxies at once. A BLE peripheral can only be connected to one central, so the extras are stale allocations that will never be released. This is a real, documented core bug — [issue #176516](https://github.com/home-assistant/core/issues/176516) — and the only method offered in that thread is "enable debug logs and read them by hand". |
| **Ghost slot** | a proxy still holds a slot for a device whose Home Assistant entities have **all** gone `unavailable`. The slot is spent on a connection that does nothing. |
| **Pairing storm** | a device produces repeated connection failures in a burst, churning slots and destabilising the proxy. |
| **Missing pairing key** | a device's pairing keeps being refused by a proxy whose own bond store holds no key for it. Needs the optional firmware below — Home Assistant can see neither half. |

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
- a Lovelace card drawing each proxy's slots as pips, naming the device holding each one, with a live incident feed

The slot data comes from `habluetooth`'s structured allocation API, so **slot visibility is exact, not inferred** — precisely the data #176516 lacks — and the deadlock detector is a plain intersection of allocated lists across proxies.

**Everything is read-only.** BlueSight never frees a slot, forces an unbond, or reflashes anything. It observes and advises.

### Optional: measured evidence instead of heuristics

Two of the diagnostics above are guesses when all you have is the Home Assistant API, because HA cannot see SMP (pairing) failures at all and cannot read a proxy's bond store.

So there is an optional **ESPHome component** you can add to proxies you already run. Two blocks pasted into a config you already have, no change to the radio or the connection count — it is a passive observer on the BLE event stream, and it consumes no slot:

```yaml
external_components:
  - source: github://dasimon135/ha-bluesight@v0.6.5
    components: [bluesight]

bluesight:
```

It publishes three things Home Assistant cannot see: **SMP failure counts**, the proxy's **NVS bond store**, and **per-connection idle time**. That upgrades pairing-storm detection from a heuristic to a count, adds the missing-pairing-key diagnosis with an exact remedy — *re-pair through this specific proxy*, because bonds are per-central and pairing through whichever proxy HA picks next will not help — and lets a ghost slot be judged for a device Home Assistant does not manage at all.

The important part for a mixed fleet: evidence is replaced **per proxy**. A proxy running it is judged on measurements, one that is not keeps the heuristic, and the integration works perfectly well with no proxy running it. You can flash one node and see the difference before deciding.

### What it found on my own setup

The day I finished 0.3.0, it surfaced — within minutes, on my own install — a ghost slot on a Daikin BRC1H thermostat and two pairing storms across two of my four thermostats. Those entities had been `unavailable` for a while and I had no idea why.

One telling detail: the ghost slot moved to a different proxy between two snapshots. The device was bouncing from proxy to proxy as it was retried in a loop — the classic storm signature, and exactly the kind of thing that is invisible without this.

More recently it found a thermostat that had been unreachable for hours because one proxy kept being handed connections for a device it held no pairing key for, while the proxy that *did* hold the key sat completely idle. That one is unfindable by hand.

### It has also been wrong, and that is the part I want help with

Twice in one week, on my own fleet, BlueSight reported a fault on something perfectly healthy.

A thermostat connected and exchanging normally through the proxy holding its bond was reported as needing a re-pair, because a *different* proxy had refused it at some point in the past — the count was the firmware's lifetime counter, so the incident could never clear. 0.6.4 made it a count of failures inside a rolling window, so a fault that stops being true stops being reported.

The other was a BLE Mesh proxy link: healthy, but silent for nine hours because nothing on the mesh had changed, and invisible to Home Assistant's device registry so nothing could corroborate the silence. Flagged as a stuck slot.

Both were the same class of mistake, and it is the risk that matters for a tool like this: **an alarm on something that is fine teaches you to ignore the tool**, and you never get that back. I would rather hear about one false positive than ten confirmations.

### Requirements

- **Home Assistant ≥ 2025.7** — the slot-allocation API arrived in 2025.2, but the proxy-health layer also needs habluetooth's scanner-registration callbacks, which landed later.
- **One or more ESPHome Bluetooth proxies** (or local adapters). With a single one you still get slot visibility plus ghost and storm detection; deadlock detection only means something across two or more.

No configuration — it reads what HA already knows. Nothing to change on the ESPHome side unless you want the optional component above.

Interface and notifications follow your language; English and French ship. The backend renders in the installation's language, the card in each viewer's profile language.

### Installing

Not in the HACS default store yet — add `https://github.com/dasimon135/ha-bluesight` as a **custom repository, category Integration**, then restart and add the integration from Settings → Devices & Services.

The card ships with the integration and registers its own dashboard resource, so there is nothing to copy and no resource to add by hand. (That was two manual steps until 0.4.0, and both failed silently — nothing errored, the card simply never appeared. If you would rather not run custom JavaScript at all, [docs/card.md](https://github.com/dasimon135/ha-bluesight/blob/main/docs/card.md) has a native-card equivalent you can paste.)

### Known limits

Being upfront about the edges:

- **Storm detection is a best-effort heuristic on any proxy that does not measure it.** With HA-only data there are no raw SMP counters, so a failed connection is inferred from the only observable thing: a slot **released while the device it belonged to is unavailable**. A healthy poll cycle also releases its slot but leaves its entities available, so it is not counted. Useful early warning, not a precise tally. The optional component replaces it with a counted one — per proxy, so on a mixed fleet the heuristic is still live on every node that does not run it.
- **Ghost detection judges Home Assistant devices from entity state, and everything else only where a proxy measures it.** A slot held for a peripheral HA does not track is treated as alive rather than flagged — deliberately, since the alternative signal (advertisement presence) false-positives on every healthy persistent connection. Where a proxy runs the component it can measure the silence instead, though measured silence is not proof either: a legitimately quiet link looks identical to a stuck one, which is why the idle threshold is a tunable with a floor and not a constant.
- **Read-only by design.** Guided remediation comes later — see below.

### Roadmap

The optional ESPHome component was the previous roadmap entry and shipped in 0.6.0. Two items from its sketch did not: connection-reject counts, still possible, and BLE RAM, which is node health rather than connection-layer evidence and which ESPHome already exposes on its own.

Next is guided remediation — "free this slot", assisted re-pair. I am deliberately **not** starting it yet. It acts on your Bluetooth stack based on a verdict, and the two false positives above happened in a single week on the only fleet I can test. That base needs to be right on other people's installations before anything is allowed to act on it.

### Feedback wanted

I would especially like to hear about:

- **false positives** — the main risk with this kind of tool, and the thing I cannot find alone. Especially on BLE devices Home Assistant does not manage, where the only evidence is how long a connection has been quiet.
- **larger multi-proxy installations**, which I cannot test myself. Everything here is tuned against four proxies in one house.
- which Bluetooth devices hold slots hostage for you; I suspect this is not just a Daikin story.

If you have Bluetooth devices going `unavailable` with no explanation, this is worth a look. Feedback and issues very welcome — here or on [GitHub Issues](https://github.com/dasimon135/ha-bluesight/issues). 🔵
