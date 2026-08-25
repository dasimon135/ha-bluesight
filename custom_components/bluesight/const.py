"""Constants for the BlueSight integration."""
from __future__ import annotations

DOMAIN = "bluesight"
DEFAULT_STORM_WINDOW_S = 300.0
DEFAULT_STORM_THRESHOLD = 5
DEFAULT_POLL_INTERVAL_S = 30
DEFAULT_STALLED_THRESHOLD_S = 180.0
DEFAULT_REBOOT_WINDOW_S = 600.0
DEFAULT_REBOOT_THRESHOLD = 3
# How long a proxy may be missing from the scanner registry before it is
# reported offline. An ESPHome proxy drops off the bus for ~20-30s on every OTA
# update and on every reload of its config entry; without a grace period each of
# those raises and then clears a "proxy offline" alert.
DEFAULT_OFFLINE_GRACE_S = 90.0
# How long a GATT connection may sit without traffic before the firmware's
# idle reading is read as a stuck slot. It has to sit above the slowest
# legitimate quiet period on the network: a device that only notifies on change
# can hold a perfectly healthy connection in silence for minutes, and that --
# not a stuck slot -- is the false positive this threshold exists to exclude.
DEFAULT_IDLE_SLOT_THRESHOLD_S = 300.0

SERVICE_FORGET_PROXY = "forget_proxy"
ATTR_SOURCE = "source"
