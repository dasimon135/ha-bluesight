"""Tap a proxy's ring to see which devices held a slot on it.

A device's detail answers "where was it". This is the other side: the proxy
that is always full, and the question "what is sitting on it". It is asked for
on tap over the ``bluesight/proxy_timeline`` command and drawn as one bar per
device on a shared axis.

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

SOURCE = "D0:CF:13:0F:05:5A"


def _states(source: str | None = SOURCE, extra: dict | None = None) -> dict:
    attributes = {
        "total": 3,
        "free": 2,
        "allocated_devices": [
            {
                "address": "AA:BB:CC:DD:EE:01",
                "name": "Madoka attic",
                "device_id": "d1",
                "path": {"rssi": -60, "quality": "good"},
            }
        ],
        "friendly_name": "Proxy Attic Slots Used",
    }
    if source is not None:
        attributes["source"] = source
    return {
        "sensor.attic_slots_used": {"state": "1", "attributes": attributes},
        **(extra or {}),
    }


ANSWER = {
    "source": SOURCE,
    "name": "Proxy Attic",
    "window_s": 86400,
    "observed_s": 86400,
    "devices": [
        {
            "address": "AA:BB:CC:DD:EE:01",
            "name": "Madoka attic",
            "stays": [[80000, 40000], [30000, 0]],
            "truncated": False,
        },
        {
            "address": "AA:BB:CC:DD:EE:02",
            "name": "",
            "stays": [[5000, 4000]],
            "truncated": False,
        },
    ],
    "omitted": 0,
}


def _run(config: dict, states: dict, **scenario) -> dict:
    out = subprocess.run(
        [
            NODE,
            str(HARNESS),
            json.dumps({"config": config, "states": states, **scenario}),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=30,
    )
    return json.loads(out.stdout)


def _tap(ws=ANSWER, states=None, config=None, **scenario) -> dict:
    return _run(
        {"layout": "rings", "show_devices": False, **(config or {})},
        states if states is not None else _states(),
        tapRing=0,
        ws=ws,
        **scenario,
    )


# --- the ring is a button ---------------------------------------------------


def test_a_ring_with_a_source_is_a_button_to_the_keyboard() -> None:
    result = _run({"layout": "rings"}, _states())
    attrs = next(v for k, v in result["attrs"].items() if k.startswith("ring"))
    assert attrs["role"] == "button"
    assert attrs["tabindex"] == "0"


def test_a_ring_without_a_source_is_only_a_picture() -> None:
    result = _run({"layout": "rings"}, _states(source=None))
    ring_attrs = [v for k, v in result["attrs"].items() if k == "ring"]
    assert not any(a.get("role") == "button" for a in ring_attrs)


def test_a_missing_proxy_is_not_a_button() -> None:
    result = _run({"layout": "rings", "proxies": ["sensor.gone_slots_used"]}, {})
    assert not any(
        v.get("role") == "button"
        for k, v in result["attrs"].items()
        if k.startswith("ring")
    )


def test_nothing_is_asked_until_a_ring_is_tapped() -> None:
    result = _run({"layout": "rings"}, _states(), ws=ANSWER)
    assert result["ws"] == []
    assert "detail-title" not in result["dialog"]


# --- the tap ----------------------------------------------------------------


def test_a_tap_asks_for_that_proxys_timeline_by_its_source() -> None:
    result = _tap()
    assert result["ws"] == [{"type": "bluesight/proxy_timeline", "source": SOURCE}]
    assert "detail-title" in result["dialog"]
    assert "Proxy Attic" in result["dialog"]


def test_the_title_line_says_how_full_the_proxy_is() -> None:
    states = _states(
        extra={"sensor.attic_saturation_24h": {"state": "42", "attributes": {}}}
    )
    dialog = _tap(states=states)["dialog"]
    assert "1/3 slots" in dialog
    assert "full 42% of the day" in dialog


def test_enter_and_space_open_it_too() -> None:
    assert "detail-title" in _tap(ringKey="Enter")["dialog"]
    assert "detail-title" in _tap(ringKey=" ")["dialog"]


def test_another_key_does_not() -> None:
    result = _tap(ringKey="a")
    assert "detail-title" not in result["dialog"]
    assert result["ws"] == []


def test_an_offline_proxy_can_still_be_asked_about() -> None:
    states = _states(
        extra={"binary_sensor.attic_online": {"state": "off", "attributes": {}}}
    )
    result = _tap(states=states)
    assert result["ws"] == [{"type": "bluesight/proxy_timeline", "source": SOURCE}]
    assert "offline" in result["dialog"]


# --- what it draws ----------------------------------------------------------


def test_each_device_is_a_line_with_its_own_bar() -> None:
    dialog = _tap()["dialog"]
    assert dialog.count("proxy-dev-track") == 2
    assert "Madoka attic" in dialog


def test_a_device_home_assistant_could_not_name_shows_its_address() -> None:
    assert "AA:BB:CC:DD:EE:02" in _tap()["dialog"]


def test_every_stay_is_a_segment() -> None:
    assert _tap()["dialog"].count("detail-seg proxy-0") == 3


def test_the_bars_share_one_axis() -> None:
    assert _tap()["dialog"].count("detail-axis") == 1


def test_a_proxy_nothing_held_says_so() -> None:
    dialog = _tap(ws={**ANSWER, "devices": []})["dialog"]
    assert "No device held a slot on this proxy in this period." in dialog


def test_a_stretch_not_watched_yet_is_hatched() -> None:
    dialog = _tap(ws={**ANSWER, "observed_s": 3600})["dialog"]
    assert "detail-unobserved" in dialog


def test_devices_left_out_are_counted_in_the_singular() -> None:
    assert "and 1 more device" in _tap(ws={**ANSWER, "omitted": 1})["dialog"]


def test_devices_left_out_are_counted_in_the_plural() -> None:
    assert "and 4 more devices" in _tap(ws={**ANSWER, "omitted": 4})["dialog"]


def test_a_device_that_reconnects_too_often_is_flagged() -> None:
    devices = [{**ANSWER["devices"][0], "truncated": True}]
    dialog = _tap(ws={**ANSWER, "devices": devices})["dialog"]
    assert "Only the most recent stays are kept" in dialog


def test_a_backend_that_cannot_answer_leaves_the_rest_standing() -> None:
    dialog = _tap(wsError=True)["dialog"]
    assert "The timeline could not be loaded." in dialog
    assert "detail-title" in dialog


def test_a_host_without_the_command_says_so() -> None:
    dialog = _tap(noWS=True)["dialog"]
    assert "The timeline could not be loaded." in dialog


# --- in the one-line card's popup -------------------------------------------


def test_in_the_popup_a_ring_changes_the_screen_and_back_returns() -> None:
    tile = {"layout": "tile", "popup_layout": "rings", "show_devices": False}
    result = _run(
        tile,
        _states(),
        tap=True,
        tapRing=0,
        ws=ANSWER,
    )
    assert "detail-title" in result["dialog"]
    assert result["dialog"].count("scrim") == 1

    back = _run(
        tile,
        _states(),
        tap=True,
        tapRing=0,
        back=True,
        ws=ANSWER,
    )
    assert "detail-title" not in back["dialog"]
    assert "ring" in back["dialog"]


# --- the language -----------------------------------------------------------


def test_it_speaks_french() -> None:
    dialog = _tap(language="fr")["dialog"]
    assert "Qui a tenu un créneau" in dialog
    assert "Who held a slot" not in dialog
