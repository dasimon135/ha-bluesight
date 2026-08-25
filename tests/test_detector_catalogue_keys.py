"""The joint between the detectors and the shipped string catalogues.

Every other test covers one side of it. The per-detector tests pin the key and
the parameters each detector emits; test_catalogue_files pins that the language
files agree with each other; test_rendering pins the renderer. Nothing pins that
a detector's key is actually *in* the catalogue, or that its parameter names are
the ones the template asks for.

That gap is silent by construction: :func:`render` never raises. A key that does
not exist renders as the bare key, and a parameter renamed on one side only
renders a literal ``{seconds}`` to the user. Both reach production looking like
working code, which is exactly why this is checked mechanically.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from custom_components.bluesight.detector import (
    detect_deadlocks,
    detect_ghost_slots,
    detect_offline_proxies,
    detect_reboot_storm,
    detect_stalled_proxies,
    detect_storm,
)
from custom_components.bluesight.model import Incident, ProxyHealth, ProxySlots
from custom_components.bluesight.window import FailureWindow

LOCALE_DIR = (
    Path(__file__).resolve().parent.parent
    / "custom_components" / "bluesight" / "frontend" / "www" / "locale"
)

#: Mirrors ``rendering._PLACEHOLDER`` -- see test_catalogue_files for why the
#: duplication is deliberate.
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _catalogue(language: str) -> dict[str, str]:
    path = LOCALE_DIR / f"incidents.{language}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _forms(catalogue: dict[str, str], key: str) -> list[str]:
    """Every template a detector's key can resolve to.

    A counted string is stored split as ``<key>.one`` / ``<key>.other`` rather
    than under the bare key, so a detector's key legitimately has no direct
    entry. Both forms are returned and each is checked, because a placeholder
    dropped from only one of them is invisible until the count reaches it.
    """
    if key in catalogue:
        return [catalogue[key]]
    return [
        catalogue[f"{key}.{suffix}"]
        for suffix in ("one", "other")
        if f"{key}.{suffix}" in catalogue
    ]


def _window(window_s: float, threshold: int, address: str, hits: int) -> FailureWindow:
    window = FailureWindow(window_s=window_s, threshold=threshold, clock=lambda: 0.0)
    for _ in range(hits):
        window.record(address)
    return window


def _every_detector_incident() -> list[Incident]:
    """One incident from each of the six detectors.

    Kept as a single list rather than a fixture per detector: the point is
    coverage of *all* of them, and a detector added later must be added here or
    ``test_every_incident_kind_is_covered`` fails.
    """
    return [
        *detect_deadlocks([
            ProxySlots("AA", "P1", 2, 1, ["11:22"]),
            ProxySlots("BB", "P2", 2, 1, ["11:22"]),
        ]),
        *detect_ghost_slots(
            [ProxySlots("AA", "Salon", 2, 1, ["11:22"])], {"11:22": False}
        ),
        *detect_offline_proxies([], {"BB"}, {"BB": 999.0}, grace_s=90.0),
        *detect_stalled_proxies(
            [ProxyHealth("AA", "p-AA", True, True, 200.0, 0)], threshold_s=180.0
        ),
        detect_storm("11:22", _window(300, 5, "11:22", 5)),
        detect_reboot_storm("PX", _window(600, 3, "PX", 3)),
    ]


INCIDENTS = _every_detector_incident()


def _ids() -> list[str]:
    return [inc.kind.value for inc in INCIDENTS]


#: Kinds that exist in the model but that no detector raises yet.
#:
#: ``BOND_LOST`` landed with the ESPHome telemetry data model, ahead of the
#: detector that will raise it from real SMP evidence. Exempting it is not a
#: way to skip the catalogue work: the assertion below is an equality, so the
#: moment a detector does emit the kind this test fails until the entry is
#: removed -- which is exactly when its strings must be written.
PENDING_DETECTOR = {"bond_lost"}


def test_every_incident_kind_is_covered():
    """A new detector must be wired into this module, not silently skipped."""
    from custom_components.bluesight.model import IncidentKind

    expected = {kind for kind in IncidentKind if kind.value not in PENDING_DETECTOR}
    assert {inc.kind for inc in INCIDENTS} == expected


def test_the_pending_list_names_real_kinds():
    """A typo or a renamed kind would silently exempt nothing -- or, worse,
    leave a real kind permanently unguarded under a name nobody greps for."""
    from custom_components.bluesight.model import IncidentKind

    unknown = PENDING_DETECTOR - {kind.value for kind in IncidentKind}
    assert not unknown, f"pending kinds that do not exist: {sorted(unknown)}"


@pytest.mark.parametrize("incident", INCIDENTS, ids=_ids())
def test_detector_emits_a_key_that_exists(incident):
    assert incident.detail_key, f"{incident.kind.value} emits no key"
    assert _forms(_catalogue("en"), incident.detail_key), (
        f"{incident.detail_key} resolves to nothing in the English catalogue"
    )


@pytest.mark.parametrize("language", ["en", "fr"])
@pytest.mark.parametrize("incident", INCIDENTS, ids=_ids())
def test_parameters_match_the_template_placeholders(incident, language):
    """Exactly the names the template asks for -- no more, no fewer.

    A missing name renders a literal ``{proxy}``; a surplus one is a parameter
    the user never sees, usually the residue of a rename on one side only.
    """
    for template in _forms(_catalogue(language), incident.detail_key):
        assert set(PLACEHOLDER.findall(template)) == set(incident.detail_params)


@pytest.mark.parametrize("incident", INCIDENTS, ids=_ids())
def test_parameter_values_are_strings(incident):
    """The catalogue substitutes text; an int here would render fine in Python
    and be wrong the moment a value needs locale-aware formatting."""
    non_strings = {
        name: value
        for name, value in incident.detail_params.items()
        if not isinstance(value, str)
    }
    assert not non_strings
