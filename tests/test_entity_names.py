"""Entity names follow the user's language, like everything else BlueSight says.

Incident details, notifications and the card have been translated since 0.5.0;
the entity names were still `_attr_name = "Slots Used"`, in English, on every
install. They are translation keys now.

Two things must not move. The **English** names are byte-for-byte what they
were, because Home Assistant derives a new entity's id from its name and an
English install's ids must keep coming out as `..._slots_used`. And the
**keys** are a contract with the card, which finds a proxy's entities by them
(`REGISTRY_ROLES` in `bluesight-card.js`).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytest.importorskip("homeassistant.components.sensor")

from custom_components.bluesight.binary_sensor import (
    IncidentBinarySensor,
    ProxyOnlineBinarySensor,
)
from custom_components.bluesight.sensor import (
    LastDeviceSeenSensor,
    SaturationSensor,
    SlotsFreeSensor,
    SlotsUsedSensor,
)

ROOT = Path(__file__).parent.parent / "custom_components" / "bluesight"

#: (class, platform, translation key, the English name it has always had)
ENTITIES = [
    (SlotsUsedSensor, "sensor", "slots_used", "Slots Used"),
    (SlotsFreeSensor, "sensor", "slots_free", "Slots Free"),
    (LastDeviceSeenSensor, "sensor", "last_device_seen", "Last device seen"),
    (SaturationSensor, "sensor", "saturation_24h", "Saturation (24h)"),
    (IncidentBinarySensor, "binary_sensor", "incident", "Incident"),
    (ProxyOnlineBinarySensor, "binary_sensor", "online", "Online"),
]


def _names(relative: str) -> dict:
    return json.loads((ROOT / relative).read_text(encoding="utf-8")).get("entity", {})


@pytest.mark.parametrize(("cls", "_platform", "key", "_name"), ENTITIES)
def test_the_name_is_a_translation_key_and_no_longer_a_string(cls, _platform, key, _name):
    # Read off an instance: Home Assistant's entity metaclass turns `_attr_*`
    # class attributes into properties, so the class itself answers with one.
    entity = object.__new__(cls)
    assert entity.translation_key == key
    assert entity.has_entity_name is True
    # A hard-coded name wins over the translation, in every language, so none
    # may be left behind in either platform module.
    for module in ("sensor.py", "binary_sensor.py"):
        assert "_attr_name =" not in (ROOT / module).read_text(encoding="utf-8")


@pytest.mark.parametrize(("_cls", "platform", "key", "name"), ENTITIES)
@pytest.mark.parametrize("source", ["strings.json", "translations/en.json"])
def test_the_english_name_is_what_it_always_was(_cls, platform, key, name, source):
    assert _names(source)[platform][key]["name"] == name


@pytest.mark.parametrize(("_cls", "platform", "key", "_name"), ENTITIES)
def test_french_names_every_entity(_cls, platform, key, _name):
    assert _names("translations/fr.json")[platform][key]["name"].strip()


def test_the_card_reads_the_same_keys():
    card = (ROOT / "frontend" / "www" / "bluesight-card.js").read_text(encoding="utf-8")
    roles = re.search(r"const REGISTRY_ROLES = \{(.*?)\};", card, re.S).group(1)
    assert set(re.findall(r"^\s*(\w+):", roles, re.M)) == {
        "slots_used", "online", "last_device_seen",
    }
