"""Tests for the embedded Lovelace card registration.

``frontend`` keeps its Home Assistant import inside the one method that needs
it (the ``adapter.py`` pattern), so the module *imports* without HA. It does
not *run* without HA: ``async_register_path`` imports ``StaticPathConfig`` from
``homeassistant.components.http`` at call time, and every test here goes
through it. So the whole module is guarded — it is real coverage only where
Home Assistant is really installed (CI/Linux), never against the stub that
``conftest.py`` installs on a machine without HA.
"""
from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("homeassistant.components.http")

from custom_components.bluesight.frontend import CARD_FILENAME, JSModuleRegistration

VERSION = "0.4.0"
EXPECTED_URL = f"/bluesight/{CARD_FILENAME}?v={VERSION}"


class _Boom(Exception):
    """Whatever the resource collection may raise at us."""


class _FakeHttp:
    """Stand-in for ``hass.http``; records what it was asked to serve."""

    def __init__(self, raises: BaseException | None = None) -> None:
        self.calls: list[Any] = []
        self._raises = raises

    async def async_register_static_paths(self, configs: Any) -> None:
        if self._raises is not None:
            raise self._raises
        self.calls.append(configs)


class _FakeResources:
    """Stand-in for Lovelace's resource storage collection."""

    def __init__(self, items: list[dict] | None = None, loaded: bool = True) -> None:
        self._items = items if items is not None else []
        self.loaded = loaded
        self.created: list[dict] = []
        self.updated: list[tuple[str, dict]] = []
        self.load_calls = 0

    def async_items(self) -> list[dict]:
        return self._items

    async def async_load(self) -> None:
        self.load_calls += 1
        self.loaded = True

    async def async_create_item(self, item: dict) -> None:
        self.created.append(item)

    async def async_update_item(self, item_id: str, item: dict) -> None:
        self.updated.append((item_id, item))


class _ExplodingResources(_FakeResources):
    async def async_create_item(self, item: dict) -> None:
        raise _Boom("storage is unhappy")


class _FakeLovelace:
    def __init__(self, mode: str = "storage", resources: Any = None) -> None:
        self.mode = mode
        self.resources = resources if resources is not None else _FakeResources()


class _FakeHass:
    def __init__(self, lovelace: Any = None, http: Any = None) -> None:
        self.http = http if http is not None else _FakeHttp()
        self.data: dict[str, Any] = {}
        if lovelace is not None:
            self.data["lovelace"] = lovelace


def _reg(hass: _FakeHass) -> JSModuleRegistration:
    return JSModuleRegistration(hass, VERSION)


async def test_storage_mode_serves_the_file_and_creates_the_resource() -> None:
    """The whole point: the user adds nothing by hand."""
    lovelace = _FakeLovelace()
    hass = _FakeHass(lovelace)

    await _reg(hass).async_register()

    assert len(hass.http.calls) == 1
    assert lovelace.resources.created == [{"res_type": "module", "url": EXPECTED_URL}]


async def test_yaml_mode_still_serves_the_file_but_registers_no_resource() -> None:
    """YAML-mode Lovelace owns its resource list; we must not write to it.

    The static path is registered either way, so the documented one-line
    manual entry resolves.
    """
    lovelace = _FakeLovelace(mode="yaml")
    hass = _FakeHass(lovelace)

    await _reg(hass).async_register()

    assert len(hass.http.calls) == 1
    assert lovelace.resources.created == []
    assert lovelace.resources.updated == []


async def test_existing_resource_at_the_same_version_is_left_alone() -> None:
    """Setup runs on every restart; it must be idempotent."""
    resources = _FakeResources([{"id": "abc", "type": "module", "url": EXPECTED_URL}])
    hass = _FakeHass(_FakeLovelace(resources=resources))

    await _reg(hass).async_register()

    assert resources.created == []
    assert resources.updated == []


async def test_existing_resource_at_an_older_version_is_updated_in_place() -> None:
    """The query string is the cache-buster: a stale one serves stale JS."""
    resources = _FakeResources(
        [{"id": "abc", "type": "module", "url": f"/bluesight/{CARD_FILENAME}?v=0.3.1"}]
    )
    hass = _FakeHass(_FakeLovelace(resources=resources))

    await _reg(hass).async_register()

    assert resources.created == []
    assert resources.updated == [("abc", {"res_type": "module", "url": EXPECTED_URL})]


async def test_unrelated_resources_are_not_touched() -> None:
    """Other cards share this collection; matching must be on our URL alone."""
    other = {"id": "zzz", "type": "module", "url": "/local/other-card.js?v=9"}
    resources = _FakeResources([other])
    hass = _FakeHass(_FakeLovelace(resources=resources))

    await _reg(hass).async_register()

    assert resources.updated == []
    assert resources.created == [{"res_type": "module", "url": EXPECTED_URL}]


async def test_a_hand_added_local_copy_is_adopted_rather_than_duplicated() -> None:
    """Upgraders followed docs/card.md and already have /local/bluesight-card.js.

    Leaving it in place would define the ``bluesight-card`` element twice, so
    the old resource is rewritten to the served path instead of adding a
    second one.
    """
    resources = _FakeResources(
        [{"id": "old", "type": "module", "url": f"/local/{CARD_FILENAME}"}]
    )
    hass = _FakeHass(_FakeLovelace(resources=resources))

    await _reg(hass).async_register()

    assert resources.created == []
    assert resources.updated == [("old", {"res_type": "module", "url": EXPECTED_URL})]


async def test_unloaded_resource_collection_is_loaded_first() -> None:
    """During startup the collection may not have read its store yet.

    Reading ``async_items()`` before that would report an empty list and
    duplicate a resource the user already has.
    """
    resources = _FakeResources(loaded=False)
    hass = _FakeHass(_FakeLovelace(resources=resources))

    await _reg(hass).async_register()

    assert resources.load_calls == 1
    assert resources.created == [{"res_type": "module", "url": EXPECTED_URL}]


async def test_already_registered_static_path_is_not_fatal() -> None:
    """HA raises RuntimeError when a path is registered twice (reload)."""
    hass = _FakeHass(_FakeLovelace(), http=_FakeHttp(raises=RuntimeError("dup")))

    await _reg(hass).async_register()  # must not raise


async def test_missing_lovelace_still_serves_the_file() -> None:
    """A Lovelace-less setup must degrade, not break integration setup."""
    hass = _FakeHass()

    await _reg(hass).async_register()

    assert len(hass.http.calls) == 1


async def test_resource_write_failure_is_swallowed() -> None:
    """A card that fails to register must never take the integration down."""
    hass = _FakeHass(_FakeLovelace(resources=_ExplodingResources()))

    await _reg(hass).async_register()  # must not raise


async def test_the_file_is_served_before_lovelace_exists() -> None:
    """The halves are split so the card never 404s while HA finishes starting.

    At entry setup Lovelace is not loaded yet. Serving must not wait for it.
    """
    hass = _FakeHass()  # no lovelace at all

    await _reg(hass).async_register_path()

    assert len(hass.http.calls) == 1


async def test_resource_registration_reads_lovelace_when_it_runs() -> None:
    """Lovelace is read lazily, not latched at construction.

    The registration object is built at entry setup and reused on the started
    event; reading Lovelace in ``__init__`` would latch None and silently skip
    the registration forever.
    """
    hass = _FakeHass()
    registration = _reg(hass)
    await registration.async_register_path()

    # Lovelace arrives later, exactly as it does during startup.
    lovelace = _FakeLovelace()
    hass.data["lovelace"] = lovelace
    await registration.async_register_resource()

    assert lovelace.resources.created == [{"res_type": "module", "url": EXPECTED_URL}]


async def test_static_path_uses_the_real_home_assistant_config() -> None:
    """Contract check against the real ``StaticPathConfig`` (CI/Linux).

    ``path`` must point at the Python-free ``www`` subdirectory: serving the
    package directory itself would publish ``__init__.py`` over HTTP.
    """
    from homeassistant.components.http import StaticPathConfig

    hass = _FakeHass(_FakeLovelace())
    await _reg(hass).async_register()

    (configs,) = hass.http.calls
    (config,) = configs
    assert isinstance(config, StaticPathConfig)
    assert config.url_path == "/bluesight"
    assert config.path.name == "www"
    assert not list(config.path.glob("*.py"))
