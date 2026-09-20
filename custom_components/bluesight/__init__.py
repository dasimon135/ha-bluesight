"""BlueSight — Home Assistant custom integration.

Makes the connection layer of Home Assistant's Bluetooth stack visible:
GATT slot allocations per ESPHome proxy, deadlocks (core issue #176516),
ghost slots, and pairing storms.

Setup builds the coordinator, stores it on the entry's ``runtime_data``, and
hangs everything else off it: the notifications, the card, the two platforms.
"""
from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, Platform
from homeassistant.core import CoreState, Event, HomeAssistant, ServiceCall, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.loader import async_get_integration

from .const import ATTR_SOURCE, DOMAIN, OPTION_DEFAULTS, SERVICE_FORGET_PROXY
from .coordinator import BlueSightCoordinator
from .device_index import looks_like_mac, own_proxy_records
from .events import IncidentEvents
from .frontend import JSModuleRegistration
from .locale import read_catalogues
from .model import normalize_address
from .notify import NotificationManager
from .proxy_retirement import RetirementIssues, async_retire_proxy
from .rendering import Catalogue

FORGET_PROXY_SCHEMA = vol.Schema({vol.Required(ATTR_SOURCE): cv.string})

type BlueSightConfigEntry = ConfigEntry[BlueSightCoordinator]

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]


async def async_setup_entry(
    hass: HomeAssistant, entry: BlueSightConfigEntry
) -> bool:
    """Set up BlueSight from a config entry.

    ``entry.options`` holds only what the options dialog actually saved, so
    every tunable starts from ``OPTION_DEFAULTS`` -- the same table the
    diagnostics dump reports, which is what keeps the two from disagreeing
    about what the integration is running at.
    """
    opts = {**OPTION_DEFAULTS, **entry.data, **entry.options}
    # Read the string catalogues once, off the event loop, and resolve the one
    # language this Home Assistant speaks. Incident details and notifications
    # are rendered from it on every snapshot, so it must never touch the disk
    # again after setup.
    catalogues = await hass.async_add_executor_job(read_catalogues)
    catalogue = Catalogue.for_language(hass.config.language, catalogues)
    coordinator = BlueSightCoordinator(
        hass,
        config_entry=entry,
        **{name: opts[name] for name in OPTION_DEFAULTS},
        catalogue=catalogue,
    )
    # "Seen online once, remembered for good" has to outlive the process: the
    # proxies this integration has a device for are the ones it has seen.
    # Before the first snapshot, so that snapshot already reports them.
    coordinator.remember_proxies(
        own_proxy_records(
            dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id),
            DOMAIN,
        )
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

    # Fire/clear persistent notifications as incidents appear and resolve, and
    # dismiss whatever is still up when the entry unloads, so a removed or
    # reloaded integration leaves no stale notification behind.
    manager = NotificationManager(hass, catalogue)
    # The same list on the bus, as `bluesight_incident` events, so an
    # automation is told an incident opened instead of having to notice.
    events = IncidentEvents(hass)
    # And, for a proxy that has stayed offline, a Repair offering to retire it.
    retirements = RetirementIssues(hass)

    @callback
    def _publish_incidents() -> None:
        data = coordinator.data
        manager.async_update(data.incidents)
        events.async_update(
            data.incidents, data.device_names, data.proxy_display_names
        )
        retirements.async_update(data.proxies_health, data.proxy_display_names)

    _publish_incidents()
    entry.async_on_unload(manager.async_shutdown)
    entry.async_on_unload(events.async_shutdown)
    entry.async_on_unload(retirements.async_shutdown)
    entry.async_on_unload(coordinator.async_add_listener(_publish_incidents))

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
        if await async_retire_proxy(hass, source):
            return
        # Still a registered scanner: forgotten as before, and seen again by
        # the refresh. Its device stays -- its entities are reporting.
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            entry.runtime_data.forget_proxy(source)
            await entry.runtime_data.async_request_refresh()

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
        await entry.runtime_data.async_shutdown()
    return unloaded
