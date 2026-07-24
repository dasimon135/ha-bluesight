from custom_components.bluesight.adapter import current_proxy_slots, SlotAdapter
from custom_components.bluesight.model import ProxySlots


class _FakeAlloc:
    def __init__(self, source, slots, free, allocated):
        self.source, self.slots, self.free, self.allocated = source, slots, free, allocated


class _FakeManager:
    def __init__(self, allocs):
        self._a = allocs
        self.registered = None
        self.register_count = 0

    def async_current_allocations(self, source=None):
        return self._a

    def async_register_allocation_callback(self, cb, source=None):
        self.registered = cb
        self.register_count += 1
        return lambda: setattr(self, "registered", None)


def test_maps_allocations_to_proxyslots():
    mgr = _FakeManager([_FakeAlloc("AA", 3, 1, ["11:22", "33:44"])])
    slots = current_proxy_slots(mgr, name_for=lambda s: f"proxy-{s}")
    assert slots == [ProxySlots("AA", "proxy-AA", 3, 1, ["11:22", "33:44"])]


def test_current_proxy_slots_handles_none():
    # async_current_allocations may return None when nothing is set up yet
    mgr = _FakeManager(None)
    assert current_proxy_slots(mgr, name_for=lambda s: s) == []


def test_adapter_registers_and_unregisters():
    mgr = _FakeManager([])
    seen = []
    ad = SlotAdapter(mgr, on_change=lambda: seen.append(1))
    ad.start()
    assert mgr.registered is not None
    mgr.registered(object())          # simulate a push
    assert seen == [1]
    ad.stop()
    assert mgr.registered is None


def test_adapter_stop_is_idempotent():
    mgr = _FakeManager([])
    ad = SlotAdapter(mgr, on_change=lambda: None)
    ad.start()
    ad.stop()
    ad.stop()   # must not raise


def test_adapter_start_is_idempotent():
    mgr = _FakeManager([])
    ad = SlotAdapter(mgr, on_change=lambda: None)
    ad.start()
    ad.start()                       # second start must be a no-op
    assert mgr.register_count == 1   # no orphaned subscription
    ad.stop()
    assert mgr.registered is None    # single stop fully unsubscribes
