"""The WebSocket commands the card calls: a device's timeline, a proxy's.

Published on demand rather than on a sensor. A timeline changes with every
connection anywhere in the house, so keeping it in an attribute would push a
fresh copy to every open dashboard each time; the card only needs it for the
one device or proxy a person tapped.

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
PROXY_COMMAND = f"{DOMAIN}/proxy_timeline"


@callback
def async_register_commands(hass: HomeAssistant) -> None:
    """Register the card's commands. Safe to call again on a reload."""
    websocket_api.async_register_command(hass, ws_device_timeline)
    websocket_api.async_register_command(hass, ws_proxy_timeline)


def _coordinator(hass, connection, msg):
    """The running coordinator, or None after telling the caller there is none."""
    entries = hass.config_entries.async_entries(DOMAIN)
    coordinator = getattr(entries[0], "runtime_data", None) if entries else None
    if coordinator is None:
        connection.send_error(msg["id"], "not_loaded", "BlueSight is not loaded")
    return coordinator


@websocket_api.websocket_command(
    {vol.Required("type"): COMMAND, vol.Required("address"): str}
)
@callback
def ws_device_timeline(hass, connection, msg) -> None:
    """Answer with the device's stays, or say BlueSight is not running."""
    coordinator = _coordinator(hass, connection, msg)
    if coordinator is not None:
        connection.send_result(msg["id"], coordinator.device_timeline(msg["address"]))


@websocket_api.websocket_command(
    {vol.Required("type"): PROXY_COMMAND, vol.Required("source"): str}
)
@callback
def ws_proxy_timeline(hass, connection, msg) -> None:
    """Answer with who held a slot on the proxy, or say BlueSight is not running."""
    coordinator = _coordinator(hass, connection, msg)
    if coordinator is not None:
        connection.send_result(msg["id"], coordinator.proxy_timeline(msg["source"]))
