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
# can hold a perfectly healthy connection in silence for a long time, and that
# -- not a stuck slot -- is the false positive this threshold exists to
# exclude.
#
# 30 minutes, and not the 300s first written here, because 300 was
# falsified by measurement rather than by argument. A Daikin Madoka BRC1H
# thermostat -- an ordinary, working device on ordinary hardware -- reported
# `slot_idle_seconds` of 430.7 on a live proxy while functioning normally. It
# escaped being flagged for one reason only: it is in the device registry, so
# `detect_idle_slots` stands down for it. The same device *absent* from the
# registry -- which is the entire population this detector judges -- would have
# raised a GHOST_SLOT at 300s while perfectly healthy.
#
# The asymmetry settles which direction to err in. A genuinely stuck slot is
# stuck indefinitely, so reporting it 25 minutes later costs nothing
# operationally; a false positive on a first-day install teaches the user to
# ignore a diagnostic integration, and that is not recovered. 1800 is still not
# universal -- a lighting mesh nobody touches overnight is silent for hours --
# which is precisely why this is a tunable. The default's only job is to avoid
# crying wolf on a typical install.
DEFAULT_IDLE_SLOT_THRESHOLD_S = 1800.0

# Measured SMP refusals on one proxy, over the storm window, before BlueSight
# names that proxy as holding no pairing key for a device.
#
# Deliberately below `DEFAULT_STORM_THRESHOLD`. A missing bond is deterministic
# -- it refuses every attempt, not intermittently -- so three refusals inside
# the window is a pattern rather than noise, while a single one is not a
# diagnosis worth sending someone to re-pair a device over. BOND_LOST already
# supersedes STORM in `incident_policy`, so firing first is the entire benefit:
# the user gets the exact remedy ("re-pair through *this* proxy") instead of
# the generic one. At the storm threshold the diagnosis could never be anything
# but a requalification of a storm already raised.
#
# One refusal in the window is invisible by design, not by oversight: the
# firmware counter it is measured from is monotonic since boot, and reading it
# directly is what made a device working normally on one proxy stay flagged
# forever over a refusal by another, long ago.
DEFAULT_BOND_THRESHOLD = 3

#: Every tunable and the value it runs at when the user has never saved one.
#:
#: A config entry's ``options`` holds only what was actually submitted, so a
#: threshold that has never been through the dialog is *absent* from it while
#: being very much in force -- ``async_setup_entry`` falls back to the constant.
#: The diagnostics dump reported the persisted map verbatim and was therefore
#: silent about exactly those thresholds, which is worst on the newest option
#: and best on the ones nobody is asking about.
#:
#: Keyed by the option name rather than derived from the schema on purpose: the
#: dump is the artifact downloaded when things are already broken, and running
#: it through voluptuous would let one out-of-range persisted value raise where
#: a report is being collected. ``test_options_schema`` pins this table against
#: the schema instead, so the two cannot drift without a test saying so.
OPTION_DEFAULTS = {
    "storm_window_s": DEFAULT_STORM_WINDOW_S,
    "storm_threshold": DEFAULT_STORM_THRESHOLD,
    "poll_interval_s": DEFAULT_POLL_INTERVAL_S,
    "stalled_threshold_s": DEFAULT_STALLED_THRESHOLD_S,
    "reboot_window_s": DEFAULT_REBOOT_WINDOW_S,
    "reboot_threshold": DEFAULT_REBOOT_THRESHOLD,
    "offline_grace_s": DEFAULT_OFFLINE_GRACE_S,
    "idle_threshold_s": DEFAULT_IDLE_SLOT_THRESHOLD_S,
    "bond_threshold": DEFAULT_BOND_THRESHOLD,
}

SERVICE_FORGET_PROXY = "forget_proxy"
ATTR_SOURCE = "source"
