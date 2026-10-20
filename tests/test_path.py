"""Which proxy a connection went through, and which one heard the device best.

BlueSight shows who holds each slot. It never said *why that proxy*, and Home
Assistant picks the route itself: by signal, minus penalties for a proxy that
is busy connecting, that has failed before, or that is down to its last slot --
and a proxy with no free slot is out of the running whatever it hears. So a
device can sit on the far side of the house from the proxy it is connected
through, and nothing anywhere says so.

The reading has to be taken **when the slot appears**. A connected device stops
advertising, and a remote scanner forgets an address a few minutes after its
last advertisement, so by the time anyone looks there is no signal left to
read on any proxy. `PathTracker` takes it once and keeps it for as long as the
slot is held.

Published, not judged -- like saturation. The one number here that is not a
measurement is `CLEARLY_STRONGER_DB`, and it is not ours: it is the margin
habluetooth itself requires before it calls one scanner meaningfully better
than another.
"""
from __future__ import annotations

from custom_components.bluesight.model import ProxySlots
from custom_components.bluesight.path import (
    CLEARLY_STRONGER_DB,
    ConnectionPath,
    PathTracker,
    judge_path,
    path_attributes,
)

VIA = "D0:CF:13:0F:05:5A"
NEAR = "D0:CF:13:0E:C9:2A"
ADDR = "1C:54:9E:8E:1D:2C"


# --- one reading, judged ----------------------------------------------------


def test_the_chosen_proxys_signal_is_recorded():
    path = judge_path(VIA, {VIA: -62}, {VIA: 2})
    assert path == ConnectionPath(rssi=-62)


def test_a_clearly_stronger_proxy_is_named_with_what_it_heard():
    path = judge_path(VIA, {VIA: -80, NEAR: -80 + CLEARLY_STRONGER_DB}, {NEAR: 2})
    assert (path.rssi, path.stronger_source, path.stronger_rssi) == (-80, NEAR, -64)
    assert path.stronger_was_full is False


def test_a_proxy_that_is_not_clearly_stronger_is_not_worth_a_line():
    """Signal wanders by several dB between two advertisements. Naming every
    proxy that happened to read one dB better would be noise on every row."""
    path = judge_path(VIA, {VIA: -80, NEAR: -80 + CLEARLY_STRONGER_DB - 1}, {NEAR: 2})
    assert path.stronger_source is None


def test_a_stronger_proxy_that_was_full_explains_the_choice():
    """Home Assistant rules a proxy with no free slot out entirely, so this is
    the most useful thing the reading can say: not a bad choice, a full proxy."""
    path = judge_path(VIA, {VIA: -80, NEAR: -55}, {NEAR: 0})
    assert path.stronger_was_full is True


def test_the_strongest_of_several_is_the_one_named():
    path = judge_path(VIA, {VIA: -85, NEAR: -60, "CC": -50}, {})
    assert (path.stronger_source, path.stronger_rssi) == ("CC", -50)


def test_no_reading_for_the_chosen_proxy_is_no_path_at_all():
    """Nothing to compare against: saying another proxy was "stronger" than an
    unknown would be a claim about a number nobody has."""
    assert judge_path(VIA, {NEAR: -55}, {}) is None
    assert judge_path(VIA, {}, {}) is None


# --- taken once, kept while the slot is held --------------------------------


def _proxy(source, free, *allocated):
    return ProxySlots(source, source, 3, free, list(allocated))


def test_the_reading_is_taken_when_the_slot_appears_and_kept():
    asked = []

    def read(address):
        asked.append(address)
        return {VIA: -70}

    tracker = PathTracker()
    first = tracker.update([_proxy(VIA, 2, ADDR)], read)
    second = tracker.update([_proxy(VIA, 2, ADDR)], lambda a: {})
    assert first == second == {(VIA, ADDR): ConnectionPath(rssi=-70)}
    # Once: by the second snapshot the device has stopped advertising.
    assert asked == [ADDR]


def test_a_slot_with_no_signal_to_read_is_not_asked_about_again():
    """After a restart every held slot is already connected and already
    silent. Asking again each snapshot would only ever get the same nothing."""
    asked = []

    def read(address):
        asked.append(address)
        return {}

    tracker = PathTracker()
    tracker.update([_proxy(VIA, 2, ADDR)], read)
    assert tracker.update([_proxy(VIA, 2, ADDR)], read) == {}
    assert asked == [ADDR]


def test_a_released_slot_forgets_its_path_and_a_new_one_is_read_afresh():
    tracker = PathTracker()
    tracker.update([_proxy(VIA, 2, ADDR)], lambda a: {VIA: -70})
    tracker.update([_proxy(VIA, 3)], lambda a: {})
    again = tracker.update([_proxy(VIA, 2, ADDR)], lambda a: {VIA: -52})
    assert again == {(VIA, ADDR): ConnectionPath(rssi=-52)}


def test_whether_the_stronger_proxy_was_full_comes_from_the_same_snapshot():
    tracker = PathTracker()
    paths = tracker.update(
        [_proxy(VIA, 2, ADDR), _proxy(NEAR, 0, "AA", "BB", "CC")],
        lambda a: {VIA: -80, NEAR: -55},
    )
    assert paths[(VIA, ADDR)].stronger_was_full is True


def test_addresses_are_canonicalised_on_both_sides():
    tracker = PathTracker()
    paths = tracker.update(
        [_proxy(VIA, 2, ADDR.lower())], lambda a: {VIA.lower(): -70}
    )
    assert paths == {(VIA, ADDR): ConnectionPath(rssi=-70)}


def test_a_reader_that_raises_costs_one_path_and_nothing_else():
    def read(address):
        raise RuntimeError("habluetooth moved")

    assert PathTracker().update([_proxy(VIA, 2, ADDR)], read) == {}


# --- what is published ------------------------------------------------------


def test_a_route_that_needs_no_comment_publishes_its_signal_alone():
    assert path_attributes(ConnectionPath(rssi=-62), {}) == {
        "rssi": -62,
        "stronger_source": None,
        "stronger_name": None,
        "stronger_rssi": None,
        "stronger_was_full": False,
    }


def test_the_stronger_proxy_is_named_as_the_user_names_it():
    path = ConnectionPath(-80, NEAR, -55, True)
    published = path_attributes(path, {NEAR: "Proxy Salon"})
    assert published["stronger_name"] == "Proxy Salon"
    assert published["stronger_was_full"] is True
    # Falls back to the address, never to a blank: it says *where*.
    assert path_attributes(path, {})["stronger_name"] == NEAR


def test_an_occupied_slot_carries_its_path_and_an_unread_one_says_so():
    """`path` is always present, None when nothing was read: an attribute that
    comes and goes is a second shape for every template to handle."""
    proxy = ProxySlots(
        VIA, "via", 3, 1, [ADDR, "AA:BB"],
        paths={ADDR: path_attributes(ConnectionPath(rssi=-70), {})},
    )
    first, second = proxy.allocated_devices
    assert first["path"]["rssi"] == -70
    assert second["path"] is None

