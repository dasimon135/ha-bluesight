# The BlueSight ESPHome component

An optional addition to a Bluetooth proxy you already have. It publishes three
text sensors of raw facts that Home Assistant cannot obtain any other way, and
BlueSight turns them into two diagnoses it otherwise cannot reach.

## You do not need this

BlueSight is complete without it. Every detector works with no firmware change,
and this page exists for people who have already hit one of its two blind spots.

Nothing degrades if you skip it, and nothing degrades on the proxies you do not
flash. Evidence is chosen **per proxy**: a proxy running the component is judged
by measurement, and every other proxy in the same fleet is judged exactly as it
was before, by the same heuristics, in the same snapshot. A mixed fleet is the
expected state, not a transitional one — put the component on the proxy you are
actually suspicious of and leave the rest alone.

It changes nothing about the radio, the connection count, or the proxy's
behaviour. The component registers as a passive observer on the BLE event stream
that ESPHome already fans out to `bluetooth_proxy`; it opens no connection,
writes and clears no bond, and never calls into `bluetooth_proxy`. BlueSight's
read-only invariant holds all the way into the firmware.

## What it measures, and what it does not

Two facts are structurally invisible to Home Assistant:

- **Why a pairing failed.** The reason arrives as a `BleakError` raised to
  whichever integration owns the connection. It is an exception on a call stack,
  not state; nothing writes it anywhere BlueSight could read it.
- **What is in a proxy's bond store.** It lives in that proxy's NVS flash. The
  ESPHome API does not expose it, so no amount of cleverness on the Home
  Assistant side can see it.

A third is merely unavailable: **how long a GATT connection has been silent**.
habluetooth knows which slots are allocated, but not whether anything is flowing
through them.

The component measures those three and stops there. It holds no threshold and
reaches no verdict — it counts events and formats three strings. Every judgement
lives in the integration, where pytest can reach it and where retuning is an
options change rather than a reflash.

It deliberately does **not** publish RAM, Wi-Fi signal, or uptime. Those describe
the health of a node rather than of the connection layer, nothing in BlueSight
would consume them, and ESPHome already exposes all three directly if you want
them on a dashboard.

## Install

The canonical, copy-pasteable version of this section is
[`esphome/bluesight-example.yaml`](../esphome/bluesight-example.yaml), which is
the file to trust if the two ever disagree.

### 1. Pull the component in

```yaml
external_components:
  - source: github://dasimon135/ha-bluesight@v0.6.1
    components: [bluesight]
```

Pin the tag that matches your installed BlueSight version. The firmware and the
integration share one wire format and ship from one repository precisely so that
the two cannot drift; `@main` tracks the newest format instead, which is what you
want while developing and not what you want on a proxy in a cupboard.

### 2. Turn it on

```yaml
bluesight:
```

One line. That is the whole configuration, and the three sensors are named for
you in codegen.

There is one option, and its default is almost always right:

```yaml
bluesight:
  update_interval: 60s
```

`update_interval` is a **floor on staleness, not a sampling rate**. An SMP
failure, and a slot opening or closing, publish the moment they happen. The tick
exists so that an idle time keeps advancing while nothing at all is happening,
and so that a bond added or removed out of band is eventually noticed.

You cannot rename the three sensors: a `name:` override is rejected at
validation time with an explanation. This is not tidiness. The integration
discovers them by the entity registry's `original_name`, so a rename produces a
proxy that compiles cleanly, boots cleanly, reports cleanly — and is invisible to
BlueSight, a failure with no symptom to follow. Rename the entity in Home
Assistant instead; discovery matches the original name and survives that.

### 3. What must already be present (and almost certainly is)

`esp32_ble:` is a hard requirement — it is where the GAP and GATTC event streams
are fanned out from. Every Bluetooth proxy has it, usually created for you by the
`esphome.bluetooth-proxy` package. Do not add a second one.

The slots sensor additionally needs the BLE *client* stack (`esp32_ble_tracker`,
which `bluetooth_proxy` always pulls in). On a scan-only node that sensor is not
created at all, and the integration reads its absence as "not observable" rather
than as "zero connections". SMP failures and bonds still work there.

## The three sensors

All three are diagnostic entities on the proxy's own ESPHome device.

| Sensor | Example state | Format |
| --- | --- | --- |
| `BlueSight SMP failures` | `aabbccddeeff:3,001122334455:1` | `address:count`, comma-separated. A count of failed pairings per address since boot. |
| `BlueSight bonds` | `aabbccddeeff,001122334455` | addresses, comma-separated. The proxy's own NVS bond store. |
| `BlueSight slots` | `aabbccddeeff:240.0` | `address:seconds.tenths` idle, comma-separated. One entry per GATT client connection the node currently holds. |

Addresses arrive as compact 12-character hex because Home Assistant caps an
entity state at 255 characters, and dropping the colons is what keeps a full bond
list inside that cap. The integration expands them, and normalises both sides of
every comparison, so they correlate with the addresses habluetooth speaks.

Note that **`BlueSight slots` lists every GATT client connection on the node**,
not only the ones Home Assistant asked for. If the same node also runs
`ble_client:` entries of its own, their connections appear here too. That is
faithful reporting — those links are real and they do consume the controller's
budget. It is not what gets judged: the integration reads an idle time only for
an address habluetooth reports as an allocated slot on that proxy, so a
`ble_client:` link shows up in the sensor state and can never be mistaken for a
stuck Home Assistant slot. See
[Ghost slots judged by idle time](#ghost-slots-judged-by-idle-time).

### Empty is not `unknown`, and that survives the trip

The whole wire contract rests on one distinction:

- **An empty state (`""`)** means *reporting, nothing to report*. Zero bonds,
  zero SMP failures, zero connections.
- **`unknown` (or `unavailable`)** means *not reporting*. A rebooting proxy, a
  bond store that could not be read, or a node that never had the sensor.

The integration treats these very differently and never reads one as the other.
It matters most for bonds: an absent bond list cannot distinguish "this proxy
holds no bond for that device" from "this proxy did not tell me", so **bond
lost** yields nothing at all rather than a confident wrong answer. The firmware
holds up its end — when the bond store cannot be read it publishes *nothing*,
leaving the sensor `unknown`, rather than publishing `""` and thereby claiming
the proxy holds no bonds.

This has been checked on hardware rather than assumed. A proxy running the
component reported:

| Sensor | State |
| --- | --- |
| `BlueSight bonds` | `1c549e90e30e` |
| `BlueSight slots` | `""` |
| `BlueSight SMP failures` | `""` |

An empty string published by ESPHome arrives in Home Assistant as an empty state
and not as `unknown`, so the two ends of the contract agree in practice and not
only in principle. It is the kind of thing that would otherwise be reasonable to
wonder about, since plenty of integrations do collapse the two.

## What it makes possible

### Bond lost

**Fires when** a device's pairing keeps failing on a proxy whose own bond store
holds no entry for it. Both halves are required, and both must be *reported*.

This is the one diagnosis that genuinely needs firmware, because Home Assistant
can see neither half. It is also the one whose remedy is exact, which is the
point of it: **re-pair through that specific proxy**.

Bonds are stored per central. Every proxy keeps its own store, so a device paired
through one proxy genuinely has no bond on the next, and Home Assistant will
still happily route a connection there. The verdict is therefore per proxy: a
bond held elsewhere neither excuses nor suppresses the proxy that is failing, and
pairing through whichever proxy Home Assistant picks next will not fix it.
Without this diagnosis the symptom is a device that paired once, works, and then
intermittently refuses to connect for no visible reason.

`bond_lost` is a new incident `kind` on `binary_sensor.bluesight_incident`. It
renders **amber** on the card, not red: red is reserved for incidents that waste
a scarce resource — a connection slot held for nothing — and a lost bond holds no
slot. It is a device that cannot connect, which is bad, and differently bad.

### Ghost slots judged by idle time

**Fires when** a slot **Home Assistant holds on that proxy** goes without GATT
traffic for longer than `idle_threshold_s`, for a device Home Assistant does not
manage.

`detect_ghost_slots` decides from entity availability, which only works for
devices in the registry. An unmanaged peripheral is conservatively treated as
alive there — deliberately, since the alternative signal, advertisement presence,
false-positives every healthy persistent connection. The result is a blind spot
exactly where it hurts: something Home Assistant knows nothing about is spending
one of a handful of connection slots, and nothing can judge whether the link is
still alive.

The firmware sees the connection itself, so it can measure the silence directly.
This raises an ordinary `ghost_slot` incident with different wording — a new
source of evidence, not a new kind.

Two conditions bound it, and both matter:

- **The address must be an allocated slot on that proxy.** The sensor reports
  every GATT client connection on the node, and only some of them are Home
  Assistant's. A `ble_client:` link the node opened for itself draws on
  `esp32_ble.max_connections`, not on the slots the proxy advertises, so calling
  it a stuck *slot* would be a true measurement under a false frame — the remedy
  would tell you to restart a proxy to free something the restart would not free.
  Nothing is lost by the restriction: habluetooth tracks an allocation per
  address and knows nothing of the device registry, so a slot Home Assistant
  opened for a device it cannot name is allocated all the same — which is exactly
  the case this detector exists for.
- **The device must be one Home Assistant cannot judge.** Devices in the registry
  are left to the entity-based verdict, which is the more semantic signal;
  letting both detectors judge one slot would draw one fault twice.

#### Choosing `idle_threshold_s`

Default 1800 s (30 minutes), floor 60 s, in the integration's **Configure**
dialog.

Measured silence is not proof of a stuck slot. A legitimately quiet link looks
identical to a dead one, which is why this is a tunable rather than a constant,
and why it should sit **above the slowest legitimate quiet period on your
network**.

A real example of the false positive it exists to exclude. One proxy here sits at
3/3 with a slot held by `C3:EB:49:65:67:55` — a BLE Mesh node, connected
permanently by a `bluetooth_mesh` integration with `keepalive_seconds: 0`. It
appears nowhere in Home Assistant, because BLE Mesh addresses nodes by mesh
unicast address: the MAC is discovered at runtime and never written down
anywhere. Its address is a *random static* address (top two bits `0b11`), so
there is not even a manufacturer to look up. It is precisely the shape of device
idle-slot detection was built for — and it is perfectly healthy. A mesh proxy
link carries GATT traffic only when something on the mesh changes, so it can sit
silent for hours between one light being switched and the next.

That example is easy to dismiss as exotic. The second one is not, and it is what
set the default. A **Daikin Madoka BRC1H thermostat** — an ordinary device on
ordinary hardware, connected and working normally — reported `430.7` s of GATT
silence on a live proxy here:

```json
"slot_idle_seconds": {"1C:54:9E:90:E3:0E": 430.7}
```

Nothing was flagged, for one reason only: that thermostat is in Home Assistant's
device registry, so this detector stands down for it and leaves the verdict to
`detect_ghost_slots`. An equivalent device *absent* from the registry — which is
the entire population this detector judges — would have been reported as a ghost
slot at the 300 s this originally defaulted to, while perfectly healthy. That is
why the default is 1800 s: not an argument, a measurement.

The asymmetry decides which way to err. A genuinely stuck slot is stuck
indefinitely, so hearing about it 25 minutes later costs nothing you can act on.
A false positive on the first day teaches you to ignore a diagnostic
integration, and that is not recovered. 1800 s is still not a universal answer —
the mesh node above beats it comfortably — which is exactly why this is a
tunable and not a constant. The default only has to avoid crying wolf on a
typical install.

So the threshold is not a detection sensitivity to be turned up. It is an
assertion about your quietest device, and getting it wrong reports healthy
connections as ghosts. The 60 s floor exists because `detect_idle_slots` carries
no internal guard by design — it flags every slot whose idle reading strictly
exceeds the threshold — so an unbounded 0 would report the entire connected fleet
at once. The floor also sits above the routine silence of a healthy GATT link,
and above Home Assistant's own Bluetooth stack, which does not consider a device
stale for 60-90 s either. It is deliberately higher than the 30 s floor on the
stalled threshold: that one measures advertisement silence, which every device
breaks every few seconds, where this one measures GATT traffic, which a healthy
connection may go minutes without.

If in doubt, raise it. A genuinely stuck slot stays stuck and will still be
reported.

### Storm detection stops guessing

Without the component, a "failed connection" is inferred from the only observable
proxy for one: a slot **released** while the device it belonged to is
unavailable. Useful, but not a tally of anything.

With it, measured SMP failures feed the same rolling window and the same storm
concept — only the evidence differs. The replacement is **per proxy**: a flashed
proxy contributes measurements while an unflashed one keeps contributing inferred
failures, in the same fleet and the same snapshot.

`Incident.evidence` records which was used (`"smp"` or `"heuristic"`). It is
**not** published as an entity attribute — the card distinguishes the two already
by naming the measuring proxies in `sources`, and the heuristic can name nobody.
It does appear in the diagnostics dump.

## Verifying it works

1. **The proxy is publishing.** After flashing, open the ESPHome device in Home
   Assistant. Three diagnostic entities named `BlueSight SMP failures`,
   `BlueSight bonds` and `BlueSight slots` should exist. On a healthy, idle proxy
   expect the first to be empty, the last to list one entry per current
   connection, and the middle one to list the devices you have paired through
   this proxy.
2. **Empty, not `unknown`.** In Developer Tools → States, an empty state is
   correct and means "reporting, nothing to report". `unknown` on all three after
   a minute or so means the component is not running. `unknown` on bonds alone in
   the first ten seconds after a boot is expected, because the bond store cannot
   be read until Bluedroid is enabled.
3. **No slots sensor at all?** That is a scan-only node: it has no BLE client
   stack, so connections cannot be observed, and ESPHome says so in a warning at
   compile time. SMP failures and bonds are unaffected.
4. **The integration is consuming it.** Download the integration's diagnostics
   (**⋮ → Download diagnostics**) and read the `telemetry` section. Your proxy
   should appear under `reporting`, with its own `source` MAC and the reading it
   sent. Each of the three signals is labelled in `signals`: `reporting` means
   the sensor was read (even if the value is empty — an empty bond list is a
   real answer), `absent` means nothing was read for it. This answers the
   question directly, without waiting for an incident to happen.

   `counter_baselines` is the SMP counter value BlueSight is measuring from.
   Failures are only counted *above* the baseline, which is why a proxy that has
   been failing since before Home Assistant started reports nothing until the
   next failure.

   Separately, every incident carries an `evidence` field; on a `storm`, `"smp"`
   means a measurement was used and `"heuristic"` means it was inferred. That is
   the only place *that* distinction is visible.
5. **Nothing matched?** A proxy that reported nothing is listed under
   `silent_sources` in the same section — it is named there rather than left
   out, so "my proxy is missing" and "my proxy has nothing to say" do not look
   alike. Discovery joins the telemetry to a proxy by the ESPHome device's
   network MAC, which is also how habluetooth identifies the scanner. If a
   proxy's telemetry never appears, check that the three sensors sit on the same
   Home Assistant device as the rest of that node's entities, and that you have
   not renamed them in ESPHome.

Waiting for a real incident is not a good test, and deliberately provoking one is
worse — un-pairing a device to see `bond_lost` fire leaves you with an un-paired
device. The sensors themselves are the check.

## Limitations

- **Resolvable private addresses can hide a lost bond.** The bond store, and the
  address carried on a pairing-failure event, report the **identity** address of
  a resolved peer. For a privacy-enabled device that may differ from the address
  habluetooth knows it by. The two then fail to correlate and `bond_lost` simply
  does not fire. This is a false negative and never a false positive: the
  detector needs an SMP failure *and* a reported bond list that omits that
  address, and an address mismatch loses the failure rather than inventing one.
- **A bond list too long for an entity state is left unpublished, never
  truncated.** Home Assistant rejects a state longer than 255 characters, which
  fits fifteen addresses — the default `CONFIG_BT_SMP_MAX_BONDS`. A store
  configured beyond that publishes nothing at all and the sensor stays `unknown`.
  That is the right failure: a truncated list shows a bonded device as absent,
  which is exactly the input `bond_lost` fires on, so half-reporting would be a
  false accusation where unreported is merely silence. Only reachable with a
  non-default `CONFIG_BT_SMP_MAX_BONDS`, and the firmware logs a warning when it
  happens.
- **Connections the node holds for itself are reported but never diagnosed.**
  The slots sensor covers the whole node, so any `ble_client:` connection appears
  in its state, but idle-slot detection judges only the addresses habluetooth
  reports as allocated on that proxy — which those are not. They are visible and
  not diagnosed: BlueSight will not tell you a `ble_client:` link has gone stale,
  and it will not call one a ghost slot either. Note they still consume
  `max_connections`, so a node that runs out of link pool starves
  `bluetooth_proxy` of the slots it advertises; the `max_connections` bullet
  below is how those two numbers relate.
- **A slot Home Assistant has already released is not judged, even while the node
  still holds the link.** The two readings come from different places at
  different instants, and the ordinary cause of a disagreement is staleness — the
  slots sensor publishes on change plus a tick, so it lags a disconnect Home
  Assistant has already booked. One snapshot cannot tell that apart from a link
  genuinely stuck on the node, and the common case is routine, so BlueSight stays
  quiet. The cost is a narrow false negative in the direction that does not cry
  wolf: the slot in question is one Home Assistant already counts as free.
- **`max_connections` is not the slot count, and both numbers are right.**
  `esp32_ble.max_connections` sizes the controller's link pool for the entire
  node: every BLE link it can hold at once, whoever owns it. The number BlueSight
  reports as slots is `bluetooth_proxy.connection_slots`, which defaults to 3 and
  is what the proxy advertises to Home Assistant as the number of devices it will
  connect on HA's behalf. It does not scale with `max_connections`. To give Home
  Assistant more slots, raise `connection_slots` *and* raise `max_connections` to
  cover it plus everything else on the node; raising `max_connections` alone only
  enlarges the pool the rest draws from.
- **SMP counts are per boot, and saturate rather than wrap.** A proxy restart
  resets them, which the integration handles by re-arming its baseline —
  under-counting, which is the safe direction. At most eight addresses are
  tracked at once; a ninth evicts the least recently updated.
- **This is young.** The firmware has been running on real hardware for hours,
  not weeks. The wire format is pinned by tests on both sides and the read-only
  invariant is structural, but the operational experience behind the default
  thresholds is thin. Treat a surprising incident as worth investigating in both
  directions.
