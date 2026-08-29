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
from homeassistant.const import PERCENTAGE, UnitOfTime
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
            new_entities.append(SaturationSensor(coordinator, source))
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
            # Published since 0.1 and read by user automations: raw address
            # strings, in habluetooth's order and spelling. Frozen by contract
            # -- `allocated_devices` was added beside it rather than replacing
            # it for exactly this reason.
            "allocated": list(proxy.allocated),
            # The same slots, named. Derived from `allocated` in the model, so
            # the two can never disagree about which slots are held. Always
            # present, `[]` on an idle proxy: an attribute that comes and goes
            # is a second shape for templates to handle.
            "allocated_devices": proxy.allocated_devices,
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


class SaturationSensor(CoordinatorEntity[BlueSightCoordinator], SensorEntity):
    """Share of the last day this proxy spent with no free slot.

    A pressure reading, not a fault. Nothing detects on it and no incident is
    raised from it: a proxy dedicated to three permanent connections is
    saturated *by design*, and the point at which busy becomes too busy is not
    knowable from one fleet. This publishes the measurement so that threshold
    can be chosen from data later -- the way `idle_threshold_s` went from an
    argued 300s to a measured 1800s.

    It is still worth reading today. A proxy sitting at 100% while its
    neighbours idle is the reason a device will go `unavailable` with no error
    days from now, and nothing else in Home Assistant can see it: it needs
    per-proxy slot accounting, which is what this integration is.
    """

    _attr_has_entity_name = True
    _attr_name = "Saturation (24h)"
    _attr_icon = "mdi:gauge"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, coordinator: BlueSightCoordinator, source: str) -> None:
        super().__init__(coordinator)
        self._source = source
        self._attr_unique_id = f"{source}_saturation_24h"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, source)})

    @property
    def _window(self):
        return self.coordinator.saturation_for(self._source)

    @property
    def native_value(self) -> float | None:
        """``None`` until the proxy has been observed at all.

        Zero would be a claim -- "comfortable all day" -- about a proxy nobody
        has watched yet. Unknown is the honest answer, and the difference
        matters most on the first snapshot after a restart, when every window
        is empty and every proxy would otherwise read as perfectly healthy.
        """
        window = self._window
        if window is None or window.observed_s() <= 0:
            return None
        return round(window.ratio() * 100, 1)

    @property
    def extra_state_attributes(self) -> dict[str, object] | None:
        """The three numbers a threshold will eventually be chosen from.

        The ratio alone cannot separate ten one-second squeezes from a single
        ten-minute lockout, and only the second is an outage. `observed_s`
        says how much evidence any of it rests on.
        """
        window = self._window
        if window is None:
            return None
        return {
            "longest_saturated_s": round(window.longest_s(), 1),
            "episodes": window.episodes(),
            "observed_s": round(window.observed_s(), 1),
            "window_s": window.window_s,
            "source": self._source,
        }
