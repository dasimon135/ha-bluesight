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
    DEFAULT_IDLE_SLOT_THRESHOLD_S,
    DEFAULT_OFFLINE_GRACE_S,
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
CONF_OFFLINE_GRACE_S = "offline_grace_s"
CONF_IDLE_THRESHOLD_S = "idle_threshold_s"


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
            vol.Required(
                CONF_OFFLINE_GRACE_S,
                default=options.get(CONF_OFFLINE_GRACE_S, DEFAULT_OFFLINE_GRACE_S),
            ): vol.All(vol.Coerce(float), vol.Range(min=0)),
            # Floored at one minute. `detect_idle_slots` carries no internal
            # guard by design -- it flags every slot whose idle reading
            # strictly exceeds this -- so an unbounded 0 would report the whole
            # connected fleet as ghost slots. The floor sits above the routine
            # silence of a healthy GATT link: a notify-on-change peripheral
            # says nothing between changes, and Home Assistant's own Bluetooth
            # stack does not consider a device stale for 60-90s either. It is
            # also >= the 30s floor on `stalled_threshold_s`, which is the
            # right ordering: that one measures advertisement silence, which
            # every device breaks every few seconds, where this one measures
            # GATT traffic, which a healthy connection may go minutes without.
            vol.Required(
                CONF_IDLE_THRESHOLD_S,
                default=options.get(
                    CONF_IDLE_THRESHOLD_S, DEFAULT_IDLE_SLOT_THRESHOLD_S
                ),
            ): vol.All(vol.Coerce(float), vol.Range(min=60)),
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
    """Expose the tunables: storm window/threshold, poll interval, stalled
    threshold, reboot window/threshold, the offline grace period, and the
    idle-slot threshold.

    The SMP-measured storm reuses the storm window and threshold above rather
    than taking a pair of its own: there is one storm concept in BlueSight,
    whether the failures were measured by the firmware or inferred by the
    coordinator, and one pair of knobs for it.

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
