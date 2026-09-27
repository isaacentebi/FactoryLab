"""No set's iteration order reaches the ledger, a checkpoint or wire bytes (R16b-9).

A set of strings iterates in an order set by the process's hash seed. Walked into a
list, a dict, a loop that ledgers or a rendered request, that order makes one seed
write a different diary in each process (``test_hashseed_determinism``). This scans the
kernel for every order-dependent walk of a set-valued expression: a ``for`` loop, a list
or dict comprehension, ``list``/``tuple``/``join``/``next(iter(...))`` or ``set.pop()``. A
walk is canonical when it is wrapped in ``sorted(...)``. Each remaining site was reviewed
and is listed with why its order cannot be observed; a new one fails here.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "factorylab"
SET_OPS = (ast.Sub, ast.BitOr, ast.BitAnd, ast.BitXor)
SET_METHODS = ("union", "intersection", "difference", "symmetric_difference")

#: Reviewed walks whose order no reader can observe: ``(file, source text) -> why``.
REVIEWED = {
    ("cortex/request.py", "next(iter(common_tool_call_limits))"):
        "read only when the set has exactly one element",
    ("kernel/artifacts.py", "kinds.pop()"): "the set has exactly one element there",
    ("runtime/vault.py", "next(iter(seats))"): "read only when the set has one element",
    ("runtime/settled.py", "for client_id in dropped:"): "each is popped from a dict",
    ("settlement/receipts.py", "for handle in gone:"): "each is popped from a dict",
    ("runtime/settled.py", "for handle in handles:"):
        "every step is a Counter increment or a pop keyed by the handle: order-free",
    ("runtime/routing.py", "defaults = reward_contracts(tuple(kinds))"):
        "a NOOP's kinds hold one kind; a seat's are its declared tuple",
    ("runtime/routing.py", "return {kind: return_channel(kind,"):
        "a NOOP's kinds hold one kind; a seat's are its declared tuple",
    # Named like a set elsewhere, but a tuple here: the scan matches attributes by name.
    ("cortex/schematics.py", "list(self.venue_tools.coins)"): "VenueTools.coins is a tuple",
    ("runtime/worlds.py", "list(self.exchange.coins)"): "ExchangeSpec.coins is a tuple",
    ("runtime/governance.py", "for kind in prop.accepts:"):
        "a registration proposal's accepts is a tuple",
    ("runtime/governance.py", '"accepts": list(prop.accepts),'):
        "a registration proposal's accepts is a tuple",
    ("cortex/schematics.py", "for owner in {d.actor, self.handle_to_assembly.get(d.handle)}:"):
        "dedups the two owners of one decision; each owner's list gets it once, in "
        "outstanding order, and the lists are read by seat, never iterated as a whole",
    ("runtime/loop.py", "for actor in holding:"):
        "the list delivery_actors() returns; the name is a set only further down",
}


def _set_attrs() -> set[str]:
    """Attribute names the kernel declares as ``set``/``frozenset``."""
    names = set()
    for path in ROOT.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if (isinstance(node, ast.AnnAssign)
                    and ast.unparse(node.annotation).split("[")[0] in ("set", "frozenset")):
                target = node.target
                if isinstance(target, ast.Attribute):
                    names.add(target.attr)
                elif isinstance(target, ast.Name):
                    names.add(target.id)
    return names


def _setish(node, local: set[str], attrs: set[str]) -> bool:
    """Whether ``node`` evaluates to a set: a literal, a set call or method, a set
    operator, a local assigned a set, or an attribute the kernel declares a set."""
    if isinstance(node, (ast.Set, ast.SetComp)):
        return True
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name) and func.id in ("set", "frozenset"):
            return True
        if isinstance(func, ast.Attribute) and func.attr in SET_METHODS:
            return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, SET_OPS):
        return _setish(node.left, local, attrs) or _setish(node.right, local, attrs)
    if isinstance(node, ast.Name):
        return node.id in local
    return isinstance(node, ast.Attribute) and node.attr in attrs


def _walks(path: Path, attrs: set[str]) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text())
    lines = path.read_text().splitlines()
    found = []
    for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef,
                                                             ast.AsyncFunctionDef))]:
        local: set[str] = set()

        def setish(node, local=local) -> bool:
            return _setish(node, local, attrs)

        for node in ast.walk(fn):
            if isinstance(node, ast.Assign) and setish(node.value):
                local.update(t.id for t in node.targets if isinstance(t, ast.Name))
        for node in ast.walk(fn):
            walked = None
            if isinstance(node, (ast.For, ast.AsyncFor)):
                walked = node.iter
            elif isinstance(node, (ast.ListComp, ast.DictComp)):
                walked = next((g.iter for g in node.generators if setish(g.iter)), None)
            elif isinstance(node, ast.Call) and node.args:
                func = node.func
                if isinstance(func, ast.Name) and func.id in ("list", "tuple", "iter"):
                    walked = node.args[0]
                elif isinstance(func, ast.Attribute) and func.attr == "join":
                    walked = node.args[0]
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "pop" and not node.args
                    and setish(node.func.value)):
                found.append((node.lineno, lines[node.lineno - 1].strip()))
            elif walked is not None and setish(walked):
                found.append((node.lineno, lines[node.lineno - 1].strip()))
    return found


def test_no_set_is_walked_in_hash_order_where_the_order_can_be_read():
    attrs = _set_attrs()
    unreviewed = []
    for path in sorted(ROOT.rglob("*.py")):
        rel = str(path.relative_to(ROOT))
        for line, text in _walks(path, attrs):
            if not any(rel == file and snippet in text for file, snippet in REVIEWED):
                unreviewed.append(f"{rel}:{line}: {text}")
    assert not unreviewed, "\n".join(unreviewed)
