"""Which proxies each platform creates entities for, as proxies come and go.

Both platforms add entities for proxies seen after startup, and remember what
they have added so a proxy present in two snapshots is not added twice. That
memory was add-only, which was right while nothing ever removed a proxy.

Retiring one does remove it: Home Assistant deletes the device and its
entities. The source stayed in the platform's memory, so a proxy that came
back afterwards -- a replacement reusing the MAC, or a Repair pressed by
mistake -- got no entities at all until the entry was reloaded, and vanished
from the card, which finds proxies through the registry.
"""
from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("homeassistant.components.sensor")

from custom_components.bluesight import binary_sensor, sensor
from custom_components.bluesight.coordinator_data import BlueSightData
from custom_components.bluesight.model import ProxyHealth, ProxySlots

PROXY = "D0:CF:13:0F:05:5A"
OTHER = "D0:CF:13:0E:C9:2A"


class _FakeCoordinator:
    def __init__(self) -> None:
        self.data = BlueSightData()
        self.last_update_success = True
        self._listeners: list = []

    def async_add_listener(self, listener):
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def publish(self, data: BlueSightData) -> None:
        self.data = data
        for listener in list(self._listeners):
            listener()

    def saturation_for(self, source):
        return None


class _FakeEntry:
    def __init__(self, coordinator) -> None:
        self.runtime_data = coordinator

    def async_on_unload(self, _unsub) -> None:
        return None


def _online(source: str) -> BlueSightData:
    return BlueSightData(
        proxies=[ProxySlots(source, "Proxy", 3, 3)],
        proxies_health=[ProxyHealth(source, "Proxy", True, True, 1.0, 4)],
    )


def _offline(source: str) -> BlueSightData:
    """Seen before and gone now: still reported, so still a known proxy."""
    return BlueSightData(
        proxies_health=[ProxyHealth(source, "Proxy", True, False, 600.0, 0)]
    )


def _setup(module, coordinator):
    """Run the platform's setup; return the batch list and where setup ended.

    ``binary_sensor`` adds the fleet-wide incident sensor in a batch of its
    own, so "what setup produced" is not simply the first batch.
    """
    batches: list[list] = []
    asyncio.run(
        module.async_setup_entry(
            object(), _FakeEntry(coordinator), lambda new: batches.append(list(new))
        )
    )
    return batches, len(batches)


def _sources(batches) -> list[str]:
    """The proxies a set of batches created entities for.

    Read off each entity's device identifier rather than its unique id: the
    two platforms suffix those differently, and the device is what the entity
    is actually attached to.
    """
    return sorted(
        {
            value
            for batch in batches
            for entity in batch
            for _domain, value in entity.device_info["identifiers"]
        }
    )


@pytest.mark.parametrize("module", [sensor, binary_sensor])
def test_a_proxy_is_not_added_twice_while_it_stays_known(module):
    coordinator = _FakeCoordinator()
    coordinator.data = _online(PROXY)
    batches, start = _setup(module, coordinator)
    coordinator.publish(_online(PROXY))
    assert [b for b in batches[start:] if b] == []


@pytest.mark.parametrize("module", [sensor, binary_sensor])
def test_a_proxy_that_merely_goes_offline_is_not_added_again(module):
    """It is still in the health snapshot, so its entities still exist; adding
    them a second time would be a duplicate unique id."""
    coordinator = _FakeCoordinator()
    coordinator.data = _online(PROXY)
    batches, start = _setup(module, coordinator)
    coordinator.publish(_offline(PROXY))
    coordinator.publish(_online(PROXY))
    assert [b for b in batches[start:] if b] == []


@pytest.mark.parametrize("module", [sensor, binary_sensor])
def test_a_retired_proxy_that_comes_back_gets_its_entities_again(module):
    """Retiring drops it from both snapshots, and Home Assistant deletes its
    entities with its device. Coming back, it has to be added afresh."""
    coordinator = _FakeCoordinator()
    coordinator.data = _online(PROXY)
    batches, start = _setup(module, coordinator)
    coordinator.publish(BlueSightData())      # retired: gone from both lists
    coordinator.publish(_online(PROXY))       # and back again
    assert _sources(batches[start:]) == [PROXY]


@pytest.mark.parametrize("module", [sensor, binary_sensor])
def test_retiring_one_proxy_does_not_re_add_the_others(module):
    coordinator = _FakeCoordinator()
    coordinator.data = BlueSightData(
        proxies_health=[
            ProxyHealth(PROXY, "A", True, True, 1.0, 0),
            ProxyHealth(OTHER, "B", True, True, 1.0, 0),
        ]
    )
    batches, start = _setup(module, coordinator)
    coordinator.publish(_online(OTHER))       # PROXY retired
    coordinator.publish(
        BlueSightData(
            proxies_health=[
                ProxyHealth(PROXY, "A", True, True, 1.0, 0),
                ProxyHealth(OTHER, "B", True, True, 1.0, 0),
            ]
        )
    )
    assert _sources(batches[start:]) == [PROXY]
