"""Codegen for the BlueSight ESPHome telemetry component.

BlueSight's read-only invariant holds all the way into the firmware: this
component registers as a passive observer on the BLE event stream and opens no
connection, writes no bond and never calls into ``bluetooth_proxy``.

It publishes facts, never verdicts. Three text sensors carry raw counters and
raw idle times; every threshold and every judgement lives in the Home Assistant
integration, where pytest can reach it and where a retune needs no reflash.

The three sensor names are the data contract with
``custom_components/bluesight/telemetry_reader.py``, which discovers them by
the entity registry's ``original_name``. They are set here, in codegen, and
rejected if a user tries to override them: a rename is not a cosmetic change,
it makes an already-flashed proxy go dark.
"""

import logging

import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import esp32_ble, text_sensor
from esphome.const import CONF_ID, CONF_NAME, ENTITY_CATEGORY_DIAGNOSTIC
from esphome.core import CORE

_LOGGER = logging.getLogger(__name__)

CODEOWNERS = ["@dasimon135"]
# ``esp32_ble`` is where the GAP/GATTC event stream is fanned out from. It is a
# hard dependency rather than an AUTO_LOAD because a BlueSight node without a
# BLE stack has nothing to observe, and a silently auto-created ``esp32_ble:``
# would change the radio configuration of a working proxy.
DEPENDENCIES = ["esp32", "esp32_ble"]
AUTO_LOAD = ["text_sensor"]

CONF_SMP_FAILURES = "smp_failures"
CONF_BONDS = "bonds"
CONF_SLOTS = "slots"

#: The wire contract. Pinned on the Python side by
#: ``tests/test_telemetry_reader.py::test_the_sensor_names_are_the_wire_contract``;
#: pinned here by ``_only_the_contract_name``. Both halves must move together.
SMP_FAILURES_NAME = "BlueSight SMP failures"
BONDS_NAME = "BlueSight bonds"
SLOTS_NAME = "BlueSight slots"

#: The GATTC event stream only exists when something in the config pulls in the
#: BLE client stack: ``esp32_ble_tracker``'s ``to_code`` is what emits
#: ``USE_ESP32_BLE_CLIENT``, and without that define ESPHome's
#: ``ESP32BLE::add_gattc_event_callback`` is not compiled at all. Every
#: Bluetooth proxy has it (``bluetooth_proxy`` -> ``esp32_ble_client`` ->
#: ``esp32_ble_tracker``); a scan-only node does not.
_GATTC_PROVIDER = "esp32_ble_tracker"

bluesight_ns = cg.esphome_ns.namespace("bluesight")
BlueSightComponent = bluesight_ns.class_("BlueSightComponent", cg.PollingComponent)


def _only_the_contract_name(expected: str):
    """Reject any name but the one ``telemetry_reader.py`` looks for.

    Defaulting the name would be enough to make the common case work, but not
    enough to keep it working: an ``name:`` override in YAML is a one-line
    change that produces a proxy which flashes cleanly, boots cleanly, reports
    cleanly -- and is invisible to Home Assistant, because discovery matches on
    ``original_name``. That failure has no symptom to follow, so it is refused
    at validation time where the message can say why.
    """

    def validate(value):
        value = cv.string_strict(value)
        if value != expected:
            raise cv.Invalid(
                f"BlueSight names this sensor {expected!r} and the Home Assistant "
                f"integration discovers it by that name. Renaming it here would "
                f"make this proxy invisible to BlueSight. Rename the entity in "
                f"Home Assistant instead -- the integration matches on the "
                f"original name and survives that."
            )
        return value

    return validate


def _telemetry_sensor(name: str, icon: str) -> cv.Schema:
    """One of the three sensors, named by us and diagnostic by default."""
    return text_sensor.text_sensor_schema(
        icon=icon,
        entity_category=ENTITY_CATEGORY_DIAGNOSTIC,
    ).extend({cv.Optional(CONF_NAME, default=name): _only_the_contract_name(name)})


CONFIG_SCHEMA = cv.Schema(
    {
        cv.GenerateID(): cv.declare_id(BlueSightComponent),
        cv.GenerateID(esp32_ble.CONF_BLE_ID): cv.use_id(esp32_ble.ESP32BLE),
        # ``default={}`` materialises the whole sub-schema -- generated id
        # included -- so a bare ``bluesight:`` yields all three sensors and the
        # user never writes a name. Keeping them as schema entries rather than
        # fabricating a config dict in `to_code` is what lets ESPHome's normal
        # id pass and duplicate-name validation see them.
        cv.Optional(CONF_SMP_FAILURES, default={}): _telemetry_sensor(
            SMP_FAILURES_NAME, "mdi:key-alert"
        ),
        cv.Optional(CONF_BONDS, default={}): _telemetry_sensor(
            BONDS_NAME, "mdi:link-lock"
        ),
        cv.Optional(CONF_SLOTS, default={}): _telemetry_sensor(
            SLOTS_NAME, "mdi:bluetooth-connect"
        ),
    }
    # 60s is the *floor* on how stale an idle time can be, not a sampling rate:
    # slot open/close and SMP failures publish the moment they happen. The tick
    # exists so a slot that goes quiet keeps ageing visibly.
).extend(cv.polling_component_schema("60s"))


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)

    parent = await cg.get_variable(config[esp32_ble.CONF_BLE_ID])
    # Observation only. ESPHome fans each BLE event out to every registered
    # handler, so this sees exactly what `bluetooth_proxy` sees without holding
    # any of its connections.
    esp32_ble.register_gap_event_handler(parent, var)

    smp = await text_sensor.new_text_sensor(config[CONF_SMP_FAILURES])
    cg.add(var.set_smp_failures_text_sensor(smp))

    bonds = await text_sensor.new_text_sensor(config[CONF_BONDS])
    cg.add(var.set_bonds_text_sensor(bonds))

    if _GATTC_PROVIDER in CORE.loaded_integrations:
        esp32_ble.register_gattc_event_handler(parent, var)
        slots = await text_sensor.new_text_sensor(config[CONF_SLOTS])
        cg.add(var.set_slots_text_sensor(slots))
    else:
        # Deliberately no sensor rather than one reporting an empty list. An
        # empty string is the wire format's "reporting, zero connections",
        # which on a node that cannot observe connections at all would be a
        # measurement nobody took. No entity means no reading, and
        # `telemetry.py` reads that as absent -- which is the truth.
        _LOGGER.warning(
            "BlueSight: no BLE client stack in this configuration (%s is not "
            "loaded), so GATT connection slots cannot be observed and the %r "
            "sensor is not created. SMP failures and bonds are unaffected. "
            "A Bluetooth proxy always has this stack; a scan-only node does not.",
            _GATTC_PROVIDER,
            SLOTS_NAME,
        )
