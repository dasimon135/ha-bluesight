"""The card says why a connection went through the proxy it went through.

A row of the rack names the device holding the slot. It never said how well
that proxy actually heard it, nor that another one heard it far better -- so a
device connected across the house looked exactly like one connected next door.

Every slot whose signal was read shows it, with its band. The second half --
another proxy heard it clearly better -- is drawn only when the backend names
one, which it does only past habluetooth's own switch margin.
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


def _path(rssi=-82, stronger_name=None, stronger_rssi=None, full=False, quality="fair"):
    return {
        "rssi": rssi,
        "quality": quality,
        "stronger_source": "D0:CF:13:0E:C9:2A" if stronger_name else None,
        "stronger_name": stronger_name,
        "stronger_rssi": stronger_rssi,
        "stronger_was_full": full,
    }


def _render(path, language="en") -> str:
    states = {
        "sensor.kitchen_slots_used": {
            "state": "1",
            "attributes": {
                "total": 3,
                "friendly_name": "Kitchen Slots Used",
                "allocated": [ADDR],
                "allocated_devices": [
                    {
                        "address": ADDR,
                        "name": "Madoka salon",
                        "device_id": "dev1",
                        "path": path,
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


def test_every_read_slot_shows_its_signal_and_band():
    """The number alone asks the reader to know what -82 dBm means."""
    html = _render(_path())
    assert "Madoka salon" in html
    assert "-82 dBm · fair" in html
    assert "heard it" not in html


def test_the_band_is_marked_for_the_eye_too():
    html = _render(_path(rssi=-93, quality="weak"))
    assert "slot-path signal-weak" in html
    assert "-93 dBm · weak" in html


def test_the_band_is_in_the_viewers_language():
    assert "-93 dBm · limite" in _render(_path(rssi=-93, quality="weak"), language="fr")


def test_a_path_without_a_band_still_shows_the_number():
    html = _render(_path(quality=None))
    assert "-82 dBm" in html
    assert "fair" not in html


def test_a_clearly_stronger_proxy_is_named_under_the_device():
    html = _render(_path(stronger_name="Proxy Salon", stronger_rssi=-54))
    assert "-82 dBm · fair · Proxy Salon heard it at -54" in html


def test_a_stronger_proxy_that_was_full_says_so():
    """The difference between "an odd route" and "a full proxy" is the whole
    value of the line: one is a question, the other is an explanation."""
    html = _render(_path(stronger_name="Proxy Salon", stronger_rssi=-54, full=True))
    assert "-82 dBm · fair · Proxy Salon heard it at -54, but was full" in html


def test_the_line_is_in_the_viewers_language():
    html = _render(_path(stronger_name="Proxy Salon", stronger_rssi=-54), language="fr")
    assert "-82 dBm · moyen · Proxy Salon l'entendait à -54" in html


def test_a_backend_too_old_to_publish_a_path_draws_the_rack_as_before():
    html = _render(None)
    assert "Madoka salon" in html
    assert "dBm" not in html
