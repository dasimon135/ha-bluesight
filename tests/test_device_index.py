"""Indexing the Home Assistant device registry by MAC.

Pure: :mod:`custom_components.bluesight.device_index` reads registry entries
duck-typed and takes the two connection-type strings as arguments, so the whole
rule is exercised here with plain objects and no Home Assistant.

The case these tests exist for is the asymmetry between the two indexes. A
Bluetooth proxy is not a BLE peripheral, and Home Assistant does not register
it as one: the ESPHome integration creates the proxy's device with
``(CONNECTION_NETWORK_MAC, mac_address)``, and that same ``mac_address`` string
is what habluetooth reports as the remote scanner's ``source``. Index only
Bluetooth connections -- correct for peripherals -- and no proxy resolves at
all, which fails silently: every proxy reads as having no telemetry.
"""
from __future__ import annotations

from custom_components.bluesight.device_index import (
    build_device_index,
    build_proxy_index,
)

#: The real values of ``homeassistant.helpers.device_registry``'s constants.
#: They are injected rather than imported so this module stays HA-free; the
#: coordinator passes the genuine constants, and that hookup is what
#: ``test_coordinator_shell`` checks on CI.
BLUETOOTH = "bluetooth"
NETWORK = "mac"

PROXY_MAC = "D8:3B:DA:11:22:33"
PERIPHERAL_MAC = "1C:54:9E:8E:1D:2C"


class _Device:
    """The three device-registry attributes this module is allowed to read."""

    def __init__(self, device_id, *, connections=(), identifiers=()):
        self.id = device_id
        self.connections = set(connections)
        self.identifiers = set(identifiers)


def _index(*devices):
    return build_device_index(
        devices, bluetooth_connection=BLUETOOTH, network_connection=NETWORK
    )


def test_an_esphome_proxy_resolves_as_a_proxy_and_not_as_a_peripheral():
    # The regression this module exists for. ESPHome registers its device with
    # a NETWORK MAC, and that string is the habluetooth scanner source.
    index = _index(_Device("dev_proxy", connections={(NETWORK, PROXY_MAC)}))
    assert index.proxies[PROXY_MAC] == "dev_proxy"
    # ... but a network MAC says nothing about a BLE peripheral, so it must not
    # be able to answer the ghost-slot question for a colliding address.
    assert PROXY_MAC not in index.peripherals
    assert index.managed_addresses == set()


def test_the_esphome_network_mac_is_matched_case_insensitively():
    # Home Assistant's registry runs CONNECTION_NETWORK_MAC through
    # `format_mac`, which lower-cases; habluetooth hands back upper-case.
    index = _index(_Device("dev_proxy", connections={(NETWORK, "d8:3b:da:11:22:33")}))
    assert index.proxies[PROXY_MAC] == "dev_proxy"


def test_a_ble_peripheral_resolves_by_identifier_and_by_connection():
    index = _index(
        # madoka: MAC lives in identifiers, connections empty (real registry).
        _Device("dev_madoka", identifiers={("daikin_madoka", PERIPHERAL_MAC)}),
        _Device("dev_other", connections={(BLUETOOTH, "AA:BB:CC:DD:EE:FF")}),
    )
    assert index.peripherals[PERIPHERAL_MAC] == "dev_madoka"
    assert index.peripherals["AA:BB:CC:DD:EE:FF"] == "dev_other"
    # Bluetooth evidence answers the proxy question too: a proxy match that is
    # wrong costs nothing (the reader finds no BlueSight sensors on it), so the
    # proxy index is deliberately the permissive one.
    assert index.proxies[PERIPHERAL_MAC] == "dev_madoka"


def test_non_mac_identifiers_do_not_pollute_either_index():
    index = _index(_Device("dev_cloud", identifiers={("some_cloud", "account-12345")}))
    assert index.peripherals == {}
    assert index.proxies == {}


def test_a_wifi_mac_never_speaks_for_a_bluetooth_address():
    # A device with BOTH a Bluetooth MAC and a network MAC: only the Bluetooth
    # one may judge availability, or a BLE allocation could collide with some
    # dead device's wifi MAC and be falsely flagged as a ghost.
    index = _index(
        _Device(
            "dev_dual",
            connections={
                (BLUETOOTH, "AA:BB:CC:DD:EE:FF"),
                (NETWORK, "11:22:33:44:55:66"),
            },
        )
    )
    assert index.peripherals == {"AA:BB:CC:DD:EE:FF": "dev_dual"}
    assert index.proxies["11:22:33:44:55:66"] == "dev_dual"


def test_a_declared_connection_beats_a_conventional_identifier_either_way_round():
    # Two passes, weakest evidence first, so the answer cannot depend on
    # registry iteration order.
    claimed = _Device("dev_claimed", identifiers={("some_domain", PERIPHERAL_MAC)})
    declared = _Device("dev_declared", connections={(BLUETOOTH, PERIPHERAL_MAC)})
    for order in ((claimed, declared), (declared, claimed)):
        index = _index(*order)
        assert index.peripherals[PERIPHERAL_MAC] == "dev_declared"
        assert index.proxies[PERIPHERAL_MAC] == "dev_declared"


def test_a_network_mac_outranks_an_identifier_for_the_proxy_question():
    # The proxy question is "which device is this scanner source?", and a
    # network MAC is the exact form the answer takes.
    claimed = _Device("dev_claimed", identifiers={("some_domain", PROXY_MAC)})
    proxy = _Device("dev_proxy", connections={(NETWORK, PROXY_MAC)})
    for order in ((claimed, proxy), (proxy, claimed)):
        assert _index(*order).proxies[PROXY_MAC] == "dev_proxy"


def test_managed_addresses_is_the_peripheral_index_whole():
    # `detect_idle_slots` skips addresses Home Assistant can judge. That is
    # every peripheral in the registry -- not the subset currently allocated,
    # and never a proxy's network MAC.
    index = _index(
        _Device("dev_madoka", identifiers={("daikin_madoka", PERIPHERAL_MAC)}),
        _Device("dev_proxy", connections={(NETWORK, PROXY_MAC)}),
    )
    assert index.managed_addresses == {PERIPHERAL_MAC}


def test_an_empty_registry_indexes_to_nothing():
    index = _index()
    assert (index.peripherals, index.proxies, index.managed_addresses) == ({}, {}, set())


# --------------------------------------------------------------------------
# Resolving a proxy to its device: identity first, MAC correlation second
# --------------------------------------------------------------------------
#
# On ESP32 the Bluetooth MAC is the base MAC + 2, `bleak_esphome` uses
# `device_info.bluetooth_mac_address or device_info.mac_address` as the scanner
# source, and Home Assistant's ESPHome integration registers the device with
# the Wi-Fi MAC. So on current firmware the source string appears in no
# device-registry connection at all, and MAC correlation resolves nothing --
# silently. Home Assistant's `bluetooth` integration keeps the real mapping:
# one config entry per external scanner, keyed by the source it registered.

PROXY_BT_MAC = "D8:3B:DA:11:22:35"


def test_the_scanner_record_resolves_a_source_no_mac_can_reach():
    fallback = _index(
        _Device("dev_proxy", connections={(NETWORK, PROXY_MAC)})
    ).proxies
    assert PROXY_BT_MAC not in fallback            # the defect, stated plainly
    index = build_proxy_index([(PROXY_BT_MAC, "dev_proxy")], fallback)
    assert index[PROXY_BT_MAC] == "dev_proxy"
    # The Wi-Fi MAC still resolves, for firmware that uses it as its source.
    assert index[PROXY_MAC] == "dev_proxy"


def test_the_scanner_record_wins_over_mac_correlation():
    # An identity beats a correlation, so a MAC collision cannot misdirect a
    # proxy whose device Home Assistant has named outright.
    index = build_proxy_index(
        [(PROXY_MAC, "dev_right")], {PROXY_MAC: "dev_wrong"}
    )
    assert index[PROXY_MAC] == "dev_right"


def test_a_half_filled_record_falls_through_to_the_fallback():
    # A local adapter's entry carries neither field; an older Home Assistant
    # may carry the source without the device id.
    index = build_proxy_index(
        [(None, None), (PROXY_MAC, None), (None, "dev_orphan")],
        {PROXY_MAC: "dev_proxy"},
    )
    assert index == {PROXY_MAC: "dev_proxy"}


def test_the_recorded_source_is_canonicalised():
    # It is stored verbatim from `scanner.source`, and habluetooth is not
    # guaranteed to be consistent about case; the lookup key is upper.
    index = build_proxy_index([("d8:3b:da:11:22:35", "dev_proxy")], {})
    assert index[PROXY_BT_MAC] == "dev_proxy"


def test_no_records_and_no_fallback_resolve_nothing():
    assert build_proxy_index([], {}) == {}
