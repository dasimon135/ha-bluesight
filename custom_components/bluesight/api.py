"""The one WebSocket command the card calls: a device's timeline.

Published on demand rather than on a sensor. The timeline changes with every
connection anywhere in the house, so keeping it in an attribute would push a
fresh copy to every open dashboard each time; the card only needs it for the
one device a person tapped.

Read-only, and open to any signed-in user, like the state the card already
reads: it says which proxy held a device and when, nothing that is not already
visible on the dashboard.
"""
from __future__ import annotations

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN

COMMAND = f"{DOMAIN}/device_timeline"


@callback
def async_register_commands(hass: HomeAssistant) -> None:
    """Register the card's commands. Safe to call again on a reload."""
    websocket_api.async_register_command(hass, ws_device_timeline)


@websocket_api.websocket_command(
    {vol.Required("type"): COMMAND, vol.Required("address"): str}
)
@callback
def ws_device_timeline(hass, connection, msg) -> None:
    """Answer with the device's stays, or say BlueSight is not running."""
    entries = hass.config_entries.async_entries(DOMAIN)
    coordinator = getattr(entries[0], "runtime_data", None) if entries else None
    if coordinator is None:
        connection.send_error(msg["id"], "not_loaded", "BlueSight is not loaded")
        return
    connection.send_result(msg["id"], coordinator.device_timeline(msg["address"]))
