"""The fix flow behind the "was this proxy retired?" Repair."""
from __future__ import annotations

import pytest

pytest.importorskip("homeassistant.components.repairs")

from custom_components.bluesight import repairs as module
from custom_components.bluesight.proxy_retirement import issue_id_for

PROXY = "D8:3B:DA:11:22:35"


async def test_confirming_retires_the_proxy_the_issue_is_about(monkeypatch):
    retired: list[str] = []

    async def _retire(hass, source):
        retired.append(source)
        return True

    monkeypatch.setattr(module, "async_retire_proxy", _retire)
    flow = await module.async_create_fix_flow(
        object(), issue_id_for(PROXY), {"source": PROXY}
    )
    flow.hass = object()

    shown = await flow.async_step_init()
    assert shown["type"] == "form"           # a question, not an action
    assert retired == []

    done = await flow.async_step_confirm({})
    assert retired == [PROXY]
    assert done["type"] == "create_entry"


async def test_a_proxy_that_came_back_is_not_retired(monkeypatch):
    """Between the Repair being raised and the button being pressed the proxy
    may have returned. Refusing is `retire_proxy`'s rule; the flow says so
    instead of claiming a success that did not happen."""

    async def _refuse(hass, source):
        return False

    monkeypatch.setattr(module, "async_retire_proxy", _refuse)
    flow = await module.async_create_fix_flow(
        object(), issue_id_for(PROXY), {"source": PROXY}
    )
    flow.hass = object()
    result = await flow.async_step_confirm({})
    assert result["type"] == "abort"
    assert result["reason"] == "proxy_online"
