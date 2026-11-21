"""Who held a slot on one proxy, and when: the other way round from the device view.

A device's timeline answers "where was it". A proxy's answers "what was on it",
which is the question an owner asks of the one that is always full. It is the
same stays read from the other side, so these tests pin that the two cannot
disagree, and that the answer stays readable on a proxy a lot of devices pass
through.
"""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.bluesight import api
from custom_components.bluesight.coordinator import (
    PROXY_TIMELINE_DEVICES,
    BlueSightCoordinator,
)
from custom_components.bluesight.coordinator_data import BlueSightData
from custom_components.bluesight.history import ConnectionHistory

A = "D0:CF:13:0F:05:5A"
B = "D0:CF:13:0E:C9:2A"
D1 = "1C:54:9E:8E:1D:01"
D2 = "1C:54:9E:8E:1D:02"
DAY = 86400.0


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _history() -> tuple[ConnectionHistory, Clock]:
    clock = Clock()
    history = ConnectionHistory(DAY, clock)
    history.update([])
    return history, clock


def _stay(history, clock, source, address, held_for, then_idle=0.0) -> None:
    history.update([(source, address)])
    clock.now += held_for
    history.update([])
    clock.now += then_idle


# --- the history ------------------------------------------------------------


def test_only_the_devices_that_used_the_proxy_are_listed() -> None:
    history, clock = _history()
    _stay(history, clock, A, D1, 600, 60)
    _stay(history, clock, B, D2, 600, 60)
    assert set(history.proxy_timeline(A)) == {D1}
    assert set(history.proxy_timeline(B)) == {D2}


def test_a_proxy_nobody_used_has_nothing() -> None:
    history, clock = _history()
    _stay(history, clock, A, D1, 600)
    assert history.proxy_timeline(B) == {}


def test_a_device_that_moved_shows_only_its_stays_through_this_proxy() -> None:
    history, clock = _history()
    _stay(history, clock, A, D1, 600, 600)
    _stay(history, clock, B, D1, 300, 300)
    _stay(history, clock, A, D1, 100)
    stays = history.proxy_timeline(A)[D1].stays
    assert [(round(a), round(b), s) for a, b, s in stays] == [
        (1900, 1300, A),
        (100, 0, A),
    ]


def test_the_source_is_matched_the_way_the_history_stores_it() -> None:
    history, clock = _history()
    _stay(history, clock, A, D1, 600)
    assert D1 in history.proxy_timeline(A.lower())


def test_the_two_views_merge_a_flap_the_same_way() -> None:
    history, clock = _history()
    _stay(history, clock, A, D1, 600, 30)
    _stay(history, clock, A, D1, 600)
    by_device = history.timeline(D1)
    by_proxy = history.proxy_timeline(A)[D1]
    assert by_proxy.stays == by_device.stays
    assert len(by_proxy.stays) == 1


# --- the coordinator --------------------------------------------------------


def _coordinator(history, device_names=None, proxy_names=None):
    stand_in = SimpleNamespace(
        _history=history,
        data=BlueSightData(
            device_names=device_names or {},
            proxy_display_names=proxy_names or {},
        ),
    )
    return stand_in


def _answer(history, source=A, **names):
    return BlueSightCoordinator.proxy_timeline(_coordinator(history, **names), source)


def test_the_answer_names_the_proxy_and_each_device() -> None:
    history, clock = _history()
    _stay(history, clock, A, D1, 600)
    answer = _answer(
        history,
        device_names={D1: "Madoka attic"},
        proxy_names={A: "Proxy Attic"},
    )
    assert answer["source"] == A
    assert answer["name"] == "Proxy Attic"
    assert answer["window_s"] == DAY
    assert answer["devices"][0]["name"] == "Madoka attic"
    assert answer["devices"][0]["address"] == D1
    assert answer["omitted"] == 0


def test_a_device_home_assistant_cannot_name_has_an_empty_name() -> None:
    history, clock = _history()
    _stay(history, clock, A, D1, 600)
    assert _answer(history)["devices"][0]["name"] == ""


def test_a_proxy_without_a_name_is_called_by_its_source() -> None:
    history, clock = _history()
    _stay(history, clock, A, D1, 600)
    assert _answer(history)["name"] == A


def test_stays_are_ages_in_seconds_and_oldest_first() -> None:
    history, clock = _history()
    _stay(history, clock, A, D1, 600, 600)
    _stay(history, clock, A, D1, 100, 0)
    assert _answer(history)["devices"][0]["stays"] == [[1300, 700], [100, 0]]


def test_the_longest_holders_come_first() -> None:
    history, clock = _history()
    _stay(history, clock, A, D1, 100, 10)
    _stay(history, clock, A, D2, 3000, 10)
    assert [d["address"] for d in _answer(history)["devices"]] == [D2, D1]


def test_a_crowded_proxy_is_cut_and_says_how_many_were_left_out() -> None:
    history, clock = _history()
    for i in range(PROXY_TIMELINE_DEVICES + 3):
        _stay(history, clock, A, f"1C:54:9E:00:00:{i:02X}", 100 + i, 5)
    answer = _answer(history)
    assert len(answer["devices"]) == PROXY_TIMELINE_DEVICES
    assert answer["omitted"] == 3


def test_what_the_timeline_rests_on_is_the_stays_not_the_connections() -> None:
    history, clock = _history()
    _stay(history, clock, A, D1, 600)
    assert _answer(history)["observed_s"] == round(history.stays_observed_s(), 1)


# --- the command ------------------------------------------------------------


class _Connection:
    def __init__(self) -> None:
        self.result = None
        self.error = None

    def send_result(self, msg_id, result) -> None:
        self.result = (msg_id, result)

    def send_error(self, msg_id, code, message) -> None:
        self.error = (msg_id, code, message)


def _hass(coordinator):
    entry = SimpleNamespace(runtime_data=coordinator)
    return SimpleNamespace(
        config_entries=SimpleNamespace(
            async_entries=lambda domain: [entry] if coordinator is not None else []
        )
    )


def test_the_command_is_named_for_the_integration() -> None:
    assert api.PROXY_COMMAND == "bluesight/proxy_timeline"


def test_the_command_answers_with_the_coordinators_reading() -> None:
    connection = _Connection()
    coordinator = SimpleNamespace(proxy_timeline=lambda source: {"source": source})
    api.ws_proxy_timeline(_hass(coordinator), connection, {"id": 4, "source": A})
    assert connection.result == (4, {"source": A})
    assert connection.error is None


def test_the_command_says_so_when_blue_sight_is_not_running() -> None:
    connection = _Connection()
    api.ws_proxy_timeline(_hass(None), connection, {"id": 5, "source": A})
    assert connection.result is None
    assert connection.error[1] == "not_loaded"


def test_both_commands_are_registered() -> None:
    registered = []
    from custom_components.bluesight import api as module

    original = module.websocket_api.async_register_command
    module.websocket_api.async_register_command = lambda hass, handler: registered.append(
        handler
    )
    try:
        module.async_register_commands(SimpleNamespace())
    finally:
        module.websocket_api.async_register_command = original
    assert module.ws_device_timeline in registered
    assert module.ws_proxy_timeline in registered
