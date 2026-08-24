"""Config and options flow tests for BlueSight.

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

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bluesight.const import DOMAIN
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType


@pytest.fixture(autouse=True)
def _auto_enable_custom_integrations(enable_custom_integrations):
    """Load custom_components.bluesight for every test in this module."""
    yield


@pytest.fixture(autouse=True)
def expected_lingering_timers() -> bool:
    """Tolerate Home Assistant's own Bluetooth scanner watchdog timer.

    Overrides the fixture of the same name from
    ``pytest_homeassistant_custom_component``. Since 0.4.0 the manifest
    depends on ``frontend`` and ``http`` so the integration can serve its
    card, which brings far more of Home Assistant up inside these tests --
    far enough that ``bluetooth`` arms
    ``BaseHaScanner._async_expire_devices_schedule_next()``
    (``homeassistant/components/bluetooth/__init__.py:404``).

    That timer belongs to Home Assistant core, not to BlueSight: nothing here
    creates it and nothing here can cancel it. The card registration itself
    leaks nothing -- its only long-lived registration is the
    ``EVENT_HOMEASSISTANT_STARTED`` listener, which is removed through
    ``entry.async_on_unload``.
    """
    return True


async def test_user_step_creates_entry(hass: HomeAssistant) -> None:
    """The user step needs no input and creates the sole entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "BlueSight"
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
    """Submitting the options form stores the tunables in options.

    Only the three storm/poll fields are supplied; the three proxy-health
    fields are ``Required`` with defaults, so voluptuous fills them in — the
    stored options therefore carry every key, not just the submitted ones.

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
        "stalled_threshold_s": 180.0,
        "reboot_window_s": 600.0,
        "reboot_threshold": 3,
        "offline_grace_s": 90.0,
    }
