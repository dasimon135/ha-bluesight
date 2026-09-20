"""Thin Home Assistant glue for the ``bluesight_incident`` event.

The twin of :mod:`.notify`: the same incident list, reconciled the same way,
turned into bus events instead of persistent notifications. Kept apart from it
because the two have different lifetimes at unload -- a notification left
behind by a removed integration is litter and gets dismissed, while an entry
being reloaded is not a fault going away and must not fire ``resolved``.

Everything that decides anything is in :mod:`.incident_policy`.
"""
from __future__ import annotations

from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .incident_policy import dedupe_incidents, event_payload
from .model import Incident

#: Fired once when an incident opens and once when it resolves.
EVENT_INCIDENT = f"{DOMAIN}_incident"


class IncidentEvents:
    """Fire ``bluesight_incident`` as incidents come and go.

    Identity is :attr:`Incident.key`, as for the notifications: an incident
    whose count climbs or whose attribution shifts is the same incident and
    fires nothing.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        # key -> the payload it opened with, minus the action. A resolved
        # incident is gone from the snapshot, and so may be the registry names
        # it was reported under, so what it *was* has to be kept here.
        self._open: dict[str, dict[str, object]] = {}

    @callback
    def async_update(
        self,
        incidents: list[Incident],
        device_names: dict[str, str],
        proxy_names: dict[str, str],
    ) -> None:
        """Fire ``opened`` for what appeared and ``resolved`` for what went."""
        current = {i.key: i for i in dedupe_incidents(incidents)}
        for key in sorted(self._open.keys() - current.keys()):
            self.hass.bus.async_fire(
                EVENT_INCIDENT, {**self._open.pop(key), "action": "resolved"}
            )
        for key, incident in current.items():
            if key in self._open:
                continue
            payload = event_payload("opened", incident, device_names, proxy_names)
            self._open[key] = payload
            self.hass.bus.async_fire(EVENT_INCIDENT, payload)

    @callback
    def async_shutdown(self) -> None:
        """Forget what is open, firing nothing: unloading resolves no fault."""
        self._open = {}
