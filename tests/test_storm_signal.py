"""The connection-failure signal behind the storm detector.

Guards the regression the old signal had: a failed connection RELEASES its
slot, so an "alive -> unavailable while still allocated" comparison could never
observe it and the storm detector was effectively dead.
"""
from custom_components.bluesight.storm_signal import ReleaseTracker


def _alive_except(*dead):
    dead_set = {d.upper() for d in dead}
    return lambda address: address.upper() not in dead_set


ALL_ALIVE = _alive_except()


def test_first_snapshot_records_nothing():
    assert ReleaseTracker().update(["AA"], ALL_ALIVE) == []


def test_release_of_a_dead_device_is_a_failure():
    t = ReleaseTracker()
    t.update(["AA"], ALL_ALIVE)
    assert t.update([], _alive_except("AA")) == ["AA"]


def test_release_of_a_live_device_is_not_a_failure():
    """A healthy poll cycle connects, reads and disconnects. Not a failure."""
    t = ReleaseTracker()
    t.update(["AA"], ALL_ALIVE)
    assert t.update([], ALL_ALIVE) == []
    assert t.update([], ALL_ALIVE) == []


def test_still_allocated_is_not_a_failure():
    t = ReleaseTracker()
    t.update(["AA"], ALL_ALIVE)
    assert t.update(["AA"], _alive_except("AA")) == []


def test_release_judged_alive_is_rechecked_once():
    """Entity states settle asynchronously, so a device can still read as alive
    at the exact snapshot its slot is released."""
    t = ReleaseTracker()
    t.update(["AA"], ALL_ALIVE)
    assert t.update([], ALL_ALIVE) == []          # undecided
    assert t.update([], _alive_except("AA")) == ["AA"]   # settled: failure


def test_recheck_happens_only_once():
    t = ReleaseTracker()
    t.update(["AA"], ALL_ALIVE)
    t.update([], ALL_ALIVE)      # pending
    t.update([], ALL_ALIVE)      # rechecked, still alive -> dropped
    assert t.update([], _alive_except("AA")) == []


def test_reconnect_cancels_a_pending_release():
    t = ReleaseTracker()
    t.update(["AA"], ALL_ALIVE)
    assert t.update([], ALL_ALIVE) == []
    assert t.update(["AA"], _alive_except("AA")) == []


def test_repeated_failed_connections_each_count():
    """The storm case: connect, fail, release, retry. Every cycle counts."""
    t = ReleaseTracker()
    dead = _alive_except("AA")
    failures = []
    for _ in range(3):
        t.update(["AA"], dead)
        failures += t.update([], dead)
    assert failures == ["AA", "AA", "AA"]


def test_addresses_are_normalized():
    t = ReleaseTracker()
    t.update([" aa:bb "], ALL_ALIVE)
    assert t.update([], _alive_except("AA:BB")) == ["AA:BB"]


def test_is_alive_is_asked_once_per_address_per_snapshot():
    calls = []

    def probe(address):
        calls.append(address)
        return False

    t = ReleaseTracker()
    t.update(["AA", "BB"], probe)
    calls.clear()
    assert sorted(t.update([], probe)) == ["AA", "BB"]
    assert sorted(calls) == ["AA", "BB"]
