"""The proxy named in an incident is the one the user named.

The regression: the first real ``bond_lost`` on a live instance rendered as

    « 1 échec d'appairage sur atomebuanderie (D0:CF:13:0F:05:5A), qui ne
      détient aucun bond pour cet appareil — réappairez via atomebuanderie
      (D0:CF:13:0F:05:5A) »

Naming the proxy twice is the wording's whole point — Home Assistant picks the
route, so the advice has to say *which* proxy to re-pair through — but the
name being substituted was habluetooth's scanner name, and the user had
already renamed that proxy to "Proxy Buanderie" in Home Assistant.

Everything below is pure: the registry entries are plain objects, the
catalogues are the shipped JSON files, and ``build_triage_data`` is the same
pure assembly the coordinator calls. The one Home-Assistant-shaped step
handing ``dr.async_get(hass).devices.values()`` to ``build_device_index`` —
lives in ``coordinator._build_device_index`` and is covered on CI by
``test_coordinator_shell``.
"""
from __future__ import annotations

from custom_components.bluesight.coordinator_data import build_triage_data
from custom_components.bluesight.device_index import (
    build_device_index,
    resolve_proxy_names,
)
from custom_components.bluesight.locale import read_catalogues
from custom_components.bluesight.model import IncidentKind, ProxySlots
from custom_components.bluesight.rendering import Catalogue
from custom_components.bluesight.telemetry import CounterDeltas, ProxyTelemetry
from custom_components.bluesight.window import FailureWindow

DOMAIN = "bluesight"
BLUETOOTH = "bluetooth"
NETWORK = "mac"

PROXY = "D0:CF:13:0F:05:5A"
SCANNER_NAME = "atomebuanderie (D0:CF:13:0F:05:5A)"
DEVICE = "1C:54:9E:8E:1D:2C"


class _RegistryDevice:
    def __init__(self, device_id, *, name=None, name_by_user=None,
                 connections=(), identifiers=()):
        self.id = device_id
        self.name = name
        self.name_by_user = name_by_user
        self.connections = set(connections)
        self.identifiers = set(identifiers)


def _bluesight_proxy_device(*, renamed_to=None):
    """BlueSight's own device for the proxy, as `sensor.py` creates it."""
    return _RegistryDevice(
        "dev_bluesight_proxy",
        name=SCANNER_NAME,
        name_by_user=renamed_to,
        identifiers={(DOMAIN, PROXY)},
    )


def _names(*devices):
    """What the coordinator computes per snapshot, in one line, as it does."""
    index = build_device_index(
        devices,
        bluetooth_connection=BLUETOOTH,
        network_connection=NETWORK,
        own_domain=DOMAIN,
    )
    return resolve_proxy_names({PROXY: SCANNER_NAME}, index.proxy_user_names)


def _bond_lost_detail(names, *, language="fr"):
    # Fed through the measured route: `detect_bond_lost` reads the rolling
    # window, so the counter has to reach it as a delta carrying the proxy
    # that measured it. A baseline of 0 makes the whole count the delta.
    deltas = CounterDeltas()
    deltas.update(PROXY, {DEVICE: 0})
    data = build_triage_data(
        [],
        {},
        FailureWindow(60.0, 3, clock=lambda: 0.0),
        catalogue=Catalogue.for_language(language, read_catalogues()),
        telemetry=[
            ProxyTelemetry(source=PROXY, smp_failures={DEVICE: 3}, bonds=set())
        ],
        counter_deltas=deltas,
        proxy_names=names,
    )
    incidents = [i for i in data.incidents if i.kind is IncidentKind.BOND_LOST]
    assert len(incidents) == 1
    return incidents[0]


def test_the_rendered_detail_uses_the_name_the_user_gave_the_proxy():
    incident = _bond_lost_detail(_names(_bluesight_proxy_device(renamed_to="Proxy Buanderie")))
    assert incident.detail == (
        "3 connexions refusées en 60s via Proxy Buanderie, qui n'a pas la clé "
        "d'appairage de cet appareil. Réappairez l'appareil en passant par "
        "Proxy Buanderie : chaque proxy garde ses propres clés, appairer via "
        "un autre ne corrigera rien."
    )
    assert SCANNER_NAME not in incident.detail


def test_an_unrenamed_proxy_still_reads_as_it_always_did():
    """The fallback is not a regression path: it is the shipped behaviour for
    every user who has not renamed anything."""
    incident = _bond_lost_detail(_names(_bluesight_proxy_device()))
    assert incident.detail == (
        f"3 connexions refusées en 60s via {SCANNER_NAME}, qui n'a pas la clé "
        f"d'appairage de cet appareil. Réappairez l'appareil en passant par "
        f"{SCANNER_NAME} : chaque proxy garde ses propres clés, appairer via "
        f"un autre ne corrigera rien."
    )


def test_a_proxy_with_no_registry_device_at_all_falls_back_to_the_scanner_name():
    """First snapshots run before the proxy's entities exist, and a disabled
    or deleted BlueSight device never comes back."""
    incident = _bond_lost_detail(_names())
    assert SCANNER_NAME in incident.detail


def test_a_rename_mid_incident_does_not_re_key_and_so_does_not_re_alert():
    """`Incident.key` folds in `kind`, `address` and `sources` — never
    `detail_params`. A user renaming a proxy while an incident is open must
    not dismiss and re-raise its notification."""
    before = _bond_lost_detail(_names(_bluesight_proxy_device()))
    after = _bond_lost_detail(
        _names(_bluesight_proxy_device(renamed_to="Proxy Buanderie"))
    )
    assert before.key == after.key
    assert before.detail != after.detail


def test_the_esphome_devices_own_rename_is_not_what_gets_shown():
    """The proxy has two Home Assistant devices: ESPHome's and BlueSight's.
    The card reads BlueSight's entities, so BlueSight's device is the one
    whose name the incident text has to agree with."""
    names = _names(
        _bluesight_proxy_device(renamed_to="Proxy Buanderie"),
        _RegistryDevice(
            "dev_esphome",
            name="atomebuanderie",
            name_by_user="Buanderie (ESPHome)",
            identifiers={("esphome", "atomebuanderie")},
            connections={(NETWORK, "D0:CF:13:0F:05:58")},
        ),
    )
    assert names[PROXY] == "Proxy Buanderie"


def test_the_ghost_slot_detail_uses_the_users_name_too():
    """The same resolution reaches every detector that names a proxy, so the
    detail and the notification built beside it cannot disagree."""
    data = build_triage_data(
        [ProxySlots(PROXY, SCANNER_NAME, 3, 2, [DEVICE])],
        {DEVICE: False},
        FailureWindow(60.0, 3, clock=lambda: 0.0),
        catalogue=Catalogue.for_language("fr", read_catalogues()),
        proxy_names=_names(_bluesight_proxy_device(renamed_to="Proxy Buanderie")),
    )
    ghosts = [i for i in data.incidents if i.kind is IncidentKind.GHOST_SLOT]
    assert len(ghosts) == 1
    assert ghosts[0].detail == (
        "Slot retenu sur Proxy Buanderie alors que l'appareil est indisponible"
    )
    # The published attribute shape is untouched: only the text inside
    # `detail` changed, and `sources` still names the proxy by address.
    assert ghosts[0].sources == [PROXY]
