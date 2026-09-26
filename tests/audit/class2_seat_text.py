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
``file::qualname``; its texts are every alternative ``Renderer`` finds, each interpolation shown as
``{expression}``.
"""

from __future__ import annotations

import ast
import functools
import hashlib
import re
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
    #: A module- or class-level name bound to an expression (a string, or one built).
    consts: dict[str, ast.AST] = field(default_factory=dict)
    #: A module- or class-level name bound to a dict literal: its values.
    dicts: dict[str, list[ast.AST]] = field(default_factory=dict)


def _module_file(dotted: str) -> str | None:
    base = ROOT / Path(*dotted.split("."))
    if base.with_suffix(".py").exists():
        return base.with_suffix(".py").relative_to(ROOT).as_posix()
    if (base / "__init__.py").exists():
        return (base / "__init__.py").relative_to(ROOT).as_posix()
    return None


#: At most this many alternatives are kept for one expression (a cartesian product of
#: concatenated alternatives stops here; none of the kernel's reasons comes close).
MAX_ALTERNATIVES = 256
_PLACEHOLDER = re.compile(r"\{[^{}]*\}")


def _placeholder(node: ast.AST) -> str:
    """An interpolation as ``{expression}`` (braces inside it dropped, so a nested
    f-string cannot unbalance the text)."""
    return "{" + re.sub(r"[{}]", "", ast.unparse(node))[:40] + "}"


def has_literal_text(text: str) -> bool:
    """Whether a rendered string carries words of its own, not only interpolations."""
    return bool(_PLACEHOLDER.sub("", text).strip(" \t\n:,.;()[]'\"-=/|"))


class Renderer:
    """Every string an expression can evaluate to, as a seat reads it.

    Guarantees every renderable alternative of every shape that builds a seat-bound
    string: a constant; an f-string, each interpolation shown as ``{expression}``; ``+``
    (every combination of the operands' alternatives), ``%`` and ``.format`` (the
    template); both branches of a conditional expression; every operand of ``or`` and
    ``and``; a name bound in the enclosing function (every assignment, in any branch),
    at module or class level, or imported; an attribute naming a module- or class-level
    constant; a lookup (``[…]`` or ``.get``) in a constant dict (every value, and a
    ``.get`` default). ``str(exc)`` renders nothing of its own: an exception's messages
    are collected where it is raised.
    """

    def __init__(self, scan: Scan, module: str, local: dict[str, list[ast.AST]] | None = None):
        self.scan, self.module, self.local = scan, module, local or {}

    def render(self, node: ast.AST, depth: int = 0) -> list[str]:
        if depth > 12:
            return []
        out = self._render(node, depth + 1)
        return list(dict.fromkeys(out))[:MAX_ALTERNATIVES]

    def _in(self, module: str) -> Renderer:
        return self if module == self.module else Renderer(self.scan, module)

    def _named(self, name: str, depth: int) -> list[str] | None:
        """A bare name's alternatives: local bindings, then the module's, then an
        import's; None when the name is bound to no string anywhere it can be read."""
        if name in self.local:
            return [t for value in self.local[name] for t in self.render(value, depth)]
        module = self.scan.modules[self.module]
        if name in module.consts:
            return self.render(module.consts[name], depth)
        if name in module.names:
            file, real = module.names[name]
            other = self.scan.modules.get(file)
            if other is not None and real in other.consts:
                return self._in(file).render(other.consts[real], depth)
        return None

    def _dict_values(self, node: ast.AST, depth: int) -> list[str] | None:
        """Every value of the constant dict ``node`` names, or None."""
        module = self.scan.modules[self.module]
        if isinstance(node, ast.Name):
            if node.id in module.dicts:
                return [t for v in module.dicts[node.id] for t in self.render(v, depth)]
            if node.id in module.names:
                file, real = module.names[node.id]
                other = self.scan.modules.get(file)
                if other is not None and real in other.dicts:
                    inner = self._in(file)
                    return [t for v in other.dicts[real] for t in inner.render(v, depth)]
        if isinstance(node, ast.Attribute):
            found = self.scan.dict_attrs.get(node.attr)
            if found:
                return [t for file, values in found for v in values
                        for t in self._in(file).render(v, depth)]
        return None

    def _render(self, node: ast.AST, depth: int) -> list[str]:
        if isinstance(node, ast.Constant):
            return [node.value] if isinstance(node.value, str) else []
        if isinstance(node, ast.JoinedStr):
            parts = [[str(v.value)] if isinstance(v, ast.Constant)
                     else [_placeholder(v.value)] for v in node.values]
            return _product(parts)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left = self.render(node.left, depth) or [_placeholder(node.left)]
            right = self.render(node.right, depth) or [_placeholder(node.right)]
            return _product([left, right])
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
            return self.render(node.left, depth)
        if isinstance(node, ast.IfExp):
            return self.render(node.body, depth) + self.render(node.orelse, depth)
        if isinstance(node, ast.BoolOp):
            return [t for value in node.values for t in self.render(value, depth)]
        if isinstance(node, ast.Name):
            return self._named(node.id, depth) or []
        if isinstance(node, ast.Attribute):
            receiver = node.value
            module = self.scan.modules[self.module]
            if isinstance(receiver, ast.Name) and receiver.id in module.modules:
                file = module.modules[receiver.id]
                other = self.scan.modules.get(file)
                if other is not None and node.attr in other.consts:
                    return self._in(file).render(other.consts[node.attr], depth)
            return [t for file, value in self.scan.const_attrs.get(node.attr, ())
                    for t in self._in(file).render(value, depth)]
        if isinstance(node, ast.Subscript):
            return self._dict_values(node.value, depth) or []
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute) and f.attr == "format":
                return self.render(f.value, depth)
            if isinstance(f, ast.Attribute) and f.attr == "get":
                values = self._dict_values(f.value, depth)
                if values is not None:
                    default = self.render(node.args[1], depth) if len(node.args) > 1 else []
                    return values + default
            # ``str(exc)`` and every other call: the text is its callee's, collected there.
            return []
        return []


def _product(parts: list[list[str]]) -> list[str]:
    out = [""]
    for options in parts:
        out = [a + b for a in out for b in options][:MAX_ALTERNATIVES]
    return out


def _own_nodes(fn: ast.AST):
    """The nodes of a function's own body, nested function bodies excluded."""
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            stack.extend(ast.iter_child_nodes(node))


def _read_constants(tree: ast.Module, module: _Module) -> None:
    """Module- and class-level bindings only: a local variable is not a constant another
    function's name can mean. A dict literal is kept by its values."""
    bodies = [tree.body] + [c.body for c in ast.walk(tree) if isinstance(c, ast.ClassDef)]
    for body in bodies:
        for stmt in body:
            targets, value = [], None
            if isinstance(stmt, ast.Assign):
                targets, value = stmt.targets, stmt.value
            elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
                targets, value = [stmt.target], stmt.value
            for t in targets:
                if not isinstance(t, ast.Name):
                    continue
                if isinstance(value, ast.Dict):
                    module.dicts.setdefault(t.id, [v for v in value.values if v])
                else:
                    module.consts.setdefault(t.id, value)


def snippet_texts(source: str, function: str = "f") -> set[str]:
    """Every text ``Renderer`` finds in the values ``function`` returns, in a one-module
    ``source`` read as the scan reads the package (its constants, constant dicts and
    local bindings): the renderer's shapes, testable one by one."""
    tree = ast.parse(source)
    module = _Module()
    _read_constants(tree, module)
    fake = type("_OneModule", (), {})()
    fake.modules = {"m": module}
    fake.const_attrs = defaultdict(list)
    fake.dict_attrs = defaultdict(list)
    for name, value in module.consts.items():
        fake.const_attrs[name].append(("m", value))
    for name, values in module.dicts.items():
        fake.dict_attrs[name].append(("m", values))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == function)
    renderer = Renderer(fake, "m", local_bindings(fn))
    return {t for node in _own_nodes(fn) if isinstance(node, ast.Return) and node.value
            for t in renderer.render(node.value) if has_literal_text(t)}


def local_bindings(fn: ast.AST) -> dict[str, list[ast.AST]]:
    """Every value a name is bound to in a function's own body, in any branch: plain,
    annotated and augmented assignments (an augmented one adds its right-hand side)."""
    out: dict[str, list[ast.AST]] = defaultdict(list)
    for node in _own_nodes(fn):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out[t.id].append(node.value)
        elif isinstance(node, ast.AnnAssign | ast.AugAssign) and node.value is not None \
                and isinstance(node.target, ast.Name):
            out[node.target.id].append(node.value)
    return dict(out)


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
        # An attribute (``self.WRITE_REFUSAL``) names a module- or class-level constant
        # (or constant dict) anywhere; a bare name, its own module's or an import's.
        self.const_attrs: dict[str, list[tuple[str, ast.AST]]] = defaultdict(list)
        self.dict_attrs: dict[str, list[tuple[str, list[ast.AST]]]] = defaultdict(list)
        for file, module in self.modules.items():
            for name, value in module.consts.items():
                self.const_attrs[name].append((file, value))
            for name, values in module.dicts.items():
                self.dict_attrs[name].append((file, values))
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
            _read_constants(tree, module)
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
            renderer = Renderer(self, fn.module, local_bindings(fn.node))
            found = [(kind, value) for kind, value in self._positions(fn, bound)]
            if key in self.reason_fns:
                found += [("returned", node.value) for node in _own_nodes(fn.node)
                          if isinstance(node, ast.Return) and node.value is not None]
            for kind, value in found:
                for text in renderer.render(value):
                    if has_literal_text(text):
                        texts[(kind, key, text)] = SeatText(kind, key, text)
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
