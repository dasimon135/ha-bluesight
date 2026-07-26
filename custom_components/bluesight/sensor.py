"""Per-proxy GATT slot sensors for BlueSight.

Each ESPHome/Bluetooth proxy gets its own HA device carrying two sensors:
``Slots Used`` and ``Slots Free``. Proxies can appear after startup, so the
platform registers a coordinator listener that adds entities for newly-seen
proxy sources without ever creating duplicates.
"""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import BlueSightConfigEntry
from .const import DOMAIN
from .coordinator import BlueSightCoordinator
from .model import ProxyHealth, ProxySlots


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BlueSightConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up per-proxy sensors, adding entities for proxies as they appear.

    A proxy can surface via its slot snapshot *or* via its health snapshot
    (a scanner with zero used slots is still a proxy), so newly-seen sources
    are drawn from the union of both lists.
    """
    coordinator = entry.runtime_data
    known_sources: set[str] = set()

    @callback
    def _add_new_proxies() -> None:
        new_entities: list[SensorEntity] = []
        sources = {p.source for p in coordinator.data.proxies} | {
            h.source for h in coordinator.data.proxies_health
        }
        for source in sources:
            if source in known_sources:
                continue
            known_sources.add(source)
            new_entities.append(SlotsUsedSensor(coordinator, source))
            new_entities.append(SlotsFreeSensor(coordinator, source))
            new_entities.append(LastDeviceSeenSensor(coordinator, source))
        if new_entities:
            async_add_entities(new_entities)

    _add_new_proxies()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_proxies))


class _BaseSlotSensor(CoordinatorEntity[BlueSightCoordinator], SensorEntity):
    """Common wiring for a sensor scoped to one proxy source MAC."""

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "slots"

    def __init__(self, coordinator: BlueSightCoordinator, source: str) -> None:
        super().__init__(coordinator)
        self._source = source
        proxy = self._proxy
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, source)},
            name=proxy.name if proxy is not None else source,
        )

    @property
    def _proxy(self) -> ProxySlots | None:
        """Return the current snapshot for this source, or None if gone."""
        for proxy in self.coordinator.data.proxies:
            if proxy.source == self._source:
                return proxy
        return None

    @property
    def available(self) -> bool:
        """Unavailable once the proxy drops out of the coordinator snapshot."""
        return super().available and self._proxy is not None


class SlotsUsedSensor(_BaseSlotSensor):
    """Number of GATT slots currently allocated on a proxy."""

    _attr_name = "Slots Used"
    _attr_icon = "mdi:bluetooth-connect"

    def __init__(self, coordinator: BlueSightCoordinator, source: str) -> None:
        super().__init__(coordinator, source)
        self._attr_unique_id = f"{source}_slots_used"

    @property
    def native_value(self) -> int | None:
        proxy = self._proxy
        return proxy.used if proxy is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, object] | None:
        proxy = self._proxy
        if proxy is None:
            return None
        return {
            "total": proxy.slots,
            "free": proxy.free,
            "allocated": list(proxy.allocated),
            "source": proxy.source,
        }


class SlotsFreeSensor(_BaseSlotSensor):
    """Number of GATT slots still free on a proxy."""

    _attr_name = "Slots Free"
    _attr_icon = "mdi:bluetooth"

    def __init__(self, coordinator: BlueSightCoordinator, source: str) -> None:
        super().__init__(coordinator, source)
        self._attr_unique_id = f"{source}_slots_free"

    @property
    def native_value(self) -> int | None:
        proxy = self._proxy
        return proxy.free if proxy is not None else None


class LastDeviceSeenSensor(CoordinatorEntity[BlueSightCoordinator], SensorEntity):
    """Seconds since a proxy last detected any BLE advertisement.

    Reads the proxy's health snapshot rather than its slot allocation, so it
    stays meaningful for a scanning proxy that currently holds no slots.
    """

    _attr_has_entity_name = True
    _attr_name = "Last device seen"
    _attr_icon = "mdi:bluetooth-audio"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS

    def __init__(self, coordinator: BlueSightCoordinator, source: str) -> None:
        super().__init__(coordinator)
        self._source = source
        self._attr_unique_id = f"{source}_last_device_seen"
        health = self._health
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, source)},
            name=health.name if health is not None else source,
        )

    @property
    def _health(self) -> ProxyHealth | None:
        """Return this source's health snapshot, or None if it is gone."""
        for health in self.coordinator.data.proxies_health:
            if health.source == self._source:
                return health
        return None

    @property
    def available(self) -> bool:
        """Unavailable once the proxy drops out of the health snapshot."""
        return super().available and self._health is not None

    @property
    def native_value(self) -> int | None:
        health = self._health
        return round(health.seconds_since_detection) if health is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, object] | None:
        health = self._health
        if health is None:
            return None
        return {
            "device_count": health.device_count,
            "connectable": health.connectable,
            "online": health.online,
        }
