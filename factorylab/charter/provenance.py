"""The two digests that bind a charter artifact to the population that voted it.

These live in the kernel, not in a script, because the load path enforces them: a
funded manifest must carry the ratified charter's content digest and the roster
hash it was surveyed against. ``scripts/draft_edition1.py`` and
``scripts/ratify_charter.py`` export the same functions, so artifacts written
before this binding existed verify unchanged.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Any

# Hashes recorded beside a charter are provenance, never part of the charter itself.
PROVENANCE_FIELDS = ("ratified_sha256", "roster_sha256")


def charter_digest(raw: dict) -> str:
    """Hash the executable charter rather than its comments or TOML layout."""
    body = json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(body).hexdigest()


def charter_content(raw: Any) -> dict:
    """The charter as voted, with the recorded provenance hashes set aside."""
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if k not in PROVENANCE_FIELDS}


def roster_hash(manifest: Any) -> str:
    """Bind the survey to the exact assemblies and the model configurations they used."""
    from factorylab.cortex.assembly import SEED_SYSTEM_PROMPT

    model_ids = {a.model_id for a in manifest.assemblies}
    roster = {"assemblies": [asdict(a) for a in manifest.assemblies],
              "system_prompt": SEED_SYSTEM_PROMPT,
              # A model tier's optional extra request body (edition 2, W10) is dropped when
              # empty so every roster surveyed before it existed keeps its recorded hash.
              "models": [{k: v for k, v in asdict(m).items() if not (k == "extra_body" and not v)}
                         for m in manifest.models if m.id in model_ids]}
    encoded = json.dumps(roster, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
