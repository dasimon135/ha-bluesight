# BLE Triage — Design

**Date:** 2026-07-24
**Status:** Validated brainstorm, pre-implementation
**Working name:** BLE Triage (`ha-ble-triage`)

## One-liner

The only tool that makes visible — and eventually repairs — the *connection*
layer of Home Assistant's Bluetooth: GATT slots, bonds, pairing storms, and
proxy deadlocks.

## Why this is novel

Verified 2026-07-24:

- HA 2025.2 added an **Advertisement Monitor** (Settings → Devices → Bluetooth →
  Configure) that lists which devices each proxy *sees*. It has no entities and
  covers visibility, not the connection layer.
- **Bermuda** (HACS) covers multi-proxy RSSI for *room localisation*, not proxy
  health.
- **Nothing** exposes GATT connection slots: how many are occupied per proxy, by
  which device, since when. Core issue
  [#176516](https://github.com/home-assistant/core/issues/176516) documents a
  slot-leak deadlock (one device holding a slot on all 5 proxies → pool
  deadlock → devices stuck `unavailable`) with no diagnostic tool — the only
  method bdraco offers in the community thread is "enable debug logs".
- No tooling for bond state, pairing-storm detection, or proxy-migration history.

## The "wow" = correlation, not action

The spectacular part is the screen that says *"pairing storm on thermostat
Salon — 15 SMP fails in 5 min, quarantined"* while HA today leaves you blind.
That is read + pattern-correlation. No dangerous internal APIs. Ships fast.

Action (free a slot, force unbond) is the risky internals coupling → deferred
to v2 on an already-proven base.

## Key decisions

1. **Source of truth: HA-only for v1**, architected to accept an optional
   ESPHome custom component in v1.5.
   - Rationale: the two flagship incidents (storm, #176516 deadlock) are
     *more* visible from HA than from an isolated proxy — HA sees all N proxies
     at once. HA-only is the right vantage point, not a degraded one.
   - HACS install, zero reflash → immediate adoption. The wow must spread.

2. **State access: pragmatic mix.** Ride the structured `habluetooth` allocation
   API (see de-risk below); no aioesphomeapi private state in v1. Any
   version-sensitive access isolated behind `adapter.py`.

## v1 architecture (HA-only, HACS, read-only)

- **Coordinator** — subscribes to the `habluetooth` allocation callback + the HA
  `bluetooth` advertisement bus. Maintains a sliding window of events per
  `(device, proxy)`.
- **`adapter.py`** — the only module touching the `habluetooth` manager. Thin,
  versioned, tested. Rest of the code sees a stable interface.
- **Detector, 3 initial rules:**
  - *Storm* — X connection failures / retry bursts within window T on one device.
  - *Slot-deadlock (#176516)* — same address in the `allocated` list of ≥N
    proxies at once → stale duplicate allocation.
  - *Ghost-slot* — address in a proxy's `allocated` list while its entity is
    `unavailable`.
- **Entities** — per proxy: total/free/used slots, allocated addresses, active
  incidents. Global: `binary_sensor.ble_triage_incident`.
- **Custom Lovelace card** — each proxy as a tile with filled/empty slots,
  live-migrating connections, and an incident feed.
- **Actionable notifications** — reuse madoka logic ("toggle BT on thermostat
  Salon").

## Moat roadmap

- **v1** — detect + advise. Visual wow, safe, immediate adoption.
- **v1.5** — optional ESPHome custom component: raw NimBLE SMP fails, connection
  rejects, BLE RAM, bond state. Auto-detected, unlocks the fine-grained view.
- **v2** — self-healing: "free this slot" / guided re-pair button, then
  automatic.

## Technical de-risk — VERIFIED 2026-07-24

Slot allocation is NOT buried in aioesphomeapi private internals — `habluetooth`
(the library HA's Bluetooth stack is built on) already aggregates it across all
proxies and exposes a structured, semi-public API:

```python
# habluetooth/models.py
@dataclass(slots=True, frozen=True)
class HaBluetoothSlotAllocations:
    source: str            # Adapter/proxy MAC
    slots: int             # Total slots
    free: int              # Free slots
    allocated: list[str]   # Addresses of devices currently holding a slot

# habluetooth/manager.py  (reachable via habluetooth.get_manager())
def async_current_allocations(source=None) -> list[HaBluetoothSlotAllocations] | None
def async_register_allocation_callback(callback, source=None) -> CALLBACK_TYPE
```

Implications:
- Slot visibility is EXACT, not inferred — precisely the data #176516 lacks.
- The #176516 deadlock detector is trivial and exact: intersect `allocated`
  lists across sources.
- Version risk is minor — we ride `habluetooth`'s API, not private state.

What still needs the ESPHome v1.5 component: raw SMP-fail counts, connection
rejects, BLE RAM, bond state — NOT exposed by this API. Storm detection in v1
stays event-correlation-based; the custom component upgrades it later.

## Verdict

GO. v1 (slots + deadlock + ghost detection + card) rests on solid ground. The
moat (ESPHome SMP/bond telemetry) is additive, not load-bearing for release 1.
