"""The card's one WebSocket command: a device's timeline.

Called with the handler and a recording connection, no Home Assistant instance:
the command is a thin read of the coordinator, so what matters is the shape the
card draws from and what it says when BlueSight is not running.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from custom_components.bluesight import api
from custom_components.bluesight.coordinator import BlueSightCoordinator
from custom_components.bluesight.coordinator_data import BlueSightData
from custom_components.bluesight.history import ConnectionHistory

DEV = "1C:54:9E:8E:1D:2C"
DAY = 86400.0


class _Connection:
    def __init__(self) -> None:
        self.result = None
        self.error = None

    def send_result(self, msg_id, result) -> None:
        self.result = (msg_id, result)

    def send_error(self, msg_id, code, message) -> None:
        self.error = (msg_id, code, message)


def _history(clock):
    history = ConnectionHistory(DAY, lambda: clock[0])
    history.update([])
    return history


def _coordinator(history, names=None):
    """What ``device_timeline`` reads, without building a real coordinator."""
    stand_in = SimpleNamespace(
        _history=history, data=BlueSightData(proxy_display_names=names or {})
    )
    return SimpleNamespace(
        device_timeline=lambda address: BlueSightCoordinator.device_timeline(
            stand_in, address
        )
    )


def _hass(coordinator):
    entry = SimpleNamespace(runtime_data=coordinator)
    return SimpleNamespace(
        config_entries=SimpleNamespace(
            async_entries=lambda domain: [entry] if coordinator is not None else []
        )
    )


def test_the_command_is_named_for_the_integration() -> None:
    assert api.COMMAND == "bluesight/device_timeline"


def test_the_timeline_names_each_proxy_once_and_points_stays_at_it() -> None:
    clock = [1000.0]
    history = _history(clock)
    clock[0] += 10
    history.update([("P1", DEV)])
    clock[0] += 100
    history.update([("P2", DEV)])
    clock[0] += 50
    connection = _Connection()
    hass = _hass(_coordinator(history, {"P1": "Proxy Salon"}))

    api.ws_device_timeline(hass, connection, {"id": 7, "address": DEV.lower()})

    msg_id, result = connection.result
    assert msg_id == 7
    assert result["address"] == DEV
    # Named as the user names the proxy; the address when unnamed.
    assert result["proxies"] == ["Proxy Salon", "P2"]
    assert result["stays"] == [[150, 50, 0], [50, 0, 1]]
    assert result["window_s"] == DAY
    assert result["truncated"] is False


def test_a_device_never_held_gets_an_empty_timeline_not_an_error() -> None:
    clock = [1000.0]
    connection = _Connection()
    api.ws_device_timeline(
        _hass(_coordinator(_history(clock))), connection, {"id": 1, "address": DEV}
    )
    _, result = connection.result
    assert result["stays"] == []
    assert result["proxies"] == []
    assert connection.error is None


def test_without_a_running_integration_it_says_so() -> None:
    connection = _Connection()
    api.ws_device_timeline(_hass(None), connection, {"id": 3, "address": DEV})
    assert connection.error == (3, "not_loaded", "BlueSight is not loaded")
    assert connection.result is None


def test_the_answer_is_plain_json() -> None:
    clock = [1000.0]
    history = _history(clock)
    clock[0] += 10
    history.update([("P1", DEV)])
    connection = _Connection()
    api.ws_device_timeline(
        _hass(_coordinator(history)), connection, {"id": 1, "address": DEV}
    )
    json.dumps(connection.result[1])
