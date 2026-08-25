from custom_components.bluesight.detector import detect_storm
from custom_components.bluesight.model import IncidentKind
from custom_components.bluesight.window import FailureWindow


def test_burst_of_failures_is_storm():
    now = [0.0]
    w = FailureWindow(window_s=300, threshold=5, clock=lambda: now[0])
    for _ in range(5):
        now[0] += 10
        w.record("11:22")
    inc = detect_storm("11:22", w)
    assert inc is not None and inc.kind is IncidentKind.STORM
    assert inc.detail_key == "incident.storm.detail"
    assert inc.detail_params == {"count": "5", "seconds": "300"}


def test_failures_outside_window_expire():
    now = [0.0]
    w = FailureWindow(window_s=300, threshold=5, clock=lambda: now[0])
    for _ in range(5):
        w.record("11:22")
        now[0] += 100   # earlier events fall outside 300s window
    assert detect_storm("11:22", w) is None


def test_count_unknown_address_does_not_create_key():
    now = [0.0]
    w = FailureWindow(window_s=300, threshold=5, clock=lambda: now[0])
    for addr in ("AA", "BB", "CC"):
        assert w.count(addr) == 0
    # Reading unknown addresses must not leak entries into the store.
    assert len(w._events) == 0


def test_window_self_cleans_after_expiry():
    now = [0.0]
    w = FailureWindow(window_s=300, threshold=5, clock=lambda: now[0])
    w.record("11:22")
    now[0] += 400   # advance past the 300s window
    assert w.count("11:22") == 0
    # The now-empty deque must not linger.
    assert "11:22" not in w._events


def test_multi_address_isolation():
    now = [0.0]
    w = FailureWindow(window_s=300, threshold=5, clock=lambda: now[0])
    for _ in range(3):
        w.record("AA")
    w.record("BB")
    assert w.count("AA") == 3
    assert w.count("BB") == 1


def test_addresses_lists_tracked_addresses():
    now = [0.0]
    w = FailureWindow(window_s=300, threshold=5, clock=lambda: now[0])
    w.record("AA")
    w.record("BB")
    assert sorted(w.addresses()) == ["AA", "BB"]


def test_addresses_empty_when_nothing_recorded():
    w = FailureWindow(window_s=300, threshold=5, clock=lambda: 0.0)
    assert w.addresses() == []


def test_addresses_evicts_stale_and_does_not_leak():
    now = [0.0]
    w = FailureWindow(window_s=300, threshold=5, clock=lambda: now[0])
    w.record("AA")
    now[0] += 400   # advance past the 300s window
    # A fully-expired address must not be reported and must not linger.
    assert w.addresses() == []
    assert "AA" not in w._events


# --- provenance: which proxy measured each event ---------------------------

def test_sources_names_every_proxy_that_measured_an_event():
    w = FailureWindow(window_s=300, threshold=5, clock=lambda: 0.0)
    w.record("11:22", "p2")
    w.record("11:22", "p1")
    w.record("11:22")          # inferred: no proxy can be named
    # Sorted and de-duplicated: this list reaches `Incident.sources`, which
    # `Incident.key` folds in, so it must not depend on record order.
    assert w.sources("11:22") == ["p1", "p2"]
    assert w.count("11:22") == 3


def test_sources_is_empty_when_every_event_was_inferred():
    w = FailureWindow(window_s=300, threshold=5, clock=lambda: 0.0)
    w.record("11:22")
    assert w.sources("11:22") == []


def test_sources_expire_with_the_events_they_describe():
    now = [0.0]
    w = FailureWindow(window_s=300, threshold=5, clock=lambda: now[0])
    w.record("11:22", "p1")
    now[0] += 400
    w.record("11:22")
    # The measured event has aged out, so the attribution goes with it: an
    # incident must not keep naming a proxy on evidence the window no longer
    # holds.
    assert w.sources("11:22") == []
    assert w.count("11:22") == 1


def test_sources_of_an_unknown_address_does_not_create_a_key():
    w = FailureWindow(window_s=300, threshold=5, clock=lambda: 0.0)
    assert w.sources("AA") == []
    assert len(w._events) == 0
