# BOND_LOST on living evidence — v0.6.4 design

**Status:** designed 2026-08-28.
**Supersedes:** the BOND_LOST section of
`docs/plans/2026-08-24-v1.5-esphome-component-design.md`, which specified a bond
threshold that never shipped.

## The defect

`detect_bond_lost` reads `ProxyTelemetry.smp_failures` — the firmware counter,
monotonic since the proxy booted — and fires at `count > 0`. There is no window,
no threshold, and no decay. One refusal in the whole life of a proxy is enough,
and the incident then stays open until that proxy reboots or a bond appears for
the address.

STORM, measured from the same counter, does not work this way: the deltas go
through `CounterDeltas` into a `FailureWindow`, so a storm opens on what is
happening and closes when it stops. The two halves of the measured evidence were
running on different clocks, and only one of them was a clock.

Found on a live fleet. A thermostat connected and exchanging normally through the
proxy that holds its bond was simultaneously reported as BOND_LOST — and
notified — because a *different* proxy had refused it five times at some point in
the past. Nothing about that report was current, and nothing would ever retract
it.

This is the failure mode the project names as its own main risk (`docs/post-community.md`,
§ Feedback wanted): a false positive, in the one detector whose remedy asks the
user to go and physically re-pair a device.

## The fix

BOND_LOST stops reading the counter and reads the window STORM already fills.

The deltas are *already* in that window with per-proxy attribution — the storm
loop calls `storm_window.record(address, tel.source)` — so nothing new is fed and
no second window is created. What is missing is only the ability to read them
back **per proxy**, because BOND_LOST is a per-proxy verdict where STORM is a
per-address one.

Reusing the window rather than adding a second one is the point. One window means
one time horizon and one place where a failure ages out, and it keeps the
invariant v0.6.3 established for precedence: one policy, every surface. A
dedicated bond window would be independently tunable, which is a real benefit,
and it would also mean two windows to reason about, two more options, and two
answers to "how long ago was this?". Not worth it until someone shows a fleet
where the storm horizon is wrong for bonds.

### `window.py`

```python
def count_by_source(self, address: str) -> dict[str, int]:
    """Live *measured* events for `address`, counted per proxy."""
```

Inferred events (`source is None`) are excluded by construction. BOND_LOST is a
verdict that requires measurement: the release heuristic cannot say which proxy
dropped a slot, so it can never implicate one. This is not a filter applied on
top of the answer, it *is* the answer — the same reason `sources()` already
returns only measured provenance.

### `detector.py`

`detect_bond_lost(telemetry, names, window, threshold)`. For each proxy that
reports its bond list, an address is BOND_LOST when

    count_by_source(address)[source] >= threshold  and  address not in bonds

The `tel.smp_failures is None` guard goes away. The evidence now lives in the
window rather than in this snapshot, so a proxy that briefly stops publishing its
counters while still publishing its bonds keeps its incident open — the same
reasoning that makes `CounterDeltas` hold a baseline through a blip instead of
dropping it.

`tel.bonds is None` stays blocking, and must. Without a bond list, "holds no
bond" and "did not say" are indistinguishable, and asserting the first from the
second would fire BOND_LOST across a fleet over a formatting disagreement — the
failure `telemetry.py` already refuses to make in the parser.

The merge-on-normalised-key step disappears with the counter it guarded: the
window is keyed on addresses that `CounterDeltas` already canonicalised on the
way in. The bond list is still normalised at the seam, for the reason its
docstring gives.

### `coordinator_data.py`

The replay cap becomes:

```python
for _ in range(min(count, max(storm_window.threshold, bond_threshold))):
```

The cap exists so a corrupt-but-well-formed `4294967295` cannot spin billions of
iterations on the event loop. Capping at the storm threshold alone would make any
bond threshold above it permanently unreachable — and, worse, would do so
silently. Taking the max keeps the guard and makes both thresholds reachable
whatever order the user tunes them into.

### Configuration

`DEFAULT_BOND_THRESHOLD = 3`, exposed in the options flow beside the others. This
is the third setting the v1.5 design specified and the only one that never
shipped.

Three sits deliberately below the storm threshold of five. A missing bond is
deterministic — it fails every attempt, not intermittently — so three measured
refusals in the window is not noise, and BOND_LOST already supersedes STORM in
`incident_policy`. Firing first is therefore the whole benefit: the user gets the
exact remedy ("re-pair through *this* proxy") instead of the generic one ("this
keeps failing"). At five the diagnosis could never be anything but a
requalification of a storm already raised.

## Wording

The count changes meaning — from "ever" to "in the last `window_s` seconds" — so
the strings must change with it. `bond_lost` gains `{seconds}`, matching the
house pattern its siblings already use:

    incident.storm.detail.other          "{count} failures in {seconds}s"
    incident.proxy_reboot_storm.detail.other  "{count} proxy reboots in {seconds}s"

Left alone, "3 connections refused through X" still reads as a lifetime tally.
That is the same defect v0.6.2 and v0.6.3 went to fix — a sentence that is
technically sourced and factually misread — and it would be reintroduced by the
very change that makes the number honest.

The `.one` variants stay in the catalogue. They are unreachable at a threshold of
three, but the threshold is a user setting and the plural machinery requires both
forms; a catalogue that is correct only at the default is not correct.

## Effect on a real fleet

On the installation this was found on: the thermostat with five old refusals and
no live events leaves the list. The address with eight live events stays.
`incident_count` goes from three to two.

Expect `incident_count` to drop on upgrade wherever a stale bond verdict was
being held open. That is the fix, not a regression — the same note v0.6.3
shipped, for the same reason.

## Testing

`tests/test_detector_bond_lost.py` covers the detector. The cases that carry the
design:

- below threshold — no incident
- at threshold, no bond — incident, `evidence="smp"`
- at threshold, bond held — nothing
- events aged out of the window — incident clears (the defect, as a test)
- inferred-only events — nothing, at any count
- `bonds is None` — nothing, whatever the window holds
- `smp_failures is None` this snapshot, window still live — incident stands
- two proxies, one bonded and one not — only the unbonded one is named

`tests/test_coordinator_shell.py` covers the cap taking the max, at a bond
threshold both above and below the storm threshold.
