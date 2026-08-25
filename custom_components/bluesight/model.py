"""Pure data model for BlueSight.

This module has no Home Assistant dependency and is fully unit-testable with
plain pytest.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


def normalize_address(addr: str) -> str:
    """Canonicalize a BLE MAC address for correlation.

    habluetooth yields upper-case MACs, so we uppercase (and strip
    surrounding whitespace) to make comparisons case-insensitive and
    consistent everywhere addresses are correlated or looked up.
    """
    return addr.strip().upper()


class IncidentKind(StrEnum):
    DEADLOCK = "deadlock"        # same address allocated on >=2 proxies (#176516)
    GHOST_SLOT = "ghost_slot"    # slot held while entity unavailable
    STORM = "storm"              # burst of connection failures
    BOND_LOST = "bond_lost"      # pairing failing with no bond on that proxy
    PROXY_OFFLINE = "proxy_offline"
    PROXY_STALLED = "proxy_stalled"
    PROXY_REBOOT_STORM = "proxy_reboot_storm"


#: Kinds whose ``sources`` say who *observed* the fault rather than *which*
#: fault it is, and which :attr:`Incident.key` therefore ignores.
#:
#: The criterion for a new kind is identity versus evidence: ask whether the
#: same address reported by two proxies is one fault or two.
#:
#: * Two -> ``sources`` are identity and belong in the key. ``GHOST_SLOT`` is a
#:   slot stuck *on a proxy* and ``BOND_LOST`` a missing entry in *a proxy's*
#:   own bond store, so each proxy is its own fault with its own remedy, and
#:   each must alert separately.
#: * One -> ``sources`` are evidence and belong here. A ``STORM`` is "this
#:   device keeps failing to connect"; which proxy measured that is how we know,
#:   not what broke -- the same argument that keeps ``evidence`` out of the key.
#:
#: The test is not "could two proxies appear?" but "would the user act twice?".
#: Getting it wrong in either direction is a real defect: a kind wrongly listed
#: here collapses two faults into one alert and hides the second, while a kind
#: wrongly left out re-alerts whenever its attribution shifts under a fault that
#: never stopped -- which is exactly what a storm does, because attribution
#: expires with the failure events that carry it while inferred failures keep
#: the incident open.
#:
#: The ``PROXY_*`` kinds carry ``sources == [address]``, so the question does
#: not arise for them and membership here would make no difference.
KINDS_WHOSE_SOURCES_ARE_EVIDENCE: frozenset[IncidentKind] = frozenset(
    {IncidentKind.STORM}
)


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
        """True only for a proxy that HAS slots and has none left.

        habluetooth registers non-connectable scanners with ``slots=0, free=0``
        (they cannot hold connections at all), so a bare ``free <= 0`` would
        report every passive scanner as saturated.
        """
        return self.slots > 0 and self.free <= 0

    @property
    def is_connectable(self) -> bool:
        """True if this proxy exposes any connection slot at all."""
        return self.slots > 0


@dataclass(frozen=True, slots=True)
class ProxyHealth:
    source: str
    name: str
    connectable: bool
    online: bool
    seconds_since_detection: float
    device_count: int


@dataclass(frozen=True, slots=True)
class Incident:
    kind: IncidentKind
    address: str
    sources: list[str] = field(default_factory=list)
    detail: str = ""
    #: Translation key and parameters for `detail`. `detail` itself stays a
    #: rendered human string: it is published in the incident attribute and
    #: real automations format notifications from it, so it is a contract.
    detail_key: str = ""
    detail_params: dict[str, str] = field(default_factory=dict)
    #: How this incident was observed: "smp" when the ESPHome telemetry
    #: component supplied real SMP evidence, "heuristic" when it was
    #: inferred from slot releases. Placed last so every existing
    #: positional construction of `detail_key`/`detail_params` keeps its
    #: meaning. Deliberately absent from `key`: the same physical fault is
    #: one incident however it was seen, so a proxy switching between the
    #: two must not raise a second alert for a fault that never stopped.
    evidence: str = "heuristic"

    @property
    def key(self) -> str:
        """Identity of this incident across snapshots.

        Deliberately excludes `detail_key` and `detail_params`: an incident
        whose parameters shift (a rising count, a renamed proxy) is the same
        incident, and folding them in would make it look new every snapshot
        and re-alert forever.

        `evidence` is excluded for the same reason: one physical fault is
        one incident however it was observed, so a proxy that gains or
        loses telemetry mid-fault must not re-alert.

        `sources` is folded in for every kind that does not appear in
        :data:`KINDS_WHOSE_SOURCES_ARE_EVIDENCE`, which states the rule and
        the criterion behind it. The excluded kinds keep carrying `sources`
        on the incident -- only its identity ignores them.
        """
        sources = (
            ""
            if self.kind in KINDS_WHOSE_SOURCES_ARE_EVIDENCE
            else ",".join(sorted(self.sources))
        )
        return f"{self.kind.value}:{self.address}:{sources}"
