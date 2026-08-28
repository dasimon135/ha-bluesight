"""The ESPHome tag pinned in the docs must be the version being shipped.

The firmware and the integration share one wire format and ship from one
repository so the two cannot drift, and the docs tell the user to pin the tag
matching their installed BlueSight version. Both copies of that snippet are
hand-maintained, so both go stale silently on a release that forgets them --
and the reader who pastes one then flashes firmware a tag behind the
integration they just updated.

That is not hypothetical: it happened on v0.6.4, where both snippets still said
v0.6.3 the day the release went out. It is the same class of defect the card's
`CARD_VERSION` guard exists for, and it gets the same treatment.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "custom_components" / "bluesight" / "manifest.json"

#: Every file carrying the `external_components` snippet a user copies.
PINNED = [
    ROOT / "docs" / "esphome-component.md",
    ROOT / "esphome" / "bluesight-example.yaml",
]

_PIN = re.compile(r"ha-bluesight@v(\d+\.\d+\.\d+)")


@pytest.fixture(scope="module")
def version() -> str:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))["version"]


@pytest.mark.parametrize("path", PINNED, ids=[p.name for p in PINNED])
def test_the_pinned_tag_is_the_shipped_version(path, version):
    """Every pinned tag in a copyable snippet names the release being shipped."""
    found = set(_PIN.findall(path.read_text(encoding="utf-8")))

    assert found, f"{path.name} carries no ha-bluesight@vX.Y.Z pin any more"
    assert found == {version}, (
        f"{path.name} pins {sorted(found)}, manifest says {version}"
    )


def test_every_pinned_file_is_actually_checked():
    """A third copy of the snippet added later must be added here too.

    The guard is only worth as much as its file list, and the failure mode is
    silent: a snippet nobody checks goes stale exactly like the two that did.
    """
    carrying = {
        path.relative_to(ROOT).as_posix()
        for path in ROOT.rglob("*")
        if path.is_file()
        and path.suffix in {".md", ".yaml", ".yml"}
        and ".git" not in path.parts
        # Design and implementation notes quote the snippet as it stood when
        # they were written; they are a record, not something to copy.
        and "plans" not in path.parts
        and _PIN.search(path.read_text(encoding="utf-8", errors="ignore"))
    }

    assert carrying == {p.relative_to(ROOT).as_posix() for p in PINNED}
