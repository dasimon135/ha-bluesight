"""Which proxy held a device, and when: the stays behind the card's timeline.

The history already counts connections, which says how often. The timeline says
where and for how long, and that needs the other half of a connection: the
moment the slot is released. It is kept apart from the counts so counting means
what it always did -- a slot already held when BlueSight starts is a stay (it is
being held) but not a connection (it is not new).
"""

from __future__ import annotations

import json

from custom_components.bluesight.history import MAX_STAYS, ConnectionHistory

A = "D0:CF:13:0F:05:5A"
B = "D0:CF:13:0E:C9:2A"
DEV = "1C:54:9E:8E:1D:2C"
DAY = 86400.0


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _history() -> tuple[ConnectionHistory, Clock]:
    clock = Clock()
    return ConnectionHistory(DAY, clock), clock


def _stay(history, clock, source, address, held_for, then_idle=0):
    """The device takes a slot, keeps it ``held_for`` seconds, and lets go."""
    history.update([(source, address)])
    clock.now += held_for
    history.update([])
    clock.now += then_idle


def test_a_stay_runs_from_the_slot_appearing_to_the_slot_going():
    history, clock = _history()
    history.update([])
    clock.now += 10
    _stay(history, clock, A, DEV, held_for=100, then_idle=50)
    timeline = history.timeline(DEV)
    assert timeline.stays == ((150.0, 50.0, A),)
    assert timeline.truncated is False


def test_a_slot_still_held_is_a_stay_that_reaches_now():
    history, clock = _history()
    history.update([])
    clock.now += 10
    history.update([(A, DEV)])
    clock.now += 30
    assert history.timeline(DEV).stays == ((30.0, 0.0, A),)


def test_a_slot_held_at_startup_is_a_stay_but_not_a_connection():
    history, clock = _history()
    history.update([(A, DEV)])
    clock.now += 40
    assert history.timeline(DEV).stays == ((40.0, 0.0, A),)
    assert history.for_address(DEV) is None


def test_a_device_never_held_has_no_timeline():
    history, _ = _history()
    history.update([])
    assert history.timeline(DEV) is None


def test_two_stays_through_one_proxy_a_few_seconds_apart_are_one():
    history, clock = _history()
    history.update([])
    clock.now += 10
    _stay(history, clock, A, DEV, held_for=100, then_idle=20)
    _stay(history, clock, A, DEV, held_for=100)
    assert history.timeline(DEV).stays == ((220.0, 0.0, A),)


def test_two_stays_through_one_proxy_far_apart_stay_two():
    history, clock = _history()
    history.update([])
    clock.now += 10
    _stay(history, clock, A, DEV, held_for=100, then_idle=500)
    _stay(history, clock, A, DEV, held_for=100)
    assert len(history.timeline(DEV).stays) == 2


def test_a_move_to_another_proxy_is_never_merged_away():
    history, clock = _history()
    history.update([])
    clock.now += 10
    _stay(history, clock, A, DEV, held_for=100, then_idle=5)
    _stay(history, clock, B, DEV, held_for=100)
    sources = [source for _, _, source in history.timeline(DEV).stays]
    assert sources == [A, B]


def test_a_stay_across_the_edge_is_clipped_and_an_old_one_is_dropped():
    history, clock = _history()
    history.update([])
    clock.now += 10
    history.update([(A, DEV)])
    clock.now += DAY + 500
    history.update([(A, DEV)])
    # Still held after a day and a bit: it began before the window.
    assert history.timeline(DEV).stays == ((DAY, 0.0, A),)
    history.update([])
    clock.now += DAY
    history.update([])
    assert history.timeline(DEV) is None


def test_a_device_that_reconnects_constantly_says_its_timeline_is_cut():
    history, clock = _history()
    history.update([])
    for _ in range(MAX_STAYS + 5):
        clock.now += 100
        _stay(history, clock, A, DEV, held_for=10)
    timeline = history.timeline(DEV)
    assert timeline.truncated is True
    assert len(timeline.stays) <= MAX_STAYS


def test_a_restored_stay_that_was_open_ends_where_it_was_written():
    history, clock = _history()
    history.update([])
    clock.now += 10
    history.update([(A, DEV)])
    clock.now += 100
    saved = history.export()

    fresh = ConnectionHistory(DAY, clock)
    fresh.restore(saved, downtime_s=600.0)
    # Held for 100 s and written 600 s ago: began 700 s ago, ended 600 s ago.
    assert fresh.timeline(DEV).stays == ((700.0, 600.0, A),)


def test_after_a_restore_the_first_snapshot_opens_a_new_stay_for_what_is_held():
    history, clock = _history()
    history.update([])
    clock.now += 10
    history.update([(A, DEV)])
    saved = history.export()

    fresh = ConnectionHistory(DAY, clock)
    fresh.restore(saved, downtime_s=600.0)
    fresh.update([(A, DEV)])
    clock.now += 30
    stays = fresh.timeline(DEV).stays
    # The downtime is a gap, not a stay: Home Assistant was not watching.
    assert len(stays) == 2
    assert stays[-1] == (30.0, 0.0, A)
    # And a baseline is not a connection.
    assert fresh.for_address(DEV).connections == 1


def test_a_saved_file_from_before_stays_still_restores():
    history, clock = _history()
    history.update([])
    clock.now += 1
    history.update([(A, DEV)])
    saved = history.export()
    del saved["stays"]

    fresh = ConnectionHistory(DAY, clock)
    fresh.restore(saved, downtime_s=10.0)
    assert fresh.for_address(DEV).connections == 1
    assert fresh.timeline(DEV) is None


def test_the_export_with_stays_is_plain_json():
    history, clock = _history()
    history.update([])
    clock.now += 1
    history.update([(A, DEV)])
    assert json.loads(json.dumps(history.export()))["stays"][DEV]


def test_a_restore_into_a_running_history_leaves_its_stays_alone():
    history, clock = _history()
    history.update([])
    clock.now += 1
    history.update([(A, DEV)])
    saved = history.export()

    running = ConnectionHistory(DAY, clock)
    running.update([])
    clock.now += 5
    running.update([(B, DEV)])
    running.restore(saved, downtime_s=10.0)
    assert [s for _, _, s in running.timeline(DEV).stays] == [B]
