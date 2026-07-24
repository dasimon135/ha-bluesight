"""Global incident binary sensor for BlueSight.

A single ``binary_sensor.bluesight_incident`` that reports a PROBLEM whenever
the coordinator has any open incidents (deadlock, ghost slot, or storm). The
full incident list is exposed as state attributes for at-a-glance triage.
"""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import BlueSightConfigEntry
from .const import DOMAIN
from .coordinator import BlueSightCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BlueSightConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the single global incident binary sensor."""
    async_add_entities([IncidentBinarySensor(entry.runtime_data)])


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
