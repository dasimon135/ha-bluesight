"""Pytest configuration for BlueSight tests.

Most of this suite is pure Python: the model, the detectors, the failure
windows, the incident policy, the rendering helpers and the entity *logic* are
all written so they can be exercised without a running Home Assistant.

That does not mean they can be *imported* without one. ``tests/test_model.py``
imports ``custom_components.bluesight.model``, and Python executes the parent
package's ``custom_components/bluesight/__init__.py`` on the way, which imports
``homeassistant.config_entries`` and friends at module scope. The same applies
to ``sensor.py``, ``binary_sensor.py`` and ``notify.py``, which subclass or call
into Home Assistant directly. So on a machine without Home Assistant installed
— which is every Windows dev box, because HA's dependency ``lru-dict`` needs a
C compiler — the whole suite failed at collection.

This module closes that gap with a deliberately small stub of the Home
Assistant symbols those module-scope imports need, installed into
``sys.modules`` **only when Home Assistant is genuinely absent** (detected with
``importlib.util.find_spec``, never by catching ``ImportError`` around a real
import). On CI, where Home Assistant is installed, nothing here engages and the
real packages are used, so CI keeps catching real import errors.

Three properties are load-bearing:

* **The stub is loud.** ``pytest_report_header`` prints a banner whenever it is
  active. A silent stub would make a broken environment look healthy.
* **The stub is not Home Assistant.** It carries none of HA's behaviour — no
  entity registry, no event loop, no state machine. A local green run proves
  the pure logic is correct; it proves nothing about integration with HA.
* **Tests that need the real thing still skip.** ``pytest.importorskip`` is
  wrapped while the stub is active so that a probe for a stubbed package skips
  rather than succeeding against the fake. Without that wrapper the guarded
  modules (``test_config_flow``, ``test_coordinator_shell``,
  ``test_diagnostics``, ``test_options_schema``, and the ``StaticPathConfig``
  test in ``test_frontend``) would run against a fake HA and report meaningless
  results.
"""
from __future__ import annotations

import enum
import sys
import types
from importlib.machinery import ModuleSpec
from importlib.util import find_spec

import pytest

#: Top-level packages this file is willing to fake. Kept explicit so the
#: ``importorskip`` wrapper below knows exactly which probes it must turn into
#: skips.
_STUBBED_ROOTS = frozenset({"homeassistant"})

#: Home Assistant is either installed or it is not; ask the import system
#: rather than trying an import and swallowing the failure, so a genuinely
#: broken HA install still explodes instead of quietly falling back to the stub.
_HA_INSTALLED = find_spec("homeassistant") is not None


def _module(name: str, *, package: bool = False) -> types.ModuleType:
    """Create, register and return one stub module."""
    module = types.ModuleType(name)
    module.__spec__ = ModuleSpec(name, None, is_package=package)
    module.__bluesight_stub__ = True
    if package:
        # An empty ``__path__`` makes this a package whose submodules can only
        # be the ones registered below: ``import homeassistant.components.http``
        # still fails, which is what keeps the guarded tests skipping.
        module.__path__ = []
        module.__spec__.submodule_search_locations = []
    sys.modules[name] = module
    return module


def _install_ha_stub() -> None:
    """Register the Home Assistant symbols BlueSight imports at module scope.

    Only symbols that are *evaluated* on import are provided. Every module in
    ``custom_components/bluesight`` uses ``from __future__ import annotations``,
    so type-only names (``HomeAssistant`` in a signature, ``AddEntitiesCallback``,
    ``EntityRegistry``) are never resolved and do not need to be faithful.
    """
    _module("homeassistant", package=True)

    # --- homeassistant.core --------------------------------------------------
    core = _module("homeassistant.core")

    class HomeAssistant:
        """Placeholder for the ``hass`` object; tests inject their own fake."""

    class ServiceCall:
        """Placeholder; only referenced in annotations."""

    class Event:
        """Placeholder; only referenced in annotations."""

    class CoreState(enum.Enum):
        """Only ``running`` is compared against in ``__init__.py``."""

        not_running = "NOT_RUNNING"
        starting = "starting"
        running = "running"
        stopping = "stopping"
        final_write = "final_write"
        stopped = "stopped"

    def callback(func):
        """HA's ``@callback`` is a marker decorator; identity is faithful."""
        return func

    core.HomeAssistant = HomeAssistant
    core.ServiceCall = ServiceCall
    core.Event = Event
    core.CoreState = CoreState
    core.callback = callback

    # --- homeassistant.const -------------------------------------------------
    const = _module("homeassistant.const")

    class Platform(enum.StrEnum):
        SENSOR = "sensor"
        BINARY_SENSOR = "binary_sensor"

    class UnitOfTime(enum.StrEnum):
        SECONDS = "s"

    const.Platform = Platform
    const.UnitOfTime = UnitOfTime
    const.EVENT_HOMEASSISTANT_STARTED = "homeassistant_started"

    # --- homeassistant.config_entries ---------------------------------------
    config_entries = _module("homeassistant.config_entries")

    class ConfigEntry:
        """Subscripted by the ``BlueSightConfigEntry`` type alias."""

        def __class_getitem__(cls, item):
            return cls

    config_entries.ConfigEntry = ConfigEntry

    # --- homeassistant.loader ------------------------------------------------
    loader = _module("homeassistant.loader")

    async def async_get_integration(hass, domain):
        raise NotImplementedError(
            "async_get_integration is stubbed; this code path needs real "
            "Home Assistant (CI)."
        )

    loader.async_get_integration = async_get_integration

    # --- homeassistant.helpers ----------------------------------------------
    helpers = _module("homeassistant.helpers", package=True)

    config_validation = _module("homeassistant.helpers.config_validation")

    def _cv_string(value):
        if value is None:
            raise ValueError("string value is None")
        return str(value)

    config_validation.string = _cv_string

    device_registry = _module("homeassistant.helpers.device_registry")

    class DeviceInfo(dict):
        """HA's ``DeviceInfo`` is a ``TypedDict``; a dict subclass matches use."""

    device_registry.DeviceInfo = DeviceInfo

    entity_registry = _module("homeassistant.helpers.entity_registry")

    entity_platform = _module("homeassistant.helpers.entity_platform")

    class AddEntitiesCallback:
        """Annotation-only in the platforms, but ``from ... import`` binds it."""

    entity_platform.AddEntitiesCallback = AddEntitiesCallback

    update_coordinator = _module("homeassistant.helpers.update_coordinator")

    class _Entity:
        """The sliver of ``homeassistant.helpers.entity.Entity`` used here.

        Real HA resolves each of these through ``cached_property`` plus an
        ``_attr_`` fallback; the entity classes under test set ``_attr_*`` in
        ``__init__`` and override the rest, so a plain fallback is enough.
        """

        @property
        def unique_id(self) -> str | None:
            return getattr(self, "_attr_unique_id", None)

        @property
        def name(self) -> str | None:
            return getattr(self, "_attr_name", None)

        @property
        def device_info(self):
            return getattr(self, "_attr_device_info", None)

        @property
        def device_class(self):
            return getattr(self, "_attr_device_class", None)

        @property
        def extra_state_attributes(self):
            return getattr(self, "_attr_extra_state_attributes", None)

        @property
        def available(self) -> bool:
            return getattr(self, "_attr_available", True)

    class DataUpdateCoordinator:
        """Base of ``BlueSightCoordinator``; only class creation is needed."""

        def __class_getitem__(cls, item):
            return cls

        def __init__(self, *args, **kwargs):
            raise NotImplementedError(
                "DataUpdateCoordinator is stubbed; coordinator construction "
                "needs real Home Assistant (see test_coordinator_shell.py)."
            )

    class CoordinatorEntity(_Entity):
        """Enough of the real class for entity-logic tests: a coordinator
        reference and the availability rule the entities call ``super()`` for.
        """

        def __class_getitem__(cls, item):
            return cls

        def __init__(self, coordinator, context=None) -> None:
            self.coordinator = coordinator
            self.coordinator_context = context

        @property
        def available(self) -> bool:
            return self.coordinator.last_update_success

    update_coordinator.DataUpdateCoordinator = DataUpdateCoordinator
    update_coordinator.CoordinatorEntity = CoordinatorEntity

    helpers.config_validation = config_validation
    helpers.device_registry = device_registry
    helpers.entity_registry = entity_registry
    helpers.entity_platform = entity_platform
    helpers.update_coordinator = update_coordinator

    # --- homeassistant.components -------------------------------------------
    components = _module("homeassistant.components", package=True)

    persistent_notification = _module(
        "homeassistant.components.persistent_notification"
    )

    def _no_persistent_notification(*args, **kwargs):
        raise NotImplementedError(
            "persistent_notification is stubbed; tests must monkeypatch "
            "custom_components.bluesight.notify.persistent_notification."
        )

    persistent_notification.async_create = _no_persistent_notification
    persistent_notification.async_dismiss = _no_persistent_notification

    sensor = _module("homeassistant.components.sensor")

    class SensorEntity(_Entity):
        @property
        def native_value(self):
            return getattr(self, "_attr_native_value", None)

    class SensorDeviceClass(enum.StrEnum):
        DURATION = "duration"

    class SensorStateClass(enum.StrEnum):
        MEASUREMENT = "measurement"

    sensor.SensorEntity = SensorEntity
    sensor.SensorDeviceClass = SensorDeviceClass
    sensor.SensorStateClass = SensorStateClass

    binary_sensor = _module("homeassistant.components.binary_sensor")

    class BinarySensorEntity(_Entity):
        @property
        def is_on(self) -> bool | None:
            return getattr(self, "_attr_is_on", None)

    class BinarySensorDeviceClass(enum.StrEnum):
        PROBLEM = "problem"
        CONNECTIVITY = "connectivity"

    binary_sensor.BinarySensorEntity = BinarySensorEntity
    binary_sensor.BinarySensorDeviceClass = BinarySensorDeviceClass

    components.persistent_notification = persistent_notification
    components.sensor = sensor
    components.binary_sensor = binary_sensor


def _install_importorskip_guard() -> None:
    """Make ``pytest.importorskip`` skip on any package the stub fakes.

    ``importorskip`` asks "is this module importable?", and while the stub is
    installed the honest answer for a stubbed package is "no — what you would
    get is a fake". Tests that probe for Home Assistant are asking for the real
    thing, so they must skip rather than run against the stub.
    """
    real_importorskip = pytest.importorskip

    def importorskip(modname: str, *args, **kwargs):
        if modname.split(".")[0] in _STUBBED_ROOTS:
            pytest.skip(
                f"{modname}: Home Assistant is not installed here; "
                "tests/conftest.py only stubs the parts BlueSight imports. "
                "This test needs the real package (CI/Linux).",
                allow_module_level=True,
            )
        return real_importorskip(modname, *args, **kwargs)

    pytest.importorskip = importorskip


HA_STUB_ACTIVE = not _HA_INSTALLED

if HA_STUB_ACTIVE:
    _install_ha_stub()
    _install_importorskip_guard()


def pytest_report_header() -> list[str]:
    """Say plainly whether this run used real Home Assistant."""
    if not HA_STUB_ACTIVE:
        return ["BlueSight: Home Assistant is installed; no stub in use."]
    return [
        "BlueSight: Home Assistant is NOT installed - tests/conftest.py "
        "installed a minimal stub.",
        "  Pure logic (model, detectors, windows, policy, entity logic) is "
        "real coverage.",
        "  HA integration is NOT covered here: HA-dependent tests skip, and "
        "the stub cannot",
        "  catch a mismatch with the real Home Assistant API. Only CI (Linux, "
        "HA installed) can.",
    ]
