"""Thin Home Assistant glue for BlueSight persistent notifications.

All of the decision logic (dedup/precedence, create/dismiss reconciliation,
notification parameters, id sanitizing) lives in the HA-free
:mod:`.incident_policy` module, and the wording itself in the string
catalogue. This manager only turns those decisions into
``persistent_notification`` create/dismiss calls and remembers which incidents
are currently surfaced so it can dismiss them when they resolve.

``persistent_notification`` is imported at module level (not inside methods) so
tests can swap it for a fake with ``monkeypatch.setattr`` and exercise the full
create/dismiss/resolve cycle without a running Home Assistant.
"""
from __future__ import annotations

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant, callback

from .incident_policy import (
    dedupe_incidents,
    notification_content,
    notification_id_for_key,
    reconcile,
)
from .model import Incident
from .rendering import Catalogue


class NotificationManager:
    """Fire and clear persistent notifications as incidents come and go.

    De-duplication is by :attr:`Incident.key`: the same physical fault reported
    on consecutive updates is notified once, and its notification is dismissed
    only when the incident actually disappears from the coordinator snapshot.
    """

    def __init__(self, hass: HomeAssistant, catalogue: Catalogue) -> None:
        """``catalogue`` is resolved once at setup, for Home Assistant's
        configured language; a persistent notification is written for the
        person running this Home Assistant, so it never varies per snapshot.
        """
        self.hass = hass
        self.catalogue = catalogue
        self._active_keys: set[str] = set()

    @callback
    def async_update(self, incidents: list[Incident]) -> None:
        """Reconcile the notification set against the latest incident list.

        Collapses redundant incidents first (one physical fault == one alert),
        then creates notifications for newly-appeared incidents and dismisses
        the ones that have resolved.
        """
        deduped = dedupe_incidents(incidents)
        to_create, to_dismiss = reconcile(self._active_keys, deduped)
        for incident in to_create:
            title, message = notification_content(incident, self.catalogue)
            persistent_notification.async_create(
                self.hass,
                message,
                title=title,
                notification_id=notification_id_for_key(incident.key),
            )
        for key in to_dismiss:
            persistent_notification.async_dismiss(
                self.hass, notification_id_for_key(key)
            )
        self._active_keys = {i.key for i in deduped}

    @callback
    def async_shutdown(self) -> None:
        """Dismiss every still-active notification (integration unload).

        Avoids leaving stale BlueSight notifications behind after the
        integration is removed or reloaded.
        """
        for key in self._active_keys:
            persistent_notification.async_dismiss(
                self.hass, notification_id_for_key(key)
            )
        self._active_keys = set()
