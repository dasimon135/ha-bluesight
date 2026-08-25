from custom_components.bluesight.adapter import SlotAdapter, current_proxy_slots
from custom_components.bluesight.model import DeviceRef, ProxySlots


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


def test_proxyslots_source_is_normalized():
    # habluetooth could yield the proxy MAC in lower case for allocations;
    # the stored source must be normalized (upper-cased) so it stays
    # byte-identical to the health/coordinator sources -> one HA device.
    mgr = _FakeManager([_FakeAlloc("aa:bb:cc:dd:ee:ff", 5, 5, [])])
    slots = current_proxy_slots(mgr, name_for=lambda s: s)
    assert slots[0].source == "AA:BB:CC:DD:EE:FF"


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


# --- the connected-device resolver -----------------------------------------
#
# `current_proxy_slots` already takes an injected resolver for the PROXY name;
# the device resolver is injected the same way, so this module stays the only
# one coupled to habluetooth and none of the naming logic (which lives in the
# device registry) leaks into it.


def test_the_device_resolver_is_asked_for_each_allocated_address():
    mgr = _FakeManager([_FakeAlloc("AA", 3, 1, ["11:22", "33:44"])])
    asked = []

    def device_for(address):
        asked.append(address)
        return DeviceRef(f"name-{address}", f"dev-{address}")

    slots = current_proxy_slots(mgr, name_for=lambda s: s, device_for=device_for)
    assert asked == ["11:22", "33:44"]
    assert slots[0].allocated_devices == [
        {"address": "11:22", "name": "name-11:22", "device_id": "dev-11:22"},
        {"address": "33:44", "name": "name-33:44", "device_id": "dev-33:44"},
    ]


def test_the_resolver_is_asked_in_the_canonical_spelling():
    """The registry index is keyed by `normalize_address`, so the resolver is
    handed the canonical form and never habluetooth's raw spelling. Getting
    this wrong resolves nothing, silently, for every device."""
    mgr = _FakeManager([_FakeAlloc("AA", 3, 2, ["c3:eb:49:65:67:55"])])
    asked = []
    current_proxy_slots(
        mgr,
        name_for=lambda s: s,
        device_for=lambda a: asked.append(a) or DeviceRef("Madoka", "dev_1"),
    )
    assert asked == ["C3:EB:49:65:67:55"]


def test_the_published_address_keeps_habluetooths_own_spelling():
    """`allocated` has published the raw string since 0.1; `allocated_devices`
    describes the same slots and must say the same addresses."""
    mgr = _FakeManager([_FakeAlloc("AA", 3, 2, ["c3:eb:49:65:67:55"])])
    slots = current_proxy_slots(
        mgr, name_for=lambda s: s, device_for=lambda a: DeviceRef("Madoka", "dev_1")
    )
    assert slots[0].allocated == ["c3:eb:49:65:67:55"]
    assert [e["address"] for e in slots[0].allocated_devices] == [
        "c3:eb:49:65:67:55"
    ]


def test_one_address_is_resolved_once_however_often_it_is_allocated():
    mgr = _FakeManager([_FakeAlloc("AA", 3, 1, ["11:22", "11:22"])])
    asked = []
    slots = current_proxy_slots(
        mgr,
        name_for=lambda s: s,
        device_for=lambda a: asked.append(a) or DeviceRef("A", "dev_a"),
    )
    assert asked == ["11:22"]          # one lookup ...
    assert len(slots[0].allocated_devices) == 2   # ... two occupied slots


def test_an_address_the_resolver_does_not_know_stays_unresolved():
    mgr = _FakeManager([_FakeAlloc("AA", 3, 1, ["11:22", "33:44"])])
    slots = current_proxy_slots(
        mgr,
        name_for=lambda s: s,
        device_for=lambda a: DeviceRef("Madoka", "dev_1") if a == "33:44" else None,
    )
    assert slots[0].allocated_devices == [
        {"address": "11:22", "name": "", "device_id": None},
        {"address": "33:44", "name": "Madoka", "device_id": "dev_1"},
    ]


def test_a_resolver_that_raises_does_not_take_the_snapshot_down():
    """Names are decoration. A registry lookup that blows up must cost the
    name, not every slot sensor the integration owns."""
    mgr = _FakeManager([_FakeAlloc("AA", 3, 1, ["11:22", "33:44"])])

    def boom(address):
        if address == "11:22":
            raise RuntimeError("device registry not loaded")
        return DeviceRef("Madoka", "dev_1")

    slots = current_proxy_slots(mgr, name_for=lambda s: s, device_for=boom)
    assert slots[0].allocated == ["11:22", "33:44"]
    assert slots[0].allocated_devices == [
        {"address": "11:22", "name": "", "device_id": None},
        # ... and the failure of one address does not lose the others.
        {"address": "33:44", "name": "Madoka", "device_id": "dev_1"},
    ]


def test_the_same_address_on_two_proxies_is_named_on_both():
    """The deadlock shape. Each ProxySlots resolves independently, so the
    address that is stuck on two proxies is legible on both tiles."""
    mgr = _FakeManager(
        [
            _FakeAlloc("AA", 3, 2, ["11:22"]),
            _FakeAlloc("BB", 3, 2, ["11:22"]),
        ]
    )
    slots = current_proxy_slots(
        mgr, name_for=lambda s: s, device_for=lambda a: DeviceRef("Madoka", "dev_1")
    )
    assert [p.allocated_devices for p in slots] == [
        [{"address": "11:22", "name": "Madoka", "device_id": "dev_1"}],
        [{"address": "11:22", "name": "Madoka", "device_id": "dev_1"}],
    ]


def test_the_device_resolver_is_optional():
    """A caller that only wants slot counts still gets a usable snapshot."""
    mgr = _FakeManager([_FakeAlloc("AA", 3, 2, ["11:22"])])
    slots = current_proxy_slots(mgr, name_for=lambda s: s)
    assert slots[0].allocated_devices == [
        {"address": "11:22", "name": "", "device_id": None}
    ]
