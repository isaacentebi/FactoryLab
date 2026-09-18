"""T21: only versioned, preflighted boolean predicates may resolve forecasts."""

import json

import pytest

from factorylab.cortex import sandbox
from factorylab.runtime.shared import PredicateRunner
from factorylab.settlement import vocabulary

FIRST = "def resolve(facts): return facts['fills'] > 0"
SECOND = "def resolve(facts): return facts['fills'] == 0"


def test_missing_resolution_facts_never_become_false():
    book = vocabulary.PredicateBook(run=lambda code, facts: (False, None))
    book.register("has-fill", "A fill occurred.", FIRST, facts={"fills": 0}, persist=lambda p: None)
    assert book.resolve("has-fill", {"horizon_events": 1}, None, version=1) == (
        None, "predicate facts unavailable")
    with pytest.raises(ValueError, match="unknown population predicate version"):
        book.resolve("has-fill", {"horizon_events": 1}, {"fills": 0}, version=2)


def test_observer_uses_the_forecasts_version_and_public_window_only():
    seen = []

    def run(code, facts):
        seen.append(facts)
        return code == FIRST, None

    book = vocabulary.PredicateBook(run=run)
    book.register("has-fill", "A fill occurred.", FIRST, facts={"fills": 1}, persist=lambda p: None)
    book.register("has-fill", "No fill occurred.", SECOND, facts={"fills": 1},
                  persist=lambda p: None)
    observer = vocabulary.Observer(book)
    facts = vocabulary.WindowFacts(10, 20, 5, (), public_window={"fills": 1})
    params = {"horizon_events": 1}
    assert observer.observe("has-fill", params, facts, version=1) == 1
    assert observer.observe("has-fill", params, facts, version=2) == 0
    assert seen[-2:] == [{"fills": 1}, {"fills": 1}]
    with pytest.raises(ValueError, match="bind a predicate version"):
        observer.observe("has-fill", params, facts)
    assert observer.observe("has-fill", params, vocabulary.WindowFacts(10, 20, 5, ()),
                            version=1) is None
    with pytest.raises(ValueError, match="unknown predicate"):
        vocabulary.Observer().observe("has-fill", params, facts, version=1)


@pytest.mark.parametrize("value", [1, 0, "true", None, [], {}])
def test_jail_output_must_be_a_boolean(monkeypatch, value):
    monkeypatch.setattr(sandbox, "jail_available", lambda: True)
    monkeypatch.setattr(sandbox, "run_python", lambda *a, **k: sandbox.SandboxResult(
        json.dumps({"value": value}), "", 0, False))
    assert PredicateRunner().run(FIRST, {"fills": 0}) == (
        None, "predicate must return a boolean")


def test_predicate_runner_never_executes_without_jail(monkeypatch):
    monkeypatch.setattr(sandbox, "jail_available", lambda: False)

    def forbidden(*args, **kwargs):
        pytest.fail("resolver dispatched without a jail")

    monkeypatch.setattr(sandbox, "run_python", forbidden)
    assert PredicateRunner().run(FIRST, {}) == (None, "no jail on this host")
