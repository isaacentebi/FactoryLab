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
* **Payloads.** Every literal a seat receives in a request, a tool result or its inbox,
  whatever its key: the arguments of every payload call (``PAYLOAD_CALLS``: a request's
  description, inputs and schema; a continuation's inputs; an inbox outcome; a fact
  noted to the owner) and every value a tool entry point returns (``ROOTS``, success
  and refusal alike), followed through dict values, list items, ``**`` spreads, local bindings and
  what is added to them, and into the return values of every function called there.

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
from typing import Any

from tests.audit.class2_corpus import PACKAGE, ROOT  # the corpus's own sources

#: Functions that deliver their ``reason`` argument to a seat, by the name they are
#: called by, and the object each names (``module:qualname``). The argument's position
#: is read from the function's real signature at scan time (``SINKS``), never written
#: here, so a reordered signature cannot drop it.
SINK_TARGETS: dict[str, str] = {
    "_refusal_to_owner": "factorylab.runtime.compute:ComputeMixin._refusal_to_owner",
    "_refuse_request": "factorylab.runtime.composition:CompositionMixin._refuse_request",
    "_refuse_order": "factorylab.runtime.venue:VenueMixin._refuse_order",
    "_connector_refused": "factorylab.runtime.compute:ComputeMixin._connector_refused",
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
#: Constructors and builders that hand a seat a payload: the name as called, the object
#: it names (``module:qualname``) and the parameters (or dataclass fields) that reach
#: the seat; ``**`` names a function's ``**kwargs``. A request's text, inputs, schema,
#: completion criterion and scoring (``Request``, ``_request``, a tool round's
#: ``continuation``), a fact addressed to the owner (``_note_to_owner``). Which
#: positional index and which keyword carries each is read from the real signature at
#: scan time (``PAYLOAD_CALLS``), so positional and keyword arguments are both covered
#: and a signature change cannot silently drop coverage.
PAYLOAD_TARGETS: dict[str, tuple[str, frozenset[str]]] = {
    "_request": ("factorylab.runtime.compute:ComputeMixin._request",
                 frozenset({"description", "inputs", "schema", "settlement"})),
    "Request": ("factorylab.cortex.request:Request",
                frozenset({"description", "inputs", "outcome_schema",
                           "completion_criterion", "settlement"})),
    "continuation": ("factorylab.cortex.request:Request.continuation",
                     frozenset({"inputs"})),
    "_note_to_owner": ("factorylab.runtime.compute:ComputeMixin._note_to_owner",
                       frozenset({"kind", "**"})),
}


def _target(path: str) -> Any:
    """The object ``module:qualname`` names, imported."""
    import importlib

    module, _, qualname = path.partition(":")
    obj: Any = importlib.import_module(module)
    for part in qualname.split("."):
        obj = getattr(obj, part)
    return obj


def call_mapping(path: str, names: frozenset[str]) -> tuple[frozenset[int], frozenset[str]]:
    """Where the parameters ``names`` of the object at ``path`` sit in a call: their
    positional indexes (``self`` not counted) and their keyword names, from its real
    signature (a dataclass's is its fields in order). ``**`` in ``names`` makes every
    keyword the function collects in its ``**kwargs`` a seat's. Raises when a name is
    no parameter of it, so a renamed field fails the scan instead of dropping out."""
    import inspect

    params = list(inspect.signature(_target(path)).parameters.values())
    if params and params[0].name == "self":
        params = params[1:]
    known = {p.name for p in params} | {"**"}
    if names - known:
        raise ValueError(f"{path} has no parameter {sorted(names - known)}")
    positional = [p for p in params if p.kind in (p.POSITIONAL_ONLY,
                                                  p.POSITIONAL_OR_KEYWORD)]
    positions = frozenset(i for i, p in enumerate(positional) if p.name in names)
    keywords = {p.name for p in params if p.name in names and p.kind in (
        p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)}
    if "**" in names and any(p.kind is p.VAR_KEYWORD for p in params):
        keywords.add("*")
    return positions, frozenset(keywords)


def _signature_tables() -> tuple[dict[str, int], dict[str, tuple[frozenset[int],
                                                                  frozenset[str]]]]:
    sinks = {}
    for name, path in SINK_TARGETS.items():
        (index,), _keywords = call_mapping(path, frozenset({"reason"}))
        sinks[name] = index
    payloads = {name: call_mapping(path, names)
                for name, (path, names) in PAYLOAD_TARGETS.items()}
    return sinks, payloads


#: The sinks' ``reason`` positions and the payload calls' seat-bound positions and
#: keywords, read from the real signatures (``call_mapping``).
SINKS, PAYLOAD_CALLS = _signature_tables()
#: A receiver whose ``append(owner, …, outcome=…)`` is a seat's inbox.
INBOX_RECEIVERS = ("outcomes",)
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
        self.class_nodes: dict[tuple[str, str], ast.ClassDef] = {}  # (module, name)
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
        #: A seat-raised package exception's message template: the ``__init__`` (or
        #: ``__str__``) that builds it -> the expressions it is built from.
        self.message_templates: dict[str, list[ast.AST]] = {}
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
                self.class_nodes.setdefault((rel, child.name), child)
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
            if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
                out += [("exception", arg) for arg in self._exception_args(fn, node.exc)]
            if isinstance(node, ast.Dict) and id(node) not in ledger_dicts:
                out += [("result", v) for k, v in zip(node.keys, node.values, strict=True)
                        if isinstance(k, ast.Constant) and k.value in SEAT_KEYS]
        return out

    # --- exceptions: the arguments that become the message a seat reads -----------------

    def _exception_args(self, fn: _Fn, call: ast.Call) -> list[ast.AST]:
        """The arguments of ``raise X(...)`` that become the exception's message: for a
        package class, those its constructor passes to ``Exception.__init__`` or its
        ``__str__`` reads (``_message_params``, e.g. ``SectionError``'s ``reason``);
        otherwise every argument (``BaseException`` keeps them all in ``args``)."""
        found = self._exception_class(fn, call.func)
        message = self._message_params(*found) if found else None
        if message is None:
            return [*call.args, *(kw.value for kw in call.keywords)]
        params, template_key, template = message
        self.message_templates[template_key] = template
        init = self._init_of(*found, set())
        order = init.params if init is not None else []
        out = [arg for i, arg in enumerate(call.args)
               if i < len(order) and order[i] in params]
        return out + [kw.value for kw in call.keywords if kw.arg in params]

    def _exception_class(self, fn: _Fn, func: ast.AST) -> tuple[str, str] | None:
        """The package class a raise constructs, as (module, name), or None."""
        return self._class_ref(fn.module, func)

    def _class_ref(self, file: str, expr: ast.AST) -> tuple[str, str] | None:
        """The package class ``expr`` names in module ``file``, as (module, name): its
        own module's class, then the one its import binds (an alias resolved), then an
        attribute of an imported module; None for any other (a builtin)."""
        module = self.modules[file]
        if isinstance(expr, ast.Name):
            if (file, expr.id) in self.class_nodes:
                return file, expr.id
            if expr.id in module.names:
                target, real = module.names[expr.id]
                return (target, real) if (target, real) in self.class_nodes else None
            return None
        if isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name) \
                and expr.value.id in module.modules:
            target = module.modules[expr.value.id]
            return (target, expr.attr) if (target, expr.attr) in self.class_nodes else None
        return None

    def _bases(self, module: str, name: str) -> list[tuple[str, str]] | None:
        """The package base classes of a class, or None when one base is not the
        package's (a builtin exception, whose message is every argument)."""
        node = self.class_nodes.get((module, name))
        if node is None:
            return None
        bases = [self._class_ref(module, base) for base in node.bases]
        return None if None in bases else bases

    def _method(self, module: str, name: str, method: str, seen: set) -> _Fn | None:
        """``method`` as the class defines it or inherits it from a package base."""
        if (module, name) in seen:
            return None
        seen.add((module, name))
        own = self.fns.get(f"{module}::{name}.{method}")
        if own is not None:
            return own
        for base in self._bases(module, name) or []:
            found = self._method(*base, method, seen)
            if found is not None:
                return found
        return None

    def _init_of(self, module: str, name: str, seen: set) -> _Fn | None:
        return self._method(module, name, "__init__", seen)

    def _message_params(self, module: str, name: str
                        ) -> tuple[set[str], str, list[ast.AST]] | None:
        """The constructor parameters the message is made of, with the function that
        builds it and the expressions it builds it from (its template, seat text in its
        own right); or None when every argument is the message.

        A ``__str__`` (the class's or a package base's) makes the message of the
        attributes it reads, each traced to the parameters ``__init__`` assigns it
        from. Otherwise the message is what ``__init__`` hands ``super().__init__``.
        With neither, ``BaseException`` keeps every argument, so every one is read."""
        init = self._init_of(module, name, set())
        if init is None:
            return None
        params = set(init.params) | {a.arg for a in init.node.args.kwonlyargs}
        text = self._method(module, name, "__str__", set())
        if text is not None:
            attrs = {n.attr for n in ast.walk(text.node) if isinstance(n, ast.Attribute)
                     and isinstance(n.value, ast.Name) and n.value.id == "self"}
            sources: set[str] = set()
            for node in ast.walk(init.node):
                if not isinstance(node, ast.Assign):
                    continue
                for target in node.targets:
                    pairs = (zip(target.elts, node.value.elts, strict=False)
                             if isinstance(target, ast.Tuple)
                             and isinstance(node.value, ast.Tuple) else [(target, node.value)])
                    for t, v in pairs:
                        if isinstance(t, ast.Attribute) and t.attr in attrs:
                            sources |= {n.id for n in ast.walk(v)
                                        if isinstance(n, ast.Name) and n.id in params}
            return sources, text.key, [n.value for n in _own_nodes(text.node)
                                       if isinstance(n, ast.Return) and n.value is not None]
        for node in ast.walk(init.node):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr == "__init__" and isinstance(node.func.value, ast.Call) \
                    and isinstance(node.func.value.func, ast.Name) \
                    and node.func.value.func.id == "super":
                built = [*node.args, *(k.value for k in node.keywords)]
                return ({n.id for arg in built for n in ast.walk(arg)
                         if isinstance(n, ast.Name) and n.id in params}, init.key, built)
        return None

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
        for key, values in self.message_templates.items():
            renderer = Renderer(self, self.fns[key].module, local_bindings(self.fns[key].node))
            for value in values:
                for text in renderer.render(value):
                    if has_literal_text(text):
                        texts[("exception", key, text)] = SeatText("exception", key, text)
        texts.update(self._payloads())
        return sorted(texts.values(), key=lambda t: (t.kind, t.source, t.text))

    # --- payloads: every literal a seat receives in a request or its inbox --------------

    def _payload_args(self, call: ast.Call) -> list[ast.AST]:
        """The expressions a payload call hands a seat (``PAYLOAD_CALLS``, the inbox)."""
        name = _call_name(call)
        f = call.func
        if name == "append" and isinstance(f, ast.Attribute) \
                and any(r in ast.unparse(f.value) for r in INBOX_RECEIVERS) \
                and not _is_ledger_row(call):
            return [kw.value for kw in call.keywords if kw.arg == "outcome"]
        if name not in PAYLOAD_CALLS:
            return []
        positions, keywords = PAYLOAD_CALLS[name]
        out = [a for i, a in enumerate(call.args) if i in positions]
        out += [kw.value for kw in call.keywords
                if kw.arg is None or "*" in keywords or kw.arg in keywords]
        return out

    def _mutations(self, fn: _Fn, name: str) -> list[ast.AST]:
        """What a function adds to a local container after binding it: ``name[k] = v``,
        ``name.update(…)``, ``name.setdefault(k, v)``, ``name.append(v)``/``extend``."""
        out: list[ast.AST] = []
        for node in _own_nodes(fn.node):
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name) \
                            and t.value.id == name:
                        out.append(node.value)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == name \
                    and node.func.attr in ("update", "setdefault", "append", "extend"):
                out += list(node.args) + [kw.value for kw in node.keywords]
        return out

    def _payloads(self) -> dict[tuple[str, str, str], SeatText]:
        """Every string a payload can carry: from every payload call's arguments, through
        dict values (any key), list and tuple items, ``**`` spreads, both branches and
        every operand, local bindings and what is added to them, and into the return
        values of every function called there (a payload builder), each leaf rendered
        by ``Renderer``."""
        texts: dict[tuple[str, str, str], SeatText] = {}
        work: list[tuple[str, ast.AST]] = []
        for key, fn in self.fns.items():
            for node in _own_nodes(fn.node):
                if isinstance(node, ast.Call):
                    work += [(key, arg) for arg in self._payload_args(node)]
        # A tool's result is a payload whatever its keys, on success as on refusal: the
        # values the tool entry points (and the callbacks they run) return.
        for root in ROOTS:
            for key in [root, *self.children.get(root, ())]:
                work += [(key, r.value) for r in _own_nodes(self.fns[key].node)
                         if isinstance(r, ast.Return) and r.value is not None]
        seen: set[tuple[str, int]] = set()
        builders: set[str] = set()
        renderers: dict[str, Renderer] = {}
        while work:
            key, node = work.pop()
            if (key, id(node)) in seen:
                continue
            seen.add((key, id(node)))
            fn = self.fns[key]
            if key not in renderers:
                renderers[key] = Renderer(self, fn.module, local_bindings(fn.node))
            if isinstance(node, ast.Dict):
                work += [(key, v) for v in node.values if v is not None]
                continue
            if isinstance(node, ast.List | ast.Tuple | ast.Set):
                work += [(key, e.value if isinstance(e, ast.Starred) else e)
                         for e in node.elts]
                continue
            if isinstance(node, ast.IfExp):
                work += [(key, node.body), (key, node.orelse)]
                continue
            if isinstance(node, ast.BoolOp):
                work += [(key, v) for v in node.values]
                continue
            if isinstance(node, ast.DictComp | ast.ListComp | ast.GeneratorExp | ast.SetComp):
                work += [(key, node.value if isinstance(node, ast.DictComp) else node.elt)]
                continue
            if isinstance(node, ast.Name):
                bound = renderers[key].local.get(node.id, [])
                work += [(key, v) for v in bound + self._mutations(fn, node.id)]
                if node.id not in renderers[key].local:
                    # A module-level dict the payload carries (its own module's, or an
                    # import's: ``COUNTERFACTUAL_FIELD`` in a schema), read by its values.
                    work += [(key, v) for v in self._module_dict(fn.module, node.id)]
            if isinstance(node, ast.Call):
                name = _call_name(node)
                if name in ("dict", "list", "tuple", "sorted", "deepcopy", "copy"):
                    work += [(key, a) for a in node.args]
                    work += [(key, kw.value) for kw in node.keywords]
                elif name not in ("str", "repr", "len", "int", "float", "round"):
                    for target in self.resolve(fn, node):
                        if target not in builders:
                            builders.add(target)
                            work += [(target, r.value) for r in _own_nodes(self.fns[target].node)
                                     if isinstance(r, ast.Return) and r.value is not None]
            for text in renderers[key].render(node):
                if has_literal_text(text):
                    texts[("payload", key, text)] = SeatText("payload", key, text)
        self.payload_builders = builders
        return texts

    def _module_dict(self, file: str, name: str) -> list[ast.AST]:
        """The values of the module-level dict ``name`` means in ``file``: its own, or the
        one an import binds it to; none when it names no such dict."""
        module = self.modules[file]
        if name in module.dicts:
            return module.dicts[name]
        if name in module.names:
            other_file, real = module.names[name]
            other = self.modules.get(other_file)
            if other is not None and real in other.dicts:
                return other.dicts[real]
        return []

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
