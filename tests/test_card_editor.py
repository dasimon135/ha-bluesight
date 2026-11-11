"""The card's visual editor, its size in a sections view, and its masonry size.

The editor is `getConfigForm`: Home Assistant renders the schema with its own
controls, so the card ships no editor element. `proxies` stays out of it --
auto-discovery is the default, and an entity picker cannot be narrowed to slot
sensors.
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


def _run(config=None, devices=(), total=4, language="en") -> dict:
    states = {
        "sensor.kitchen_slots_used": {
            "state": str(len(devices)),
            "attributes": {
                "total": total,
                "allocated": [d["address"] for d in devices],
                "allocated_devices": list(devices),
            },
        },
        INCIDENT: QUIET,
    }
    out = subprocess.run(
        [NODE, str(HARNESS), json.dumps({"config": config or {}, "states": states, "language": language})],
        capture_output=True, text=True, encoding="utf-8", check=True, timeout=30,
    )
    return json.loads(out.stdout)


def _device(i, *, path=True, history=None, known=True):
    return {
        "address": f"AA:BB:CC:DD:EE:{i:02X}",
        "name": f"Device {i}",
        "device_id": f"dev{i}" if known else None,
        "path": {"rssi": -60, "quality": "good", "stronger_name": None} if path else None,
        "history": history,
    }


# --- getCardSize -------------------------------------------------------------


def test_a_bare_rack_sizes_as_before():
    """Four slots, nothing beyond each name: 2 + (1 + ceil(4/2))."""
    assert _run(devices=[_device(i, path=False) for i in range(4)])["size"] == 5


def test_signal_and_history_lines_make_the_card_taller():
    bare = _run(devices=[_device(i, path=False) for i in range(4)])["size"]
    busy = _run(
        devices=[
            _device(i, history={"connections": 9, "proxy_changes": 1})
            for i in range(4)
        ]
    )["size"]
    # Eight more lines of ~16px is ~128px: a little over two and a half units.
    assert busy == bare + 3


def test_the_unknown_marker_is_a_line_too():
    known = _run(devices=[_device(0, path=False)])["size"]
    unknown = _run(devices=[_device(i, path=False, known=False) for i in range(1)])["size"]
    assert unknown >= known


def test_label_lines_cost_nothing_without_names():
    devices = [_device(i, history={"connections": 9, "proxy_changes": 1}) for i in range(4)]
    assert _run({"show_devices": False}, devices=devices)["size"] == 2 + 2


# --- getGridOptions ----------------------------------------------------------


def test_the_full_card_fills_the_section_and_grows_with_its_content():
    grid = _run()["grid"]
    assert grid == {"columns": 12, "min_columns": 6}
    assert "rows" not in grid  # "auto": the rack's height is its content


def test_the_one_line_layout_is_a_tile_sized_cell():
    assert _run({"layout": "tile"})["grid"] == {
        "columns": 6, "rows": 1, "min_columns": 3, "min_rows": 1, "max_rows": 1,
    }


# --- getConfigForm -----------------------------------------------------------


def test_the_editor_offers_every_option_but_the_proxy_list():
    form = _run()["form"]
    assert [f["name"] for f in form] == [
        "title", "layout", "show_devices", "tile_tap", "incident_entity",
    ]
    by_name = {f["name"]: f for f in form}
    # The defaults the card applies when a key is absent, so the form shows
    # what is actually drawn.
    assert by_name["layout"]["default"] == "full"
    assert by_name["show_devices"]["default"] is True
    assert by_name["tile_tap"]["default"] == "popup"
    assert by_name["incident_entity"]["selector"] == "entity"


def test_the_editor_is_labelled_in_english():
    by_name = {f["name"]: f for f in _run()["form"]}
    assert by_name["show_devices"]["label"] == "Name the device in each slot"
    assert by_name["layout"]["options"] == ["Full card", "One line"]
    assert by_name["tile_tap"]["helper"] == (
        "What tapping the one-line card opens. Not set: the full card, in a popup."
    )
    assert by_name["title"]["helper"] is None


def test_the_editor_is_labelled_in_the_displayed_language():
    by_name = {f["name"]: f for f in _run(language="fr")["form"]}
    assert by_name["show_devices"]["label"] == "Nommer l'appareil de chaque slot"
    assert by_name["layout"]["options"] == ["Carte complète", "Une ligne"]


def test_a_blank_select_says_what_blank_means():
    """`ha-form` shows a select whose key is absent as blank -- it hands
    `default` to the control as a placeholder the dropdown ignores -- so the
    help text carries the default instead, in the displayed language."""
    en = {f["name"]: f for f in _run()["form"]}
    assert en["layout"]["helper"] == "Not set: the full card."
    fr = {f["name"]: f for f in _run(language="fr")["form"]}
    assert fr["layout"]["helper"] == "Non défini : la carte complète."
    assert "Non défini" in fr["tile_tap"]["helper"]
