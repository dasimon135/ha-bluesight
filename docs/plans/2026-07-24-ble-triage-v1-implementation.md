# BLE Triage v1 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Ship a read-only HACS custom integration that exposes ESPHome Bluetooth-proxy GATT slot allocations as entities, detects three connection-layer incidents (deadlock #176516, ghost-slot, pairing storm), and renders them in a custom Lovelace card with actionable notifications.

**Architecture:** A single-instance config entry starts a `DataUpdateCoordinator` that subscribes to the `habluetooth` allocation callback (push) and polls `async_current_allocations()` as a safety net. The coordinator builds a per-proxy state map; a pure `detector` module turns snapshots + a rolling event window into a list of `Incident` objects. Sensors/binary_sensors render state; a notification helper fires on new incidents. All `habluetooth`-manager access is isolated in `adapter.py` so a HA API change touches exactly one file.

**Tech Stack:** Python 3.13, Home Assistant custom integration (`homeassistant`, `habluetooth`), pytest + pytest-homeassistant-custom-component, HACS, a vanilla-JS Lovelace card. English-only for all public content (per user convention for public GitHub repos).

---

## Conventions

- Domain: `ble_triage`. Package: `custom_components/ble_triage/`.
- All code, comments, docstrings, README in **English**.
- TDD: failing test first, minimal code, green, commit. Small commits.
- Test runner: `pytest` via the same docker pattern used for madoka (`madoka-tests`) or a local venv with `pytest-homeassistant-custom-component`.
- Pure logic (`detector.py`, `model.py`) has **zero** HA imports so it tests without a HASS fixture.

---

## Task 0: Repo scaffold + CI

**Files:**
- Create: `custom_components/ble_triage/__init__.py` (empty stub for now)
- Create: `custom_components/ble_triage/manifest.json`
- Create: `hacs.json`
- Create: `README.md`
- Create: `.github/workflows/validate.yml`
- Create: `requirements_test.txt`
- Create: `tests/__init__.py`, `tests/conftest.py`
- Create: `.gitignore`

**Step 1: Write `manifest.json`**

```json
{
  "domain": "ble_triage",
  "name": "BLE Triage",
  "codeowners": ["@dasimon135"],
  "config_flow": true,
  "dependencies": ["bluetooth"],
  "documentation": "https://github.com/dasimon135/ha-ble-triage",
  "iot_class": "local_push",
  "issue_tracker": "https://github.com/dasimon135/ha-ble-triage/issues",
  "requirements": [],
  "version": "0.1.0"
}
```

`dependencies: ["bluetooth"]` guarantees the HA bluetooth stack (and thus the `habluetooth` manager) is set up before us.

**Step 2: Write `hacs.json`**

```json
{ "name": "BLE Triage", "render_readme": true, "homeassistant": "2025.2.0" }
```

**Step 3: Write `.github/workflows/validate.yml`** — hassfest + HACS validation + pytest (mirror the ha-bluetooth-mesh workflow; reference memory `reference_ha_integration_ci` for gotchas).

**Step 4: Write `requirements_test.txt`**

```
pytest-homeassistant-custom-component
```

**Step 5: Commit**

```bash
git add -A
git commit -m "chore: scaffold ble_triage integration + CI"
```

---

## Task 1: Data model (pure, no HA imports)

**Files:**
- Create: `custom_components/ble_triage/model.py`
- Test: `tests/test_model.py`

**Step 1: Write the failing test**

```python
from custom_components.ble_triage.model import ProxySlots, Incident, IncidentKind

def test_proxyslots_used_derived():
    p = ProxySlots(source="AA:BB", name="Salon Proxy", slots=3, free=1,
                   allocated=["11:22", "33:44"])
    assert p.used == 2
    assert p.is_full is False

def test_incident_identity_is_stable():
    a = Incident(kind=IncidentKind.DEADLOCK, address="11:22", sources=["AA", "BB"])
    b = Incident(kind=IncidentKind.DEADLOCK, address="11:22", sources=["BB", "AA"])
    assert a.key == b.key   # order-independent identity
```

**Step 2: Run — expect FAIL** (`ModuleNotFoundError`).
Run: `pytest tests/test_model.py -v`

**Step 3: Implement `model.py`**

```python
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum

class IncidentKind(str, Enum):
    DEADLOCK = "deadlock"        # same address allocated on >=2 proxies (#176516)
    GHOST_SLOT = "ghost_slot"    # slot held while entity unavailable
    STORM = "storm"              # burst of connection failures

@dataclass(frozen=True, slots=True)
class ProxySlots:
    source: str            # proxy/adapter MAC
    name: str              # friendly name
    slots: int
    free: int
    allocated: list[str] = field(default_factory=list)

    @property
    def used(self) -> int:
        return self.slots - self.free

    @property
    def is_full(self) -> bool:
        return self.free <= 0

@dataclass(frozen=True, slots=True)
class Incident:
    kind: IncidentKind
    address: str
    sources: list[str] = field(default_factory=list)
    detail: str = ""

    @property
    def key(self) -> str:
        return f"{self.kind.value}:{self.address}:{','.join(sorted(self.sources))}"
```

**Step 4: Run — expect PASS.**

**Step 5: Commit** `feat: add ble_triage data model`

---

## Task 2: Detector — deadlock rule (#176516)

**Files:**
- Create: `custom_components/ble_triage/detector.py`
- Test: `tests/test_detector_deadlock.py`

**Step 1: Failing test**

```python
from custom_components.ble_triage.model import ProxySlots, IncidentKind
from custom_components.ble_triage.detector import detect_deadlocks

def test_address_on_two_proxies_is_deadlock():
    proxies = [
        ProxySlots("AA", "P1", 2, 1, ["11:22"]),
        ProxySlots("BB", "P2", 2, 1, ["11:22"]),
        ProxySlots("CC", "P3", 2, 2, []),
    ]
    incidents = detect_deadlocks(proxies)
    assert len(incidents) == 1
    inc = incidents[0]
    assert inc.kind is IncidentKind.DEADLOCK
    assert inc.address == "11:22"
    assert sorted(inc.sources) == ["AA", "BB"]

def test_address_on_one_proxy_is_not_deadlock():
    proxies = [ProxySlots("AA", "P1", 2, 1, ["11:22"])]
    assert detect_deadlocks(proxies) == []
```

**Step 2: Run — expect FAIL.**

**Step 3: Implement `detect_deadlocks` in `detector.py`**

```python
from __future__ import annotations
from collections import defaultdict
from .model import ProxySlots, Incident, IncidentKind, normalize_address

def detect_deadlocks(proxies: list[ProxySlots]) -> list[Incident]:
    """A single BLE peripheral can be connected to one central at a time.
    An address in the `allocated` list of >=2 DISTINCT proxies is a stale
    duplicate allocation (core issue #176516)."""
    by_addr: dict[str, set[str]] = defaultdict(set)   # DISTINCT sources, not occurrences
    for p in proxies:
        for addr in p.allocated:
            by_addr[normalize_address(addr)].add(p.source)   # normalize for case-insensitive correlation
    return [
        Incident(IncidentKind.DEADLOCK, addr, sorted(sources),
                 detail=f"Held on {len(sources)} proxies simultaneously")
        for addr, sources in by_addr.items() if len(sources) >= 2
    ]
```

> **Review fixes baked in (2026-07-24):** correlate over a `set` of DISTINCT
> proxy sources (a duplicate address within one proxy must NOT self-flag), and
> normalize MAC case before correlating. Add `normalize_address(addr) ->
> addr.strip().upper()` to `model.py`. Regression tests:
> `test_duplicate_address_within_one_proxy_is_not_deadlock`,
> `test_three_proxies_sharing_address`, `test_deadlock_correlates_across_case`.

**Step 4: Run — expect PASS.**

**Step 5: Commit** `feat: detect #176516 slot-leak deadlocks`

---

## Task 3: Detector — ghost-slot rule

**Files:**
- Modify: `custom_components/ble_triage/detector.py`
- Test: `tests/test_detector_ghost.py`

**Step 1: Failing test**

```python
from custom_components.ble_triage.model import ProxySlots, IncidentKind
from custom_components.ble_triage.detector import detect_ghost_slots

def test_allocated_but_unavailable_is_ghost():
    proxies = [ProxySlots("AA", "P1", 2, 1, ["11:22"])]
    # address -> is the device entity available?
    availability = {"11:22": False}
    incidents = detect_ghost_slots(proxies, availability)
    assert len(incidents) == 1
    assert incidents[0].kind is IncidentKind.GHOST_SLOT
    assert incidents[0].address == "11:22"

def test_allocated_and_available_is_fine():
    proxies = [ProxySlots("AA", "P1", 2, 1, ["11:22"])]
    assert detect_ghost_slots(proxies, {"11:22": True}) == []
```

**Step 2: Run — expect FAIL.**

**Step 3: Implement**

```python
def detect_ghost_slots(
    proxies: list[ProxySlots], availability: dict[str, bool]
) -> list[Incident]:
    """A slot held for a device whose entity is unavailable is likely stale."""
    avail = {normalize_address(k): v for k, v in availability.items()}  # normalize keys
    out: list[Incident] = []
    for p in proxies:
        for addr in p.allocated:
            norm = normalize_address(addr)
            if avail.get(norm, True) is False:
                out.append(Incident(
                    IncidentKind.GHOST_SLOT, norm, [p.source],
                    detail=f"Slot held on {p.name} while device unavailable"))
    return out
```

> **Review fix (2026-07-24):** normalize BOTH the availability-dict keys and the
> allocated address before lookup (a case mismatch must not miss a ghost).
> Regression test: `test_ghost_slot_case_insensitive_availability`.

**Step 4: Run — expect PASS.**

**Step 5: Commit** `feat: detect ghost slots`

---

## Task 4: Detector — storm rule (rolling event window)

**Files:**
- Create: `custom_components/ble_triage/window.py` (pure rolling counter)
- Modify: `custom_components/ble_triage/detector.py`
- Test: `tests/test_detector_storm.py`

**Step 1: Failing test** — a monotonic clock is injected so tests are deterministic (no wall clock).

```python
from custom_components.ble_triage.window import FailureWindow
from custom_components.ble_triage.detector import detect_storm
from custom_components.ble_triage.model import IncidentKind

def test_burst_of_failures_is_storm():
    now = [0.0]
    w = FailureWindow(window_s=300, threshold=5, clock=lambda: now[0])
    for _ in range(5):
        now[0] += 10
        w.record("11:22")
    inc = detect_storm("11:22", w)
    assert inc is not None and inc.kind is IncidentKind.STORM

def test_failures_outside_window_expire():
    now = [0.0]
    w = FailureWindow(window_s=300, threshold=5, clock=lambda: now[0])
    for _ in range(5):
        w.record("11:22")
        now[0] += 100   # 4th/5th fall outside 300s of the 1st
    assert detect_storm("11:22", w) is None
```

**Step 2: Run — expect FAIL.**

**Step 3: Implement `window.py` + `detect_storm`**

```python
# window.py
from __future__ import annotations
from collections import defaultdict, deque
from collections.abc import Callable   # not typing.Callable (ruff UP035)

class FailureWindow:
    def __init__(self, window_s: float, threshold: int, clock: Callable[[], float]):
        self.window_s = window_s
        self.threshold = threshold
        self._clock = clock
        self._events: dict[str, deque[float]] = defaultdict(deque)

    def record(self, address: str) -> None:
        self._events[address].append(self._clock())
        self._evict(address)

    def count(self, address: str) -> int:
        if address not in self._events:   # do NOT auto-create keys on read (leak fix)
            return 0
        self._evict(address)
        return len(self._events.get(address, ()))

    def _evict(self, address: str) -> None:
        cutoff = self._clock() - self.window_s
        q = self._events[address]
        while q and q[0] < cutoff:
            q.popleft()
        if not q:                          # self-clean: drop empty deques (leak fix)
            del self._events[address]
```

> **Review fix (2026-07-24, I2):** `count()` must not create a dict key for an
> unseen address, and `_evict()` drops an address once its deque empties, so a
> 24/7 coordinator loop does not leak. Regression tests:
> `test_count_unknown_address_does_not_create_key`,
> `test_window_self_cleans_after_expiry`, `test_multi_address_isolation`.

```python
# detector.py (add)
from .window import FailureWindow

def detect_storm(address: str, window: FailureWindow) -> Incident | None:
    if window.count(address) >= window.threshold:
        return Incident(IncidentKind.STORM, address, [],
                        detail=f"{window.count(address)} failures in "
                               f"{int(window.window_s)}s")
    return None
```

**Step 4: Run — expect PASS.**

**Step 5: Commit** `feat: detect pairing storms via rolling failure window`

---

## Task 5: Adapter — reach the habluetooth manager (probe-first)

**Files:**
- Create: `custom_components/ble_triage/adapter.py`
- Test: `tests/test_adapter.py`

This is the ONE version-sensitive module. Verified API (2026-07-24):
`habluetooth.get_manager()` → manager with
`async_current_allocations(source=None) -> list[HaBluetoothSlotAllocations] | None`
and `async_register_allocation_callback(callback, source=None) -> CALLBACK_TYPE`,
where `HaBluetoothSlotAllocations` has `source, slots, free, allocated`.

**Step 1: Failing test** — use a fake manager to assert we map its objects to our `ProxySlots` and register/unregister cleanly. No real HASS needed.

```python
from custom_components.ble_triage.adapter import current_proxy_slots, SlotAdapter
from custom_components.ble_triage.model import ProxySlots

class _FakeAlloc:
    def __init__(self, source, slots, free, allocated):
        self.source, self.slots, self.free, self.allocated = source, slots, free, allocated

class _FakeManager:
    def __init__(self, allocs): self._a = allocs; self.registered = None
    def async_current_allocations(self, source=None): return self._a
    def async_register_allocation_callback(self, cb, source=None):
        self.registered = cb
        return lambda: setattr(self, "registered", None)

def test_maps_allocations_to_proxyslots():
    mgr = _FakeManager([_FakeAlloc("AA", 3, 1, ["11:22", "33:44"])])
    slots = current_proxy_slots(mgr, name_for=lambda s: f"proxy-{s}")
    assert slots == [ProxySlots("AA", "proxy-AA", 3, 1, ["11:22", "33:44"])]

def test_adapter_registers_and_unregisters():
    mgr = _FakeManager([])
    seen = []
    ad = SlotAdapter(mgr, on_change=lambda: seen.append(1))
    ad.start()
    assert mgr.registered is not None
    mgr.registered(object())          # simulate a push
    assert seen == [1]
    ad.stop()
    assert mgr.registered is None
```

**Step 2: Run — expect FAIL.**

**Step 3: Implement `adapter.py`**

```python
from __future__ import annotations
from typing import Any, Callable
from .model import ProxySlots

def get_manager() -> Any:
    """Isolated import: the single point coupled to habluetooth's API."""
    from habluetooth import get_manager as _gm
    return _gm()

def current_proxy_slots(manager: Any, name_for: Callable[[str], str]) -> list[ProxySlots]:
    allocs = manager.async_current_allocations() or []
    return [
        ProxySlots(a.source, name_for(a.source), a.slots, a.free, list(a.allocated))
        for a in allocs
    ]

class SlotAdapter:
    def __init__(self, manager: Any, on_change: Callable[[], None]):
        self._manager = manager
        self._on_change = on_change
        self._unsub: Callable[[], None] | None = None

    def start(self) -> None:
        self._unsub = self._manager.async_register_allocation_callback(
            lambda _alloc: self._on_change())

    def stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
```

**Step 4: Run — expect PASS.**

**Step 5: Manual probe (real HA)** — before wiring the coordinator, run one throwaway `hass` script/log line asserting `get_manager()` returns an object with both methods on the user's live HA. If the names moved, this is the ONLY file to patch. Document the confirmed HA version in a comment.

**Step 6: Commit** `feat: habluetooth slot adapter (isolated API surface)`

---

## Task 6: Coordinator

**Files:**
- Create: `custom_components/ble_triage/coordinator.py`
- Create: `custom_components/ble_triage/const.py`
- Test: `tests/test_coordinator.py`

**Step 1: Failing test** — with `pytest-homeassistant-custom-component`'s `hass` fixture, feed a fake manager and assert `coordinator.data` holds proxies + incidents, and that a deadlock across two proxies surfaces.

**Step 2–4:** Implement a `DataUpdateCoordinator[BleTriageData]` where `BleTriageData` bundles `proxies: list[ProxySlots]` and `incidents: list[Incident]`. `_async_update_data`:
1. `slots = current_proxy_slots(manager, name_for)`
2. `availability = {addr: _entity_available(addr) for addr in all allocated}` — resolve via `bluetooth.async_address_present` / entity registry.
3. `incidents = detect_deadlocks(slots) + detect_ghost_slots(slots, availability) + storm incidents from the window`
4. Return `BleTriageData(slots, incidents)`.
The `SlotAdapter.on_change` calls `coordinator.async_set_updated_data(...)` for push; keep a slow poll (e.g. 30 s) as a backstop.

**Step 5: Commit** `feat: coordinator wiring push + poll`

---

## Task 7: Config flow (single instance)

**Files:**
- Create: `custom_components/ble_triage/config_flow.py`
- Modify: `custom_components/ble_triage/__init__.py` (setup/unload entry)
- Create: `custom_components/ble_triage/strings.json` + `translations/en.json`
- Test: `tests/test_config_flow.py`

Single-instance flow (`async_step_user` → create entry, abort if already configured). An **options flow** exposes storm thresholds (window seconds, failure count) and the deadlock proxy count `N` (default 2). Tests: user flow creates entry; second attempt aborts `single_instance_allowed`; options round-trip.

**Commit** `feat: config + options flow`

---

## Task 8: Sensor entities (per proxy)

**Files:**
- Create: `custom_components/ble_triage/sensor.py`
- Test: `tests/test_sensor.py`

Per proxy (device grouped by proxy MAC): `sensor.<proxy>_slots_used` (state = used, attrs: total, free, allocated addresses), `sensor.<proxy>_slots_free`. Entities read from `coordinator.data`. Tests assert state + attributes after a coordinator refresh.

**Commit** `feat: per-proxy slot sensors`

---

## Task 9: Global incident binary_sensor

**Files:**
- Create: `custom_components/ble_triage/binary_sensor.py`
- Test: `tests/test_binary_sensor.py`

`binary_sensor.ble_triage_incident` — `is_on` when `coordinator.data.incidents` non-empty; attributes: incident count + a compact list (`kind`, `address`, `detail`). `device_class = problem`. Test on/off transitions.

**Commit** `feat: global incident binary sensor`

---

## Task 10: Actionable notifications

**Files:**
- Create: `custom_components/ble_triage/notify.py`
- Test: `tests/test_notify.py`

On a NEW incident key (diff against previous set), fire `persistent_notification` with a human message reusing the madoka playbook (e.g. storm → "Toggle Bluetooth on <device>, then Reconnect"). De-dup by `Incident.key`; clear notification when the incident resolves. Test: new incident fires once; repeat refresh does not re-fire; resolution clears.

**Commit** `feat: actionable incident notifications`

---

## Task 11: Lovelace card

**Files:**
- Create: `www/ble-triage-card.js`
- Create: `docs/card.md` (install + example config)
- Test: manual (browser) + a small JS lint step in CI

Vanilla-JS custom card (no build step, matches the ha-dooya/ha-bluetooth-mesh approach). Renders one tile per proxy: filled/empty slot pips, allocated addresses, and an incident banner (red for deadlock/ghost, amber for storm). Reads the `sensor.*_slots_*` and `binary_sensor.ble_triage_incident` states. Keep v1 static-per-refresh; live migration animation is a nice-to-have, not a blocker (YAGNI).

**Commit** `feat: BLE Triage Lovelace card`

---

## Task 12: Release polish

- README: what it is, the #176516 story, install via HACS custom repo, card setup, screenshots.
- `brand/` icon (per memory `reference_ha_brands_local`: HA ≥2026.3 ships custom brand icons inside the integration — no PR to the brands repo).
- Verify hassfest + HACS action green (memory `reference_ha_integration_ci`).
- Tag `v0.1.0`.

**Commit** `docs: README + brand icon` then release.

---

## Definition of done (v1)

- [ ] Deadlock (#176516), ghost-slot, storm detectors unit-tested (pure, no HASS).
- [ ] `adapter.py` confirmed against the user's live HA version (probe done, version noted).
- [ ] Per-proxy sensors + global incident binary_sensor working on live HA.
- [ ] Notifications fire once per incident and clear on resolution.
- [ ] Card renders proxies + incidents.
- [ ] hassfest + HACS validation green; `v0.1.0` tagged.
- [ ] All public content English.

## Explicitly OUT of scope for v1 (YAGNI)

- Any *action* (freeing slots, forcing unbond) — that is v2.
- The ESPHome custom component (raw SMP/RAM/bond) — that is v1.5.
- Proxy-migration history / long-term stats DB.
- Live animated card transitions.
