from custom_components.bluesight.detector import detect_reboot_storm
from custom_components.bluesight.model import IncidentKind
from custom_components.bluesight.window import FailureWindow


def test_repeated_reboots_is_storm():
    now = [0.0]
    w = FailureWindow(window_s=600, threshold=3, clock=lambda: now[0])
    for _ in range(3):
        now[0] += 10
        w.record("AA")
    inc = detect_reboot_storm("AA", w)
    assert inc is not None and inc.kind is IncidentKind.PROXY_REBOOT_STORM
    assert inc.address == "AA"


def test_few_reboots_not_storm():
    now = [0.0]
    w = FailureWindow(window_s=600, threshold=3, clock=lambda: now[0])
    w.record("AA")
    assert detect_reboot_storm("AA", w) is None
