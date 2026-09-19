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
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, Platform
from homeassistant.core import CoreState, Event, HomeAssistant, ServiceCall, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.loader import async_get_integration

from .const import (
    ATTR_SOURCE,
    DEFAULT_BOND_THRESHOLD,
    DEFAULT_IDLE_SLOT_THRESHOLD_S,
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
from .device_index import looks_like_mac, own_proxy_records
from .frontend import JSModuleRegistration
from .locale import read_catalogues
from .model import normalize_address
from .notify import NotificationManager
from .rendering import Catalogue

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
    # Read the string catalogues once, off the event loop, and resolve the one
    # language this Home Assistant speaks. Incident details and notifications
    # are rendered from it on every snapshot, so it must never touch the disk
    # again after setup.
    catalogues = await hass.async_add_executor_job(read_catalogues)
    catalogue = Catalogue.for_language(hass.config.language, catalogues)
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
        idle_threshold_s=opts.get(
            "idle_threshold_s", DEFAULT_IDLE_SLOT_THRESHOLD_S
        ),
        bond_threshold=opts.get("bond_threshold", DEFAULT_BOND_THRESHOLD),
        catalogue=catalogue,
    )
    # "Seen online once, remembered for good" has to outlive the process: the
    # proxies this integration has a device for are the ones it has seen.
    # Before the first snapshot, so that snapshot already reports them.
    coordinator.remember_proxies(
        own_proxy_records(dr.async_get(hass).devices.values(), DOMAIN)
    )
    await coordinator.async_setup()
    entry.runtime_data = coordinator
    if hass.state is not CoreState.running:
        # An ESPHome proxy can reconnect minutes after this runs. The offline
        # grace period is patience with the proxy, not with the startup.
        @callback
        def _on_started(_event: Event) -> None:
            coordinator.restart_offline_clock()

        entry.async_on_unload(
            hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _on_started)
        )

    # Fire/clear persistent notifications as incidents appear and resolve. The
    # manager rides on the coordinator instance so runtime_data stays the
    # coordinator (the entity platforms read it directly) and remains
    # retrievable at unload time.
    manager = NotificationManager(hass, catalogue)
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
    await _async_setup_card(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_setup_card(
    hass: HomeAssistant, entry: BlueSightConfigEntry
) -> None:
    """Serve the Lovelace card, then register it as a resource.

    The two halves are deliberately not done together. Serving the file needs
    nothing but the HTTP component, so it happens now. Registering the resource
    needs Lovelace, which is not up until Home Assistant has finished starting
    — a minute or more after the UI is reachable on a large install. Waiting
    for that to serve the file too would leave the dashboard rendering
    "Custom element doesn't exist" in the meantime.
    """
    # The version comes from the loaded integration rather than a constant, so
    # the cache-busting URL cannot drift from manifest.json.
    integration = await async_get_integration(hass, DOMAIN)
    registration = JSModuleRegistration(hass, str(integration.version))
    await registration.async_register_path()

    if hass.state is CoreState.running:
        await registration.async_register_resource()
        return

    async def _on_started(_event: Event) -> None:
        await registration.async_register_resource()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _on_started)
    )


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
        source = normalize_address(call.data[ATTR_SOURCE])
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            coordinator: BlueSightCoordinator = entry.runtime_data
            if coordinator.retire_proxy(source):
                # Its device is this integration's memory of it; left in place
                # it would bring the proxy, and its alert, back at the next
                # restart.
                registry = dr.async_get(hass)
                for device in dr.async_entries_for_config_entry(
                    registry, entry.entry_id
                ):
                    if (DOMAIN, source) in {
                        (domain, normalize_address(value))
                        for domain, value in device.identifiers
                    }:
                        registry.async_remove_device(device.id)
            else:
                # Still a registered scanner: forgotten as before, and seen
                # again by the refresh below.
                coordinator.forget_proxy(source)
            await coordinator.async_request_refresh()

    hass.services.async_register(
        DOMAIN, SERVICE_FORGET_PROXY, _forget_proxy, schema=FORGET_PROXY_SCHEMA
    )


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: BlueSightConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Let the user delete the device of a proxy that is gone.

    Without this a retired proxy's device, and its five unavailable entities,
    had no Delete button at all. The hub device and a proxy that is still a
    registered scanner are refused.
    """
    coordinator = entry.runtime_data
    sources = [
        value
        for domain, value in device.identifiers
        if domain == DOMAIN and looks_like_mac(value)
    ]
    if not sources or not all(coordinator.retire_proxy(s) for s in sources):
        return False
    await coordinator.async_request_refresh()
    return True


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
