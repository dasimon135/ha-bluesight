"""Pure schema tests for the options flow.

These exercise ``build_options_schema`` directly, without a ConfigFlow
instance or a ``hass`` fixture, so they run under plain pytest on any OS.
Importing ``config_flow`` pulls in ``homeassistant.config_entries``; guard
with ``importorskip`` so the pure suite still collects where HA core cannot
import at all.
"""
import pytest

import voluptuous as vol

pytest.importorskip("homeassistant.config_entries")

from custom_components.bluesight.config_flow import build_options_schema
from custom_components.bluesight.const import (
    DEFAULT_POLL_INTERVAL_S,
    DEFAULT_STORM_THRESHOLD,
    DEFAULT_STORM_WINDOW_S,
)


def test_defaults_come_from_const_when_options_empty():
    validated = build_options_schema({})({})
    assert validated == {
        "storm_window_s": DEFAULT_STORM_WINDOW_S,
        "storm_threshold": DEFAULT_STORM_THRESHOLD,
        "poll_interval_s": DEFAULT_POLL_INTERVAL_S,
    }


def test_existing_options_override_defaults():
    validated = build_options_schema(
        {"storm_window_s": 120, "storm_threshold": 3, "poll_interval_s": 10}
    )({})
    assert validated["storm_window_s"] == 120.0
    assert validated["storm_threshold"] == 3
    assert validated["poll_interval_s"] == 10


def test_string_inputs_are_coerced_to_numbers():
    validated = build_options_schema({})(
        {"storm_window_s": "90", "storm_threshold": "4", "poll_interval_s": "15"}
    )
    assert validated["storm_window_s"] == 90.0
    assert isinstance(validated["storm_window_s"], float)
    assert validated["storm_threshold"] == 4
    assert validated["poll_interval_s"] == 15


@pytest.mark.parametrize(
    "field,value",
    [
        ("storm_window_s", 29),   # min 30
        ("storm_threshold", 1),   # min 2
        ("poll_interval_s", 4),   # min 5
    ],
)
def test_below_minimum_is_rejected(field, value):
    payload = {
        "storm_window_s": 300,
        "storm_threshold": 5,
        "poll_interval_s": 30,
        field: value,
    }
    with pytest.raises(vol.Invalid):
        build_options_schema({})(payload)
