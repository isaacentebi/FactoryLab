"""§I.a: attribution cannot erase aggregate evidence or share mutable state."""

import builtins
import contextlib
import io
import json
import sys
from dataclasses import replace
from types import SimpleNamespace

import pytest

from factorylab.charter.holdout import behavioural_reads
from factorylab.cortex import sandbox
from factorylab.runtime.shared import PredicateRunner
from tests.runtime.test_pricing_review import attribution_runtime


@pytest.fixture
def runner(monkeypatch):
    def jail(code, *, stdin, **kwargs):
        stream = io.StringIO()
        try:
            with monkeypatch.context() as patch:
                patch.setattr(sys, "stdin", io.StringIO(stdin))
                with contextlib.redirect_stdout(stream):
                    exec(code, {})  # noqa: S102 - deterministic execution boundary
        except Exception:
            return SimpleNamespace(timed_out=False, returncode=1, stdout="", stderr="error")
        return SimpleNamespace(timed_out=False, returncode=0, stdout=stream.getvalue())

    monkeypatch.setattr(sandbox, "jail_available", lambda: True)
    monkeypatch.setattr(sandbox, "run_python", jail)
    result = PredicateRunner()
    result.available = True
    return result


@pytest.mark.parametrize("statement", [
    "__builtins__.setdefault('batch_seen', True)",
    "math.pi = facts['notional_micro']",
    "del math.pi",
    "setattr(math, 'pi', 0)",
    "delattr(math, 'pi')",
    "__state = 1",
    "globals()", "vars()", "locals()", "getattr(math, 'pi')",
    "exec('pass')", "eval('True')", "compile('True', '', 'eval')",
    "__import__('math')", "import builtins", "import os",
    "from math import __dict__", "from .math import pi",
    "from math import pi as __hidden",
    "def __hidden(): pass",
    "def helper(__hidden): pass",
])
def test_holdout_admission_rejects_shared_state_access(statement):
    code = (f"import math\ndef resolve(facts):\n    {statement}\n"
            "    return facts['notional_micro'] == 0\n")
    with pytest.raises(ValueError):
        behavioural_reads(code)


def test_batch_builtins_are_fresh_per_item(runner):
    # Bypass admission to check the harness's independent defense, not just the AST.
    code = "def resolve(facts):\n    return __builtins__.setdefault('batch_seen', facts['ok'])\n"
    facts = [{"ok": True}, {"ok": False}]
    try:
        assert runner.run_batch(code, facts) == [True, False]
    finally:
        builtins.__dict__.pop("batch_seen", None)


def test_mixed_batch_equals_independent_runs(runner):
    code = """import math
from statistics import mean
def resolve(facts):
    scratch = []
    scratch.append(facts['notional_micro'])
    if scratch[0] < 0:
        raise ValueError('missing measurement')
    if scratch[0] == 0:
        return 0
    return math.isfinite(mean(scratch)) and scratch[0] > 1
"""
    behavioural_reads(code)
    facts = [{"notional_micro": v} for v in [2, -1, 0, 1, 3]]
    expected = [runner.run(code, item)[0] for item in facts]
    assert expected == [True, None, None, False, True]
    assert runner.run_batch(code, facts) == expected
    assert runner.run_batch(code, list(reversed(facts))) == list(reversed(expected))


@pytest.mark.gate
def test_attribution_timeout_preserves_aggregate_violation():
    runner = PredicateRunner()
    if not runner.available:
        pytest.skip("no jail on this host")
    rt = attribution_runtime(count=2)
    code = """def resolve(facts):
    if facts['notional_micro'] == 1:
        while True:
            pass
    return False
"""
    behavioural_reads(code)
    rt.predicates.register("timeout", "constraint", code,
                           facts={"notional_micro": 3}, persist=lambda _: None)
    rt.charter.cards = (replace(rt.charter.cards[0], holdout=("timeout@1",)),)
    rt.window.notional_micro = 3
    rt.card_samples.windows.clear()
    rt.card_samples.closed(rt.window)
    rt.predicate_runner = runner
    results = rt._holdout_results({"c": 0.8})
    assert results["c"]["results"] == {"timeout@1": False}
    assert results["c"]["violation"] == 0.02
    assert results["c"]["decision_results"] == {"timeout@1": {"0": None, "1": None}}
    rt.window.closed_holdout_attribution = rt._holdout_attribution(results)
    assert rt.window.closed_holdout_attribution["c"]["shares"] == {}
    rt._ledger_unattributed()
    row, = rt.ledger._recovery_items()
    assert row["kind"] == "price.unattributed"
    assert row["predicate"] == "timeout@1"
    assert row["reason"] == "no_supported_owner"
    assert json.dumps(results, allow_nan=False)
