"""How long a proxy spends with nowhere left to put a connection.

This measures a *pressure*, not a fault. A saturated proxy is not broken --
every slot is doing useful work -- but the next device that needs it will not
get in, and it will go `unavailable` with no error, which is the symptom this
whole integration exists to explain. Saturation is that symptom seen before it
happens.

Deliberately no verdict attached. The threshold at which "busy" becomes "too
busy" is not knowable from one fleet, and inventing one would repeat the
mistake `idle_threshold_s` was measured out of. This publishes the measurement
so a threshold can be chosen from data later.
"""
from __future__ import annotations

from custom_components.bluesight.saturation import SaturationWindow

WINDOW = 1000.0


def _window(now):
    return SaturationWindow(window_s=WINDOW, clock=lambda: now[0])


# ------------------------------------------------------------------- the ratio


def test_a_window_with_nothing_recorded_reports_no_pressure_and_no_evidence():
    """Zero is the right answer, but "no evidence" and "no pressure" are
    different claims -- `observed_s` is what tells them apart."""
    w = _window([0.0])
    assert w.ratio() == 0.0
    assert w.observed_s() == 0.0


def test_a_proxy_that_is_never_full_reports_zero():
    now = [0.0]
    w = _window(now)
    w.record(False)
    now[0] += 500
    w.record(False)
    assert w.ratio() == 0.0


def test_a_proxy_that_is_always_full_reports_one():
    now = [0.0]
    w = _window(now)
    w.record(True)
    now[0] += 500
    assert w.ratio() == 1.0


def test_half_the_observed_time_full_reports_a_half():
    now = [0.0]
    w = _window(now)
    w.record(True)
    now[0] += 200
    w.record(False)
    now[0] += 200
    assert w.ratio() == 0.5


def test_the_ratio_is_over_time_observed_not_over_the_whole_window():
    """A proxy adopted a minute ago has not been quiet for the other 23 hours;
    it has not been *watched* for them. Dividing by the full window would
    report a fresh, permanently-full proxy as barely busy."""
    now = [0.0]
    w = _window(now)
    w.record(True)
    now[0] += 10

    assert w.observed_s() == 10
    assert w.ratio() == 1.0


def test_saturation_that_scrolls_out_of_the_window_stops_counting():
    """The point of a window: pressure that has passed is not pressure."""
    now = [0.0]
    w = _window(now)
    w.record(True)
    now[0] += 100
    w.record(False)
    now[0] += WINDOW + 50   # the full stretch is now older than the window

    assert w.ratio() == 0.0


def test_a_stretch_straddling_the_window_edge_is_counted_only_from_the_edge():
    """Half in, half out: only the half still inside the window counts, or the
    number would keep describing a burst long after it ended."""
    now = [0.0]
    w = _window(now)
    w.record(True)
    now[0] += 200
    w.record(False)
    # Slide until 100s of that 200s stretch has aged out.
    now[0] += WINDOW - 100

    assert w.observed_s() == WINDOW
    assert w.ratio() == 100 / WINDOW


# ---------------------------------------------------------------- the episodes


def test_an_unchanged_reading_does_not_start_a_new_episode():
    """The coordinator records every snapshot, so most readings repeat the
    previous one. Counting those would turn the poll interval into the
    measurement."""
    now = [0.0]
    w = _window(now)
    for _ in range(10):
        w.record(True)
        now[0] += 10

    assert w.episodes() == 1


def test_each_entry_into_saturation_is_one_episode():
    now = [0.0]
    w = _window(now)
    for _ in range(3):
        w.record(True)
        now[0] += 10
        w.record(False)
        now[0] += 10

    assert w.episodes() == 3


def test_episodes_that_aged_out_are_not_counted():
    now = [0.0]
    w = _window(now)
    w.record(True)
    now[0] += 10
    w.record(False)
    now[0] += WINDOW + 50
    w.record(True)

    assert w.episodes() == 1


# ----------------------------------------------------------- the worst stretch


def test_the_longest_stretch_is_reported_not_the_last():
    """Ten one-second squeezes and one ten-minute lockout give the same ratio
    and mean very different things. The worst stretch is what says which."""
    now = [0.0]
    w = _window(now)
    w.record(True)
    now[0] += 50
    w.record(False)
    now[0] += 10
    w.record(True)
    now[0] += 20
    w.record(False)

    assert w.longest_s() == 50


def test_an_open_stretch_counts_up_to_now():
    """A proxy that is full *right now* has its stretch measured to the
    present, not to the last time anything changed -- otherwise the number
    freezes exactly when the situation is worst."""
    now = [0.0]
    w = _window(now)
    w.record(True)
    now[0] += 300

    assert w.longest_s() == 300


def test_a_proxy_that_was_never_full_has_no_stretch():
    now = [0.0]
    w = _window(now)
    w.record(False)
    now[0] += 100
    assert w.longest_s() == 0.0


# ------------------------------------------------------------------- the store


def test_the_store_does_not_grow_with_every_snapshot():
    """This runs every poll for the life of the integration. Keeping one entry
    per reading would leak steadily on a proxy whose state never changes."""
    now = [0.0]
    w = _window(now)
    for _ in range(500):
        w.record(True)
        now[0] += 1

    assert len(w._transitions) <= 2


def test_transitions_older_than_the_window_are_dropped():
    now = [0.0]
    w = _window(now)
    for _ in range(50):
        w.record(True)
        now[0] += 10
        w.record(False)
        now[0] += 10

    now[0] += WINDOW
    w.record(False)

    # Everything has aged out; at most the one entry holding the current state.
    assert len(w._transitions) <= 1
    assert w.ratio() == 0.0
