"""Pure notification policy for BlueSight.

No Home Assistant dependency: incident de-duplication/precedence, create /
dismiss reconciliation, actionable message wording, and the notification-id
sanitizer all live here so they are fully unit-testable under plain pytest on
any platform. The thin HA glue in :mod:`.notify` only calls into these.
"""
from __future__ import annotations

from .const import DOMAIN
from .model import Incident, IncidentKind, normalize_address


def dedupe_incidents(incidents: list[Incident]) -> list[Incident]:
    """Collapse redundant incidents so one physical fault == one alert.

    Two orthogonal precedence rules apply, on separate namespaces (a device
    address and a proxy source never collide in practice):

    * Address layer — ``DEADLOCK`` supersedes ``GHOST_SLOT``: a held-but-dead
      slot is exactly what a deadlock looks like, so if both are raised for the
      same address we keep only the deadlock (the actionable root cause).
    * Proxy layer — ``PROXY_OFFLINE`` supersedes ``PROXY_STALLED``: an offline
      proxy shouldn't also alert as stalled, so for the same proxy source we
      keep only the offline incident.

    ``STORM`` and ``PROXY_REBOOT_STORM`` are orthogonal signals (repeated
    failures / reboots over time) and may legitimately co-exist with a
    structural incident for the same address/source, so they are always kept.

    Input order is preserved for every incident that survives.
    """
    deadlock_addrs = {
        normalize_address(i.address)
        for i in incidents
        if i.kind is IncidentKind.DEADLOCK
    }
    offline_sources = {
        normalize_address(i.address)
        for i in incidents
        if i.kind is IncidentKind.PROXY_OFFLINE
    }
    kept: list[Incident] = []
    for incident in incidents:
        if (
            incident.kind is IncidentKind.GHOST_SLOT
            and normalize_address(incident.address) in deadlock_addrs
        ):
            continue
        if (
            incident.kind is IncidentKind.PROXY_STALLED
            and normalize_address(incident.address) in offline_sources
        ):
            continue
        kept.append(incident)
    return kept


def reconcile(
    previous_keys: set[str], incidents: list[Incident]
) -> tuple[list[Incident], list[str]]:
    """Diff the current incident set against what was last notified.

    Returns ``(to_create, to_dismiss)`` where ``to_create`` are incidents whose
    key is newly present and ``to_dismiss`` are keys that were active before but
    have since resolved. The caller is responsible for storing the current key
    set afterwards. ``to_dismiss`` is sorted for deterministic behaviour.
    """
    current_keys = {i.key for i in incidents}
    to_create = [i for i in incidents if i.key not in previous_keys]
    to_dismiss = sorted(previous_keys - current_keys)
    return to_create, to_dismiss


def notification_content(incident: Incident) -> tuple[str, str]:
    """Build an actionable ``(title, message)`` pair for one incident.

    Wording follows the "madoka playbook" style: state the observed fault, then
    tell the user the physical action that clears it.
    """
    address = incident.address
    if incident.kind is IncidentKind.STORM:
        title = "BlueSight: pairing storm"
        message = (
            f"Repeated connection failures on {address} ({incident.detail}). "
            "If this is a Daikin/BRC1H-type device, toggle its Bluetooth off "
            "and on, then trigger a reconnect."
        )
    elif incident.kind is IncidentKind.DEADLOCK:
        sources = ", ".join(incident.sources)
        title = "BlueSight: proxy slot deadlock"
        message = (
            f"{address} is holding a connection slot on {len(incident.sources)} "
            f"proxies at once ({sources}) with no working link — this stalls "
            "the connection pool (HA core issue #176516). Power-cycle the "
            "device or restart the affected proxies to release the slots."
        )
    elif incident.kind is IncidentKind.GHOST_SLOT:
        proxy = incident.sources[0] if incident.sources else "a proxy"
        title = "BlueSight: ghost slot"
        message = (
            f"A slot on {proxy} is held for {address} but the device is "
            "unavailable. If it stays stuck, restart that proxy to free the "
            "slot."
        )
    elif incident.kind is IncidentKind.PROXY_OFFLINE:
        title = "BlueSight: proxy offline"
        message = (
            f"Bluetooth proxy {address} is offline — check its power and Wi-Fi."
        )
    elif incident.kind is IncidentKind.PROXY_STALLED:
        title = "BlueSight: proxy stalled"
        message = (
            f"Bluetooth proxy {address} is online but hasn't seen any device "
            f"for a while ({incident.detail}) — power-cycle it."
        )
    elif incident.kind is IncidentKind.PROXY_REBOOT_STORM:
        title = "BlueSight: proxy rebooting"
        message = (
            f"Bluetooth proxy {address} keeps rebooting ({incident.detail}) — "
            "check its power supply / for brownouts."
        )
    else:  # pragma: no cover - defensive; IncidentKind is a closed enum
        title = "BlueSight: incident"
        message = f"An incident was detected on {address}."
    return title, message


def notification_id_for_key(key: str) -> str:
    """Derive a stable, valid ``persistent_notification`` id from an incident key.

    Incident keys contain ``:`` and ``,`` separators; every non-alphanumeric
    character is mapped to ``_`` so the id is a safe slug. The transform is
    deterministic, so the id built at create time matches the one built at
    dismiss time for the same key (dismiss silently no-ops otherwise).
    """
    slug = "".join(c if c.isalnum() else "_" for c in key)
    return f"{DOMAIN}_{slug}"
