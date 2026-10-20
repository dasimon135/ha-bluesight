"""The fix flow for the "was this proxy retired?" Repair.

One question, one button. Confirming applies the same retirement the
``bluesight.forget_proxy`` action does (see :mod:`.proxy_retirement`); doing
nothing leaves the proxy tracked, and the Repair clears by itself the moment
the proxy comes back.
"""
from __future__ import annotations

import voluptuous as vol

from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .proxy_retirement import async_retire_proxy, source_for_issue


class RetireProxyFlow(RepairsFlow):
    """Ask once, then retire the proxy the issue is about."""

    def __init__(self, source: str, proxy: str) -> None:
        self._source = source
        self._placeholders = {"proxy": proxy, "source": source}

    async def async_step_init(
        self, user_input: dict[str, str] | None = None
    ) -> FlowResult:
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, str] | None = None
    ) -> FlowResult:
        if user_input is None:
            return self.async_show_form(
                step_id="confirm",
                data_schema=vol.Schema({}),
                description_placeholders=self._placeholders,
            )
        if not await async_retire_proxy(self.hass, self._source):
            # It came back between the Repair being raised and the button being
            # pressed. Say so, rather than report a retirement that did not
            # happen; the Repair clears itself on the next snapshot.
            return self.async_abort(
                reason="proxy_online", description_placeholders=self._placeholders
            )
        return self.async_create_entry(data={})


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, str] | None
) -> RepairsFlow:
    """Called by the Repairs integration when the user opens one of ours."""
    data = data or {}
    source = data.get("source") or source_for_issue(issue_id) or ""
    return RetireProxyFlow(source, data.get("proxy") or source)
