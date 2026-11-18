"""The device tiles under `layout: rings`, sorted by what needs a look.

A fleet of a dozen proxies holds thirty devices, and the card's job is to put
the one that needs a look first. A device that keeps changing proxy leads, then
the weakest signals; the rest follow. Each tile carries the device's day as a
row of bars, drawn from the connections sensor.

Nothing is a verdict. The amber outline is display, set by `bounce_threshold`;
no incident is raised and no new detector reads these numbers.

They run the shipped card under Node (``tests/card_harness.js``) and are
skipped when Node is not installed.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
HARNESS = Path(__file__).with_name("card_harness.js")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

CONNECTIONS = "sensor.bluesight_connections_24h"


def _device(name, rssi, quality, changes=0, connections=1, address="AA:BB:CC:DD:EE:01"):
    entry = {"address": address, "name": name, "device_id": "d"}
    if rssi is not None:
        entry["path"] = {"rssi": rssi, "quality": quality}
    if connections >= 2:
        entry["history"] = {"connections": connections, "proxy_changes": changes}
    return entry


def _proxy(slug, devices, total=3):
    return {
        f"sensor.{slug}_slots_used": {
            "state": str(len(devices)),
            "attributes": {
                "total": total,
                "free": total - len(devices),
                "allocated_devices": devices,
                "friendly_name": f"{slug} Slots Used",
            },
        }
    }


def _connections(*listed):
    return {
        CONNECTIONS: {
            "state": "10",
            "last_updated": "2026-11-18T10:00:00+00:00",
            "attributes": {"devices": list(listed)},
        }
    }


def _run(config, states, language=None, more=False, entities=None):
    scenario = {"config": {"layout": "rings", **config}, "states": states}
    if language:
        scenario["language"] = language
    if more:
        scenario["more"] = True
    if entities is not None:
        scenario["entities"] = entities
    out = subprocess.run(
        [NODE, str(HARNESS), json.dumps(scenario)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=30,
    )
    return json.loads(out.stdout)


def _tiles(html):
    return len(re.findall(r'class="device-tile(?: flagged)?"', html))


def _titles(html):
    return re.findall(r'<div class="device-title">([^<]*)</div>', html)


# --- the order --------------------------------------------------------------


def test_the_weakest_signal_comes_first() -> None:
    devices = [
        _device("Bon", -58, "good", address="AA:BB:CC:DD:EE:01"),
        _device("Limite", -91, "weak", address="AA:BB:CC:DD:EE:02"),
        _device("Moyen", -77, "fair", address="AA:BB:CC:DD:EE:03"),
    ]
    assert _titles(_run({}, _proxy("a", devices))["html"]) == ["Limite", "Moyen", "Bon"]


def test_a_device_that_keeps_changing_proxy_leads_whatever_its_signal() -> None:
    devices = [
        _device("Faible", -91, "weak", address="AA:BB:CC:DD:EE:01"),
        _device(
            "Rebondit",
            -58,
            "good",
            changes=7,
            connections=19,
            address="AA:BB:CC:DD:EE:02",
        ),
    ]
    assert _titles(_run({}, _proxy("a", devices))["html"]) == ["Rebondit", "Faible"]


def test_flagged_devices_are_ordered_by_how_often_they_changed() -> None:
    devices = [
        _device(
            "Cinq", -60, "good", changes=5, connections=9, address="AA:BB:CC:DD:EE:01"
        ),
        _device(
            "Neuf", -60, "good", changes=9, connections=14, address="AA:BB:CC:DD:EE:02"
        ),
    ]
    assert _titles(_run({}, _proxy("a", devices))["html"]) == ["Neuf", "Cinq"]


def test_a_device_with_no_reading_comes_after_those_with_one() -> None:
    devices = [
        _device("Sans mesure", None, None, address="AA:BB:CC:DD:EE:01"),
        _device("Bon", -58, "good", address="AA:BB:CC:DD:EE:02"),
    ]
    assert _titles(_run({}, _proxy("a", devices))["html"]) == ["Bon", "Sans mesure"]


def test_devices_from_every_proxy_share_one_list() -> None:
    states = {
        **_proxy("a", [_device("A1", -60, "good", address="AA:BB:CC:DD:EE:01")]),
        **_proxy("b", [_device("B1", -90, "weak", address="AA:BB:CC:DD:EE:02")]),
    }
    result = _run({}, states)
    assert _titles(result["html"]) == ["B1", "A1"]
    assert "via b" in result["html"]


# --- the outline ------------------------------------------------------------


def test_the_outline_starts_at_the_threshold() -> None:
    four = _device("Quatre", -60, "good", changes=4, connections=8)
    five = _device(
        "Cinq", -60, "good", changes=5, connections=8, address="AA:BB:CC:DD:EE:02"
    )
    html = _run({}, _proxy("a", [four, five]))["html"]
    assert html.count("device-tile flagged") == 1
    assert "5 proxy changes" in html
    assert "4 proxy changes" not in html


def test_the_threshold_is_the_users_to_set() -> None:
    device = _device("Deux", -60, "good", changes=2, connections=4)
    assert "flagged" not in _run({}, _proxy("a", [device]))["html"]
    assert "flagged" in _run({"bounce_threshold": 2}, _proxy("a", [device]))["html"]


@pytest.mark.parametrize("bad", [0, -3, "lots", None, ""])
def test_a_threshold_that_makes_no_sense_falls_back_to_five(bad) -> None:
    device = _device("Quatre", -60, "good", changes=4, connections=8)
    html = _run({"bounce_threshold": bad}, _proxy("a", [device]))["html"]
    assert "flagged" not in html


def test_the_outline_raises_nothing() -> None:
    device = _device("Rebondit", -60, "good", changes=9, connections=14)
    html = _run({}, _proxy("a", [device]))["html"]
    assert "No incidents" not in html or "incident-ok" in html
    assert 'class="incident ' not in html


# --- the tile ---------------------------------------------------------------


def test_the_tile_says_which_proxy_and_how_well() -> None:
    html = _run({}, _proxy("a", [_device("Madoka", -83, "fair")]))["html"]
    assert "via a · -83 dBm" in html
    assert "device-badge band-fair" in html


def test_the_badge_counts_its_bars_by_band() -> None:
    for quality, rssi, lit in (("good", -55, 3), ("fair", -80, 2), ("weak", -90, 1)):
        html = _run({}, _proxy("a", [_device("X", rssi, quality)]))["html"]
        assert html.count('<i class="on">') == lit


def test_a_tile_without_a_signal_has_a_grey_badge() -> None:
    html = _run({}, _proxy("a", [_device("Sans mesure", None, None)]))["html"]
    assert 'class="device-badge"' in html
    assert '<i class="on">' not in html


def test_the_badge_names_its_band_for_a_screen_reader() -> None:
    result = _run({}, _proxy("a", [_device("X", -91, "weak")]))
    badge = next(v for k, v in result["attrs"].items() if k.startswith("device-badge"))
    assert badge["aria-label"] == "weak"


# --- the day as bars --------------------------------------------------------


def _hourly(*counts):
    return [0] * (24 - len(counts)) + list(counts)


def test_a_listed_device_gets_its_day_as_bars() -> None:
    states = {
        **_proxy("a", [_device("Madoka", -83, "fair", address="AA:BB:CC:DD:EE:01")]),
        **_connections({"address": "AA:BB:CC:DD:EE:01", "hourly": _hourly(1, 0, 4, 2)}),
    }
    html = _run({}, states)["html"]
    assert html.count('<rect class="on">') == 3
    assert html.count('<rect class="off">') == 21


def test_the_bars_say_how_many_connections_in_words() -> None:
    states = {
        **_proxy("a", [_device("Madoka", -83, "fair", address="AA:BB:CC:DD:EE:01")]),
        **_connections({"address": "AA:BB:CC:DD:EE:01", "hourly": _hourly(1, 0, 4, 2)}),
    }
    result = _run({}, states)
    spark = next(v for k, v in result["attrs"].items() if k.startswith("device-spark"))
    assert spark["aria-label"] == "7 connections in 24 h"


def test_the_address_is_matched_whatever_its_case() -> None:
    states = {
        **_proxy("a", [_device("Madoka", -83, "fair", address="aa:bb:cc:dd:ee:01")]),
        **_connections({"address": "AA:BB:CC:DD:EE:01", "hourly": _hourly(2)}),
    }
    assert "device-spark" in _run({}, states)["html"]


def test_a_device_the_sensor_does_not_list_has_no_bars() -> None:
    states = {
        **_proxy("a", [_device("Madoka", -83, "fair")]),
        **_connections({"address": "FF:FF:FF:FF:FF:FF", "hourly": _hourly(3)}),
    }
    assert "device-spark" not in _run({}, states)["html"]


def test_a_device_with_a_silent_day_has_no_bars() -> None:
    states = {
        **_proxy("a", [_device("Madoka", -83, "fair", address="AA:BB:CC:DD:EE:01")]),
        **_connections({"address": "AA:BB:CC:DD:EE:01", "hourly": _hourly()}),
    }
    assert "device-spark" not in _run({}, states)["html"]


def test_without_the_connections_sensor_the_tiles_still_draw() -> None:
    html = _run({}, _proxy("a", [_device("Madoka", -83, "fair")]))["html"]
    assert "device-tile" in html
    assert "device-spark" not in html


def test_the_sensor_is_found_through_the_registry_when_its_id_is_translated() -> None:
    states = {
        **_proxy("a", [_device("Madoka", -83, "fair", address="AA:BB:CC:DD:EE:01")]),
        "sensor.bluesight_connexions_24_h": {
            "state": "3",
            "last_updated": "x",
            "attributes": {
                "devices": [{"address": "AA:BB:CC:DD:EE:01", "hourly": _hourly(2)}]
            },
        },
    }
    entities = {
        "sensor.bluesight_connexions_24_h": {
            "platform": "bluesight",
            "translation_key": "connections_24h",
            "device_id": "svc",
        }
    }
    assert "device-spark" in _run({}, states, entities=entities)["html"]


# --- how many ---------------------------------------------------------------


def _many(count):
    return [
        _device(f"D{i:02d}", -50 - i, "good", address=f"AA:BB:CC:DD:EE:{i:02X}")
        for i in range(count)
    ]


def _fleet(count):
    states = {}
    for i in range(0, count, 3):
        states.update(_proxy(f"p{i}", _many(count)[i : i + 3]))
    return states


def test_eight_tiles_are_drawn_and_the_rest_are_offered() -> None:
    html = _run({}, _fleet(12))["html"]
    assert _tiles(html) == 8
    assert "Show the 4 other devices" in html


def test_a_fleet_that_fits_has_no_button() -> None:
    html = _run({}, _fleet(6))["html"]
    assert _tiles(html) == 6
    assert 'class="more"' not in html


def test_the_button_shows_the_others_and_offers_to_fold_them() -> None:
    html = _run({}, _fleet(12), more=True)["html"]
    assert _tiles(html) == 12
    assert "Show fewer" in html


def test_one_hidden_device_is_said_in_the_singular() -> None:
    assert "Show the 1 other device" in _run({}, _fleet(9))["html"]


def test_the_card_grows_with_the_tiles_it_shows() -> None:
    few = _run({}, _fleet(3))["size"]
    capped = _run({}, _fleet(12))["size"]
    expanded = _run({}, _fleet(12), more=True)["size"]
    assert few < capped < expanded


# --- the rings alone, the editor, the language ------------------------------


def test_show_devices_off_draws_no_tiles_and_no_heading() -> None:
    html = _run({"show_devices": False}, _proxy("a", _many(3)))["html"]
    assert "device-tile" not in html
    assert "devices-heading" not in html


def test_an_offline_proxys_devices_are_not_listed() -> None:
    states = {
        **_proxy("a", _many(2)),
        "binary_sensor.a_online": {"state": "off", "attributes": {}},
    }
    assert "device-tile" not in _run({}, states)["html"]


def test_the_full_card_is_not_given_tiles() -> None:
    result = subprocess.run(
        [
            NODE,
            str(HARNESS),
            json.dumps({"config": {}, "states": _proxy("a", _many(2))}),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=30,
    )
    assert "device-tile" not in json.loads(result.stdout)["html"]


def test_the_editor_offers_the_threshold_as_a_number() -> None:
    result = subprocess.run(
        [NODE, str(HARNESS), json.dumps({"config": {}, "states": {}})],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=30,
    )
    field = next(
        f for f in json.loads(result.stdout)["form"] if f["name"] == "bounce_threshold"
    )
    assert field["selector"] == "number"
    assert field["default"] == 5
    assert "5" in field["helper"]


def test_the_tiles_speak_french() -> None:
    result = _run({}, _fleet(12), language="fr")
    assert "Appareils" in result["html"]
    assert "via p9" in result["html"]
    assert "Voir les 4 autres appareils" in result["html"]
