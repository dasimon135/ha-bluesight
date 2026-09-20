"""Reading what each proxy last heard a device at, through habluetooth.

One public call -- ``async_scanner_devices_by_address`` -- and nothing private:
habluetooth's own connection scoring also weighs per-scanner failure counts,
but those live in a private attribute and BlueSight reads none of those.
"""
from __future__ import annotations

from custom_components.bluesight.adapter import current_signal_by_proxy

ADDR = "1C:54:9E:8E:1D:2C"


class _Adv:
    def __init__(self, rssi):
        self.rssi = rssi


class _Scanner:
    def __init__(self, source):
        self.source = source


class _ScannerDevice:
    def __init__(self, source, rssi):
        self.scanner = _Scanner(source)
        self.advertisement = _Adv(rssi)


class _Manager:
    def __init__(self, devices):
        self._devices = devices
        self.asked = []

    def async_scanner_devices_by_address(self, address, connectable):
        self.asked.append((address, connectable))
        return self._devices


def test_every_proxy_that_heard_the_device_is_reported_by_source():
    manager = _Manager([_ScannerDevice("aa:bb", -70), _ScannerDevice("CC:DD", -55)])
    assert current_signal_by_proxy(manager, ADDR) == {"AA:BB": -70, "CC:DD": -55}
    # Only proxies that could have taken the connection are a route.
    assert manager.asked == [(ADDR, True)]


def test_a_scanner_with_no_signal_reading_is_left_out():
    """habluetooth reports a missing RSSI as None or as its -127 sentinel;
    either is the absence of a reading, not a very weak one."""
    manager = _Manager(
        [_ScannerDevice("AA", None), _ScannerDevice("BB", -127), _ScannerDevice("CC", -60)]
    )
    assert current_signal_by_proxy(manager, ADDR) == {"CC": -60}


def test_a_manager_without_the_call_reads_nothing():
    assert current_signal_by_proxy(object(), ADDR) == {}


def test_one_unreadable_scanner_does_not_cost_the_others():
    class _Broken:
        scanner = None
        advertisement = None

    manager = _Manager([_Broken(), _ScannerDevice("CC", -60)])
    assert current_signal_by_proxy(manager, ADDR) == {"CC": -60}
