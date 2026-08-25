"""Pure schema tests for the options flow.

These exercise ``build_options_schema`` directly, without a ConfigFlow
instance or a ``hass`` fixture, so they run under plain pytest on any OS.
Importing ``config_flow`` pulls in ``homeassistant.config_entries``; guard
with ``importorskip`` so the pure suite still collects where HA core cannot
import at all.
"""
import json
from pathlib import Path

import pytest
import voluptuous as vol

pytest.importorskip("homeassistant.config_entries")

from custom_components.bluesight.config_flow import build_options_schema
from custom_components.bluesight.const import (
    DEFAULT_IDLE_SLOT_THRESHOLD_S,
    DEFAULT_OFFLINE_GRACE_S,
    DEFAULT_POLL_INTERVAL_S,
    DEFAULT_REBOOT_THRESHOLD,
    DEFAULT_REBOOT_WINDOW_S,
    DEFAULT_STALLED_THRESHOLD_S,
    DEFAULT_STORM_THRESHOLD,
    DEFAULT_STORM_WINDOW_S,
)


def test_defaults_come_from_const_when_options_empty():
    validated = build_options_schema({})({})
    assert validated == {
        "storm_window_s": DEFAULT_STORM_WINDOW_S,
        "storm_threshold": DEFAULT_STORM_THRESHOLD,
        "poll_interval_s": DEFAULT_POLL_INTERVAL_S,
        "stalled_threshold_s": DEFAULT_STALLED_THRESHOLD_S,
        "reboot_window_s": DEFAULT_REBOOT_WINDOW_S,
        "reboot_threshold": DEFAULT_REBOOT_THRESHOLD,
        "offline_grace_s": DEFAULT_OFFLINE_GRACE_S,
        "idle_threshold_s": DEFAULT_IDLE_SLOT_THRESHOLD_S,
    }


def test_entry_without_idle_threshold_still_gets_the_constant():
    """Every entry created before 0.6.0 has no ``idle_threshold_s`` key.

    Opening the options dialog must pre-fill the field from ``const`` rather
    than leaving it blank, and the value it fills in must be the *same*
    constant ``__init__.async_setup_entry`` falls back to -- a literal copied
    here would let the pre-filled form silently change behaviour the moment
    the user pressed Submit without touching anything.
    """
    legacy = {
        "storm_window_s": 300.0,
        "storm_threshold": 5,
        "poll_interval_s": 30,
        "stalled_threshold_s": 180.0,
        "reboot_window_s": 600.0,
        "reboot_threshold": 3,
        "offline_grace_s": 90.0,
    }
    validated = build_options_schema(legacy)({})
    assert validated["idle_threshold_s"] == DEFAULT_IDLE_SLOT_THRESHOLD_S
    # ...and nothing else moved.
    assert {k: validated[k] for k in legacy} == legacy


def test_idle_threshold_is_coerced_and_overridable():
    validated = build_options_schema({"idle_threshold_s": "600"})({})
    assert validated["idle_threshold_s"] == 600.0
    assert isinstance(validated["idle_threshold_s"], float)


def test_idle_threshold_floor_is_enforced():
    """``detect_idle_slots`` has no internal guard by design: it flags every
    slot whose idle reading strictly exceeds the threshold. At 0 that is every
    connected device on the fleet, reported as a ghost slot. The bound is the
    only thing standing between the user and that, so it is pinned here."""
    for rejected in (0, 1, 30, 59, -1):
        with pytest.raises(vol.Invalid):
            build_options_schema({})({"idle_threshold_s": rejected})
    assert build_options_schema({})({"idle_threshold_s": 60})[
        "idle_threshold_s"
    ] == 60.0


def test_every_schema_field_is_named_in_strings_json():
    """A field with no entry in ``strings.json`` renders in the options dialog
    with its raw key ("idle_threshold_s") as the label. The per-language guards
    live in ``test_translation_files``; this one closes the other side of the
    seam, where the schema gains a field the string files never hear about."""
    strings = json.loads(
        (
            Path(__file__).resolve().parent.parent
            / "custom_components"
            / "bluesight"
            / "strings.json"
        ).read_text(encoding="utf-8")
    )
    init = strings["options"]["step"]["init"]
    fields = set(build_options_schema({})({}))
    assert fields == set(init["data"])
    assert fields == set(init["data_description"])


def test_offline_grace_accepts_zero_to_disable_it():
    """0 restores the pre-grace behaviour: report the first absent snapshot."""
    assert build_options_schema({})({"offline_grace_s": 0})["offline_grace_s"] == 0.0


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
        ("storm_window_s", 29),        # min 30
        ("storm_threshold", 1),        # min 2
        ("poll_interval_s", 4),        # min 5
        ("stalled_threshold_s", 10),   # min 30
        ("reboot_window_s", 59),       # min 60
        ("reboot_threshold", 1),       # min 2
    ],
)
def test_below_minimum_is_rejected(field, value):
    payload = {
        "storm_window_s": 300,
        "storm_threshold": 5,
        "poll_interval_s": 30,
        "stalled_threshold_s": 180,
        "reboot_window_s": 600,
        "reboot_threshold": 3,
        "idle_threshold_s": 300,
        field: value,
    }
    with pytest.raises(vol.Invalid):
        build_options_schema({})(payload)
