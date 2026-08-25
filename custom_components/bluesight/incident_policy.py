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
from .rendering import Catalogue, plural_count, render


def dedupe_incidents(incidents: list[Incident]) -> list[Incident]:
    """Collapse redundant incidents so one physical fault == one alert.

    Three precedence rules apply, on two separate namespaces (a device address
    and a proxy source never collide in practice):

    * Address layer — ``DEADLOCK`` supersedes ``GHOST_SLOT``: a held-but-dead
      slot is exactly what a deadlock looks like, so if both are raised for the
      same address we keep only the deadlock (the actionable root cause).
    * Address layer — ``BOND_LOST`` supersedes ``STORM``: a proxy that holds no
      bond for a device fails to pair with it over and over, which is exactly
      what the storm window counts, so the two are one fault. The bond-lost
      incident is the more specific reading *and* the only one that names the
      remedy — re-pair through that particular proxy — so it is the one kept.
      Deliberately decided on the address alone, never on which proxies the two
      incidents name: a storm merges measured and inferred failures and
      frequently names no proxy at all, so requiring an overlap would stand the
      rule down in the cases it exists for.
    * Proxy layer — ``PROXY_OFFLINE`` supersedes ``PROXY_STALLED``: an offline
      proxy shouldn't also alert as stalled, so for the same proxy source we
      keep only the offline incident.

    ``PROXY_REBOOT_STORM`` is an orthogonal signal (repeated reboots over time)
    and may legitimately co-exist with a structural incident for the same
    source, so it is always kept. So is ``STORM`` for an address with no bond
    evidence — the heuristic keeps its voice wherever the firmware is silent.

    Every rule reads the *input* set, never what an earlier rule left behind,
    so no rule can chain into another and the result does not depend on the
    order incidents arrive in. Nothing supersedes ``DEADLOCK``, ``BOND_LOST``
    or ``PROXY_OFFLINE``, so there is no chain to build in the first place.

    Input order is preserved for every incident that survives.
    """
    deadlock_addrs = {
        normalize_address(i.address)
        for i in incidents
        if i.kind is IncidentKind.DEADLOCK
    }
    bond_lost_addrs = {
        normalize_address(i.address)
        for i in incidents
        if i.kind is IncidentKind.BOND_LOST
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
            incident.kind is IncidentKind.STORM
            and normalize_address(incident.address) in bond_lost_addrs
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


def _measured(incident: Incident, *names: str) -> dict[str, str]:
    """Take the named measurements straight off the incident's parameters.

    Notification wording is built from these, never from ``incident.detail``.
    ``detail`` is rendered by ``build_triage_data``, so interpolating it here
    made a notification's text correct only if that had run first -- an
    ordering nothing enforces, and a caller notifying on a freshly detected
    incident got an empty parenthetical.

    A name the incident does not carry is left out rather than guessed, so it
    renders as a visible placeholder instead of a plausible wrong number.
    """
    return {
        name: incident.detail_params[name]
        for name in names
        if name in incident.detail_params
    }


#: Detail keys that mean a different *fault* under a kind another detector
#: already owns, and so need their own wording.
#:
#: ``detect_idle_slots`` raises ``GHOST_SLOT`` from measured idle time for an
#: address Home Assistant has no device for, while ``detect_ghost_slots``
#: raises it from an unavailable entity. Rendering the second's wording for the
#: first is not merely vaguer: "the device is unavailable" is a statement about
#: an entity that does not exist, and the measured ``{seconds}`` the alert
#: actually rests on is dropped on the floor. The kind stays one kind — it is
#: one class of fault, and the card labels and de-duplicates it as one — so the
#: split lives here, at the wording, and nowhere else.
_NOTIFY_VARIANTS: dict[str, str] = {
    "incident.ghost_slot.idle_detail": "ghost_slot_idle",
}

#: Per-wording parameters for the ``notify.<name>.message`` templates, on top
#: of the ``{address}`` every one of them gets. The catalogue holds the
#: wording; this table holds only what has to be computed from the incident.
#:
#: Keyed by wording name rather than by :class:`IncidentKind` so a variant from
#: :data:`_NOTIFY_VARIANTS` can carry its own parameters: the idle ghost slot
#: needs a measurement the entity-based one has never heard of.
_NOTIFY_PARAMS: dict[str, Callable[[Incident, Catalogue], dict[str, str]]] = {
    "storm": lambda i, c: _measured(i, "count", "seconds"),
    "deadlock": lambda i, c: {
        "count": str(len(i.sources)),
        "sources": ", ".join(i.sources),
    },
    "ghost_slot": lambda i, c: {"proxy": _ghost_proxy(i, c)},
    "ghost_slot_idle": lambda i, c: {
        "proxy": _ghost_proxy(i, c),
        **_measured(i, "seconds"),
    },
    # The proxy is *not* resolved through `_ghost_proxy` here, on purpose. Its
    # last resort is the catalogue's "an unspecified proxy", which reads fine
    # in the ghost-slot message but would turn this one's remedy into nonsense
    # ("re-pair it through an unspecified proxy"). The detector always supplies
    # the name, and if some future caller does not, `_measured` leaves a
    # visible `{proxy}` — plainly broken, rather than plausibly useless.
    "bond_lost": lambda i, c: _measured(i, "count", "proxy"),
    "proxy_offline": lambda i, c: {},
    "proxy_stalled": lambda i, c: _measured(i, "seconds"),
    "proxy_reboot_storm": lambda i, c: _measured(i, "count", "seconds"),
}

#: Grammatical number to render with when a message names a count the incident
#: did not carry. Anything but 1 selects the plural form; see
#: :func:`notification_content` for why a form must be selected at all.
_UNKNOWN_COUNT = 0


def notification_content(
    incident: Incident, catalogue: Catalogue
) -> tuple[str, str]:
    """Build an actionable ``(title, message)`` pair for one incident.

    The wording lives in the catalogue (``notify.<kind>.title`` /
    ``notify.<kind>.message``) so a notification is written in Home
    Assistant's configured language; this function only computes the
    parameters those templates interpolate.

    Wording follows the "madoka playbook" style: state the observed fault
    with its measured numbers, then tell the user the physical action that
    clears it.

    ``incident.detail`` is deliberately never read: it is rendered elsewhere
    (``build_triage_data``), so using it here would make this function's
    output depend on an ordering nothing enforces. Every number a message
    needs comes from ``incident.detail_params``, which the detector fills in
    at detection time.

    Two detectors may raise one kind from different evidence, so the wording is
    chosen from the incident's ``detail_key`` first (see
    :data:`_NOTIFY_VARIANTS`) and from its kind otherwise.

    A wording with no entry in :data:`_NOTIFY_PARAMS` — a detector added
    without its catalogue strings — degrades to the generic ``notify.unknown.*``
    wording rather than raising: this runs inside the coordinator's update
    callback, where an exception would take the snapshot down.
    """
    params: dict[str, str] = {"address": incident.address}
    name = _NOTIFY_VARIANTS.get(incident.detail_key, "") or str(
        getattr(incident.kind, "value", incident.kind)
    )
    build = _NOTIFY_PARAMS.get(name)
    if build is None:
        name = "unknown"
    else:
        params.update(build(incident, catalogue))
    # The plural pivot is the same "{count}" the message interpolates, so
    # the noun always agrees with the number the user reads.
    #
    # A message the incident gave no count for still has to be *rendered*: a
    # plural-split key has no unsuffixed entry to fall back on, so leaving the
    # pivot unset would send `render` to its key-of-last-resort and put a bare
    # "notify.bond_lost.message" where the fault and its remedy belong. Picking
    # a form keeps the prose and leaves the unknown number as a visible
    # "{count}" — the same degradation `_measured` already chooses for every
    # other value an incident does not carry. Wordings with no plural forms are
    # unaffected: their ".other" lookup misses and falls through to the bare key
    # exactly as before.
    count = plural_count(params)
    if count is None:
        count = _UNKNOWN_COUNT
    title = render(f"notify.{name}.title", params, catalogue, count=count)
    message = render(
        f"notify.{name}.message", params, catalogue, count=count
    )
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
