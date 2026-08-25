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
        """
        return f"{self.kind.value}:{self.address}:{','.join(sorted(self.sources))}"
