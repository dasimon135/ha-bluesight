"""Isolated habluetooth slot-allocation surface for BlueSight.

This is the ONLY module coupled to the habluetooth manager API. Everything
else depends on the stable interface exposed here, so a future
HA/habluetooth API change touches only this file.

Confirmed against habluetooth as bundled in Home Assistant 2026.7.4.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from .model import ProxyHealth, ProxySlots, normalize_address

_LOGGER = logging.getLogger(__name__)


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
        ProxySlots(
            normalize_address(a.source),
            name_for(a.source),
            a.slots,
            a.free,
            list(a.allocated),
        )
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
        """Register the allocation callback; no-op if already started."""
        if self._unsub is not None:
            return
        self._unsub = self._manager.async_register_allocation_callback(
            lambda _alloc: self._on_change()
        )

    def stop(self) -> None:
        """Unsubscribe the allocation callback; no-op if already stopped."""
        if self._unsub is not None:
            self._unsub()
            self._unsub = None


def _scanner_health(scanner: Any) -> ProxyHealth:
    """Read one scanner's health, tolerating an absent optional attribute.

    ``time_since_last_detection`` and ``discovered_devices`` are the two
    habluetooth members most likely to move; a scanner subclass that lacks
    either degrades to a neutral value rather than taking the whole snapshot
    down (see :func:`current_proxy_health`).
    """
    since = getattr(scanner, "time_since_last_detection", None)
    devices = getattr(scanner, "discovered_devices", None)
    return ProxyHealth(
        source=scanner.source,
        name=(getattr(scanner, "name", None) or scanner.source),
        connectable=bool(getattr(scanner, "connectable", False)),
        online=True,
        seconds_since_detection=float(since()) if callable(since) else 0.0,
        device_count=len(devices) if devices is not None else 0,
    )


def current_proxy_health(manager: Any) -> list[ProxyHealth]:
    """Snapshot current per-proxy scanner health as ProxyHealth.

    A scanner that cannot be read is skipped rather than aborting the whole
    snapshot: this runs every poll interval, so one misbehaving scanner would
    otherwise blank every entity the integration owns.
    """
    scanners = manager.async_current_scanners() or []
    health: list[ProxyHealth] = []
    for scanner in scanners:
        try:
            health.append(_scanner_health(scanner))
        except (AttributeError, TypeError, ValueError):
            _LOGGER.debug(
                "Skipping unreadable scanner %r in health snapshot",
                scanner,
                exc_info=True,
            )
    return health


class ScannerAdapter:
    """Subscribe to habluetooth scanner-registration events. Fires on_change on
    every event and on_removed(source) when a scanner is removed (the reboot
    signal). Mirrors SlotAdapter: single coupling point, idempotent start/stop.
    """

    def __init__(
        self,
        manager: Any,
        on_change: Callable[[], None],
        on_removed: Callable[[str], None],
    ) -> None:
        self._manager = manager
        self._on_change = on_change
        self._on_removed = on_removed
        self._unsub: Callable[[], None] | None = None

    def start(self) -> None:
        """Register the scanner-registration callback (no-op if already started)."""
        if self._unsub is not None:
            return
        self._unsub = self._manager.async_register_scanner_registration_callback(
            self._handle, None
        )

    def _handle(self, registration: Any) -> None:
        event = getattr(registration.event, "value", registration.event)
        if event == "removed":
            self._on_removed(registration.scanner.source)
        self._on_change()

    def stop(self) -> None:
        """Unregister the callback (idempotent)."""
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
