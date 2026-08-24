"""Serve and register the BlueSight Lovelace card from the integration.

HACS installs BlueSight as an *integration*, so it copies
``custom_components/bluesight/`` and nothing else. Shipping the card inside
that directory is therefore what makes HACS deliver it: the two manual steps
the docs used to require (copy into ``config/www/``, declare the resource)
disappear, and so does their silent failure mode — nothing crashed, the card
simply never appeared.

Two things happen here:

1. The card file is served over HTTP, always.
2. In storage-mode Lovelace (the default) the resource is registered too, so
   the card is usable with no user action at all. YAML-mode Lovelace owns its
   resource list from configuration, so we must not write to it; the served
   path means one documented line resolves.

Home Assistant imports stay inside the method that needs them, mirroring
``adapter.py``: it keeps the registration logic unit-testable off-Linux.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger(__name__)

#: URL prefix the card is served under.
URL_BASE = "/bluesight"

#: The card file, living in a Python-free ``www`` subdirectory. The static
#: path serves a whole directory, so pointing it at the package directory
#: would publish this module's source over HTTP.
CARD_FILENAME = "bluesight-card.js"
CARD_DIR = Path(__file__).parent / "www"


class JSModuleRegistration:
    """Serve the card, and register it as a Lovelace resource when we may."""

    def __init__(self, hass: Any, version: str) -> None:
        self.hass = hass
        self.version = version

    @property
    def lovelace(self) -> Any:
        """Read Lovelace lazily.

        Serving the file happens at entry setup, long before Lovelace is
        loaded; reading this in ``__init__`` would latch ``None`` and make the
        later resource registration a no-op.
        """
        return self.hass.data.get("lovelace")

    @property
    def url(self) -> str:
        """Versioned URL of the card.

        The query string is a cache-buster: browsers hold onto Lovelace
        modules, so an upgrade that reused the URL would keep serving the
        previous card until a hard refresh.
        """
        return f"{URL_BASE}/{CARD_FILENAME}?v={self.version}"

    async def async_register(self) -> None:
        """Serve the card and, in storage mode, register its resource."""
        await self.async_register_path()
        await self.async_register_resource()

    async def async_register_resource(self) -> None:
        """Register the Lovelace resource, if we may.

        Split from serving the file because only this half needs Lovelace, and
        Lovelace is not up until Home Assistant has finished starting. On a
        large install that is a minute or two after the UI is reachable —
        during which the card would 404 if the file waited too.
        """
        lovelace = self.lovelace
        if lovelace is None:
            _LOGGER.debug("Lovelace not loaded; card served but not registered")
            return

        # `mode` on older cores, `resource_mode` on newer ones. Default to
        # "yaml" when neither is readable: not writing is the safe failure.
        mode = getattr(lovelace, "mode", getattr(lovelace, "resource_mode", "yaml"))
        if mode != "storage":
            _LOGGER.debug("Lovelace in %s mode; resource left to the user", mode)
            return

        await self._async_register_module(lovelace)

    async def async_register_path(self) -> None:
        """Serve the card directory over HTTP."""
        from homeassistant.components.http import StaticPathConfig

        try:
            await self.hass.http.async_register_static_paths(
                [StaticPathConfig(URL_BASE, CARD_DIR, True)]
            )
        except RuntimeError:
            # Already registered: the entry is being reloaded, which is normal.
            _LOGGER.debug("Static path %s already registered", URL_BASE)

    async def _async_register_module(self, lovelace: Any) -> None:
        """Create or refresh our entry in the Lovelace resource collection.

        Never raises: a card that fails to register must not take the
        Bluetooth diagnostics down with it.
        """
        resources = getattr(lovelace, "resources", None)
        if resources is None:
            return

        try:
            # During startup the collection may not have read its store yet.
            # Listing it first would report empty and duplicate a resource the
            # user already has.
            if not getattr(resources, "loaded", True):
                await resources.async_load()
                resources.loaded = True

            existing = self._find_existing(resources.async_items() or [])
            if existing is None:
                await resources.async_create_item(
                    {"res_type": "module", "url": self.url}
                )
                _LOGGER.debug("Registered Lovelace resource %s", self.url)
                return

            if existing.get("url") != self.url:
                await resources.async_update_item(
                    existing["id"], {"res_type": "module", "url": self.url}
                )
                _LOGGER.debug("Updated Lovelace resource to %s", self.url)
        # Deliberately broad: the card is cosmetic, the diagnostics are not.
        except Exception:
            _LOGGER.warning(
                "Could not register the BlueSight card as a Lovelace resource; "
                "add %s manually if you want the card",
                self.url,
                exc_info=True,
            )

    def _find_existing(self, items: list[dict]) -> dict | None:
        """Return our resource among ``items``, ignoring the version suffix.

        Also matches a hand-added ``/local/bluesight-card.js`` from the old
        install instructions. Adopting that entry rather than adding a second
        one matters: two resources would load the module twice and the second
        ``customElements.define("bluesight-card", ...)`` would throw.
        """
        for item in items:
            url = str(item.get("url", ""))
            base = url.split("?", 1)[0]
            if base in (f"{URL_BASE}/{CARD_FILENAME}", f"/local/{CARD_FILENAME}"):
                return item
        return None
