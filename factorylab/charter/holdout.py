"""What a holdout may read: behavioural facts of a closed window, and nothing else.

Essay II.IV.a: the evaluatory layer adds "holdout test criteria to a given charter
(based on adversarially induced conditions or just real production traffic)". A
holdout tests the factory's behaviour. A predicate that reads the window's index, a
timestamp, a balance or a market series tests the calendar or the world instead: it
can pass its preflight and trial and then fail in every later window whatever the
factory does. Such a predicate is refused as a holdout before its motion is admitted.
"""

from __future__ import annotations

import ast

#: The window facts a holdout may read: what the factory's own seats did in it.
BEHAVIOURAL_FACTS = frozenset({
    "amendments_activated", "amendments_proposed", "censored", "compute_spend_micro",
    "consequences_paid_off", "consequences_settled", "costs", "exposures_settled",
    "exposures_won", "fills", "forecast_skills", "invocations", "market_purchases",
    "max_position_notional_micro", "meta_verdicts", "noop_returns", "notional_micro", "ok",
    "outcomes", "producer_returns", "realized_pnl_micro", "registration_rejections",
    "registrations", "resolved_verdicts", "revised_decisions", "revision_returns",
    "tool_calls", "verdicts",
})
#: Modules a holdout may import: arithmetic only, no clock, no randomness, no host.
PURE_MODULES = frozenset({"math", "statistics"})
_FORBIDDEN_NAMES = frozenset({"__import__", "eval", "exec", "compile", "getattr", "globals",
                              "locals", "vars", "open", "input", "breakpoint"})


def behavioural_reads(code: str) -> frozenset[str]:
    """The facts a holdout predicate reads; refuses one that reads anything else.

    Guarantees every read of the ``resolve`` parameter is a subscript or ``.get``
    with a literal key in ``BEHAVIOURAL_FACTS``; any other use of it (iteration, a
    computed key, passing it on, aliasing it) is refused, because what it reads
    could not be known. Imports outside ``PURE_MODULES`` and the introspection
    builtins are refused, so no clock or host state can enter.
    """
    try:
        module = ast.parse(code)
    except (SyntaxError, ValueError, RecursionError):
        raise ValueError("holdout predicate: code does not parse") from None
    resolve = next((node for node in module.body
                    if isinstance(node, ast.FunctionDef) and node.name == "resolve"), None)
    if resolve is None or not resolve.args.args:
        raise ValueError("holdout predicate: code must define resolve(facts)")
    facts = resolve.args.args[0].arg
    allowed: set[int] = set()
    reads: set[str] = set()

    def key(node: ast.AST) -> str:
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            raise ValueError("holdout predicate: a fact is read by a literal key only")
        if node.value not in BEHAVIOURAL_FACTS:
            raise ValueError(f"holdout predicate: {node.value!r} is not a behavioural fact; "
                             "a holdout reads what the factory did, not the window's index, "
                             "the clock, balances or market series")
        return node.value

    for node in ast.walk(module):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = ([alias.name for alias in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""])
            if any(name.split(".")[0] not in PURE_MODULES for name in names):
                raise ValueError("holdout predicate: imports only math and statistics")
        elif isinstance(node, ast.Name) and node.id in _FORBIDDEN_NAMES:
            raise ValueError(f"holdout predicate: {node.id} is not available to a holdout")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise ValueError("holdout predicate: dunder attributes are not available")
        elif (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
              and node.value.id == facts):
            reads.add(key(node.slice))
            allowed.add(id(node.value))
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
              and node.func.attr == "get" and isinstance(node.func.value, ast.Name)
              and node.func.value.id == facts and node.args):
            reads.add(key(node.args[0]))
            allowed.add(id(node.func.value))
    for node in ast.walk(module):
        if isinstance(node, ast.Name) and node.id == facts and id(node) not in allowed:
            raise ValueError("holdout predicate: the facts are read by subscript or .get only")
    if not reads:
        raise ValueError("holdout predicate: reads no behavioural fact")
    return frozenset(reads)
