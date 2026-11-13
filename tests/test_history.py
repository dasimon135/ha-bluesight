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


# --- surviving a restart ------------------------------------------------------


def _busy_history() -> tuple[ConnectionHistory, Clock]:
    history, clock = _history()
    history.update([])
    for held in ([(A, DEV)], [], [(B, DEV)], [], [(B, OTHER)]):
        clock.now += 600
        history.update(held)
    return history, clock


def test_a_restored_history_reads_what_the_saved_one_read():
    """Home Assistant restarts several times a week, and a day's counts that
    start over each time are not a day's counts."""
    saved, clock = _busy_history()
    clock.now += 300
    expected = saved.all()
    data = saved.export()

    fresh, fresh_clock = _history()
    fresh_clock.now = 50_000.0            # a different process, a different clock
    fresh.restore(data, downtime_s=0.0)
    assert fresh.all() == expected
    assert fresh.observed_s() == saved.observed_s()


def test_downtime_ages_the_restored_events_but_is_not_observed():
    saved, clock = _busy_history()
    data = saved.export()
    observed = saved.observed_s()

    fresh, fresh_clock = _history()
    fresh_clock.now = 50_000.0
    fresh.restore(data, downtime_s=3600.0)
    # Nothing was watched while Home Assistant was down.
    assert fresh.observed_s() == observed
    fresh_clock.now += 60
    assert fresh.observed_s() == observed + 60
    # And the events are an hour older than they were: past the window, gone.
    fresh_clock.now += DAY
    fresh.update([])
    assert fresh.all() == {}


def test_the_first_snapshot_after_a_restore_is_still_a_baseline():
    """A slot held when Home Assistant comes back is not a connection: the
    device may or may not have reconnected while nothing was watching."""
    saved, _clock = _busy_history()
    fresh, fresh_clock = _history()
    fresh.restore(saved.export(), downtime_s=0.0)
    before = fresh.all()
    fresh_clock.now += 30
    fresh.update([(B, DEV), (A, OTHER)])
    assert fresh.all() == before
    fresh_clock.now += 30
    fresh.update([(B, DEV)])
    fresh_clock.now += 30
    fresh.update([(B, DEV), (A, OTHER)])
    assert fresh.for_address(OTHER).connections == before[OTHER].connections + 1


def test_a_change_is_still_judged_against_the_last_connection_after_a_restore():
    saved, _clock = _busy_history()
    last = saved.for_address(DEV)
    fresh, fresh_clock = _history()
    fresh.restore(saved.export(), downtime_s=60.0)
    fresh_clock.now += 30
    fresh.update([])                      # baseline
    fresh_clock.now += 30
    fresh.update([(A, DEV)])              # DEV last connected via B
    assert fresh.for_address(DEV).proxy_changes == last.proxy_changes + 1


def test_the_export_is_plain_json():
    import json

    saved, _clock = _busy_history()
    assert json.loads(json.dumps(saved.export())) == saved.export()


def test_a_restore_into_a_history_already_running_changes_nothing():
    """Only a fresh history takes a restore; one that has started watching
    must not have its own past overwritten by a stale file."""
    saved, _clock = _busy_history()
    live, live_clock = _history()
    live.update([])
    live_clock.now += 10
    live.update([(A, OTHER)])
    live.restore(saved.export(), downtime_s=0.0)
    assert set(live.all()) == {OTHER}


def test_down_for_longer_than_the_window_restores_nothing():
    saved, _clock = _busy_history()
    fresh, _fresh_clock = _history()
    fresh.restore(saved.export(), downtime_s=DAY)
    assert fresh.observed_s() == 0.0
    assert fresh.all() == {}


def test_observed_is_limited_to_what_the_window_still_reaches():
    saved, _clock = _busy_history()        # 3000 s observed
    fresh, _fresh_clock = _history()
    fresh.restore(saved.export(), downtime_s=DAY - 1000.0)
    assert fresh.observed_s() == 1000.0
