"""Pure MAC -> device_id indexing over the Home Assistant device registry.

No Home Assistant dependency: the registry entries are read duck-typed
(``.id``, ``.connections``, ``.identifiers``) and the connection-type strings
are injected by the caller, exactly as :mod:`.telemetry_reader` takes its two
lookups as callables. That keeps the whole indexing rule -- which is where two
subsystems' idea of "the address of a thing" have to agree -- unit-testable
under plain pytest.

**Two indexes, built from one pass, because the two questions are different.**

* *Which Home Assistant device is this BLE peripheral?* -- asked of an address
  habluetooth reports as allocated, to judge whether the slot is a ghost. A
  wrong answer here fabricates an incident, so only Bluetooth evidence counts:
  a device's Wi-Fi ``CONNECTION_NETWORK_MAC`` must never enter this index or a
  BLE address could collide with some dead device's network MAC and be flagged.

* *Which Home Assistant device is this proxy?* -- asked of a scanner
  ``source``, to find the BlueSight telemetry entities on it. Home Assistant's
  ESPHome integration registers a proxy with ``(CONNECTION_NETWORK_MAC,
  mac_address)``, so excluding network MACs -- correct for the peripheral
  question -- would resolve nothing for a proxy that uses its Wi-Fi MAC as its
  source. This index therefore admits them.

  It is still only half the answer, and :func:`build_proxy_index` supplies the
  other half: on current firmware the scanner source is the ESP32's
  *Bluetooth* MAC, which appears in no device-registry connection at all.

The asymmetry is safe in the direction it is applied. A wrong proxy match
costs nothing: the reader looks for three specific sensor names on that device
and finds none, so the proxy reports no signal, which is what it would have
reported anyway. Only the peripheral index can turn a bad match into an alert,
and that is the one kept strict.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .model import normalize_address

#: A canonicalized BLE MAC: six colon-separated hex octets. Used to keep the
#: indexes clean of non-MAC identifiers (integrations put all sorts of strings
#: in the second identifier element).
_MAC_RE = re.compile(r"^[0-9A-F]{2}(:[0-9A-F]{2}){5}$")


def looks_like_mac(value: object) -> bool:
    """True if ``value`` is a plausible colon-separated MAC address."""
    return (
        isinstance(value, str)
        and ":" in value
        and _MAC_RE.match(normalize_address(value)) is not None
    )


@dataclass(frozen=True, slots=True)
class DeviceIndex:
    """The two lookups the coordinator needs, built from one registry scan."""

    #: BLE address -> device_id, Bluetooth evidence only. Also serves as the
    #: set of addresses Home Assistant can *judge*: an address absent here has
    #: no device, so entity-based availability cannot speak for it.
    peripherals: dict[str, str]
    #: Proxy MAC -> device_id, network MACs included (see module docstring).
    #: The *fallback* half of proxy resolution; :func:`build_proxy_index`
    #: layers Home Assistant's own scanner record on top.
    proxies: dict[str, str]

    @property
    def managed_addresses(self) -> set[str]:
        """Addresses resolving to a registry device, for ``detect_idle_slots``.

        Deliberately the peripheral index and not the proxy one: the question
        that detector asks is "can Home Assistant judge this device from its
        entities?", and a network-MAC match is not evidence about a BLE
        peripheral. Deliberately the *whole* index and not the subset that is
        currently allocated, per that detector's contract.
        """
        return set(self.peripherals)


def build_device_index(
    devices: Iterable[Any],
    *,
    bluetooth_connection: str,
    network_connection: str,
) -> DeviceIndex:
    """Index the device registry by MAC, scanning connections and identifiers.

    A BLE device's MAC may live in ``connections`` as
    ``(CONNECTION_BLUETOOTH, mac)`` (some integrations) or in ``identifiers``
    as ``(domain, mac)`` (e.g. daikin_madoka). Identifier values are only
    mapped when MAC-shaped, to avoid polluting the indexes with the many
    non-MAC identifiers integrations register.

    Three passes over the same materialized list, weakest evidence first, so
    the result never depends on registry iteration order: a MAC-shaped
    identifier is a convention, a connection is a declaration, and the
    declaration must win when both name the same address. Within the proxy
    index the network MAC is the strongest evidence of all, because it is
    literally the string habluetooth hands back as a remote scanner's
    ``source``.

    Three passes rather than one loop with three tests, and one loop rather
    than three calls: this runs on every snapshot, so the registry is walked
    once and both answers come back together.
    """
    devices = list(devices)
    peripherals: dict[str, str] = {}
    proxies: dict[str, str] = {}
    for device in devices:
        for ident in device.identifiers:
            if looks_like_mac(ident[1]):
                address = normalize_address(ident[1])
                peripherals.setdefault(address, device.id)
                proxies.setdefault(address, device.id)
    for device in devices:
        for conn in device.connections:
            if conn[0] == bluetooth_connection and looks_like_mac(conn[1]):
                address = normalize_address(conn[1])
                peripherals[address] = device.id
                proxies[address] = device.id
    for device in devices:
        for conn in device.connections:
            if conn[0] == network_connection and looks_like_mac(conn[1]):
                # Proxies only. A network MAC says nothing about a BLE
                # peripheral, and letting it speak for one is how a healthy
                # device gets reported as a ghost.
                proxies[normalize_address(conn[1])] = device.id
    return DeviceIndex(peripherals=peripherals, proxies=proxies)


def build_proxy_index(
    scanner_records: Iterable[tuple[str | None, str | None]],
    fallback: dict[str, str],
) -> dict[str, str]:
    """``scanner source -> device_id``, Home Assistant's own record first.

    ``fallback`` is :attr:`DeviceIndex.proxies`, i.e. MAC correlation against
    the device registry. It is a fallback and not the answer, because on a
    current proxy the two MACs are not the same MAC:

    * ``bleak_esphome.connect_scanner`` takes the scanner ``source`` as
      ``device_info.bluetooth_mac_address or device_info.mac_address`` -- the
      ESP32's **Bluetooth** MAC when the firmware reports one, which on ESP32
      is the base MAC + 2 and therefore differs from the Wi-Fi MAC.
    * Home Assistant's ESPHome integration registers the device with
      ``(CONNECTION_NETWORK_MAC, device_info.mac_address)`` -- the **Wi-Fi**
      MAC. The Bluetooth MAC is kept in the config entry's data, not in the
      device registry.

    So on any firmware new enough to report ``bluetooth_mac_address``, no
    device-registry connection holds the string habluetooth calls ``source``,
    and MAC correlation alone resolves nothing at all -- silently, with every
    proxy reading as having no telemetry.

    ``scanner_records`` closes that gap with the record Home Assistant keeps
    for exactly this purpose: the ``bluetooth`` integration creates one config
    entry per external scanner, keyed by the scanner's own ``source`` string
    and carrying the ``source_device_id`` of the device that provides it. It is
    an identity, not a correlation, so it wins; it is also vendor-neutral,
    covering any proxy that registers a remote scanner rather than ESPHome
    alone. Records missing either half are skipped, which is what a local
    adapter's entry (and an older Home Assistant that did not record the device
    id) looks like -- those fall through to MAC correlation, which is still
    right for a proxy old enough to use its Wi-Fi MAC as the source.
    """
    index = dict(fallback)
    for source, device_id in scanner_records:
        if source and device_id:
            index[normalize_address(source)] = device_id
    return index
