"""`layout: rings` -- each proxy as a ring of slots, the signal as its colour.

The rack answers "who holds which slot" with a row per slot; a ring answers
"how full is this proxy, and how well are its occupants heard" in one glance,
which is what a fleet of a dozen proxies needs. It is opt-in: a stored config
without `layout` still means the rack, and these tests pin that too.

They run the shipped card under Node (``tests/card_harness.js``) and are
skipped when Node is not installed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
HARNESS = Path(__file__).with_name("card_harness.js")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

INCIDENT = "binary_sensor.bluesight_incident"


def _device(name: str, rssi: int | None, quality: str | None, **extra) -> dict:
    entry: dict = {"address": "AA:BB:CC:DD:EE:FF", "name": name, "device_id": "d1"}
    if rssi is not None:
        entry["path"] = {"rssi": rssi, "quality": quality}
    entry.update(extra)
    return entry


def _proxy(slug: str, used: int, total: int, devices: list | None = None) -> dict:
    return {
        f"sensor.{slug}_slots_used": {
            "state": str(used),
            "attributes": {
                "total": total,
                "free": total - used,
                "allocated_devices": devices or [],
                "friendly_name": f"{slug} Slots Used",
            },
        }
    }


def _run(config: dict, states: dict, language: str | None = None) -> dict:
    scenario = {"config": config, "states": states}
    if language:
        scenario["language"] = language
    out = subprocess.run(
        [NODE, str(HARNESS), json.dumps(scenario)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=30,
    )
    return json.loads(out.stdout)


def _rings(states: dict, **config) -> dict:
    return _run({"layout": "rings", **config}, states)


THREE = [
    _device("Madoka Buanderie", -58, "good"),
    _device("Capteur cave", -77, "fair"),
    _device("Boîtier Mesh", -91, "weak"),
]


# --- the shape --------------------------------------------------------------


def test_a_ring_replaces_the_rack() -> None:
    html = _rings(_proxy("a", 3, 3, THREE))["html"]
    assert 'class="ring' in html
    assert "slot-rack" not in html


def test_without_the_option_nothing_changes() -> None:
    html = _run({}, _proxy("a", 3, 3, THREE))["html"]
    assert "ring-gauge" not in html
    assert "slot-rack" in html


def test_an_unknown_layout_is_the_full_card() -> None:
    html = _run({"layout": "bogus"}, _proxy("a", 3, 3, THREE))["html"]
    assert "ring-gauge" not in html


def test_there_is_one_segment_per_slot() -> None:
    html = _rings(_proxy("a", 1, 3, THREE[:1]))["html"]
    assert html.count('class="seg') == 3


def test_a_segment_takes_the_band_of_the_device_in_it() -> None:
    html = _rings(_proxy("a", 3, 3, THREE))["html"]
    assert 'class="seg held good"' in html
    assert 'class="seg held fair"' in html
    assert 'class="seg held weak"' in html


def test_a_free_slot_is_a_plain_segment() -> None:
    html = _rings(_proxy("a", 1, 3, THREE[:1]))["html"]
    assert html.count('class="seg"') == 2


def test_a_held_slot_with_no_signal_is_held_but_unrated() -> None:
    html = _rings(_proxy("a", 1, 3, [_device("Sans mesure", None, None)]))["html"]
    assert 'class="seg held"' in html


def test_the_count_sits_in_the_middle() -> None:
    html = _rings(_proxy("a", 2, 3, THREE[:2]))["html"]
    assert '<text class="ring-count">2/3</text>' in html


def test_an_occupant_past_the_end_of_the_rack_still_gets_a_segment() -> None:
    # Dropping it would under-report how many devices hold a connection.
    html = _rings(_proxy("a", 3, 2, THREE))["html"]
    assert html.count('class="seg') == 3
    assert "overflow" in html


def test_a_proxy_with_no_slots_draws_a_dash() -> None:
    html = _rings(_proxy("a", 0, 0))["html"]
    assert '<text class="ring-count">\u2013</text>' in html
    assert 'class="seg' not in html


# --- offline ----------------------------------------------------------------


def test_an_offline_proxy_draws_no_segments_and_no_occupants() -> None:
    states = {
        **_proxy("a", 3, 3, THREE),
        "binary_sensor.a_online": {"state": "off", "attributes": {}},
    }
    result = _rings(states)
    assert 'class="ring offline"' in result["html"]
    assert 'class="seg' not in result["html"]
    assert "Madoka Buanderie" not in result["html"]
    assert "offline" in result["html"]


def test_a_missing_proxy_says_so() -> None:
    result = _run(
        {"layout": "rings", "proxies": ["sensor.gone_slots_used"]},
        {},
    )
    assert "missing" in result["html"]


# --- saturation -------------------------------------------------------------


def _with_saturation(value: str) -> dict:
    return {
        **_proxy("a", 1, 3, THREE[:1]),
        "sensor.a_saturation_24h": {"state": value, "attributes": {}},
    }


def test_the_day_so_far_is_said_in_words() -> None:
    html = _rings(_with_saturation("42.4"))["html"]
    assert "full 42% of the day" in html


def test_a_saturated_proxy_has_an_outer_arc() -> None:
    assert 'class="sat"' in _rings(_with_saturation("42"))["html"]


def test_an_unobserved_proxy_says_nothing_about_its_day() -> None:
    # `unknown` is not zero: zero would claim a comfortable day nobody watched.
    for value in ("unknown", "unavailable"):
        html = _rings(_with_saturation(value))["html"]
        assert "of the day" not in html
        assert 'class="sat"' not in html


def test_a_proxy_never_full_has_the_words_but_no_arc() -> None:
    html = _rings(_with_saturation("0"))["html"]
    assert "full 0% of the day" in html
    assert 'class="sat"' not in html


# --- the devices ------------------------------------------------------------


def test_each_device_is_a_tile_with_its_name_and_signal() -> None:
    html = _rings(_proxy("a", 3, 3, THREE))["html"]
    assert "Madoka Buanderie" in html
    assert "-91 dBm" in html
    assert "device-badge band-weak" in html


def test_show_devices_off_leaves_the_ring_alone() -> None:
    html = _rings(_proxy("a", 3, 3, THREE), show_devices=False)["html"]
    assert "device-tiles" not in html
    assert "ring-gauge" in html


def test_a_nameless_device_is_shown_by_address() -> None:
    html = _rings(_proxy("a", 1, 3, [_device("", -60, "good")]))["html"]
    assert "AA:BB:CC:DD:EE:FF" in html


# --- the fleet strip --------------------------------------------------------


def test_the_strip_counts_proxies_slots_and_weak_signals() -> None:
    states = {**_proxy("a", 3, 3, THREE), **_proxy("b", 0, 3)}
    html = _rings(states)["html"]
    assert "2 proxies" in html
    assert "3/6 slots" in html
    assert "1 weak signal" in html


def test_no_weak_signal_is_a_green_chip() -> None:
    html = _rings(_proxy("a", 1, 3, THREE[:1]))["html"]
    assert "0 weak signals" in html
    assert "chip-dot signal-good" in html


def test_an_offline_proxys_stale_devices_are_not_counted_as_weak() -> None:
    states = {
        **_proxy("a", 3, 3, THREE),
        "binary_sensor.a_online": {"state": "off", "attributes": {}},
    }
    assert "0 weak signals" in _rings(states)["html"]


def test_the_incident_area_is_still_there() -> None:
    states = {
        **_proxy("a", 1, 3, THREE[:1]),
        INCIDENT: {
            "state": "off",
            "attributes": {"incident_count": 0, "incidents": []},
        },
    }
    assert "No incidents" in _rings(states)["html"]


# --- accessibility ----------------------------------------------------------


def test_the_gauge_says_what_it_shows_without_colour() -> None:
    attrs = _rings(_proxy("a", 2, 3, THREE[:2]))["attrs"]["ring-gauge"]
    assert attrs["role"] == "img"
    assert attrs["aria-label"] == "a: 2 of 3 slots"


def test_a_tile_carries_the_whole_story_in_its_tooltip() -> None:
    device = _device(
        "Madoka Manon", -83, "fair", history={"connections": 19, "proxy_changes": 7}
    )
    attrs = next(
        value
        for key, value in _rings(_proxy("a", 1, 3, [device]))["attrs"].items()
        if key.startswith("device-tile")
    )
    assert "Madoka Manon" in attrs["title"]
    assert "-83 dBm" in attrs["title"]
    assert "19 connections in 24 h" in attrs["title"]


# --- size, grid, editor, language -------------------------------------------


def test_the_card_is_taller_with_more_devices_to_name() -> None:
    few = _rings(_proxy("a", 1, 3, THREE[:1]))["size"]
    many = _rings(_proxy("a", 3, 3, THREE))["size"]
    assert many > few


def test_the_card_does_not_reserve_room_for_devices_it_does_not_name() -> None:
    named = _rings(_proxy("a", 3, 3, THREE))["size"]
    bare = _rings(_proxy("a", 3, 3, THREE), show_devices=False)["size"]
    assert bare < named


def test_an_empty_fleet_still_gets_the_empty_sentence() -> None:
    result = _rings({})
    assert "No BlueSight proxies found" in result["html"]
    assert result["size"] == 3


def test_a_sections_view_gives_it_the_full_width() -> None:
    assert _rings(_proxy("a", 1, 3, THREE[:1]))["grid"] == {
        "columns": 12,
        "min_columns": 6,
    }


def test_the_strings_come_in_french() -> None:
    result = _run({"layout": "rings"}, _with_saturation("42"), language="fr")
    assert "plein 42 % du jour" in result["html"]
    assert "1 proxy" in result["html"]
