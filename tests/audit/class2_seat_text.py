"""Every kernel string that can reach a seat, found in the code (phase-2 design B1).

AGENTS.md rules 1 and 3 read every string a seat can see: request text, tool
descriptions, schematics (rendered by ``class2_corpus``), and the text a seat reads
back as a tool result, a refusal reason or an error message. That last class is not
reached by a short run, so it is found here, statically, in the source:

* **Delivery.** Text reaches a seat through a *seat position*: the reason argument of a
  refusal sink (``SINKS``, closed under wrappers that pass a parameter on to one), or
  an ``error`` / ``reason`` value of a dict built on a seat-bound path (a ledger row,
  ``….ledger.append({...})``, is not read by a seat).
* **Seat-bound paths.** The tool, connector, web-search and child-request entry points
  (``ROOTS``), and every try-block whose handler puts the caught exception's text in a
  seat position (an *exception funnel*: ``_run_tool``'s ``except Exception``, a refused
  registration's reason): whatever those run, transitively, can raise a message a seat
  reads.
* **Sources.** On those paths: every ``raise``'s message and every error/reason value;
  anywhere: every sink's reason argument; and every string a *reason function* returns
  (a function whose result is placed in a seat position).

Calls are resolved as far as the source says: ``self.f`` within the runtime's mixins to
every mixin's ``f``, ``self.f`` elsewhere to the class's own ``f``, a bare or imported
name to its definition, a module attribute to that module's function, and any other
receiver by name across the package (which over-reaches rather than under-reaches).
Container and builtin method names (``STOP``) are not followed. A source is
``file::qualname``; its texts are rendered with each interpolation shown as ``{…}``.
"""

from __future__ import annotations

import ast
import functools
import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "factorylab"

#: Functions that deliver their reason argument to a seat, by name, and the index of
#: that argument among the call's positional arguments (``self`` not counted).
SINKS: dict[str, int] = {
    "_refusal_to_owner": 2,   # compute.py: into the decision owner's inbox
    "_refuse_request": 2,     # composition.py: a child request's tool result
    "_refuse_order": 1,       # venue.py: an order refusal, result and inbox
    "_connector_refused": 1,  # compute.py: a connector's tool result
}
#: Where seat-bound work starts: every tool call, connector fetch, web search and
#: child request a seat makes runs under one of these.
ROOTS = frozenset({
    "factorylab/runtime/compute.py::ComputeMixin._run_tool",
    "factorylab/runtime/compute.py::ComputeMixin._fetch_connector",
    "factorylab/runtime/composition.py::CompositionMixin._run_tool",
    "factorylab/runtime/composition.py::CompositionMixin._invoke_child",
    "factorylab/runtime/uptake.py::UptakeMixin._run_tool",
    "factorylab/runtime/websearch.py::run",
})
#: The keys of a dict whose value a seat reads as a reason.
SEAT_KEYS = frozenset({"error", "reason"})
#: Receivers that name the runtime itself (``rt.f`` in a module the runtime calls).
RUNTIME_NAMES = frozenset({"self", "rt", "runtime"})
#: Method names never followed: containers, strings, builtins and the ledger's append.
STOP = frozenset({
    "append", "extend", "get", "pop", "items", "keys", "values", "update", "add", "remove",
    "discard", "setdefault", "join", "split", "strip", "rstrip", "lstrip", "format",
    "replace", "startswith", "endswith", "lower", "upper", "casefold", "encode", "decode",
    "read", "write", "copy", "sort", "index", "count", "insert", "clear", "isoformat",
    "hexdigest", "digest", "__init__", "__post_init__", "__call__", "len", "str", "int",
    "float", "bool", "dict", "list", "tuple", "set", "frozenset", "min", "max", "sum",
    "sorted", "isinstance", "getattr", "hasattr", "print", "range", "enumerate", "zip",
    "any", "all", "type", "repr", "round", "abs", "open", "iter", "next", "super",
})


@dataclass(frozen=True)
class SeatText:
    """One string a seat can read, by its kind and the function that writes it."""

    kind: str      # exception | result | sink | returned
    source: str    # file::qualname
    text: str

    @property
    def leaf(self) -> str:
        """``seat_text/<kind>/<source>/<digest>``, the source's slashes and ``.py``
        folded into ``:`` so no path segment reads as a charter or norm section."""
        digest = hashlib.sha256(self.text.encode()).hexdigest()[:8]
        source = self.source.replace(".py::", "::").replace("/", ":")
        return f"seat_text/{self.kind}/{source}/{digest}"


@dataclass
class _Fn:
    key: str
    node: ast.AST
    module: str
    cls: str | None
    params: list[str] = field(default_factory=list)


@dataclass
class _Module:
    #: A local name bound by ``from factorylab.… import name`` -> (module file, name).
    names: dict[str, tuple[str, str]] = field(default_factory=dict)
    #: A local name bound to a factorylab module -> its file.
    modules: dict[str, str] = field(default_factory=dict)
    consts: dict[str, str] = field(default_factory=dict)


def _module_file(dotted: str) -> str | None:
    base = ROOT / Path(*dotted.split("."))
    if base.with_suffix(".py").exists():
        return base.with_suffix(".py").relative_to(ROOT).as_posix()
    if (base / "__init__.py").exists():
        return (base / "__init__.py").relative_to(ROOT).as_posix()
    return None


def _render(node: ast.AST, consts: dict[str, str]) -> str | None:
    """A string expression as a seat reads it, interpolations as ``{…}``; None when it
    carries no literal text."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        text = "".join(str(v.value) if isinstance(v, ast.Constant) else "{…}"
                       for v in node.values)
        return text if text.replace("{…}", "").strip(" :,.;()") else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add | ast.Mod):
        left, right = _render(node.left, consts), _render(node.right, consts)
        if left is None and right is None:
            return None
        return (left or "{…}") + ("" if isinstance(node.op, ast.Mod) else (right or "{…}"))
    if isinstance(node, ast.Name):
        return consts.get(node.id)
    if isinstance(node, ast.Attribute):
        return consts.get(f".{node.attr}")
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
            and node.func.attr == "format":
        return _render(node.func.value, consts)
    if isinstance(node, ast.IfExp):
        return _render(node.body, consts) or _render(node.orelse, consts)
    return None


def _own_nodes(fn: ast.AST):
    """The nodes of a function's own body, nested function bodies excluded."""
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            stack.extend(ast.iter_child_nodes(node))


def _call_name(call: ast.Call) -> str | None:
    f = call.func
    return f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)


def _is_ledger_row(call: ast.Call) -> bool:
    """``….ledger.append({...})`` and its private spellings: a ledger row, never read by
    a seat as a result."""
    f = call.func
    return isinstance(f, ast.Attribute) and f.attr == "append" and "ledger" in ast.unparse(f.value)


class Scan:
    """The seat-text scan over the package: its reachable functions and its sources."""

    def __init__(self) -> None:
        self.fns: dict[str, _Fn] = {}
        self.by_name: dict[str, list[str]] = defaultdict(list)
        self.modules: dict[str, _Module] = {}
        self.classes: dict[str, str] = {}  # class name -> module (first definition)
        self._index()
        self.children: dict[str, list[str]] = defaultdict(list)
        for key in self.fns:
            head, _, _ = key.rpartition(".")
            if "::" in head:
                self.children[head].append(key)
        # An attribute (``self.WRITE_REFUSAL``, ``module.NAME``) names a module- or
        # class-level constant anywhere; a bare name, its own module's or an import's.
        self.consts = {f".{k}": v for m in self.modules.values() for k, v in m.consts.items()}
        self.runtime_classes = {c for c, m in self.classes.items()
                                if m.startswith("factorylab/runtime/")
                                and (c.endswith("Mixin") or c == "Runtime")}
        self.sinks = dict(SINKS)
        self._close_sinks()
        self.reachable = self._reach()
        self.reason_fns: set[str] = set()
        self.texts = self._collect()

    # --- the index -----------------------------------------------------------------------

    def _index(self) -> None:
        for path in sorted(PACKAGE.rglob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            tree = ast.parse(path.read_text())
            module = self.modules.setdefault(rel, _Module())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module \
                        and node.module.startswith("factorylab"):
                    for alias in node.names:
                        sub = _module_file(f"{node.module}.{alias.name}")
                        if sub is not None:
                            module.modules[alias.asname or alias.name] = sub
                        target = _module_file(node.module)
                        if target is not None:
                            module.names[alias.asname or alias.name] = (target, alias.name)
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.startswith("factorylab"):
                            target = _module_file(alias.name)
                            if target is not None:
                                module.modules[alias.asname or alias.name] = target
            # String constants at module and class level only: a local variable is not
            # a constant another function's name can mean.
            bodies = [tree.body] + [c.body for c in ast.walk(tree) if isinstance(c, ast.ClassDef)]
            for body in bodies:
                for stmt in body:
                    if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Constant) \
                            and isinstance(stmt.value.value, str):
                        for t in stmt.targets:
                            if isinstance(t, ast.Name):
                                module.consts.setdefault(t.id, stmt.value.value)
                    elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) \
                            and isinstance(stmt.value, ast.Constant) \
                            and isinstance(stmt.value.value, str):
                        module.consts.setdefault(stmt.target.id, stmt.value.value)
            self._visit(tree, rel, "", None)

    def _visit(self, node: ast.AST, rel: str, prefix: str, cls: str | None) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                self.classes.setdefault(child.name, rel)
                self._visit(child, rel, f"{prefix}{child.name}.", child.name)
            elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                key = f"{rel}::{prefix}{child.name}"
                params = [a.arg for a in child.args.posonlyargs + child.args.args]
                if params and params[0] in ("self", "cls"):
                    params = params[1:]
                self.fns[key] = _Fn(key, child, rel, cls, params)
                self.by_name[child.name].append(key)
                self._visit(child, rel, f"{prefix}{child.name}.", cls)

    # --- resolution --------------------------------------------------------------------

    def _methods(self, classes: set[str], name: str) -> set[str]:
        return {k for k in self.by_name.get(name, ())
                if self.fns[k].cls in classes}

    def _constructor(self, module: str, cls: str) -> set[str]:
        return {k for k in self.fns
                if k.startswith(f"{module}::{cls}.") and k.rsplit(".", 1)[-1]
                in ("__init__", "__post_init__", "__new__")}

    def resolve(self, fn: _Fn, node: ast.AST) -> set[str]:
        """The functions a call (or a callback reference) in ``fn`` can run."""
        target = node.func if isinstance(node, ast.Call) else node
        module = self.modules[fn.module]
        if isinstance(target, ast.Name):
            name = target.id
            if name in STOP:
                return set()
            local = {k for k in self.by_name.get(name, ()) if self.fns[k].module == fn.module
                     and self.fns[k].cls is None}
            if name in module.names:
                file, real = module.names[name]
                local |= {k for k in self.by_name.get(real, ())
                          if self.fns[k].module == file and self.fns[k].cls is None}
                if real in self.classes:
                    local |= self._constructor(self.classes[real], real)
            if name in self.classes:
                local |= self._constructor(self.classes[name], name)
            # A nested function called by name from its parent.
            local |= {k for k in self.children.get(fn.key, ())
                      if k.rsplit(".", 1)[-1] == name}
            return local
        if isinstance(target, ast.Attribute):
            name = target.attr
            if name in STOP:
                return set()
            receiver = target.value
            if isinstance(receiver, ast.Name) and receiver.id in RUNTIME_NAMES:
                if receiver.id != "self" or fn.cls in self.runtime_classes:
                    return self._methods(self.runtime_classes, name)
                if fn.cls is not None:
                    own = self._methods({fn.cls}, name)
                    return own or {k for k in self.by_name.get(name, ())}
            if isinstance(receiver, ast.Name) and receiver.id in module.modules:
                file = module.modules[receiver.id]
                return {k for k in self.by_name.get(name, ())
                        if self.fns[k].module == file and self.fns[k].cls is None}
            if isinstance(receiver, ast.Name) and receiver.id in self.classes:
                return self._methods({receiver.id}, name)
            # Any other receiver: by name, outside the runtime's own mixins.
            return {k for k in self.by_name.get(name, ())
                    if self.fns[k].cls not in self.runtime_classes}
        return set()

    # --- sinks: a wrapper that passes a parameter on to a sink is a sink ---------------

    def _sink_arg(self, call: ast.Call) -> ast.AST | None:
        name = _call_name(call)
        if name not in self.sinks:
            return None
        for kw in call.keywords:
            if kw.arg == "reason":
                return kw.value
        index = self.sinks[name]
        return call.args[index] if len(call.args) > index else None

    def _close_sinks(self) -> None:
        changed = True
        while changed:
            changed = False
            for fn in self.fns.values():
                name = fn.node.name
                if name in self.sinks:
                    continue
                for node in _own_nodes(fn.node):
                    if isinstance(node, ast.Call):
                        arg = self._sink_arg(node)
                        if isinstance(arg, ast.Name) and arg.id in fn.params:
                            self.sinks[name] = fn.params.index(arg.id)
                            changed = True
                            break

    # --- reachability ------------------------------------------------------------------

    def _seat_positions(self, node: ast.AST, *, dicts: bool) -> list[ast.AST]:
        out = []
        if isinstance(node, ast.Call):
            arg = self._sink_arg(node)
            if arg is not None:
                out.append(arg)
        if dicts and isinstance(node, ast.Dict):
            out += [v for k, v in zip(node.keys, node.values, strict=True)
                    if isinstance(k, ast.Constant) and k.value in SEAT_KEYS]
        return out

    def funnels(self, reachable: set[str] | None = None) -> dict[str, set[str]]:
        """Each exception funnel's function, and what its guarded blocks can run.

        A try-block is a funnel when its handler puts the exception's text in a sink's
        reason (delivered wherever it is called), or in an error/reason value of a dict
        built in a function already seat-bound (``reachable``): an operator's error
        dict elsewhere is not a seat's.
        """
        out: dict[str, set[str]] = {}
        for fn in self.fns.values():
            dicts = reachable is not None and fn.key in reachable
            for node in _own_nodes(fn.node):
                if not isinstance(node, ast.Try):
                    continue
                funnel = any(
                    any(isinstance(n, ast.Name) and n.id == handler.name
                        for value in self._seat_positions(sub, dicts=dicts)
                        for n in ast.walk(value))
                    for handler in node.handlers if handler.name is not None
                    for sub in ast.walk(handler))
                if not funnel:
                    continue
                for stmt in node.body:
                    for sub in ast.walk(stmt):
                        if isinstance(sub, ast.Call):
                            out.setdefault(fn.key, set()).update(self.resolve(fn, sub))
        return out

    def _closure(self, frontier: list[str], seen: set[str]) -> set[str]:
        while frontier:
            key = frontier.pop()
            if key in seen:
                continue
            seen.add(key)
            fn = self.fns[key]
            # Nested functions run as callbacks of their parent (``execute`` under the
            # meter): they are reached with it.
            frontier += [k for k in self.children.get(key, ()) if k not in seen]
            for node in _own_nodes(fn.node):
                if isinstance(node, ast.Call | ast.Name | ast.Attribute):
                    frontier += [k for k in self.resolve(fn, node) if k not in seen]
        return seen

    def _reach(self) -> set[str]:
        missing = sorted(set(ROOTS) - set(self.fns))
        if missing:
            raise KeyError(f"seat-text roots not in the code: {missing}")
        seen = self._closure(list(ROOTS) + sorted(
            k for targets in self.funnels().values() for k in targets), set())
        while True:
            more = sorted({k for targets in self.funnels(seen).values() for k in targets}
                          - seen)
            if not more:
                return seen
            seen = self._closure(more, seen)

    # --- sources -----------------------------------------------------------------------

    def _positions(self, fn: _Fn, seat_bound: bool) -> list[tuple[str, ast.AST]]:
        """The seat positions in ``fn``'s own body: (kind, value expression)."""
        out: list[tuple[str, ast.AST]] = []
        ledger_dicts = {id(node.args[0]) for node in _own_nodes(fn.node)
                        if isinstance(node, ast.Call) and _is_ledger_row(node) and node.args}
        for node in _own_nodes(fn.node):
            if isinstance(node, ast.Call):
                arg = self._sink_arg(node)
                if arg is not None:
                    out.append(("sink", arg))
            if not seat_bound:
                continue
            if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) and node.exc.args:
                out.append(("exception", node.exc.args[0]))
            if isinstance(node, ast.Dict) and id(node) not in ledger_dicts:
                out += [("result", v) for k, v in zip(node.keys, node.values, strict=True)
                        if isinstance(k, ast.Constant) and k.value in SEAT_KEYS]
        return out

    def _collect(self) -> list[SeatText]:
        # Reason functions: a call whose result lands in a seat position, directly or
        # through a local name, runs a function whose returned strings a seat reads.
        changed = True
        while changed:
            changed = False
            for key, fn in self.fns.items():
                bound = key in self.reachable or key in self.reason_fns
                assigned: dict[str, list[ast.Call]] = defaultdict(list)
                for node in _own_nodes(fn.node):
                    if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                        for t in node.targets:
                            if isinstance(t, ast.Name):
                                assigned[t.id].append(node.value)
                calls: list[ast.Call] = []
                for _kind, value in self._positions(fn, bound):
                    if isinstance(value, ast.Call):
                        calls.append(value)
                    if isinstance(value, ast.Name):
                        calls += assigned.get(value.id, [])
                if key in self.reason_fns:
                    # A reason function that returns another call's result delegates.
                    calls += [n.value for n in _own_nodes(fn.node)
                              if isinstance(n, ast.Return) and isinstance(n.value, ast.Call)]
                for call in calls:
                    for target in self.resolve(fn, call):
                        if target not in self.reason_fns:
                            self.reason_fns.add(target)
                            changed = True
        texts: dict[tuple[str, str, str], SeatText] = {}
        for key, fn in self.fns.items():
            bound = key in self.reachable or key in self.reason_fns
            module = self.modules[fn.module]
            imported = {local: self.modules[file].consts[real]
                        for local, (file, real) in module.names.items()
                        if real in self.modules.get(file, _Module()).consts}
            consts = {**self.consts, **imported, **module.consts}
            for kind, value in self._positions(fn, bound):
                text = _render(value, consts)
                if text is not None:
                    texts[(kind, key, text)] = SeatText(kind, key, text)
            if key in self.reason_fns:
                for node in _own_nodes(fn.node):
                    if isinstance(node, ast.Return) and node.value is not None:
                        text = _render(node.value, consts)
                        if text is not None:
                            texts[("returned", key, text)] = SeatText("returned", key, text)
        return sorted(texts.values(), key=lambda t: (t.kind, t.source, t.text))

    @property
    def sources(self) -> set[str]:
        return {t.source for t in self.texts}


def _package_state() -> tuple:
    return tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size)
                 for p in sorted(PACKAGE.rglob("*.py")))


@functools.cache
def _scan_of(state: tuple) -> Scan:
    return Scan()


def scan() -> Scan:
    """The scan of the package as it is on disk now (taken once per state of its files:
    the scan is a pure function of the source)."""
    return _scan_of(_package_state())
