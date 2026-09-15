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


def norms_raw(norms: Any) -> list:
    """The TOML form of a charter's norms: a bare name where there is no definition.

    Edition 3 norms carry their definitions in the charter object, so they are
    written as ``{id, definition}`` tables. A norm without a definition is
    written as the bare string it has always been, so a charter surveyed before
    definitions existed renders and hashes exactly as it did.
    """
    out = []
    for norm in norms:
        if isinstance(norm, dict):
            # Already the TOML form (a charter read back from disk, as the ratification
            # script reads its candidate): pass it through unchanged.
            definition = norm.get("definition") or ""
            out.append({"id": str(norm.get("id")), "definition": definition}
                       if definition else str(norm.get("id")))
            continue
        definition = getattr(norm, "definition", "")
        out.append({"id": str(norm), "definition": definition} if definition else str(norm))
    return out


def roster_hash(manifest: Any) -> str:
    """Bind the survey to the exact assemblies and the model configurations they used."""
    from factorylab.cortex.assembly import SEED_SYSTEM_PROMPT

    model_ids = {a.model_id for a in manifest.assemblies}
    # Edition 3's per-seat keys (cadence_floor, initial_state, system_prompt) are dropped
    # at their defaults for the same reason extra_body is: a roster surveyed before they
    # existed must keep the exact hash its ratification recorded.
    seed_defaults = {"cadence_floor": 1, "initial_state": {}, "system_prompt": None}
    roster = {"assemblies": [{k: v for k, v in asdict(a).items()
                              if seed_defaults.get(k, object()) != v}
                             for a in manifest.assemblies],
              "system_prompt": SEED_SYSTEM_PROMPT,
              # A model tier's optional extra request body (edition 2, W10) is dropped when
              # empty so every roster surveyed before it existed keeps its recorded hash.
              "models": [{k: v for k, v in asdict(m).items() if not (k == "extra_body" and not v)}
                         for m in manifest.models if m.id in model_ids]}
    encoded = json.dumps(roster, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
