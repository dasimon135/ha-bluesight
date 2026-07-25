"""Pure data model for BlueSight.

This module has no Home Assistant dependency and is fully unit-testable with
plain pytest.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


def normalize_address(addr: str) -> str:
    """Canonicalize a BLE MAC address for correlation.

    habluetooth yields upper-case MACs, so we uppercase (and strip
    surrounding whitespace) to make comparisons case-insensitive and
    consistent everywhere addresses are correlated or looked up.
    """
    return addr.strip().upper()


class IncidentKind(str, Enum):
    DEADLOCK = "deadlock"        # same address allocated on >=2 proxies (#176516)
    GHOST_SLOT = "ghost_slot"    # slot held while entity unavailable
    STORM = "storm"              # burst of connection failures
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
        return self.free <= 0


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

    @property
    def key(self) -> str:
        return f"{self.kind.value}:{self.address}:{','.join(sorted(self.sources))}"
