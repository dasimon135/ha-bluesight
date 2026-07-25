from custom_components.bluesight.model import ProxyHealth, IncidentKind
from custom_components.bluesight.detector import detect_offline_proxies


def _h(src, online):
    return ProxyHealth(src, f"p-{src}", True, online, 0.0, 0)


def test_known_but_absent_is_offline():
    current = [_h("AA", True)]
    incs = detect_offline_proxies(current, known_sources={"AA", "BB"})
    assert [i.kind for i in incs] == [IncidentKind.PROXY_OFFLINE]
    assert incs[0].address == "BB"


def test_unknown_absent_is_not_flagged():
    assert detect_offline_proxies([_h("AA", True)], known_sources={"AA"}) == []


def test_known_source_present_but_offline_is_flagged():
    current = [_h("AA", True), _h("BB", False)]   # BB present but offline
    incs = detect_offline_proxies(current, known_sources={"AA", "BB"})
    assert [i.address for i in incs] == ["BB"]
