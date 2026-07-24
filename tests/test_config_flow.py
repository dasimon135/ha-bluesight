"""Config and options flow tests for BLE Triage.

These require the ``hass`` fixture from
``pytest-homeassistant-custom-component``, whose pytest plugin does not load
on Windows (it imports the Unix-only ``fcntl`` via the HA test runner). The
whole module is skipped when that plugin is unavailable, so the default
Windows ``python -m pytest`` run stays green. They are exercised on CI/Linux,
where the plugin loads and the ``-p no:homeassistant`` addopt is dropped.
"""
import pytest

# The bare package imports on Windows; it is the fixture-providing ``.plugins``
# submodule that pulls in the Unix-only ``fcntl`` and fails there. Skip on that
# so the default Windows run cleanly skips (rather than erroring on a missing
# ``hass`` fixture), while CI/Linux collects and runs these.
pytest.importorskip("pytest_homeassistant_custom_component.plugins")

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ble_triage.const import DOMAIN


@pytest.fixture(autouse=True)
def _auto_enable_custom_integrations(enable_custom_integrations):
    """Load custom_components.ble_triage for every test in this module."""
    yield


async def test_user_step_creates_entry(hass: HomeAssistant) -> None:
    """The user step needs no input and creates the sole entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "BLE Triage"
    assert result["data"] == {}


async def test_second_instance_aborts(hass: HomeAssistant) -> None:
    """A second user flow aborts as single-instance."""
    first = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert first["type"] is FlowResultType.CREATE_ENTRY

    second = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert second["type"] is FlowResultType.ABORT
    assert second["reason"] == "single_instance_allowed"


async def test_options_flow_round_trips_tunables(hass: HomeAssistant) -> None:
    """Submitting the options form stores the three tunables in options.

    The entry is added to hass but deliberately not set up: the round-trip
    into ``entry.options`` is a property of the options flow alone and should
    not drag in the coordinator/habluetooth wiring.
    """
    entry = MockConfigEntry(domain=DOMAIN, data={}, unique_id=DOMAIN)
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            "storm_window_s": 120,
            "storm_threshold": 3,
            "poll_interval_s": 10,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {
        "storm_window_s": 120.0,
        "storm_threshold": 3,
        "poll_interval_s": 10,
    }
