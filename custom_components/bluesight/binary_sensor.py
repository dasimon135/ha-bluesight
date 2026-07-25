"""Global incident binary sensor for BlueSight.

A single ``binary_sensor.bluesight_incident`` that reports a PROBLEM whenever
the coordinator has any open incidents, whether slot-layer (deadlock, ghost
slot, storm) or proxy-health (offline, stalled, reboot storm). The full
incident list is exposed as state attributes for at-a-glance triage.
"""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import BlueSightConfigEntry
from .const import DOMAIN
from .coordinator import BlueSightCoordinator
from .model import ProxyHealth


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BlueSightConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the global incident sensor plus a per-proxy online sensor.

    The online sensors follow the same dynamic-add pattern as the slot
    sensors: a proxy can surface via its slot snapshot *or* its health
    snapshot, so newly-seen sources are drawn from the union of both.
    """
    coordinator = entry.runtime_data
    async_add_entities([IncidentBinarySensor(coordinator)])

    known_sources: set[str] = set()

    @callback
    def _add_new_proxies() -> None:
        new_entities: list[BinarySensorEntity] = []
        sources = {p.source for p in coordinator.data.proxies} | {
            h.source for h in coordinator.data.proxies_health
        }
        for source in sources:
            if source in known_sources:
                continue
            known_sources.add(source)
            new_entities.append(ProxyOnlineBinarySensor(coordinator, source))
        if new_entities:
            async_add_entities(new_entities)

    _add_new_proxies()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_proxies))


class IncidentBinarySensor(
    CoordinatorEntity[BlueSightCoordinator], BinarySensorEntity
):
    """On whenever the coordinator is tracking one or more incidents."""

    _attr_has_entity_name = True
    _attr_name = "Incident"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:bluetooth-alert"

    def __init__(self, coordinator: BlueSightCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_incident"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, "service")},
            name="BlueSight",
        )

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.data.incidents)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        incidents = self.coordinator.data.incidents
        return {
            "incident_count": len(incidents),
            "incidents": [
                {
                    "kind": incident.kind.value,
                    "address": incident.address,
                    "sources": incident.sources,
                    "detail": incident.detail,
                }
                for incident in incidents
            ],
        }


class ProxyOnlineBinarySensor(
    CoordinatorEntity[BlueSightCoordinator], BinarySensorEntity
):
    """On while a proxy is a current BLE scanner (per its health snapshot)."""

    _attr_has_entity_name = True
    _attr_name = "Online"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator: BlueSightCoordinator, source: str) -> None:
        super().__init__(coordinator)
        self._source = source
        self._attr_unique_id = f"{source}_online"
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
    def is_on(self) -> bool | None:
        health = self._health
        return health.online if health is not None else None
