"""Retiring a proxy: the one remedy BlueSight can apply itself.

Two callers reach it -- the ``bluesight.forget_proxy`` action and the Repair
this module raises for a proxy that has stayed offline -- so the act lives here
once, and both say the same thing when they refuse.

The Repair is the point. ``proxy_offline`` has two causes with opposite
remedies: the proxy is down, or it is gone for good. The notification speaks to
the first. For the second, the way out used to be finding the proxy's MAC and
calling an action by hand; the Repair offers it, by name, after the proxy has
been missing long enough that the question is worth asking.
"""
from __future__ import annotations

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN
from .incident_policy import retirement_candidates
from .model import ProxyHealth, normalize_address

#: How long a proxy stays offline before the Repair asks whether it was
#: retired. An hour, and not the offline grace period: an OTA update, a router
#: reboot or a power cut must not put that question in front of anyone. Not a
#: tunable -- nothing detects on it, and the notification has already spoken.
RETIRE_AFTER_S = 3600.0

_ISSUE_PREFIX = "proxy_retired_"


def issue_id_for(source: str) -> str:
    """The Repair's id for ``source``; stable, so it is created once."""
    return f"{_ISSUE_PREFIX}{normalize_address(source)}"


def source_for_issue(issue_id: str) -> str | None:
    """The proxy an issue id is about, or None if it is not one of these."""
    if not issue_id.startswith(_ISSUE_PREFIX):
        return None
    return issue_id[len(_ISSUE_PREFIX):]


async def async_retire_proxy(hass: HomeAssistant, source: str) -> bool:
    """Stop tracking ``source`` and delete its device. False if it is online.

    The device goes with the proxy because it is this integration's memory of
    it: left in place it would bring the proxy, and its alert, back at the next
    restart. A proxy that is still a registered scanner is refused -- deleting
    its device would orphan entities that are reporting.
    """
    source = normalize_address(source)
    retired = False
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        coordinator = entry.runtime_data
        if not coordinator.retire_proxy(source):
            continue
        retired = True
        registry = dr.async_get(hass)
        for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
            if (DOMAIN, source) in {
                (domain, normalize_address(value))
                for domain, value in device.identifiers
            }:
                registry.async_remove_device(device.id)
        await coordinator.async_request_refresh()
    return retired


class RetirementIssues:
    """Raise and clear the "was this proxy retired?" Repair, per proxy."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._raised: set[str] = set()

    @callback
    def async_update(
        self, proxies_health: list[ProxyHealth], proxy_names: dict[str, str]
    ) -> None:
        """Reconcile the Repairs against the latest health snapshot.

        ``proxy_names`` is ``BlueSightData.proxy_display_names``: what the
        card, the sensors and the incident text call each proxy, so the Repair
        does not introduce a second name for it.
        """
        wanted = {
            issue_id_for(h.source): h
            for h in retirement_candidates(proxies_health, RETIRE_AFTER_S)
        }
        for issue_id in sorted(self._raised - wanted.keys()):
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
        for issue_id, health in wanted.items():
            if issue_id in self._raised:
                continue
            name = proxy_names.get(health.source) or health.name or health.source
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=True,
                is_persistent=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="proxy_retired",
                translation_placeholders={"proxy": name, "source": health.source},
                # Handed to the fix flow, so it names the proxy without going
                # back to a registry that may have moved on since.
                data={"source": health.source, "proxy": name},
            )
        self._raised = set(wanted)

    @callback
    def async_shutdown(self) -> None:
        """Take every Repair down with the entry that raised it."""
        for issue_id in sorted(self._raised):
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
        self._raised = set()
