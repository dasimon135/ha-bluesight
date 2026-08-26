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

Both indexes come back with a companion ``names`` map -- the display name of
every device either one reached -- because the caller that asks "which device
is this?" is usually about to ask "what is it called?", and the registry has
already been walked.

The asymmetry is safe in the direction it is applied. A wrong proxy match
costs nothing: the reader looks for three specific sensor names on that device
and finds none, so the proxy reports no signal, which is what it would have
reported anyway. Only the peripheral index can turn a bad match into an alert,
and that is the one kept strict.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
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


def user_given_name(device: Any) -> str:
    """The name the *user* chose for ``device``, or ``""`` if they chose none.

    Strictly ``name_by_user``: the registry's other ``name`` is what the
    integration suggested, which is a different claim. Callers that only want
    to override a name they already hold need the two kept apart -- see
    :func:`resolve_proxy_names`.

    Whitespace is stripped and a blank result reads as no name at all. A
    ``name_by_user`` of spaces is not a name the user chose to see, and
    Home Assistant will happily store one.

    Read with ``getattr`` for the same reason the rest of this module reads
    registry entries duck-typed, and because the tests build registry
    stand-ins that carry only what they are about.
    """
    name = getattr(device, "name_by_user", None)
    return str(name).strip() if name else ""


def display_name(device: Any) -> str:
    """The name Home Assistant shows for ``device``, or ``""`` if it has none.

    ``name_by_user`` is what the user renamed the device to and is what they
    see everywhere else in Home Assistant, so it wins over the integration's
    own ``name``.

    A device really can have no name; that is not the same fact as an address
    the registry cannot account for at all, and the two must stay
    distinguishable downstream (see :class:`.model.DeviceRef`).
    """
    name = getattr(device, "name", None)
    return user_given_name(device) or (str(name).strip() if name else "")


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
    #: device_id -> display name, for the devices that entered either index
    #: above and no others (a real registry holds hundreds, and this is rebuilt
    #: every snapshot). Keyed by id rather than by address because one device
    #: can be reachable through both an identifier and a connection.
    #:
    #: Naming is a *display* concern and cannot fabricate an incident, but it
    #: is fed from the peripheral index all the same: a name is a claim about
    #: which device holds a slot, and answering it from a Wi-Fi MAC would put a
    #: confident wrong name on a BLE address -- worse than the raw MAC.
    names: dict[str, str] = field(default_factory=dict)
    #: Proxy source -> the name the *user* gave that proxy, for the proxies
    #: they have actually renamed and no others. Populated only when
    #: :func:`build_device_index` is told which identifier domain is the
    #: caller's own; empty otherwise.
    #:
    #: A third question, and the reason it cannot be answered from
    #: :attr:`proxies` above: that index answers "which device *is* this
    #: proxy?", and for a proxy it resolves to the ESPHome device -- the one
    #: whose telemetry entities we read. The rename the user made is on the
    #: caller's *own* device for the same proxy, which is what the card and
    #: the proxy's sensors are named from, so it is the one the incident text
    #: has to agree with. Keyed by address rather than by device id because
    #: unlike :attr:`names` there is exactly one such device per address.
    proxy_user_names: dict[str, str] = field(default_factory=dict)

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
    own_domain: str = "",
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

    ``own_domain`` is the caller's own integration domain. Given one, the
    first pass also records what the user renamed the caller's own per-proxy
    devices to (:attr:`DeviceIndex.proxy_user_names`), which is a *third*
    question and deliberately answered from the identifier domain rather than
    from the proxy index -- see that attribute. It is opt-in so that a caller
    that owns no devices reads no other integration's identifiers, and only
    MAC-shaped identifier values are taken, which is what keeps a
    ``(domain, "service")`` hub device out.

    Three passes rather than one loop with three tests, and one loop rather
    than three calls: this runs on every snapshot, so the registry is walked
    once and both answers come back together. A fourth pass then names the
    devices an index reached, for the same reason: the caller needs the names
    on the same snapshot and must not walk the registry again to get them.
    """
    devices = list(devices)
    peripherals: dict[str, str] = {}
    proxies: dict[str, str] = {}
    proxy_user_names: dict[str, str] = {}
    for device in devices:
        for ident in device.identifiers:
            if looks_like_mac(ident[1]):
                address = normalize_address(ident[1])
                peripherals.setdefault(address, device.id)
                proxies.setdefault(address, device.id)
                # Only a rename is recorded. The absence of one is the signal
                # that the caller should keep the name it already holds, which
                # is live rather than whatever the registry kept from the last
                # time the entities were created.
                if (
                    own_domain
                    and ident[0] == own_domain
                    and (renamed := user_given_name(device))
                ):
                    proxy_user_names[address] = renamed
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
    # A fourth pass, over the devices an index actually reached. Names are only
    # ever asked for by device id, so naming the whole registry would be work
    # thrown away on every snapshot.
    indexed = set(peripherals.values()) | set(proxies.values())
    names = {
        device.id: display_name(device)
        for device in devices
        if device.id in indexed
    }
    return DeviceIndex(
        peripherals=peripherals,
        proxies=proxies,
        names=names,
        proxy_user_names=proxy_user_names,
    )


def resolve_proxy_names(
    scanner_names: dict[str, str], user_names: dict[str, str]
) -> dict[str, str]:
    """What to call each proxy, the user's own name winning.

    ``scanner_names`` is what habluetooth reports a scanner as -- on a current
    ESPHome proxy something like ``"atomebuanderie (D0:CF:13:0F:05:5A)"``,
    which is a node name with a MAC glued to it. That string is fine as a
    device name, where it appears once, and unreadable inside an incident
    detail that has to name the proxy twice (``bond_lost`` says where the
    pairing failed *and* where to re-pair, because Home Assistant, not the
    user, picks the route). ``user_names`` is what the user renamed the
    proxy's BlueSight device to, and it is already what every entity on that
    device reports and what the card draws.

    A proxy in neither map simply is not here, and the detectors fall back to
    the raw source address, exactly as before. Blank names on either side are
    dropped rather than carried: an empty ``{proxy}`` is worse than a MAC, and
    a blank rename must not displace a real scanner name.

    Deliberately a resolution and not a rename of anything: the caller keeps
    ``scanner_names`` as the name it creates proxy *devices* with, so the
    registry's ``name`` stays the integration's own suggestion and clearing a
    rename in Home Assistant restores it. Feeding this result back into device
    creation would write the user's name into that field and make the rename
    impossible to undo.
    """
    resolved = {k: v.strip() for k, v in scanner_names.items() if v and v.strip()}
    resolved.update(
        {k: v.strip() for k, v in user_names.items() if v and v.strip()}
    )
    return resolved


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
