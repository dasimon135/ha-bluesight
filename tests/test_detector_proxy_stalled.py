from custom_components.bluesight.model import ProxyHealth, IncidentKind
from custom_components.bluesight.detector import detect_stalled_proxies


def _h(src, online, age):
    return ProxyHealth(src, f"p-{src}", True, online, age, 0)


def test_online_but_stale_is_stalled():
    incs = detect_stalled_proxies([_h("AA", True, 200.0)], threshold_s=180.0)
    assert incs[0].kind is IncidentKind.PROXY_STALLED and incs[0].address == "AA"


def test_online_and_fresh_is_fine():
    assert detect_stalled_proxies([_h("AA", True, 5.0)], threshold_s=180.0) == []


def test_offline_proxy_is_not_stalled():
    assert detect_stalled_proxies([_h("AA", False, 999.0)], threshold_s=180.0) == []
