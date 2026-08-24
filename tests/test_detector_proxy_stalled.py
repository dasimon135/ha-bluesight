from custom_components.bluesight.detector import detect_stalled_proxies
from custom_components.bluesight.model import IncidentKind, ProxyHealth


def _h(src, online, age):
    return ProxyHealth(src, f"p-{src}", True, online, age, 0)


def test_online_but_stale_is_stalled():
    incs = detect_stalled_proxies([_h("AA", True, 200.0)], threshold_s=180.0)
    assert incs[0].kind is IncidentKind.PROXY_STALLED and incs[0].address == "AA"
    assert incs[0].detail_key == "incident.proxy_stalled.detail"
    # Truncated, not rounded -- same conversion the prose used to do inline.
    assert incs[0].detail_params == {"seconds": "200"}


def test_online_and_fresh_is_fine():
    assert detect_stalled_proxies([_h("AA", True, 5.0)], threshold_s=180.0) == []


def test_offline_proxy_is_not_stalled():
    assert detect_stalled_proxies([_h("AA", False, 999.0)], threshold_s=180.0) == []


def test_at_threshold_is_not_stalled():
    assert detect_stalled_proxies([_h("AA", True, 180.0)], threshold_s=180.0) == []
