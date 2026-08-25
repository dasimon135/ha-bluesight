# Connected devices per proxy — design

**Status:** validated, queued as Task 13 of the 0.6.0 milestone
**Date:** 2026-08-25

## The problem

The card shows `Proxy Buanderie 3/3` with three filled pips. That tells you the
proxy is saturated. It does not tell you *what* to blame, so the one action the
card provokes — "something must give up a slot" — is the one it gives you no
help with.

## What this is, and what it is not

**This is a capacity feature, not a fault feature.** When a slot is held by a
device that has gone away, BlueSight already raises `ghost_slot` naming the
address; the fault case is covered. What is missing is seeing who consumes the
slots *before* anything is wrong.

It is worth being explicit about that, because "show me what's connected" sounds
like a diagnostic and buying it as one would be a mistake.

## Not redundant with anything visible

Home Assistant has no per-proxy connection view. The ESPHome device page does not
list what its proxy is connected to, and the Bluetooth integration's own pages
are adapter- and device-scoped, never proxy-scoped.

It is *partially* redundant with BlueSight's own `allocated` attribute, which has
published the raw MAC list since 0.1. That attribute is machine-readable and
useless at a glance — which is exactly the gap.

## Design

### Resolution happens in the backend

`adapter.py` already builds `ProxySlots` from a resolver injected for the
**proxy** name (`_name_for`). A second resolver is injected the same way, for the
**connected device** names. `model.py` stays pure: `ProxySlots` gains a plain data
field, no Home Assistant import.

The coordinator supplies that resolver. It already builds `mac_index`
(normalised MAC → `device_id`) on every snapshot to decide whether a device is
alive, so naming costs one more lookup in a registry that is already in memory.

This is deliberately **not** done in the card. `_build_mac_index()` settles two
subtleties that would have to be reimplemented in JavaScript otherwise: a
MAC-shaped `identifier` is a convention while a declared `CONNECTION_BLUETOOTH`
is a declaration, and the declaration must win when both name the same address;
and non-Bluetooth connections (a device's Wi-Fi MAC) must never enter the index,
or an allocated BLE address could collide with some dead device's network MAC
and be falsely flagged. That logic is written and tested in Python. Duplicating
it in the card would be a correctness risk for no benefit.

### One attribute, added beside the old one

The slots sensor gains `allocated_devices`: an ordered list, one entry per
occupied slot, each carrying the address, the resolved name, and the `device_id`.

`allocated` does not change. It has been published since 0.1 and automations may
read it. We add beside it; we do not replace it — the same reasoning that kept
`detail` a rendered string through the 0.5.0 internationalisation.

The `device_id` costs nothing (the index already produces it) and makes the
attribute useful to automations that want to *target* the device rather than
merely name it.

### The fallback is the interesting case

The name is the registry's `name_by_user` or `name`. When the registry does not
know the address, the card shows the raw MAC with a translated marker.

**That is a diagnostic, not a display defect.** A device Home Assistant knows
nothing about, holding one of a proxy's three connection slots, is precisely
what you want surfaced.

## Considered and rejected

**Publish only `device_id` and let the card resolve the live name from
`hass.devices`.** Leaner — no name duplicated into the recorder, and a rename
would reflect instantly. Rejected because the freshness argument does not hold:
the coordinator repolls on an interval, so a renamed device's staleness window is
one poll. And the published name is what serves automations, which was the whole
lesson of `detail` in 0.5.0.

**Two attributes, `allocated` plus a parallel `allocated_names` map.** Rejected:
one would be a superset of the other and they would have to be kept in sync, in a
contract we have published.

## Rendering

A list under the pips, one line per occupied slot. A proxy at 0/3 contributes
nothing, so the card grows only where there is information.

Rejected alternatives: a tooltip on hover (hover does not exist on a phone, and
Home Assistant dashboards are read on phones), and showing the list only when a
proxy is saturated (you lose the warning — at 2/3 you cannot yet anticipate).

One new catalogue key: the "unknown to the registry" marker. Both languages.

## Testing

- `model.py` — the new field defaults empty and does not enter `ProxySlots`
  identity.
- `adapter.py` — the device-name resolver is called per allocated address, and a
  resolver that raises must not take the snapshot down.
- Coordinator — resolution uses the existing per-snapshot `mac_index`, is built
  once, and an address absent from the registry resolves to the raw MAC.
- Sensor — `allocated` is byte-identical to what 0.5.0 published (a regression
  guard on the contract), and `allocated_devices` carries one entry per occupied
  slot in the same order.
- Card — renders one line per occupied slot, nothing for an empty proxy, and the
  marker for an unresolved address, in both languages.

## Known limits, accepted

**Card height.** Five added lines on a four-proxy fleet. Thirty on ten saturated
proxies. No truncation is being built for a problem nobody has reported; this is
where it will break first if it breaks.

**Name history in the recorder.** The attribute records the name as it was at
that moment, so a renamed device shows its old name in history. Accepted: that
is a faithful record, not a bug.

## Sequencing

Queued as **Task 13, after the twelve ESPHome-telemetry tasks.** Tasks 3 and 5
modify `Incident` and the detectors; running two efforts across `model.py`
concurrently would manufacture conflicts for no gain.
