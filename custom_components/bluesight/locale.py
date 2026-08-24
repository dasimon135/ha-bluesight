"""Where the string catalogues live on disk, and how they are read.

The catalogues are shipped inside the card's ``www`` tree because the card
fetches them over HTTP; the backend reads the very same files, so the two
halves of the integration can never drift apart. This module owns that layout
and nothing else — :mod:`.rendering` stays pure (no I/O, no filesystem), and
:mod:`.frontend` stays about serving the card.

Reading is blocking, so :func:`read_catalogues` must be called through an
executor, and only once per setup — never per snapshot.

Nothing here raises. A missing directory, an unreadable file or malformed JSON
must degrade to "English, or bare keys", never abort setup: a broken
translation file is not a reason to leave a user without an integration.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

#: Directory holding ``incidents.<lang>.json``, one file per language.
LOCALE_DIR = Path(__file__).parent / "frontend" / "www" / "locale"


def read_catalogues() -> dict[str, dict[str, str]]:
    """Read every catalogue in :data:`LOCALE_DIR`, keyed by base language.

    Blocking (filesystem): call via ``hass.async_add_executor_job``.

    A file that cannot be read or parsed is logged and skipped, so one broken
    translation cannot take the others — English included — down with it.
    """
    out: dict[str, dict[str, str]] = {}
    if not LOCALE_DIR.is_dir():
        _LOGGER.warning(
            "No string catalogues at %s; incident details and notifications "
            "will fall back to their translation keys",
            LOCALE_DIR,
        )
        return out
    for path in sorted(LOCALE_DIR.glob("incidents.*.json")):
        lang = path.name.split(".")[1]
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("catalogue is not a JSON object")
        except (OSError, ValueError):
            _LOGGER.warning("Unreadable catalogue %s", path, exc_info=True)
            continue
        # Only string values are usable as templates; a nested object would
        # blow up in `render` inside the coordinator's snapshot loop.
        out[lang] = {k: v for k, v in data.items() if isinstance(v, str)}
    return out
