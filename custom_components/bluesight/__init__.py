"""BlueSight — Home Assistant custom integration.

Makes the connection layer of Home Assistant's Bluetooth stack visible:
GATT slot allocations per ESPHome proxy, deadlocks (core issue #176516),
ghost slots, and pairing storms.

The config flow and platforms arrive in later tasks; setup here only
constructs the coordinator and stores it on the entry's ``runtime_data``.
"""
from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import config_validation as cv

from .const import (
    ATTR_SOURCE,
    DEFAULT_OFFLINE_GRACE_S,
    DEFAULT_POLL_INTERVAL_S,
    DEFAULT_REBOOT_THRESHOLD,
    DEFAULT_REBOOT_WINDOW_S,
    DEFAULT_STALLED_THRESHOLD_S,
    DEFAULT_STORM_THRESHOLD,
    DEFAULT_STORM_WINDOW_S,
    DOMAIN,
    SERVICE_FORGET_PROXY,
)
from .coordinator import BlueSightCoordinator
from .notify import NotificationManager

FORGET_PROXY_SCHEMA = vol.Schema({vol.Required(ATTR_SOURCE): cv.string})

type BlueSightConfigEntry = ConfigEntry[BlueSightCoordinator]

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]


async def async_setup_entry(
    hass: HomeAssistant, entry: BlueSightConfigEntry
) -> bool:
    """Set up BlueSight from a config entry.

    The config flow (Task 7) defines the option keys read here; until then
    they simply fall back to the module defaults.
    """
    opts = {**entry.data, **entry.options}
    coordinator = BlueSightCoordinator(
        hass,
        config_entry=entry,
        storm_window_s=opts.get("storm_window_s", DEFAULT_STORM_WINDOW_S),
        storm_threshold=opts.get("storm_threshold", DEFAULT_STORM_THRESHOLD),
        poll_interval_s=opts.get("poll_interval_s", DEFAULT_POLL_INTERVAL_S),
        stalled_threshold_s=opts.get(
            "stalled_threshold_s", DEFAULT_STALLED_THRESHOLD_S
        ),
        reboot_window_s=opts.get("reboot_window_s", DEFAULT_REBOOT_WINDOW_S),
        reboot_threshold=opts.get("reboot_threshold", DEFAULT_REBOOT_THRESHOLD),
        offline_grace_s=opts.get("offline_grace_s", DEFAULT_OFFLINE_GRACE_S),
    )
    await coordinator.async_setup()
    entry.runtime_data = coordinator

    # Fire/clear persistent notifications as incidents appear and resolve. The
    # manager rides on the coordinator instance so runtime_data stays the
    # coordinator (the entity platforms read it directly) and remains
    # retrievable at unload time.
    manager = NotificationManager(hass)
    coordinator.notification_manager = manager
    manager.async_update(coordinator.data.incidents)
    entry.async_on_unload(
        coordinator.async_add_listener(
            lambda: manager.async_update(coordinator.data.incidents)
        )
    )

    # Reload the entry when the user edits options so new tunables take effect.
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    _async_register_services(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


@callback
def _async_register_services(hass: HomeAssistant) -> None:
    """Register the integration-level actions (idempotent).

    BlueSight is single-instance, so the action resolves the one loaded entry
    itself rather than taking a target.
    """
    if hass.services.has_service(DOMAIN, SERVICE_FORGET_PROXY):
        return

    async def _forget_proxy(call: ServiceCall) -> None:
        """Stop tracking a proxy, clearing its ``proxy_offline`` incident.

        A retired or replaced proxy is remembered forever, so its offline
        incident can never resolve on its own. This is the escape hatch.
        """
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            coordinator: BlueSightCoordinator = entry.runtime_data
            if coordinator.forget_proxy(call.data[ATTR_SOURCE]):
                await coordinator.async_request_refresh()

    hass.services.async_register(
        DOMAIN, SERVICE_FORGET_PROXY, _forget_proxy, schema=FORGET_PROXY_SCHEMA
    )


async def _async_update_listener(
    hass: HomeAssistant, entry: BlueSightConfigEntry
) -> None:
    """Reload the entry so edited options are re-read at setup."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(
    hass: HomeAssistant, entry: BlueSightConfigEntry
) -> bool:
    """Unload the platforms first, then tear down the coordinator."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinator = entry.runtime_data
        manager = getattr(coordinator, "notification_manager", None)
        if manager is not None:
            manager.async_shutdown()
        await coordinator.async_shutdown()
    return unloaded
