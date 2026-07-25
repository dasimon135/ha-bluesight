from custom_components.bluesight.adapter import current_proxy_health, ScannerAdapter
from custom_components.bluesight.model import ProxyHealth


class _FakeScanner:
    def __init__(self, source, name, connectable, age, devices):
        self.source, self.name, self.connectable = source, name, connectable
        self._age, self._devices = age, devices

    def time_since_last_detection(self):
        return self._age

    @property
    def discovered_devices(self):
        return self._devices


class _FakeEvent:
    def __init__(self, value):
        self.value = value


class _FakeReg:
    def __init__(self, event, scanner):
        self.event, self.scanner = event, scanner


class _FakeManager:
    def __init__(self, scanners):
        self._s = scanners
        self.registered = None
        self.reg_count = 0

    def async_current_scanners(self):
        return self._s

    def async_register_scanner_registration_callback(self, cb, source=None):
        self.registered = cb
        self.reg_count += 1
        return lambda: setattr(self, "registered", None)


def test_maps_scanners_to_proxyhealth():
    mgr = _FakeManager([_FakeScanner("AA", "Salon", True, 3.0, ["d1", "d2"])])
    assert current_proxy_health(mgr) == [ProxyHealth("AA", "Salon", True, True, 3.0, 2)]


def test_current_proxy_health_handles_none():
    class _M:
        def async_current_scanners(self):
            return None

    assert current_proxy_health(_M()) == []


def test_scanner_adapter_records_removed_and_fires():
    mgr = _FakeManager([])
    events, removed = [], []
    ad = ScannerAdapter(mgr, on_change=lambda: events.append(1),
                        on_removed=lambda src: removed.append(src))
    ad.start()
    assert mgr.registered is not None
    mgr.registered(_FakeReg(_FakeEvent("removed"), _FakeScanner("AA", "x", True, 0.0, [])))
    assert events == [1] and removed == ["AA"]
    ad.stop()
    assert mgr.registered is None


def test_scanner_adapter_added_fires_change_only():
    mgr = _FakeManager([])
    events, removed = [], []
    ad = ScannerAdapter(mgr, on_change=lambda: events.append(1),
                        on_removed=lambda src: removed.append(src))
    ad.start()
    mgr.registered(_FakeReg(_FakeEvent("added"), _FakeScanner("BB", "y", True, 0.0, [])))
    assert events == [1] and removed == []   # added → change, no removed
    ad.stop()


def test_scanner_adapter_start_is_idempotent():
    mgr = _FakeManager([])
    ad = ScannerAdapter(mgr, on_change=lambda: None, on_removed=lambda s: None)
    ad.start()
    ad.start()
    assert mgr.reg_count == 1   # second start is a no-op
    ad.stop()
    ad.stop()        # idempotent stop, no raise
