"""The "this proxy is often full" Repair.

A saturated proxy is not broken, so this is not an incident: nothing is pushed,
nothing lands on the incident sensor, and no event fires. It is a Repair, which
is where Home Assistant puts "worth knowing, act if it applies" -- and which a
user who runs a proxy full on purpose can ignore for good. The rule itself is
pure and lives in :func:`.saturation.saturation_notices`.
"""
from __future__ import annotations

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN
from .model import normalize_address
from .saturation import saturation_notices

_ISSUE_PREFIX = "proxy_saturated_"


def issue_id_for(source: str) -> str:
    """The Repair's id for ``source``; stable, so ignoring it sticks."""
    return f"{_ISSUE_PREFIX}{normalize_address(source)}"


class SaturationIssues:
    """Raise and clear the saturation Repair, per proxy."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._raised: set[str] = set()

    @property
    def raised(self) -> set[str]:
        """The sources carrying the Repair now (a copy, for diagnostics)."""
        return set(self._raised)

    @callback
    def async_update(
        self,
        readings: dict[str, tuple[float, float]],
        proxy_names: dict[str, str],
    ) -> None:
        """Reconcile the Repairs against the latest saturation readings.

        The placeholders are refreshed on every update while the Repair is up,
        so the percentage it states is today's rather than the one it was
        raised at. ``async_create_issue`` on an existing id updates it in
        place, and leaves an ignored Repair ignored.
        """
        wanted = saturation_notices(readings, self._raised)
        for source in sorted(self._raised - wanted):
            ir.async_delete_issue(self.hass, DOMAIN, issue_id_for(source))
        for source in sorted(wanted):
            ratio, observed_s = readings[source]
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id_for(source),
                is_fixable=False,
                is_persistent=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="proxy_saturated",
                translation_placeholders={
                    "proxy": proxy_names.get(source) or source,
                    "source": source,
                    # Clamped: the ratio is a fraction of the observed span,
                    # and a reading taken on the very edge of a transition must
                    # not tell anyone a proxy was full 101 % of the time.
                    "percent": str(min(100, round(ratio * 100))),
                    "hours": str(round(observed_s / 3600)),
                },
            )
        self._raised = wanted

    @callback
    def async_shutdown(self) -> None:
        """Take every Repair down with the entry that raised it."""
        for source in sorted(self._raised):
            ir.async_delete_issue(self.hass, DOMAIN, issue_id_for(source))
        self._raised = set()
