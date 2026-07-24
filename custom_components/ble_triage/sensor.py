"""Per-proxy GATT slot sensors for BLE Triage.

Each ESPHome/Bluetooth proxy gets its own HA device carrying two sensors:
``Slots Used`` and ``Slots Free``. Proxies can appear after startup, so the
platform registers a coordinator listener that adds entities for newly-seen
proxy sources without ever creating duplicates.
"""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import BleTriageConfigEntry
from .const import DOMAIN
from .coordinator import BleTriageCoordinator
from .model import ProxySlots


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BleTriageConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up slot sensors, adding entities for proxies as they appear."""
    coordinator = entry.runtime_data
    known_sources: set[str] = set()

    @callback
    def _add_new_proxies() -> None:
        new_entities: list[SensorEntity] = []
        for proxy in coordinator.data.proxies:
            if proxy.source in known_sources:
                continue
            known_sources.add(proxy.source)
            new_entities.append(SlotsUsedSensor(coordinator, proxy.source))
            new_entities.append(SlotsFreeSensor(coordinator, proxy.source))
        if new_entities:
            async_add_entities(new_entities)

    _add_new_proxies()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_proxies))


class _BaseSlotSensor(CoordinatorEntity[BleTriageCoordinator], SensorEntity):
    """Common wiring for a sensor scoped to one proxy source MAC."""

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "slots"

    def __init__(self, coordinator: BleTriageCoordinator, source: str) -> None:
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

    def __init__(self, coordinator: BleTriageCoordinator, source: str) -> None:
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

    def __init__(self, coordinator: BleTriageCoordinator, source: str) -> None:
        super().__init__(coordinator, source)
        self._attr_unique_id = f"{source}_slots_free"

    @property
    def native_value(self) -> int | None:
        proxy = self._proxy
        return proxy.free if proxy is not None else None
