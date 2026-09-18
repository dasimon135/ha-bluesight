"""`layout: tile` — the fleet on one line, with a colour that says go and look.

A dashboard is not a diagnostic screen. The card as it shipped answers *where*
the problem is, which costs one row per slot and a place of its own on the page.
The tile answers only *is there one*: a coloured dot, how many slots the fleet
is spending, how many incidents are open. Tapping it opens the card that
answers the rest, in a popup — the same gesture the madoka, rf-fan and dooya
cards already use.

The colours are the ones the incident feed already uses, plus one the feed has
no need for: **grey**, for "the diagnostic cannot speak". A green dot on a card
whose sensor is missing would be the one lie this card must never tell.

These run the shipped card under Node (``tests/card_harness.js``) and are
skipped when Node is not installed. They need no Home Assistant harness, so
they also run on Windows.
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


def _proxy(source: str, used: int, total: int) -> dict:
    return {
        "state": str(used),
        "attributes": {
            "total": total,
            "free": total - used,
            "allocated": [],
            "source": source,
            "friendly_name": f"Proxy {source} slots used",
        },
    }


def _incident(state: str, **attributes) -> dict:
    return {"state": state, "attributes": attributes}


def _run(config: dict, states: dict, tap: bool = False) -> dict:
    scenario = {"config": config, "states": states}
    if tap:
        scenario["tap"] = True
    out = subprocess.run(
        [NODE, str(HARNESS), json.dumps(scenario)],
        capture_output=True,
        text=True,
        # Node writes UTF-8; Windows would otherwise decode it as cp1252.
        encoding="utf-8",
        check=True,
        timeout=30,
    )
    return json.loads(out.stdout)


def _tile(states: dict, **config) -> dict:
    return _run({"layout": "tile", **config}, states)


FLEET = {
    "sensor.proxy_a_slots_used": _proxy("AA", 2, 3),
    "sensor.proxy_b_slots_used": _proxy("BB", 3, 9),
}
QUIET = {**FLEET, INCIDENT: _incident("off", incident_count=0, incidents=[])}


# --- the shape --------------------------------------------------------------


def test_the_tile_is_one_line_and_not_the_rack() -> None:
    html = _tile(QUIET)["html"]
    assert 'class="tile' in html
    assert "slot-rack" not in html


def test_the_tile_is_one_row_high() -> None:
    assert _tile(QUIET)["size"] == 1


def test_without_the_option_nothing_changes() -> None:
    result = _run({}, QUIET)
    assert 'class="tile' not in result["html"]
    assert result["size"] > 1


# --- the colour -------------------------------------------------------------


def test_green_when_the_fleet_is_quiet() -> None:
    assert "dot green" in _tile(QUIET)["html"]


def test_amber_for_an_incident_that_holds_no_slot() -> None:
    states = {
        **FLEET,
        INCIDENT: _incident(
            "on", incident_count=1, incidents=[{"kind": "bond_lost", "address": "AA:BB"}]
        ),
    }
    assert "dot amber" in _tile(states)["html"]


def test_red_for_an_incident_that_wastes_a_slot() -> None:
    states = {
        **FLEET,
        INCIDENT: _incident(
            "on", incident_count=1, incidents=[{"kind": "deadlock", "address": "AA:BB"}]
        ),
    }
    assert "dot red" in _tile(states)["html"]


def test_red_wins_over_amber() -> None:
    states = {
        **FLEET,
        INCIDENT: _incident(
            "on",
            incident_count=2,
            incidents=[
                {"kind": "bond_lost", "address": "AA:BB"},
                {"kind": "ghost_slot", "address": "CC:DD"},
            ],
        ),
    }
    assert "dot red" in _tile(states)["html"]


def test_amber_when_an_incident_is_open_with_no_detail() -> None:
    """`on` with an empty list still means something is wrong."""
    states = {**FLEET, INCIDENT: _incident("on", incident_count=1, incidents=[])}
    assert "dot amber" in _tile(states)["html"]


# --- the colour the feed never needed --------------------------------------


def test_grey_when_the_incident_sensor_is_missing() -> None:
    assert "dot grey" in _tile(FLEET)["html"]


def test_grey_when_the_sensor_is_unavailable() -> None:
    states = {**FLEET, INCIDENT: _incident("unavailable")}
    assert "dot grey" in _tile(states)["html"]


def test_grey_when_the_data_is_known_to_be_partial() -> None:
    states = {
        **FLEET,
        INCIDENT: _incident(
            "off", incident_count=0, incidents=[], availability_degraded=True
        ),
    }
    assert "dot grey" in _tile(states)["html"]


def test_partial_data_never_hides_an_open_incident() -> None:
    states = {
        **FLEET,
        INCIDENT: _incident(
            "on",
            incident_count=1,
            incidents=[{"kind": "deadlock", "address": "AA:BB"}],
            availability_degraded=True,
        ),
    }
    assert "dot red" in _tile(states)["html"]


# --- what the line says -----------------------------------------------------


def test_the_line_counts_the_whole_fleet_s_slots() -> None:
    assert "5/12" in _tile(QUIET)["html"]


def test_the_line_says_when_nothing_is_wrong() -> None:
    assert "No incidents" in _tile(QUIET)["html"]


def test_the_line_counts_the_open_incidents() -> None:
    states = {
        **FLEET,
        INCIDENT: _incident(
            "on",
            incident_count=2,
            incidents=[
                {"kind": "bond_lost", "address": "AA:BB"},
                {"kind": "deadlock", "address": "CC:DD"},
            ],
        ),
    }
    assert "2 incidents" in _tile(states)["html"]


def test_the_line_carries_the_card_title() -> None:
    assert "BlueSight" in _tile(QUIET)["html"]


# --- the tap ----------------------------------------------------------------


def test_tapping_opens_the_full_card_in_a_popup() -> None:
    result = _run({"layout": "tile"}, QUIET, tap=True)
    assert "bluesight-card" in result["dialog"]
    assert "slot-rack" in result["dialog"]


def test_the_popup_is_not_open_before_the_tap() -> None:
    assert "bluesight-card" not in _tile(QUIET)["dialog"]
