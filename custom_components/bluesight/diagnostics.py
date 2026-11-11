"""Diagnostics dump for BlueSight.

For a triage integration this is the single most useful thing to attach to a
bug report: the exact slot allocations, the scanner health, the rolling failure
windows, every incident the detectors currently raise, and what -- if anything
-- each proxy's ESPHome telemetry reported (see :mod:`.diagnostics_data`, which
holds the shaping so it can be tested without Home Assistant).

Addresses are NOT redacted. They are local BLE/adapter MACs and they are the
whole subject of the report — a redacted dump cannot show that the same address
is held on two proxies, which is the failure mode this integration exists to
diagnose.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from . import BlueSightConfigEntry
from .const import DOMAIN, OPTION_DEFAULTS
from .diagnostics_data import telemetry_report
from .incident_policy import dedupe_incidents
from .window import FailureWindow


def _window_state(window: FailureWindow) -> dict[str, Any]:
    """Summarize a rolling window without leaking its internal deques."""
    return {
        "window_s": window.window_s,
        "threshold": window.threshold,
        "counts": {address: window.count(address) for address in window.addresses()},
    }


def _repairs(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Every Repair BlueSight has raised, and whether the user ignored it.

    Read from the issue registry rather than from the objects that raised them:
    the registry is what the user sees, and an ignored saturation Repair is
    still there -- dismissed, not deleted -- which is exactly the fact a report
    about "it never told me" needs.
    """
    registry = ir.async_get(hass)
    return [
        {
            "issue_id": issue.issue_id,
            "translation_key": issue.translation_key,
            "placeholders": dict(issue.translation_placeholders or {}),
            "active": issue.active,
            "ignored": issue.dismissed_version is not None,
        }
        for (domain, _id), issue in sorted(registry.issues.items())
        if domain == DOMAIN
    ]


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: BlueSightConfigEntry
) -> dict[str, Any]:
    """Return the full triage state for this config entry."""
    coordinator = entry.runtime_data
    data = coordinator.data
    return {
        # Defaults first, so a threshold in force but never submitted is
        # reported rather than absent. `entry.options` holds only what the
        # dialog actually saved, and `async_setup_entry` falls back to the
        # same constants -- so this is what the integration is *running at*,
        # which is the only version of the answer a bug report can use.
        "options": {**OPTION_DEFAULTS, **entry.data, **entry.options},
        "availability_degraded": data.availability_degraded,
        "proxies": [asdict(proxy) for proxy in data.proxies],
        "proxies_health": [asdict(health) for health in data.proxies_health],
        "incidents": [
            {**asdict(incident), "kind": incident.kind.value, "key": incident.key}
            for incident in data.incidents
        ],
        "notified_incidents": [
            incident.key for incident in dedupe_incidents(data.incidents)
        ],
        # Whether the ESPHome telemetry is actually being read, and what it
        # said. Every way that reading can fail is silent, so without this
        # section "no telemetry incidents" is indistinguishable from "the
        # reader has never seen anything".
        #
        # The silence baseline is the health + allocation snapshots, exactly
        # what the coordinator feeds ``read_fleet_telemetry`` -- NOT
        # ``tracked_sources``, which is every proxy seen online since setup.
        # A proxy that has since gone offline is absent from telemetry because
        # nobody asked it, and listing it as silent would point the reader at
        # the telemetry chain for a proxy whose real problem is already
        # reported as PROXY_OFFLINE.
        "telemetry": telemetry_report(
            data.telemetry,
            [p.source for p in data.proxies_health if p.online]
            + [p.source for p in data.proxies],
            coordinator.counter_baselines,
        ),
        "storm_window": _window_state(coordinator.storm_window),
        "reboot_window": _window_state(coordinator.reboot_window),
        "tracked_sources": sorted(coordinator.tracked_sources),
        # Which incidents a `bluesight_incident` automation has been told are
        # open: the list to compare against when a `resolved` never came.
        "open_event_keys": (
            coordinator.incident_events.open_keys
            if coordinator.incident_events is not None
            else []
        ),
        "repairs": _repairs(hass),
        # The readings the saturation Repair is judged on, so a Repair that
        # did or did not appear can be checked against its own numbers.
        "saturation": {
            source: {"ratio": round(ratio, 4), "observed_s": round(observed, 1)}
            for source, (ratio, observed) in sorted(
                coordinator.saturation_readings().items()
            )
        },
        "connection_history": {
            "observed_s": round(coordinator.connection_history.observed_s(), 1),
            "devices": {
                address: asdict(reading)
                for address, reading in sorted(
                    coordinator.connection_history.all().items()
                )
            },
        },
    }
