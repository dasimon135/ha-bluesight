"""Pure shaping of the ESPHome telemetry for the diagnostics dump.

The twin of :mod:`.diagnostics` in the same way :mod:`.coordinator_data` is the
twin of :mod:`.coordinator`: no Home Assistant import lives here, so this is
testable with plain pytest on a machine that has no Home Assistant, which is
where the shape decisions below actually get exercised.

This section exists to answer one question -- **"is this proxy reporting, and
what did it say?"** -- and every failure mode it has to expose is silent by
design. A proxy whose Home Assistant device cannot be resolved, a sensor whose
name does not match, a firmware whose wire format disagrees: each degrades to
"no telemetry" with no error anywhere. Without this section, "no incidents
appeared" and "the reader has never seen anything" look identical on a live
system.

Three decisions carry the shape.

**Absence is not emptiness, in words as well as in JSON.** ``None`` means the
signal is not reporting; ``{}`` or ``[]`` means a proxy reporting zero entries.
``json.dumps`` already renders those as ``null`` and ``[]``, but a human
reading a dump at 2am is being asked to remember which of the two means the
firmware is missing. So each proxy carries a ``signals`` map that says
``absent`` or ``reporting`` in a word, next to the value itself.

**A proxy that reported nothing is named, not omitted.**
``BlueSightData.telemetry`` leaves such a proxy out entirely, deliberately, so
the field means what its docstring says. That is right for the field and
useless here: the likeliest reader of this dump is someone asking why their
flashed proxy is not showing up, and silence that reads exactly like health is
the worst possible answer to give them. ``silent_sources`` is therefore
computed against the proxies habluetooth knows about -- the same superset the
coordinator feeds the reader -- so the dump can say "this proxy exists and sent
us nothing" rather than saying nothing at all.

**The counter baselines come too.** They are what decides whether a rising SMP
counter becomes a storm incident, they are invisible from every other surface,
and a wrong one fails *silently downward* -- a baseline captured too high
swallows real failures and nothing looks broken. The dump already exposes the
equivalent internals of both failure windows, so this is precedent rather than
a new kind of exposure.

Addresses are not redacted, matching :mod:`.diagnostics`: they are the subject
of the report, and a bond list with the MACs removed cannot show that the
device BlueSight says is unbonded is missing from the proxy's bond store.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .model import normalize_address
from .telemetry import ProxyTelemetry

#: Said in the dump itself rather than left to documentation: the operator
#: reading this is holding the artefact, not the repository. It names the
#: causes because they are not distinguishable from here -- the reader drops a
#: proxy for any of them without recording which (see
#: ``telemetry_reader.read_fleet_telemetry``), so an honest dump lists the
#: possibilities instead of implying it knows.
_SILENT_NOTE = (
    "silent_sources are proxies habluetooth knows about that reported no "
    "BlueSight telemetry in this snapshot. Expected for any proxy not running "
    "the BlueSight ESPHome component. Otherwise: Home Assistant has no device "
    "for the proxy (so no sensor could be found), the sensors are named "
    "something other than the three the reader matches on, or every one of "
    "them was unavailable at snapshot time (a rebooting proxy). All of these "
    "look the same from here."
)


def _signal(value: object) -> str:
    """One word for whether a signal reported at all.

    ``absent`` is not ``0``: an empty reading is a proxy actively telling us it
    has nothing, which is a claim, while absence is the lack of one.
    """
    return "absent" if value is None else "reporting"


def _sorted_mapping[T](values: Mapping[str, T] | None) -> dict[str, T] | None:
    """Sort a per-address reading by address, preserving ``None``.

    Sorted so that two dumps from the same instance diff on what changed
    instead of on dict ordering.
    """
    if values is None:
        return None
    return {address: values[address] for address in sorted(values)}


def _telemetry_entry(telemetry: ProxyTelemetry) -> dict[str, Any]:
    """One proxy's reading, JSON-native throughout.

    ``bonds`` is a ``set`` on the dataclass and a ``set`` is not JSON
    serialisable, so it becomes a sorted list -- the reason a plain
    ``asdict()`` cannot be dropped into the dump and the reason this module
    exists.
    """
    return {
        "source": telemetry.source,
        "signals": {
            "smp_failures": _signal(telemetry.smp_failures),
            "bonds": _signal(telemetry.bonds),
            "slot_idle_seconds": _signal(telemetry.slot_idle_seconds),
        },
        "smp_failures": _sorted_mapping(telemetry.smp_failures),
        "bonds": None if telemetry.bonds is None else sorted(telemetry.bonds),
        "slot_idle_seconds": _sorted_mapping(telemetry.slot_idle_seconds),
    }


def telemetry_report(
    telemetry: Iterable[ProxyTelemetry],
    known_sources: Iterable[str],
    baselines: Mapping[str, Mapping[str, int]],
) -> dict[str, Any]:
    """Build the ``telemetry`` section of the diagnostics dump.

    ``telemetry`` is ``BlueSightData.telemetry`` -- only the proxies that
    reported something. ``known_sources`` is every proxy source habluetooth
    reported this snapshot (health *and* allocation snapshots, exactly what the
    coordinator hands the reader); anything in it that did not report is listed
    as silent. ``baselines`` is :attr:`.CounterDeltas.baselines`.

    Everything returned is JSON-native and freshly built, so a caller cannot
    hand a download a live reference to coordinator state, and the sort order
    is stable across snapshots.
    """
    entries = [_telemetry_entry(t) for t in telemetry]
    reporting = {entry["source"] for entry in entries}
    silent = {
        source
        for raw in known_sources
        if (source := normalize_address(raw)) not in reporting
    }
    return {
        "reporting": entries,
        "silent_sources": sorted(silent),
        "note": _SILENT_NOTE,
        "counter_baselines": {
            source: _sorted_mapping(counts)
            for source, counts in sorted(baselines.items())
        },
    }
