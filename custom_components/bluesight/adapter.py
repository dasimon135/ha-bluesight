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

from .model import DeviceRef, ProxyHealth, ProxySlots, normalize_address

_LOGGER = logging.getLogger(__name__)


def get_manager() -> Any:
    """Return the global habluetooth manager.

    Isolated import: the single point coupled to habluetooth's package
    layout, kept out of module scope so the unit tests stay HA-free.
    """
    from habluetooth import get_manager as _gm

    return _gm()


def _resolve_devices(
    allocated: list[str], device_for: Callable[[str], DeviceRef | None] | None
) -> dict[str, DeviceRef]:
    """Ask ``device_for`` who holds each allocated address.

    The resolver is handed the **canonical** spelling, because the thing on the
    other end of it is the device-registry index, which is keyed by
    :func:`normalize_address`. Passing habluetooth's raw string instead would
    resolve nothing, silently, for every device -- and the resulting card would
    look like a display bug rather than a mismatch between two subsystems'
    idea of an address. The same canonical key is what
    :attr:`ProxySlots.allocated_devices` looks the answers back up by.

    One lookup per distinct address, however many slots it holds.

    Any exception from the resolver costs that one name and nothing else. The
    breadth is deliberate: this runs inside the snapshot every entity the
    integration owns is built from, and a friendly name is not worth blanking
    the slot counts, the incidents and the proxy health for. Debug, not warn --
    the availability path already warns loudly when the same registry breaks
    under it, and that failure actually changes a verdict.
    """
    devices: dict[str, DeviceRef] = {}
    if device_for is None:
        return devices
    for address in dict.fromkeys(normalize_address(a) for a in allocated):
        try:
            ref = device_for(address)
        # Broad on purpose; see the docstring. A name is never worth a blank
        # snapshot, so nothing the resolver can raise may escape here.
        except Exception:
            _LOGGER.debug(
                "Could not resolve a device name for %s; showing the raw "
                "address instead",
                address,
                exc_info=True,
            )
            continue
        if ref is not None:
            devices[address] = ref
    return devices


def current_proxy_slots(
    manager: Any,
    name_for: Callable[[str], str],
    device_for: Callable[[str], DeviceRef | None] | None = None,
) -> list[ProxySlots]:
    """Snapshot current per-proxy slot allocations as ProxySlots.

    ``name_for`` names the **proxy**; ``device_for`` names the **devices
    holding its slots**, and is injected the same way and for the same reason:
    both answers come from Home Assistant, and this module is the only one
    coupled to habluetooth. A caller that wants slot counts alone may omit it,
    and every allocated address then reads as unresolved.
    """
    allocs = manager.async_current_allocations() or []
    proxies: list[ProxySlots] = []
    for a in allocs:
        allocated = list(a.allocated)
        proxies.append(
            ProxySlots(
                normalize_address(a.source),
                name_for(a.source),
                a.slots,
                a.free,
                allocated,
                _resolve_devices(allocated, device_for),
            )
        )
    return proxies


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
