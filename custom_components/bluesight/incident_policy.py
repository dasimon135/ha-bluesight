"""Pure notification policy for BlueSight.

No Home Assistant dependency: incident de-duplication/precedence, create /
dismiss reconciliation, the parameters each notification interpolates, and the
notification-id sanitizer all live here so they are fully unit-testable under
plain pytest on any platform. The wording itself comes from the string
catalogue, so notifications are written in Home Assistant's configured
language. The thin HA glue in :mod:`.notify` only calls into these.
"""
from __future__ import annotations

from collections.abc import Callable

from .const import DOMAIN
from .model import Incident, IncidentKind, normalize_address
from .rendering import Catalogue, render


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


def _ghost_proxy(incident: Incident, catalogue: Catalogue) -> str:
    """Name the proxy holding the ghost slot, as the user knows it.

    The rendered detail and this notification sit side by side in the same
    output and must not call the same proxy two different things. The
    detector puts the proxy's *friendly name* in ``detail_params["proxy"]``,
    which is what Home Assistant shows everywhere else, so that wins over the
    MAC in ``sources``.

    An incident with neither is not expected, but a notification that names no
    proxy still beats an IndexError inside the snapshot loop -- and the phrase
    it falls back to comes from the catalogue, so it is not English in a
    French notification.
    """
    return (
        incident.detail_params.get("proxy")
        or (incident.sources[0] if incident.sources else "")
        or render("notify.ghost_slot.proxy_unknown", None, catalogue)
    )


#: Per-kind parameters for the ``notify.<kind>.message`` templates, on top of
#: the ``{address}`` every kind gets. The catalogue holds the wording; this
#: table holds only what has to be computed from the incident.
_NOTIFY_PARAMS: dict[
    IncidentKind, Callable[[Incident, Catalogue], dict[str, str]]
] = {
    IncidentKind.STORM: lambda i, c: {"detail": i.detail},
    IncidentKind.DEADLOCK: lambda i, c: {
        "count": str(len(i.sources)),
        "sources": ", ".join(i.sources),
    },
    IncidentKind.GHOST_SLOT: lambda i, c: {"proxy": _ghost_proxy(i, c)},
    IncidentKind.PROXY_OFFLINE: lambda i, c: {},
    IncidentKind.PROXY_STALLED: lambda i, c: {"detail": i.detail},
    IncidentKind.PROXY_REBOOT_STORM: lambda i, c: {"detail": i.detail},
}


def notification_content(
    incident: Incident, catalogue: Catalogue
) -> tuple[str, str]:
    """Build an actionable ``(title, message)`` pair for one incident.

    The wording lives in the catalogue (``notify.<kind>.title`` /
    ``notify.<kind>.message``) so a notification is written in Home
    Assistant's configured language; this function only computes the
    parameters those templates interpolate.

    Wording follows the "madoka playbook" style: state the observed fault,
    then tell the user the physical action that clears it.

    A kind with no entry in :data:`_NOTIFY_PARAMS` — a detector added without
    its catalogue strings — degrades to the generic ``notify.unknown.*``
    wording rather than raising: this runs inside the coordinator's update
    callback, where an exception would take the snapshot down.
    """
    params: dict[str, str] = {"address": incident.address}
    build = _NOTIFY_PARAMS.get(incident.kind)
    if build is None:
        name = "unknown"
    else:
        name = IncidentKind(incident.kind).value
        params.update(build(incident, catalogue))
    title = render(f"notify.{name}.title", params, catalogue)
    message = render(f"notify.{name}.message", params, catalogue)
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
