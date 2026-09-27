"""A checkpoint written before a field was deleted still restores (Codex on #152).

``resume.decode`` rebuilds each record type from its fields; a field an older checkpoint
carries that the type no longer has raises, unless ``_RETIRED_FIELDS`` names it.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import subprocess
from pathlib import Path

import pytest

from factorylab.runtime.resume import _RETIRED_FIELDS, _record_types, decode, encode
from factorylab.world.exchange import Fill

ROOT = Path(__file__).resolve().parents[2]
#: The revision this branch started from.
BASE = "5db5ead"


def test_a_checkpointed_fill_with_the_retired_fill_id_restores():
    """Revision 7619732 wrote each fill with ``fill_id``; its checkpoint restores."""
    written = encode(Fill("7", "BTC", True, 1, 2, 0, 3))
    written["fields"]["fill_id"] = "0xabc:1"  # as that revision encoded it
    restored = decode(written)
    assert restored == Fill("7", "BTC", True, 1, 2, 0, 3)


def _fields_at(revision: str, cls: type) -> set[str] | None:
    path = Path(inspect.getsourcefile(cls)).resolve().relative_to(ROOT)
    shown = subprocess.run(["git", "-C", str(ROOT), "show", f"{revision}:{path}"],
                           capture_output=True, text=True)
    if shown.returncode:
        return None
    for node in ast.walk(ast.parse(shown.stdout)):
        if isinstance(node, ast.ClassDef) and node.name == cls.__name__:
            return {n.target.id for n in node.body
                    if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
                    and "ClassVar" not in ast.unparse(n.annotation)}
    return None


def test_every_field_deleted_since_the_base_is_retired():
    """Every dataclass field a checkpoint of the base revision could carry, for every
    record type resume encodes, is still a field or is named retired."""
    known = subprocess.run(["git", "-C", str(ROOT), "cat-file", "-e", f"{BASE}^{{commit}}"],
                           capture_output=True)
    if known.returncode:
        pytest.skip(f"the base revision {BASE} is not in this clone")
    missing = {}
    for name, cls in _record_types().items():
        if not dataclasses.is_dataclass(cls):
            continue
        before = _fields_at(BASE, cls)
        if before is None:
            continue  # a type the base did not have
        now = {field.name for field in dataclasses.fields(cls)}
        gone = before - now - set(_RETIRED_FIELDS.get(name, ()))
        if gone:
            missing[name] = sorted(gone)
    assert not missing, missing
