"""BLE Triage — Home Assistant custom integration.

Makes the connection layer of Home Assistant's Bluetooth stack visible:
GATT slot allocations per ESPHome proxy, deadlocks (core issue #176516),
ghost slots, and pairing storms.

The config flow and platforms arrive in later tasks; setup here only
constructs the coordinator and stores it on the entry's ``runtime_data``.
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    DEFAULT_POLL_INTERVAL_S,
    DEFAULT_STORM_THRESHOLD,
    DEFAULT_STORM_WINDOW_S,
)
from .coordinator import BleTriageCoordinator

type BleTriageConfigEntry = ConfigEntry[BleTriageCoordinator]


async def async_setup_entry(
    hass: HomeAssistant, entry: BleTriageConfigEntry
) -> bool:
    """Set up BLE Triage from a config entry.

    The config flow (Task 7) defines the option keys read here; until then
    they simply fall back to the module defaults.
    """
    opts = {**entry.data, **entry.options}
    coordinator = BleTriageCoordinator(
        hass,
        config_entry=entry,
        storm_window_s=opts.get("storm_window_s", DEFAULT_STORM_WINDOW_S),
        storm_threshold=opts.get("storm_threshold", DEFAULT_STORM_THRESHOLD),
        poll_interval_s=opts.get("poll_interval_s", DEFAULT_POLL_INTERVAL_S),
    )
    await coordinator.async_setup()
    entry.runtime_data = coordinator
    # Reload the entry when the user edits options so new tunables take effect.
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    # No platforms forwarded yet; sensors arrive in a later task.
    return True


async def _async_update_listener(
    hass: HomeAssistant, entry: BleTriageConfigEntry
) -> None:
    """Reload the entry so edited options are re-read at setup."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(
    hass: HomeAssistant, entry: BleTriageConfigEntry
) -> bool:
    """Tear down the coordinator on unload."""
    await entry.runtime_data.async_shutdown()
    return True
