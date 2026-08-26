"""Pure parser for the telemetry the BlueSight ESPHome component publishes.

No Home Assistant dependency; fully unit-testable with plain pytest. (The
stdlib ``logging`` import is not a Home Assistant dependency: HA configures the
root logger, and a module-level logger costs this module none of its purity.)

The firmware publishes facts, never verdicts, in three bounded strings (see
``docs/plans/2026-08-24-v1.5-esphome-component-design.md``). This module turns
those strings into typed structure and nothing more: every threshold and every
judgement lives in the detectors, where pytest can reach it and where a retune
needs no reflash.

The addresses arrive as compact 12-character hex, because Home Assistant caps
an entity state at 255 characters and dropping the colons is what keeps a full
bond list inside that cap. They are expanded here so they correlate with the
habluetooth addresses the rest of BlueSight speaks.

Two rules govern everything below.

**Every field is validated, not merely converted.** This is the one place in
BlueSight where the input is a string from a microcontroller instead of an
object from habluetooth, and Python's built-in conversions are far more
permissive than that input deserves: ``float("nan")``, ``int("3_0")`` and
``int("-5")`` all succeed and would each hand a detector a number nobody
measured. NaN is the worst of them, comparing False against every threshold so
that a genuinely stuck slot is silently missed.

**Absence and emptiness are different answers, and total rejection is
absence.** A dropped field is one BlueSight cannot see; if *every* field is
dropped, the honest report is ``None`` -- "no telemetry" -- and never an empty
container. The difference is load-bearing rather than academic:
``esphome::format_hex_pretty()`` renders a MAC as ``D0.CF.13.0E.C9.2A`` and is
the obvious helper for firmware to reach for. If that mismatch yielded an empty
bond *set*, a proxy holding a full bond list would report "I have no bonds", and
BOND_LOST would fire across an entire fleet over nothing worse than a formatting
disagreement -- the module would not merely lose the reading, it would assert the
opposite of it. An empty input string, by contrast, is a proxy legitimately
reporting zero entries, and still parses to an empty container.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass

from .model import normalize_address

_LOGGER = logging.getLogger(__name__)

#: States that mean "this proxy is not talking to us right now". They must map
#: to None rather than an empty reading: a rebooting proxy drops its ESPHome
#: entities to `unavailable`, and reporting zero SMP failures at exactly the
#: moment a proxy is in trouble would be a lie.
_NO_SIGNAL = frozenset({"unavailable", "unknown", "none", "restored"})

#: A MAC is hex, not merely twelve characters long. Without this, any 12-char
#: field is reshaped into a plausible-looking address that correlates with no
#: real device -- and on the SMP side could name a phantom in an incident.
_COMPACT_MAC = re.compile(r"\A[0-9A-Fa-f]{12}\Z")
_EXPANDED_MAC = re.compile(r"\A(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\Z")

#: Plain decimals only. The firmware prints plain decimals; anything else --
#: a sign, an underscore separator, an exponent, ``nan``, ``inf`` -- is a
#: corrupt field, and non-finite values are the dangerous ones: NaN compares
#: False against every threshold (a stuck slot goes unreported) and infinity
#: trips them all (a permanent false alarm).
_COUNT = re.compile(r"\A[0-9]+\Z")
_SECONDS = re.compile(r"\A[0-9]+(?:\.[0-9]+)?\Z")


def expand_compact_mac(raw: str) -> str:
    """Turn ``d0cf130ec92a`` into ``D0:CF:13:0E:C9:2A``.

    An already-expanded address passes through, so the parser tolerates a
    firmware build that decides to send colons. Anything that is not one of
    those two shapes raises ``ValueError``; callers skip the field.
    """
    text = raw.strip()
    if ":" in text:
        if not _EXPANDED_MAC.match(text):
            raise ValueError(f"not an expanded MAC: {raw!r}")
        return normalize_address(text)
    if not _COMPACT_MAC.match(text):
        raise ValueError(f"not a compact MAC: {raw!r}")
    pairs = [text[i : i + 2] for i in range(0, 12, 2)]
    return normalize_address(":".join(pairs))


def _split(raw: str | None) -> list[str] | None:
    """Return the comma-separated fields, or None when there is no signal."""
    if raw is None:
        return None
    text = raw.strip()
    if text.lower() in _NO_SIGNAL:
        return None
    if not text:
        return []
    return [stripped for field in text.split(",") if (stripped := field.strip())]


def _drop(field: str, reason: object) -> None:
    """Record one dropped field.

    Silent disappearance is the failure mode this module is most exposed to,
    and the firmware is the one part of the contract CI cannot test, so the
    drop path is never wordless: without this line nothing distinguishes "the
    proxy reported nothing for that address" from "we threw it away".
    """
    _LOGGER.debug("BlueSight telemetry: dropped field %r (%s)", field, reason)


def _is_total_rejection(parsed: set | dict, fields: list[str], raw: str | None) -> bool:
    """True when the payload carried fields and not one of them survived.

    Warns rather than debugs: total rejection means the firmware and this parser
    disagree about the wire format, which is a bug in one of the two.
    """
    if not fields or parsed:
        return False
    # NOTE: the coordinator re-reads every poll, so a permanently mismatched
    # firmware warns on every cycle. Task 8 weighed throttling this and chose
    # not to; the reasoning is recorded in `telemetry_reader.read_proxy_telemetry`
    # (short version: the fix is a reported per-signal rejection flag with a
    # state surface, not a dedupe, and neither this pure parser nor the pure
    # reader may hold the state a dedupe needs).
    _LOGGER.warning(
        "BlueSight telemetry: discarding a reading whose %d field(s) were all "
        "unreadable (%r). Reporting it as absent rather than empty: an empty "
        "reading would assert that the proxy has nothing to report, which is "
        "the opposite of what is known. The firmware and telemetry.py disagree "
        "about the wire format.",
        len(fields),
        raw,
    )
    return True


def parse_addresses(raw: str | None) -> set[str] | None:
    """Parse a plain address list (the bond list)."""
    fields = _split(raw)
    if fields is None:
        return None
    out: set[str] = set()
    for field in fields:
        try:
            out.add(expand_compact_mac(field))
        except ValueError as err:
            # Firmware is the least trustworthy input we have: drop, never crash.
            _drop(field, err)
    if _is_total_rejection(out, fields, raw):
        return None
    return out


def _parse_count(text: str) -> int:
    if not _COUNT.match(text):
        raise ValueError(f"not a count: {text!r}")
    return int(text)


def _parse_seconds(text: str) -> float:
    if not _SECONDS.match(text):
        raise ValueError(f"not a duration: {text!r}")
    return float(text)


def _parse_mapping[T](raw: str | None, cast: Callable[[str], T]) -> dict[str, T] | None:
    """Parse ``address:value`` fields, dropping any the firmware mangled.

    Splits on the *last* colon so that an already-expanded address parses as
    one address rather than as ``D0`` plus an unreadable value; a compact
    address, which holds no colon at all, is unaffected.
    """
    fields = _split(raw)
    if fields is None:
        return None
    out: dict[str, T] = {}
    for field in fields:
        address, separator, value = field.rpartition(":")
        if not separator or not value:
            _drop(field, "no 'address:value' separator")
            continue
        try:
            out[expand_compact_mac(address)] = cast(value.strip())
        except ValueError as err:
            _drop(field, err)
    if _is_total_rejection(out, fields, raw):
        return None
    return out


def parse_counts(raw: str | None) -> dict[str, int] | None:
    """Parse ``MAC:count`` pairs (the SMP failure counters)."""
    return _parse_mapping(raw, _parse_count)


def parse_idle_seconds(raw: str | None) -> dict[str, float] | None:
    """Parse ``MAC:seconds`` pairs (per-connection time since GATT traffic)."""
    return _parse_mapping(raw, _parse_seconds)


@dataclass(frozen=True, slots=True)
class ProxyTelemetry:
    """One proxy's telemetry snapshot. ``None`` means the signal is absent.

    Frozen, and therefore **not hashable**: ``frozen=True`` asks the dataclass
    machinery for a ``__hash__`` built from the fields, and two of those fields
    are a dict and a set, so ``hash()`` raises ``TypeError``. Key on ``source``
    when a set member or a dict key is wanted.

    ``eq`` is deliberately left on. Turning it off would restore hashability,
    but only by falling back to identity semantics -- two snapshots with
    identical contents would then compare unequal, and any "has anything
    changed since the last poll?" check would answer "yes" forever. A loud
    ``TypeError`` from ``hash()`` beats a silent wrong answer from ``==``.
    """

    source: str
    smp_failures: dict[str, int] | None = None
    bonds: set[str] | None = None
    slot_idle_seconds: dict[str, float] | None = None

    @property
    def has_signal(self) -> bool:
        """True when at least one of the three sensors is reporting.

        Drives the per-proxy choice between SMP evidence and the heuristic.
        """
        return any(
            v is not None
            for v in (self.smp_failures, self.bonds, self.slot_idle_seconds)
        )



class CounterDeltas:
    """Turn per-proxy monotonic counters into per-snapshot event counts.

    Stateful across snapshots but pure: no clock, no I/O, no Home Assistant.

    Three rules carry the weight:

    * The first reading only establishes a baseline. The counter has been
      climbing since the proxy booted, possibly for weeks; replaying that
      history as fresh failures would open a storm incident on startup. The
      same applies per address: an address seen for the first time from a
      known proxy only baselines, because "absent last snapshot" does not
      mean "new" -- :func:`parse_counts` drops individual malformed fields,
      so it may equally be an address whose field has never yet parsed and
      whose counter has been climbing all along.
    * A decrease means the proxy rebooted, not that anything recovered. The
      baseline rearms at the new value and nothing is counted, so the climb
      back up is not counted a second time.
    * Keys are canonicalised on the way in, both the proxy and the address.
      Everything downstream correlates on the normalised form -- the storm
      window is keyed verbatim, and an incident address is matched against
      the device registry -- so a key that arrived in the wrong case would
      quietly open a second bucket for a device that already has one, and
      split one real storm into two counts below the threshold.

    Every difference between what happened and what is reported is an
    *under*-count, never an over-count: a reboot swallows the failures that
    preceded it in the same interval. That direction is deliberate. This class
    feeds the storm detector, and BlueSight's value is surfacing real
    incidents without crying wolf.
    """

    def __init__(self) -> None:
        self._baseline: dict[str, dict[str, int]] = {}

    @property
    def baselines(self) -> dict[str, dict[str, int]]:
        """A copy of every baseline, for diagnostics. Read-only by copy.

        Copied one level down rather than handed out: this is reached from a
        diagnostics download, and a caller holding the live inner dicts could
        rearm a counter -- which would replay a proxy's whole history as fresh
        failures, i.e. turn a read-only report into a fabricated storm.

        Worth exposing at all because the baseline is what decides whether a
        rising counter becomes an incident, and it is invisible everywhere
        else: too high and it silently swallows real failures, with nothing in
        the entity surface to show for it.
        """
        return {source: dict(counts) for source, counts in self._baseline.items()}

    def update(
        self, source: str, counts: dict[str, int] | None
    ) -> dict[str, int]:
        """Advance one snapshot for ``source``; return new events per address.

        ``counts`` of None means the telemetry is absent this snapshot. The
        baseline is deliberately kept rather than dropped: a proxy that blips
        unavailable would otherwise replay its entire counter on return.

        Addresses missing from an otherwise-present ``counts`` keep their
        baseline for the same reason, and this must stay that way. The parser
        drops malformed fields one at a time, so an address can vanish from
        one reading and return in the next; holding the baseline defers those
        failures to the next good reading, where pruning would either lose
        them or rearm and count them twice.
        """
        if counts is None:
            return {}
        previous = self._baseline.setdefault(normalize_address(source), {})
        new_events: dict[str, int] = {}
        for raw_address, current in counts.items():
            address = normalize_address(raw_address)
            before = previous.get(address)
            if before is not None and current > before:
                new_events[address] = current - before
            previous[address] = current
        return new_events

    def forget(self, source: str) -> None:
        """Drop a retired proxy's baselines (pairs with ``forget_proxy``).

        Normalises ``source`` because the ``forget_proxy`` service hands
        through whatever MAC the user typed. Without that, a lowercase call
        would report success while leaving the baseline in place, and a
        replacement proxy reusing the MAC would inherit a stranger's counter.
        """
        self._baseline.pop(normalize_address(source), None)
