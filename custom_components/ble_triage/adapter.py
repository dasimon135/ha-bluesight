"""Isolated habluetooth slot-allocation surface for BLE Triage.

This is the ONLY module coupled to the habluetooth manager API. Everything
else depends on the stable interface exposed here, so a future
HA/habluetooth API change touches only this file.

Confirmed against habluetooth as bundled in Home Assistant 2026.7.4.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .model import ProxySlots


def get_manager() -> Any:
    """Return the global habluetooth manager.

    Isolated import: the single point coupled to habluetooth's package
    layout, kept out of module scope so the unit tests stay HA-free.
    """
    from habluetooth import get_manager as _gm

    return _gm()


def current_proxy_slots(
    manager: Any, name_for: Callable[[str], str]
) -> list[ProxySlots]:
    """Snapshot current per-proxy slot allocations as ProxySlots."""
    allocs = manager.async_current_allocations() or []
    return [
        ProxySlots(a.source, name_for(a.source), a.slots, a.free, list(a.allocated))
        for a in allocs
    ]


class SlotAdapter:
    """Subscribe to habluetooth allocation-change pushes and fan out a
    plain ``on_change()`` to the coordinator.

    Keeps unsubscribe handling in one place and makes ``stop()`` idempotent.
    """

    def __init__(self, manager: Any, on_change: Callable[[], None]) -> None:
        self._manager = manager
        self._on_change = on_change
        self._unsub: Callable[[], None] | None = None

    def start(self) -> None:
        self._unsub = self._manager.async_register_allocation_callback(
            lambda _alloc: self._on_change()
        )

    def stop(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
