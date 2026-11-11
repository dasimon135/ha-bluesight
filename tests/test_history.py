"""How often each device took a slot, and through how many proxies, in a day.

A device that keeps reconnecting is the early form of most of what BlueSight
detects: a storm is the same device failing faster, a deadlock usually starts
as a device bouncing between two proxies. The storm window only counts
*failures*, over five minutes; nothing counted the connections themselves, nor
noticed a device handed from one proxy to the next all day.

Published, not judged, like saturation: a sensor that wakes every five minutes
to report a reading connects 288 times a day by design.
"""
from __future__ import annotations

from custom_components.bluesight.history import ConnectionHistory, DeviceHistory

A = "D0:CF:13:0F:05:5A"
B = "D0:CF:13:0E:C9:2A"
DEV = "1C:54:9E:8E:1D:2C"
OTHER = "F0:B3:1E:87:AF:FE"
DAY = 86400.0


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _history() -> tuple[ConnectionHistory, Clock]:
    clock = Clock()
    return ConnectionHistory(DAY, clock), clock


def test_slots_already_held_at_startup_are_not_connections():
    """After a restart every held slot is new to us, and none of them just
    connected: counting them would start every day with a burst."""
    history, _ = _history()
    history.update([(A, DEV)])
    assert history.for_address(DEV) is None


def test_a_slot_that_appears_is_one_connection():
    history, clock = _history()
    history.update([])
    clock.now += 10
    history.update([(A, DEV)])
    assert history.for_address(DEV) == DeviceHistory(
        connections=1, proxy_changes=0, proxies=(A,)
    )


def test_a_slot_held_across_snapshots_is_counted_once():
    history, clock = _history()
    history.update([])
    for _ in range(5):
        clock.now += 30
        history.update([(A, DEV)])
    assert history.for_address(DEV).connections == 1


def test_a_reconnect_through_the_same_proxy_is_not_a_change():
    history, clock = _history()
    history.update([])
    for held in ([(A, DEV)], [], [(A, DEV)], [], [(A, DEV)]):
        clock.now += 30
        history.update(held)
    assert history.for_address(DEV) == DeviceHistory(
        connections=3, proxy_changes=0, proxies=(A,)
    )


def test_a_reconnect_through_another_proxy_is_a_change():
    history, clock = _history()
    history.update([])
    for held in ([(A, DEV)], [], [(B, DEV)], [], [(A, DEV)]):
        clock.now += 30
        history.update(held)
    assert history.for_address(DEV) == DeviceHistory(
        connections=3, proxy_changes=2, proxies=(B, A)
    )


def test_a_slot_moving_proxy_between_two_snapshots_is_a_connection_and_a_change():
    """The release and the new slot can land in one push burst, so the device
    is never seen without a slot. It still connected again, elsewhere."""
    history, clock = _history()
    history.update([])
    clock.now += 30
    history.update([(A, DEV)])
    clock.now += 30
    history.update([(B, DEV)])
    assert history.for_address(DEV) == DeviceHistory(
        connections=2, proxy_changes=1, proxies=(B, A)
    )


def test_addresses_and_sources_are_normalized():
    history, clock = _history()
    history.update([])
    clock.now += 1
    history.update([(A.lower(), DEV.lower())])
    assert history.for_address(DEV).proxies == (A,)
    assert history.for_address(DEV.lower()).connections == 1


def test_connections_older_than_the_window_age_out():
    history, clock = _history()
    history.update([])
    clock.now += 1
    history.update([(A, DEV)])
    clock.now += 1
    history.update([])
    clock.now += DAY
    history.update([])
    assert history.for_address(DEV) is None
    assert history.all() == {}


def test_a_change_is_judged_against_the_last_connection_even_if_it_aged_out():
    """Yesterday's proxy is still where the device was before today's first
    connection: moving from it is a change, even though the connection that
    put it there no longer counts."""
    history, clock = _history()
    history.update([])
    clock.now += 1
    history.update([(A, DEV)])
    clock.now += DAY - 10
    history.update([(A, DEV)])
    clock.now += 20
    history.update([(B, DEV)])
    assert history.for_address(DEV) == DeviceHistory(
        connections=1, proxy_changes=1, proxies=(B,)
    )


def test_all_lists_every_device_seen_connecting():
    history, clock = _history()
    history.update([])
    clock.now += 1
    history.update([(A, DEV), (B, OTHER)])
    assert set(history.all()) == {DEV, OTHER}


def test_observed_says_how_much_of_the_day_was_watched():
    """Zero connections in the hour after a restart is not a quiet day, it is
    an hour of evidence, and the sensor must be able to say so."""
    history, clock = _history()
    assert history.observed_s() == 0.0
    history.update([])
    clock.now += 3600
    assert history.observed_s() == 3600
    clock.now += 2 * DAY
    assert history.observed_s() == DAY
