"""Every consumer of stored card rows reads them through the one response selector.

Codex on #152 (rule 3: published = enforced): a response exists iff an invocation
happened (``measurement.is_response``). A non-response (a ballot whose assembly was
unavailable) must never enter a mean, a median, a share or a retained horizon, so no
code that measures, prices or retains card rows may iterate the stored rows raw. This
parses the modules that do and fails on any raw access that is not inside a
``_selected(...)`` call or a comprehension filtered by ``is_response``, outside the
few functions allowed below, each with its reason.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parents[2] / "factorylab"
#: The stored row lists of ``CardSamples``.
ROW_LISTS = frozenset({"returns", "forecasts", "readings"})
#: Modules that measure, price or retain card rows, and the names a ``CardSamples``
#: goes by in each (``self`` only inside the class itself).
MODULES = {
    "charter/measurement.py": {"samples"},
    "runtime/pricing.py": {"samples"},
    "runtime/loop.py": set(),
    "runtime/governance.py": set(),
    "runtime/feedback.py": set(),
}
#: The functions that must see every stored row, and why.
ALLOWED = {
    "CardSamples.returned": "the recorder appends the row it records",
    "CardSamples.read": "the recorder appends the reading it records",
    "CardSamples.resolved_forecast": "the recorder appends the forecast row it records",
    "CardSamples.revised": "marks the named return's own row by its handle; a write that "
                           "measures nothing",
    "CardSamples._keep": "the storage filter: it keeps rows by the horizons computed over "
                         "selected rows, and must see non-responses to drop them",
    "Runtime._invoke": "the recorder completes the row it just appended (its tool calls)",
}


def _raw_accesses(tree: ast.AST, sample_names: set[str]):
    """Yield (qualname, node, ancestors) for each raw access to a stored row list."""
    def visit(node, ancestors, scope, in_samples_class):
        if isinstance(node, ast.ClassDef):
            scope = f"{node.name}"
            in_samples_class = node.name == "CardSamples"
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            scope = f"{scope}.{node.name}" if scope else node.name
        names = set(sample_names) | ({"self"} if in_samples_class else set())
        raw = False
        if isinstance(node, ast.Attribute) and node.attr in ROW_LISTS:
            base = node.value
            raw = ((isinstance(base, ast.Name) and base.id in names)
                   or (isinstance(base, ast.Attribute) and base.attr == "card_samples"))
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
              and node.func.id == "getattr" and node.args
              and isinstance(node.args[0], ast.Name) and node.args[0].id in names):
            raw = True
        if raw:
            yield scope, node, ancestors
        for child in ast.iter_child_nodes(node):
            yield from visit(child, (*ancestors, node), scope, in_samples_class)

    yield from visit(tree, (), "", False)


def _selected(node, ancestors) -> bool:
    """Whether a raw access goes through ``_selected(...)`` or an ``is_response`` filter."""
    chain = (*ancestors, node)
    for i, parent in enumerate(ancestors):
        if (isinstance(parent, ast.Call) and isinstance(parent.func, ast.Name)
                and parent.func.id == "_selected"):
            return True
        if isinstance(parent, ast.comprehension):
            inside_iter = chain[i + 1] is parent.iter
            filtered = any(isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                           and call.func.id == "is_response"
                           for condition in parent.ifs for call in ast.walk(condition))
            if inside_iter and filtered:
                return True
    return False


def _violations() -> list[str]:
    found = []
    for module, names in MODULES.items():
        path = ROOT / module
        tree = ast.parse(path.read_text(), filename=str(path))
        for scope, node, ancestors in _raw_accesses(tree, names):
            if scope in ALLOWED or _selected(node, ancestors):
                continue
            found.append(f"{module}:{node.lineno} in {scope}: {ast.unparse(node)}")
    return found


def test_every_card_row_consumer_goes_through_the_response_selector():
    assert _violations() == []


def test_the_check_finds_a_raw_iteration_and_accepts_the_selector():
    """The check itself: a raw iteration is flagged; the selector and the filter pass."""
    source = '''
def measured(samples):
    return [r["cost"] for r in samples.returns]
def selected(samples):
    return _selected("cost_per_return", samples.returns)
def filtered(samples):
    return [r for r in samples.returns if is_response(r)]
def grouped(samples, kind):
    return getattr(samples, kind)
'''
    tree = ast.parse(source)
    flagged = sorted(scope for scope, node, ancestors in _raw_accesses(tree, {"samples"})
                     if not _selected(node, ancestors))
    assert flagged == ["grouped", "measured"]


def _card(window):
    from factorylab.charter.charter import MetricCard

    return MetricCard(id="cost", norm="n", description="d", units="micro-USD per attempt",
                      window=window, acceptable_region="at most 100",
                      observation="cost_per_attempt", answers_for="all")


def _row(handle, cost, invoked, window=0):
    return {"handle": handle, "assembly": "a", "role": "voter", "window": window,
            "cost": cost, "ok": invoked, "invoked": invoked, "noop": False,
            "revision": False, "tool_calls": 0}


def test_non_responses_never_evict_a_real_invocation_from_retention():
    """Codex on #152: two ballots whose assembly was unavailable, recorded after two real
    invocations of the same scope, took the retained horizon's two slots and evicted the
    invocations. The horizon is kept over responses only, and the non-responses go."""
    from factorylab.charter.measurement import CardSamples
    from factorylab.charter.windows import MetricWindow

    samples = CardSamples()
    samples.returns.extend([_row("h0", 4, True), _row("h1", 8, True),
                            _row("b0", 0, False), _row("b1", 0, False)])
    samples.windows.append({"index": 5})  # no stored row is inside the retained windows
    samples.prune((_card(MetricWindow("returns", 2, "assembly")),))
    assert [row["handle"] for row in samples.returns] == ["h0", "h1"]


def test_a_non_response_never_moves_the_median_a_region_rolls_on():
    """Codex on #152: a zero-cost ballot whose assembly was unavailable entered the cost
    median's horizon (``prev_median`` regions roll on it). The median is taken over the
    responses the card measures: 6 over costs 4 and 8, never 4 over 8 and the ballot."""
    from factorylab.charter.measurement import CardSamples, measure_cards
    from factorylab.charter.windows import MetricWindow
    from factorylab.runtime.pricing import MeasureWindow

    samples = CardSamples()
    samples.returns.extend([_row("h0", 4, True, 1), _row("h1", 8, True, 1),
                            _row("b0", 0, False, 1)])
    card = _card(MetricWindow("returns", 2, "assembly"))
    assert measure_cards((card,), samples, MeasureWindow(1, None)) == {"cost": 6.0}
    assert samples.medians == {"cost": 6.0}
