"""The card says how often a device took a slot over the last day.

One connection, held, is what a well-behaved device looks like, so nothing is
drawn below two. Above it the numbers are shown and never judged: a sensor that
wakes every few minutes reconnects all day by design.
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
ADDR = "1C:54:9E:8E:1D:2C"


def _render(history, language="en") -> str:
    states = {
        "sensor.kitchen_slots_used": {
            "state": "1",
            "attributes": {
                "total": 3,
                "allocated": [ADDR],
                "allocated_devices": [
                    {
                        "address": ADDR,
                        "name": "Plant sensor",
                        "device_id": "dev1",
                        "path": None,
                        "history": history,
                    }
                ],
            },
        },
        INCIDENT: QUIET,
    }
    out = subprocess.run(
        [NODE, str(HARNESS), json.dumps({"config": {}, "states": states, "language": language})],
        capture_output=True, text=True, encoding="utf-8", check=True, timeout=30,
    )
    return json.loads(out.stdout)["html"]


def test_one_held_connection_is_not_worth_a_line():
    html = _render({"connections": 1, "proxy_changes": 0})
    assert "Plant sensor" in html
    assert "24 h" not in html


def test_a_slot_held_since_startup_draws_as_before():
    assert "24 h" not in _render(None)


def test_reconnections_are_counted():
    html = _render({"connections": 12, "proxy_changes": 0})
    assert "12 connections in 24 h" in html
    assert "proxy change" not in html


def test_proxy_changes_follow_the_count_with_their_own_plural():
    assert "5 connections in 24 h · 1 proxy change" in _render(
        {"connections": 5, "proxy_changes": 1}
    )
    assert "5 connections in 24 h · 3 proxy changes" in _render(
        {"connections": 5, "proxy_changes": 3}
    )


def test_the_line_is_in_the_viewers_language():
    assert "5 connexions en 24 h · 2 changements de proxy" in _render(
        {"connections": 5, "proxy_changes": 2}, language="fr"
    )
