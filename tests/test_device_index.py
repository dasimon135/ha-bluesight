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


# --- display names ----------------------------------------------------------
#
# Naming an allocated address is one more lookup in a registry the coordinator
# already walks once per snapshot, so the name comes back from the same pass
# that produced the ids. Keyed by device id, not by address: one device can be
# reachable through both an identifier and a connection.


class _NamedDevice(_Device):
    def __init__(self, device_id, *, name=None, name_by_user=None, **kwargs):
        super().__init__(device_id, **kwargs)
        self.name = name
        self.name_by_user = name_by_user


def test_a_devices_display_name_comes_back_with_its_id():
    index = _index(
        _NamedDevice(
            "dev_madoka",
            name="Madoka BRC1H",
            identifiers={("daikin_madoka", PERIPHERAL_MAC)},
        )
    )
    assert index.names[index.peripherals[PERIPHERAL_MAC]] == "Madoka BRC1H"


def test_the_users_own_name_wins_over_the_integrations():
    """`name_by_user` is what the user sees everywhere else in Home Assistant,
    so it is what the card has to show."""
    index = _index(
        _NamedDevice(
            "dev_madoka",
            name="Madoka BRC1H",
            name_by_user="Madoka salon",
            identifiers={("daikin_madoka", PERIPHERAL_MAC)},
        )
    )
    assert index.names["dev_madoka"] == "Madoka salon"


def test_a_device_with_no_name_at_all_resolves_to_the_empty_string():
    """Home Assistant allows a nameless device. It is still a *known* device,
    so it keeps its id -- the card decides what to draw from that, not from
    the emptiness of the name."""
    index = _index(
        _NamedDevice(
            "dev_nameless", identifiers={("daikin_madoka", PERIPHERAL_MAC)}
        )
    )
    assert index.names["dev_nameless"] == ""
    assert index.peripherals[PERIPHERAL_MAC] == "dev_nameless"


def test_a_whitespace_only_name_reads_as_no_name():
    index = _index(
        _NamedDevice(
            "dev_blank",
            name="   ",
            identifiers={("daikin_madoka", PERIPHERAL_MAC)},
        )
    )
    assert index.names["dev_blank"] == ""


def test_a_whitespace_only_rename_falls_through_to_the_integrations_name():
    """A `name_by_user` of spaces is not a name the user chose, so it must not
    blank out a device that does have one. Home Assistant will store one."""
    index = _index(
        _NamedDevice(
            "dev_madoka",
            name="Madoka BRC1H",
            name_by_user="   ",
            identifiers={("daikin_madoka", PERIPHERAL_MAC)},
        )
    )
    assert index.names["dev_madoka"] == "Madoka BRC1H"


def test_a_registry_entry_without_name_attributes_is_tolerated():
    """The entries are read duck-typed, exactly as the module docstring says;
    a registry object that lacks the attribute must not abort the snapshot."""
    index = _index(_Device("dev_bare", identifiers={("daikin_madoka", PERIPHERAL_MAC)}))
    assert index.names["dev_bare"] == ""


def test_names_cover_only_the_devices_that_entered_an_index():
    """A real registry holds hundreds of devices and this runs every snapshot;
    naming the ones no BLE address can reach would be pure waste."""
    index = _index(
        _NamedDevice("dev_ble", name="Madoka", identifiers={("daikin_madoka", PERIPHERAL_MAC)}),
        _NamedDevice("dev_proxy", name="Proxy Buanderie", connections={(NETWORK, PROXY_MAC)}),
        _NamedDevice("dev_cloud", name="Weather", identifiers={("met", "home")}),
    )
    assert set(index.names) == {"dev_ble", "dev_proxy"}


def test_names_default_empty_so_a_hand_built_index_still_works():
    from custom_components.bluesight.device_index import DeviceIndex

    assert DeviceIndex({}, {}).names == {}


# --- the name the user gave a proxy -----------------------------------------
#
# BlueSight registers its own device per proxy, identified `(bluesight,
# <source>)`, and that is the device the user renames: the card and the proxy's
# sensors read their friendly name from it. habluetooth's scanner name -- e.g.
# "atomebuanderie (D0:CF:13:0F:05:5A)" -- is what the integration *suggested*,
# not what the user chose, and it is unreadable inside a sentence that has to
# name the proxy twice. So the registry is asked, in the same pass, what the
# user called each proxy.
#
# Strictly `name_by_user` and never `name`: the fallback is the live
# habluetooth name the coordinator already holds, which is fresher than the
# copy the registry kept from whenever the entities were last created.

BLUESIGHT = "bluesight"


def _own(*devices):
    return build_device_index(
        devices,
        bluetooth_connection=BLUETOOTH,
        network_connection=NETWORK,
        own_domain=BLUESIGHT,
    ).proxy_user_names


def test_a_renamed_bluesight_proxy_device_yields_the_users_name():
    assert _own(
        _NamedDevice(
            "dev_bs_proxy",
            name="atomebuanderie (D8:3B:DA:11:22:33)",
            name_by_user="Proxy Buanderie",
            identifiers={(BLUESIGHT, PROXY_MAC)},
        )
    ) == {PROXY_MAC: "Proxy Buanderie"}


def test_an_unrenamed_proxy_device_yields_nothing_rather_than_its_scanner_name():
    """No entry, not an entry holding the integration's own name: absence is
    what makes the caller fall through to the live habluetooth name."""
    assert _own(
        _NamedDevice(
            "dev_bs_proxy",
            name="atomebuanderie (D8:3B:DA:11:22:33)",
            identifiers={(BLUESIGHT, PROXY_MAC)},
        )
    ) == {}


def test_a_blank_rename_is_not_a_rename():
    """A `name_by_user` of spaces is not a name the user chose to see."""
    assert _own(
        _NamedDevice(
            "dev_bs_proxy",
            name="atomebuanderie",
            name_by_user="   ",
            identifiers={(BLUESIGHT, PROXY_MAC)},
        )
    ) == {}


def test_only_our_own_devices_are_read_for_a_proxy_rename():
    """The ESPHome device for the same proxy carries its own rename, and the
    card does not show it. Naming the proxy from it would put one name in the
    incident text and a different one on the entity beside it."""
    assert _own(
        _NamedDevice(
            "dev_esphome",
            name="atomebuanderie",
            name_by_user="Buanderie ESP",
            connections={(NETWORK, PROXY_MAC)},
            identifiers={("esphome", "atomebuanderie")},
        )
    ) == {}


def test_the_bluesight_service_device_is_not_a_proxy():
    """`(bluesight, "service")` is the hub device the incident sensor lives on.
    Its identifier is not MAC-shaped and it names no proxy."""
    assert _own(
        _NamedDevice(
            "dev_service",
            name="BlueSight",
            name_by_user="Bluetooth doctor",
            identifiers={(BLUESIGHT, "service")},
        )
    ) == {}


def test_a_proxy_rename_is_keyed_by_canonical_address():
    assert _own(
        _NamedDevice(
            "dev_bs_proxy",
            name_by_user="Proxy Buanderie",
            identifiers={(BLUESIGHT, "d8:3b:da:11:22:33")},
        )
    ) == {PROXY_MAC: "Proxy Buanderie"}


def test_renames_are_not_collected_unless_a_domain_is_named():
    """The rule is opt-in, so a caller that does not own devices in the
    registry pays nothing and no other integration's identifiers are read."""
    assert _index(
        _NamedDevice(
            "dev_bs_proxy",
            name_by_user="Proxy Buanderie",
            identifiers={(BLUESIGHT, PROXY_MAC)},
        )
    ).proxy_user_names == {}


def test_proxy_user_names_default_empty_so_a_hand_built_index_still_works():
    from custom_components.bluesight.device_index import DeviceIndex

    assert DeviceIndex({}, {}).proxy_user_names == {}


# --- precedence -------------------------------------------------------------


def test_the_users_rename_wins_over_the_live_scanner_name():
    from custom_components.bluesight.device_index import resolve_proxy_names

    assert resolve_proxy_names(
        {PROXY_MAC: "atomebuanderie (D8:3B:DA:11:22:33)"},
        {PROXY_MAC: "Proxy Buanderie"},
    ) == {PROXY_MAC: "Proxy Buanderie"}


def test_a_proxy_with_no_registry_device_at_all_keeps_its_scanner_name():
    from custom_components.bluesight.device_index import resolve_proxy_names

    assert resolve_proxy_names({PROXY_MAC: "atomebuanderie"}, {}) == {
        PROXY_MAC: "atomebuanderie"
    }


def test_a_rename_for_a_proxy_no_scanner_reports_is_still_carried():
    """A retired proxy's BlueSight device outlives its scanner. Carrying the
    name costs one entry and keeps a still-open incident readable."""
    from custom_components.bluesight.device_index import resolve_proxy_names

    assert resolve_proxy_names({}, {PROXY_MAC: "Proxy Buanderie"}) == {
        PROXY_MAC: "Proxy Buanderie"
    }


def test_a_blank_name_on_either_side_never_displaces_a_real_one():
    from custom_components.bluesight.device_index import resolve_proxy_names

    assert resolve_proxy_names({PROXY_MAC: "atomebuanderie"}, {PROXY_MAC: "  "}) == {
        PROXY_MAC: "atomebuanderie"
    }
    assert resolve_proxy_names({PROXY_MAC: ""}, {}) == {}
