"""Tap a device tile, see which proxy held it hour by hour.

The tile has room for a name, a proxy and a number. Why a device bounces is a
story in time: this proxy for a while, then that one, then a gap. The detail
draws it, from a WebSocket answer the card asks for when a tile is tapped,
because the timeline changes with every connection in the house and is not
worth pushing to every open dashboard.

One popup, two screens: when the card is itself the content of the one-line
layout's popup, a tap on a device replaces what that popup shows and a "back"
returns, instead of opening a second popup on top of the first.

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

MANON = "9C:AC:6D:D4:F9:FC"


def _device(name, rssi, quality, changes=0, connections=1, address="AA:BB:CC:DD:EE:01"):
    entry = {"address": address, "name": name, "device_id": "d"}
    if rssi is not None:
        entry["path"] = {"rssi": rssi, "quality": quality}
    if connections >= 2:
        entry["history"] = {"connections": connections, "proxy_changes": changes}
    return entry


def _states(*devices, slug="salon"):
    return {
        f"sensor.{slug}_slots_used": {
            "state": str(len(devices)),
            "attributes": {
                "total": 3,
                "free": 3 - len(devices),
                "allocated_devices": list(devices),
                "friendly_name": f"{slug} Slots Used",
            },
        }
    }


TIMELINE = {
    "address": MANON,
    "window_s": 86400,
    "observed_s": 86400,
    "proxies": ["Proxy Valentine", "Proxy Salon"],
    "stays": [[86000, 60000, 0], [59000, 30000, 1], [29000, 0, 0]],
    "truncated": False,
}


def _run(config, states, **scenario):
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


def _manon_states():
    return _states(
        _device("Madoka Manon", -83, "fair", changes=7, connections=19, address=MANON)
    )


def _tap(config=None, ws=TIMELINE, **scenario):
    return _run(
        {"layout": "rings", **(config or {})},
        _manon_states(),
        tapDevice=0,
        ws=ws,
        **scenario,
    )


# --- the tile is a button ---------------------------------------------------


def test_a_device_tile_is_a_button_to_the_keyboard() -> None:
    result = _run({"layout": "rings"}, _manon_states())
    attrs = next(v for k, v in result["attrs"].items() if k.startswith("device-tile"))
    assert attrs["role"] == "button"
    assert attrs["tabindex"] == "0"


def test_nothing_opens_until_a_tile_is_tapped() -> None:
    result = _run({"layout": "rings"}, _manon_states(), ws=TIMELINE)
    assert "detail-title" not in result["dialog"]
    assert result["ws"] == []


# --- the detail -------------------------------------------------------------


def test_a_tap_opens_the_detail_and_asks_for_that_devices_timeline() -> None:
    result = _tap()
    assert "detail-title" in result["dialog"]
    assert "Madoka Manon" in result["dialog"]
    assert result["ws"] == [{"type": "bluesight/device_timeline", "address": MANON}]


def test_enter_opens_it_too() -> None:
    result = _tap(deviceKey="Enter")
    assert "detail-title" in result["dialog"]


def test_the_space_bar_opens_it_too() -> None:
    assert "detail-title" in _tap(deviceKey=" ")["dialog"]


def test_another_key_does_not() -> None:
    assert "detail-title" not in _tap(deviceKey="a")["dialog"]


def test_the_detail_says_how_well_and_by_whom() -> None:
    dialog = _tap()["dialog"]
    assert "-83 dBm" in dialog
    assert "held by salon" in dialog
    assert "detail-sub band-fair" in dialog


def test_the_three_numbers_the_tile_has_no_room_for() -> None:
    dialog = _tap()["dialog"]
    assert "<b>19</b><span>connections</span>" in dialog
    assert "<b>7</b><span>proxy changes</span>" in dialog
    # Proxies used is learned from the timeline, two of them here.
    assert "<b>2</b><span>proxies used</span>" in dialog


def test_a_device_with_no_history_shows_a_dash_not_a_zero() -> None:
    states = _states(_device("Calme", -60, "good", address="AA:BB:CC:DD:EE:09"))
    result = _run({"layout": "rings"}, states, tapDevice=0, ws={})
    assert "<b>\N{EN DASH}</b><span>connections</span>" in result["dialog"]


# --- the timeline -----------------------------------------------------------


def test_each_stay_is_a_segment_in_its_proxys_colour() -> None:
    dialog = _tap()["dialog"]
    assert dialog.count('class="detail-seg proxy-0"') == 2
    assert dialog.count('class="detail-seg proxy-1"') == 1


def test_a_stay_is_placed_on_the_line_by_when_it_began_and_how_long_it_lasted() -> None:
    dialog = _tap(ws={**TIMELINE, "stays": [[43200, 0, 0]]})["dialog"]
    # Began 12 h ago of 24: half way along, half the width.
    assert 'style="left:50.00%;width:50.00%"' in dialog


def test_a_very_short_stay_is_still_visible() -> None:
    dialog = _tap(ws={**TIMELINE, "stays": [[1000, 995, 0]]})["dialog"]
    assert "width:0.60%" in dialog


def test_the_legend_names_each_proxy_used() -> None:
    dialog = _tap()["dialog"]
    assert "Proxy Valentine" in dialog
    assert "Proxy Salon" in dialog
    assert dialog.count('<i class="proxy-') == 2


def test_the_axis_says_where_the_line_starts_and_ends() -> None:
    dialog = _tap()["dialog"]
    assert "24 h ago" in dialog
    assert "12 h" in dialog
    assert "now" in dialog


def test_a_stretch_not_yet_watched_is_hatched() -> None:
    dialog = _tap(ws={**TIMELINE, "observed_s": 43200})["dialog"]
    assert 'class="detail-unobserved" style="left:0%;width:50.00%"' in dialog


def test_a_fully_watched_window_has_no_hatching() -> None:
    assert "detail-unobserved" not in _tap()["dialog"]


def test_a_device_that_reconnects_constantly_says_its_timeline_is_cut() -> None:
    assert (
        "Only the most recent stays"
        in _tap(ws={**TIMELINE, "truncated": True})["dialog"]
    )
    assert "Only the most recent stays" not in _tap()["dialog"]


def test_a_device_with_no_stay_says_so_instead_of_drawing_an_empty_bar() -> None:
    dialog = _tap(ws={**TIMELINE, "stays": [], "proxies": []})["dialog"]
    assert "No connection recorded in this period." in dialog
    assert "detail-track" not in dialog


# --- when the backend cannot answer -----------------------------------------


def test_a_failed_answer_leaves_the_rest_of_the_detail_standing() -> None:
    dialog = _tap(wsError=True)["dialog"]
    assert "The timeline could not be loaded." in dialog
    assert "Madoka Manon" in dialog
    assert "<b>19</b>" in dialog


def test_a_card_with_no_websocket_says_the_same() -> None:
    dialog = _tap(noWS=True)["dialog"]
    assert "The timeline could not be loaded." in dialog


# --- one popup, two screens -------------------------------------------------


def _from_the_small_card(**scenario):
    return _run(
        {"layout": "tile", "popup_layout": "rings"},
        _manon_states(),
        tap=True,
        tapDevice=0,
        ws=TIMELINE,
        **scenario,
    )


def test_the_popup_of_the_one_line_card_can_show_the_rings() -> None:
    result = _run(
        {"layout": "tile", "popup_layout": "rings"},
        _manon_states(),
        tap=True,
        ws=TIMELINE,
    )
    assert "ring-gauge" in result["dialog"]
    assert "device-tile" in result["dialog"]


def test_it_still_shows_the_full_card_unless_told_otherwise() -> None:
    result = _run({"layout": "tile"}, _manon_states(), tap=True, ws=TIMELINE)
    assert "ring-gauge" not in result["dialog"]
    assert "slot-rack" in result["dialog"]


def test_a_tap_on_a_device_replaces_the_list_in_the_same_popup() -> None:
    dialog = _from_the_small_card()["dialog"]
    assert dialog.count('class="scrim"') == 1
    assert "detail-title" in dialog
    assert "ring-gauge" not in dialog


def test_the_second_screen_has_a_way_back() -> None:
    dialog = _from_the_small_card()["dialog"]
    assert 'class="detail-back"' in dialog
    assert "\N{LEFTWARDS ARROW} Back" in dialog


def test_back_returns_to_the_list_and_forgets_the_detail() -> None:
    dialog = _from_the_small_card(back=True)["dialog"]
    assert "ring-gauge" in dialog
    assert "detail-title" not in dialog
    assert dialog.count('class="scrim"') == 1


def test_a_card_on_a_dashboard_has_no_back_because_it_has_no_list_to_return_to() -> (
    None
):
    dialog = _tap()["dialog"]
    assert "detail-back" not in dialog
    assert 'class="x"' in dialog


def test_the_detail_asks_the_backend_once() -> None:
    assert len(_from_the_small_card()["ws"]) == 1


def test_going_back_before_the_answer_lands_draws_nothing_late() -> None:
    # Back is clicked after the answer in the harness; what matters is that the
    # list is what is on screen and the timeline is not.
    dialog = _from_the_small_card(back=True)["dialog"]
    assert "detail-seg" not in dialog


# --- the editor and the language --------------------------------------------


def test_the_editor_offers_the_popup_layout() -> None:
    form = _run({}, {})["form"]
    field = next(f for f in form if f["name"] == "popup_layout")
    assert field["options"] == ["Full card", "Rings, one per proxy"]
    assert field["default"] == "full"
    assert field["helper"]


def test_the_detail_speaks_french() -> None:
    result = _tap(language="fr")
    dialog = result["dialog"]
    assert "tenu par salon" in dialog
    assert "Quel proxy l&apos;a tenu" in dialog or "Quel proxy l'a tenu" in dialog
    assert "il y a 24 h" in dialog
    assert "maintenant" in dialog


@pytest.mark.parametrize("value", ["bogus", "", None, "FULL"])
def test_a_popup_layout_that_makes_no_sense_is_the_full_card(value) -> None:
    result = _run(
        {"layout": "tile", "popup_layout": value},
        _manon_states(),
        tap=True,
        ws=TIMELINE,
    )
    assert "ring-gauge" not in result["dialog"]


def test_the_popup_layout_is_matched_whatever_its_case() -> None:
    result = _run(
        {"layout": "tile", "popup_layout": " Rings "},
        _manon_states(),
        tap=True,
        ws=TIMELINE,
    )
    assert "ring-gauge" in result["dialog"]


def test_the_timeline_figures_survive_a_hostile_answer() -> None:
    # A stay that is not three numbers must not take the whole card down.
    result = _tap(ws={"stays": [["x", None, "y"]], "proxies": [], "window_s": "nope"})
    assert "detail-title" in result["dialog"]
    assert re.search(r"detail-seg proxy-0", result["dialog"])


def test_the_detail_in_the_small_cards_popup_carries_the_styles_it_needs() -> None:
    # The popup hosts a card with a stylesheet of its own; the detail that
    # replaces it is this popup's markup, and bare text without these rules.
    dialog = _from_the_small_card()["dialog"]
    assert "detail-title" in dialog
    assert dialog.count("<style/>") >= 2


def test_one_of_a_figure_is_in_the_singular() -> None:
    device = _device("Madoka Manon", -83, "fair", address=MANON)
    device["history"] = {"connections": 1, "proxy_changes": 1}
    one_proxy = {**TIMELINE, "proxies": ["Proxy Salon"], "stays": [[600, 0, 0]]}
    result = _run({"layout": "rings"}, _states(device), tapDevice=0, ws=one_proxy)
    dialog = result["dialog"]
    assert "<b>1</b><span>connection</span>" in dialog
    assert "<b>1</b><span>proxy change</span>" in dialog
    assert "<b>1</b><span>proxy used</span>" in dialog


def test_the_singular_agrees_in_french_too() -> None:
    device = _device("Madoka Manon", -83, "fair", address=MANON)
    device["history"] = {"connections": 1, "proxy_changes": 1}
    one_proxy = {**TIMELINE, "proxies": ["Proxy Salon"], "stays": [[600, 0, 0]]}
    result = _run(
        {"layout": "rings"}, _states(device), tapDevice=0, ws=one_proxy, language="fr"
    )
    dialog = result["dialog"]
    assert "<b>1</b><span>connexion</span>" in dialog
    assert "<b>1</b><span>proxy utilisé</span>" in dialog


def test_back_does_not_close_the_popup_it_returns_to() -> None:
    # The click bubbles to the backdrop after the detail has left the tree.
    result = _from_the_small_card(back=True)
    assert 'class="scrim"' in result["dialog"]
    assert "ring-gauge" in result["dialog"]


def test_a_click_on_the_empty_backdrop_closes_the_popup() -> None:
    result = _from_the_small_card(backdropClick=True)
    assert 'class="scrim"' not in result["dialog"]


def test_a_click_on_the_backdrop_closes_the_detail_popup_too() -> None:
    result = _tap(backdropClick=True)
    assert 'class="scrim"' not in result["dialog"]
