"""Per-proxy reads of the rolling failure window.

``count`` answers "how bad is it for this device", which is what STORM asks.
BOND_LOST asks a different question -- "how bad is it for this device *on this
proxy*" -- because a bond is per-central and the remedy names one proxy. That
is what ``count_by_source`` is for.
"""
from __future__ import annotations

from custom_components.bluesight.window import FailureWindow

ADDR = "D0:CF:13:0E:C9:2A"


def _window(now, window_s=300, threshold=5):
    return FailureWindow(window_s=window_s, threshold=threshold, clock=lambda: now[0])


def test_measured_events_are_counted_per_proxy():
    now = [0.0]
    w = _window(now)
    w.record(ADDR, "proxy1")
    w.record(ADDR, "proxy1")
    w.record(ADDR, "proxy2")
    assert w.count_by_source(ADDR) == {"proxy1": 2, "proxy2": 1}


def test_inferred_events_are_excluded():
    """``source=None`` is the release heuristic, which cannot say which proxy
    dropped the slot. BOND_LOST implicates one proxy by name, so an event that
    names nobody can never contribute to it -- at any count."""
    now = [0.0]
    w = _window(now)
    for _ in range(10):
        w.record(ADDR)
    assert w.count_by_source(ADDR) == {}


def test_expired_events_stop_being_counted():
    """The whole point of reading the window instead of the lifetime counter:
    a failure that stopped happening stops being evidence."""
    now = [0.0]
    w = _window(now)
    w.record(ADDR, "proxy1")
    w.record(ADDR, "proxy1")
    now[0] += 400  # past the 300s window
    assert w.count_by_source(ADDR) == {}


def test_only_the_expired_half_is_dropped():
    now = [0.0]
    w = _window(now)
    w.record(ADDR, "proxy1")
    now[0] += 200
    w.record(ADDR, "proxy1")
    now[0] += 200  # first event is now 400s old, second 200s
    assert w.count_by_source(ADDR) == {"proxy1": 1}


def test_a_mixed_window_counts_only_the_measured_half():
    """A real fleet is mixed: one proxy runs the firmware and one does not,
    and both land in this window. Only the measured half is attributable."""
    now = [0.0]
    w = _window(now)
    w.record(ADDR, "proxy1")
    w.record(ADDR)
    w.record(ADDR)
    assert w.count_by_source(ADDR) == {"proxy1": 1}


def test_unknown_address_does_not_create_a_key():
    """Same contract as ``count``: a read must not leak an entry into the
    store, which the 24/7 coordinator loop would otherwise accumulate."""
    now = [0.0]
    w = _window(now)
    assert w.count_by_source("AA:BB:CC:DD:EE:FF") == {}
    assert len(w._events) == 0
