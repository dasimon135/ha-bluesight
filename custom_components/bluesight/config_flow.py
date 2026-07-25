"""Config and options flow for BlueSight.

Single-instance integration: there is exactly one BLE stack per Home
Assistant, so only one config entry may exist. The user step needs no input
to start (sensible defaults cover everything); all tunables live in the
options flow.
"""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback

from .const import (
    DEFAULT_POLL_INTERVAL_S,
    DEFAULT_REBOOT_THRESHOLD,
    DEFAULT_REBOOT_WINDOW_S,
    DEFAULT_STALLED_THRESHOLD_S,
    DEFAULT_STORM_THRESHOLD,
    DEFAULT_STORM_WINDOW_S,
    DOMAIN,
)

# Field keys must match the option keys read in __init__.async_setup_entry.
CONF_STORM_WINDOW_S = "storm_window_s"
CONF_STORM_THRESHOLD = "storm_threshold"
CONF_POLL_INTERVAL_S = "poll_interval_s"
CONF_STALLED_THRESHOLD_S = "stalled_threshold_s"
CONF_REBOOT_WINDOW_S = "reboot_window_s"
CONF_REBOOT_THRESHOLD = "reboot_threshold"


def build_options_schema(options: dict[str, Any]) -> vol.Schema:
    """Voluptuous schema for the options form, defaulting from ``options``.

    Kept module-level and hass-free so the coercion/range rules can be unit
    tested under plain pytest without constructing a flow or a ``hass``.
    """
    return vol.Schema(
        {
            vol.Required(
                CONF_STORM_WINDOW_S,
                default=options.get(CONF_STORM_WINDOW_S, DEFAULT_STORM_WINDOW_S),
            ): vol.All(vol.Coerce(float), vol.Range(min=30)),
            vol.Required(
                CONF_STORM_THRESHOLD,
                default=options.get(CONF_STORM_THRESHOLD, DEFAULT_STORM_THRESHOLD),
            ): vol.All(vol.Coerce(int), vol.Range(min=2)),
            vol.Required(
                CONF_POLL_INTERVAL_S,
                default=options.get(CONF_POLL_INTERVAL_S, DEFAULT_POLL_INTERVAL_S),
            ): vol.All(vol.Coerce(int), vol.Range(min=5)),
            vol.Required(
                CONF_STALLED_THRESHOLD_S,
                default=options.get(
                    CONF_STALLED_THRESHOLD_S, DEFAULT_STALLED_THRESHOLD_S
                ),
            ): vol.All(vol.Coerce(float), vol.Range(min=30)),
            vol.Required(
                CONF_REBOOT_WINDOW_S,
                default=options.get(CONF_REBOOT_WINDOW_S, DEFAULT_REBOOT_WINDOW_S),
            ): vol.All(vol.Coerce(float), vol.Range(min=60)),
            vol.Required(
                CONF_REBOOT_THRESHOLD,
                default=options.get(CONF_REBOOT_THRESHOLD, DEFAULT_REBOOT_THRESHOLD),
            ): vol.All(vol.Coerce(int), vol.Range(min=2)),
        }
    )


class BlueSightConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the single-instance config flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create the sole entry.

        Single-instance enforcement lives in the manifest
        (``single_config_entry: true``): HA's flow manager auto-aborts a
        second entry with reason ``single_instance_allowed`` before this step
        runs, so no unique-id guard is needed here. Nothing to enter to start.
        """
        return self.async_create_entry(title="BlueSight", data={})

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> BlueSightOptionsFlow:
        """Return the options flow handler."""
        return BlueSightOptionsFlow()


class BlueSightOptionsFlow(OptionsFlow):
    """Expose the six tunables (storm window/threshold, poll interval,
    stalled threshold, reboot window/threshold).

    ``config_entry`` is provided by the flow manager in current HA
    (>=2024.11); it must NOT be assigned in ``__init__``.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show/handle the options form."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=build_options_schema(dict(self.config_entry.options)),
        )
