"""The card finds its proxies by what they are, not by what they are called.

Discovery used to be `sensor.*_slots_used`, the health companions were derived
from that slug, and the proxy's name was the friendly name with " Slots Used"
cut off the end. All three read English out of an entity id or a label.

Home Assistant builds a new entity's id from its *translated* name in 41
languages, French among them. The moment entity names are translated, a proxy
adopted on a French install is `sensor.proxy_salon_emplacements_utilises`, and
every one of those three rules misses it: no tile, no health line, no name.

The entity registry the frontend already holds says which integration made an
entity, which one it is, and which device it belongs to -- in no language.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
HARNESS = Path(__file__).parent / "card_harness.js"

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

INCIDENT = "binary_sensor.bluesight_incident"
QUIET = {"state": "off", "attributes": {"incident_count": 0, "incidents": []}}


def _render(states: dict, entities=None, devices=None, **config) -> dict:
    scenario = {"config": config, "states": states}
    if entities is not None:
        scenario["entities"] = entities
    if devices is not None:
        scenario["devices"] = devices
    out = subprocess.run(
        [NODE, str(HARNESS), json.dumps(scenario)],
        capture_output=True, text=True, encoding="utf-8", check=True, timeout=30,
    )
    return json.loads(out.stdout)


def _entry(device_id: str, key: str, platform: str = "bluesight") -> dict:
    return {"device_id": device_id, "platform": platform, "translation_key": key}


#: A French install after the names were translated: not one English word in an
#: entity id or a friendly name.
FRENCH_STATES = {
    "sensor.proxy_salon_emplacements_utilises": {
        "state": "2",
        "attributes": {
            "total": 3, "free": 1, "allocated": [], "allocated_devices": [],
            "friendly_name": "Proxy Salon Emplacements utilisés",
        },
    },
    "binary_sensor.proxy_salon_en_ligne": {"state": "on", "attributes": {}},
    "sensor.proxy_salon_derniere_annonce": {
        "state": "12", "attributes": {"device_count": 4},
    },
    INCIDENT: QUIET,
}
FRENCH_ENTITIES = {
    "sensor.proxy_salon_emplacements_utilises": _entry("dev1", "slots_used"),
    "binary_sensor.proxy_salon_en_ligne": _entry("dev1", "online"),
    "sensor.proxy_salon_derniere_annonce": _entry("dev1", "last_device_seen"),
}
DEVICES = {"dev1": {"name": "atom (D0:CF)", "name_by_user": "Proxy Salon"}}


def test_a_proxy_is_found_whatever_its_entity_id_says():
    html = _render(FRENCH_STATES, FRENCH_ENTITIES, DEVICES)["html"]
    assert "2/3" in html
    assert "No BlueSight proxies found" not in html


def test_its_name_is_the_devices_not_a_label_with_a_suffix_cut_off():
    html = _render(FRENCH_STATES, FRENCH_ENTITIES, DEVICES)["html"]
    assert ">Proxy Salon<" in html
    assert "Emplacements" not in html


def test_its_health_companions_are_found_through_the_device():
    html = _render(FRENCH_STATES, FRENCH_ENTITIES, DEVICES)["html"]
    assert "last advert 12 s ago · 4 devices seen" in html


def test_an_offline_proxy_is_still_drawn_offline():
    states = {
        **FRENCH_STATES,
        "binary_sensor.proxy_salon_en_ligne": {"state": "off", "attributes": {}},
    }
    html = _render(states, FRENCH_ENTITIES, DEVICES)["html"]
    assert "offline" in html
    assert "2/3" not in html


def test_another_integrations_lookalike_is_not_a_proxy():
    """The old rule took any `sensor.*_slots_used` carrying a `total`. With a
    registry to ask, only BlueSight's own entities are BlueSight's."""
    states = {
        **FRENCH_STATES,
        "sensor.nas_slots_used": {"state": "9", "attributes": {"total": 12}},
    }
    entities = {**FRENCH_ENTITIES, "sensor.nas_slots_used": _entry("nas", "slots_used", "nas")}
    assert "9/12" not in _render(states, entities, DEVICES)["html"]


def test_the_tile_counts_a_fleet_found_this_way():
    html = _render(FRENCH_STATES, FRENCH_ENTITIES, DEVICES, layout="tile")["html"]
    assert "2/3 slots" in html


def test_without_a_registry_the_card_still_finds_english_ids():
    """An older Home Assistant hands the card no registry. The suffix rule
    stays as the fallback, so nobody's dashboard empties on upgrade."""
    states = {
        "sensor.kitchen_slots_used": {
            "state": "1",
            "attributes": {"total": 3, "friendly_name": "Kitchen Slots Used"},
        },
        INCIDENT: QUIET,
    }
    html = _render(states)["html"]
    assert "1/3" in html
    assert ">Kitchen<" in html
