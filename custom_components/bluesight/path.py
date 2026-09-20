"""Which proxy a connection went through, and which one heard the device best.

Pure: no Home Assistant, no habluetooth, no clock. The signal readings arrive
through an injected callable, as the device names do in :mod:`.adapter`.

Home Assistant picks the route to a device itself -- by signal, minus penalties
for a proxy that is busy connecting, that has failed before or that is down to
its last slot, and with a proxy that has **no** free slot ruled out entirely.
BlueSight showed who held each slot and never why that proxy, so a device
connected through the far side of the house looked exactly like one connected
through the proxy beside it.

**The reading is taken when the slot appears, or not at all.** A connected
device stops advertising, and a remote scanner forgets an address a few minutes
after its last advertisement, so by the time anyone opens the dashboard there is
no signal left to read on any proxy. :class:`PathTracker` reads once and keeps
the answer for as long as the slot is held.

**Published, not judged**, for the reason :mod:`.saturation` gives: what counts
as a bad route is not knowable from one fleet. No incident is raised from this.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .model import ProxySlots, normalize_address

_LOGGER = logging.getLogger(__name__)

#: How much stronger another proxy must have heard the device to be worth
#: naming. Not a threshold of ours: it mirrors habluetooth's
#: ``ADV_RSSI_SWITCH_THRESHOLD``, the margin the Bluetooth stack itself demands
#: before it treats one scanner as meaningfully better than another -- signal
#: wanders by several dB between two advertisements, and below this the stack
#: does not act on the difference either. Mirrored by value rather than
#: imported, because this module is pure; if the stack moves its figure this
#: one only decides what is worth a line on a card.
CLEARLY_STRONGER_DB = 16


@dataclass(frozen=True, slots=True)
class ConnectionPath:
    """One connection's route, as read when its slot appeared."""

    #: What the proxy holding the slot heard the device at, in dBm.
    rssi: int
    #: The proxy that heard it clearly better, if one did. None otherwise --
    #: which is the ordinary case, and means the route needs no comment.
    stronger_source: str | None = None
    stronger_rssi: int | None = None
    #: That proxy had no free slot at the time. Home Assistant rules such a
    #: proxy out whatever it hears, so this turns "an odd choice" into "a full
    #: proxy", which is a different problem with a different remedy.
    stronger_was_full: bool = False


def judge_path(
    chosen: str, readings: dict[str, int], free_slots: dict[str, int]
) -> ConnectionPath | None:
    """The route through ``chosen``, given what every proxy heard.

    ``readings`` maps a proxy source to the RSSI of its last advertisement from
    the device; ``free_slots`` maps a source to its free slots in the same
    snapshot. None when ``chosen`` itself has no reading: another proxy cannot
    be "stronger" than a number nobody has.
    """
    rssi = readings.get(chosen)
    if rssi is None:
        return None
    others = {src: value for src, value in readings.items() if src != chosen}
    if not others:
        return ConnectionPath(rssi=rssi)
    # Strongest first; the source breaks a tie so the answer does not depend on
    # the order scanners were polled in.
    stronger_source, stronger_rssi = max(
        others.items(), key=lambda item: (item[1], item[0])
    )
    if stronger_rssi - rssi < CLEARLY_STRONGER_DB:
        return ConnectionPath(rssi=rssi)
    return ConnectionPath(
        rssi=rssi,
        stronger_source=stronger_source,
        stronger_rssi=stronger_rssi,
        stronger_was_full=free_slots.get(stronger_source, 1) == 0,
    )


def path_attributes(
    path: ConnectionPath, proxy_names: dict[str, str]
) -> dict[str, object]:
    """``path`` as it is published under each entry of ``allocated_devices``.

    Plain values, because it goes through the recorder and the websocket. The
    stronger proxy is named as the user names it -- the same map the incident
    text is rendered from -- and falls back to its address, never to a blank:
    this field says *where*.
    """
    source = path.stronger_source
    return {
        "rssi": path.rssi,
        "stronger_source": source,
        "stronger_name": proxy_names.get(source, source) if source else None,
        "stronger_rssi": path.stronger_rssi,
        "stronger_was_full": path.stronger_was_full,
    }


class PathTracker:
    """Read each connection's route once, when its slot first appears.

    Stateful across snapshots, but pure. Keyed by ``(proxy, address)``: the same
    device reconnecting through another proxy is a new route and is read again.
    """

    def __init__(self) -> None:
        # None is a slot that was seen and had nothing to read -- remembered so
        # it is not asked about again. After a restart every held slot is
        # already connected and already silent, and would answer nothing on
        # every snapshot for as long as it stayed connected.
        self._paths: dict[tuple[str, str], ConnectionPath | None] = {}

    def update(
        self,
        proxies: Iterable[ProxySlots],
        read_rssi: Callable[[str], dict[str, int]],
    ) -> dict[tuple[str, str], ConnectionPath]:
        """Advance one snapshot; return the known route of every held slot."""
        proxies = list(proxies)
        free_slots = {
            normalize_address(p.source): p.free for p in proxies if p.is_connectable
        }
        held = {
            (normalize_address(p.source), normalize_address(address))
            for p in proxies
            for address in p.allocated
        }
        for key in self._paths.keys() - held:
            del self._paths[key]
        for source, address in sorted(held - self._paths.keys()):
            self._paths[(source, address)] = self._read(
                source, address, read_rssi, free_slots
            )
        return {key: path for key, path in self._paths.items() if path is not None}

    @staticmethod
    def _read(
        source: str,
        address: str,
        read_rssi: Callable[[str], dict[str, int]],
        free_slots: dict[str, int],
    ) -> ConnectionPath | None:
        try:
            readings = {
                normalize_address(src): int(value)
                for src, value in read_rssi(address).items()
            }
        # Broad on purpose, as in `adapter._resolve_devices`: this runs inside
        # the snapshot every entity is built from, and a signal reading is not
        # worth blanking the slot counts and the incidents for.
        except Exception:
            _LOGGER.debug("Could not read the signal for %s", address, exc_info=True)
            return None
        return judge_path(source, readings, free_slots)
