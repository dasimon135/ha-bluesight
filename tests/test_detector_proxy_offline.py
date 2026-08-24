from custom_components.bluesight.detector import detect_offline_proxies
from custom_components.bluesight.model import IncidentKind, ProxyHealth


def _h(src, online):
    return ProxyHealth(src, f"p-{src}", True, online, 0.0, 0)


def test_known_but_absent_is_offline():
    current = [_h("AA", True)]
    incs = detect_offline_proxies(current, known_sources={"AA", "BB"})
    assert [i.kind for i in incs] == [IncidentKind.PROXY_OFFLINE]
    assert incs[0].address == "BB"
    assert incs[0].detail_key == "incident.proxy_offline.detail"
    assert incs[0].detail_params == {}


def test_unknown_absent_is_not_flagged():
    assert detect_offline_proxies([_h("AA", True)], known_sources={"AA"}) == []


def test_known_source_present_but_offline_is_flagged():
    current = [_h("AA", True), _h("BB", False)]   # BB present but offline
    incs = detect_offline_proxies(current, known_sources={"AA", "BB"})
    assert [i.address for i in incs] == ["BB"]


def test_absence_within_the_grace_period_is_not_flagged():
    """An ESPHome proxy drops off the bus on every OTA; don't alert on that."""
    incs = detect_offline_proxies(
        [_h("AA", True)], {"AA", "BB"}, {"AA": 0.0, "BB": 30.0}, grace_s=90.0
    )
    assert incs == []


def test_absence_beyond_the_grace_period_is_flagged():
    incs = detect_offline_proxies(
        [_h("AA", True)], {"AA", "BB"}, {"AA": 0.0, "BB": 120.0}, grace_s=90.0
    )
    assert [i.address for i in incs] == ["BB"]


def test_unmeasured_source_is_treated_as_freshly_missing():
    incs = detect_offline_proxies([_h("AA", True)], {"AA", "BB"}, {}, grace_s=90.0)
    assert incs == []


def test_detail_carries_no_elapsed_time():
    """The rendered detail lands in entity attributes; a ticking counter would
    churn the state machine on every single snapshot. The parameters carry the
    elapsed time or nothing, so it is the parameters that must not move."""
    first = detect_offline_proxies([], {"BB"}, {"BB": 100.0}, grace_s=90.0)
    later = detect_offline_proxies([], {"BB"}, {"BB": 9999.0}, grace_s=90.0)
    assert first[0].detail_key == later[0].detail_key
    assert first[0].detail_params == later[0].detail_params == {}
    assert first[0].key == later[0].key
