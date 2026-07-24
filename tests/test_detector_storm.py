from custom_components.ble_triage.window import FailureWindow
from custom_components.ble_triage.detector import detect_storm
from custom_components.ble_triage.model import IncidentKind


def test_burst_of_failures_is_storm():
    now = [0.0]
    w = FailureWindow(window_s=300, threshold=5, clock=lambda: now[0])
    for _ in range(5):
        now[0] += 10
        w.record("11:22")
    inc = detect_storm("11:22", w)
    assert inc is not None and inc.kind is IncidentKind.STORM


def test_failures_outside_window_expire():
    now = [0.0]
    w = FailureWindow(window_s=300, threshold=5, clock=lambda: now[0])
    for _ in range(5):
        w.record("11:22")
        now[0] += 100   # earlier events fall outside 300s window
    assert detect_storm("11:22", w) is None
