"""T21: only versioned, preflighted boolean predicates may resolve forecasts."""

import json

import pytest

from factorylab.cortex import sandbox
from factorylab.runtime.shared import PredicateRunner
from factorylab.settlement import vocabulary
from tests.cortex.test_jail import require_jail
from tests.helpers import parse


def test_population_predicate_versions_preserve_sealed_meanings():
    def run(code, facts):
        return facts["fills"] > 0 if code == FIRST else facts["fills"] == 0, None

    book = vocabulary.PredicateBook(run=run)
    committed = []
    first = book.register("has-fill", "A fill occurred.", FIRST, facts={"fills": 1},
                          persist=committed.append)
    second = book.register("has-fill", "No fill occurred.", SECOND, facts={"fills": 1},
                           persist=committed.append)
    assert (first.version, second.version) == (1, 2)
    assert committed == [first, second]
    assert book.resolve("has-fill", {"horizon_events": 1}, {"fills": 1}, version=1) == (True, None)
    assert book.resolve("has-fill", {"horizon_events": 1}, {"fills": 1}, version=2) == (False, None)


FIRST = "def resolve(facts): return facts['fills'] > 0"
SECOND = "def resolve(facts): return facts['fills'] == 0"


@pytest.mark.parametrize("value", [1, 0, "true", None, [], {}])
def test_nonboolean_preflight_never_admits_a_predicate(value):
    book = vocabulary.PredicateBook(run=lambda code, facts: (value, None))
    committed = []
    with pytest.raises(ValueError, match="boolean"):
        book.register("has-fill", "A fill occurred.", FIRST, facts={"fills": 1},
                      persist=committed.append)
    assert committed == []
    assert book.get("has-fill") is None


def test_failed_persistence_cannot_publish_a_predicate():
    book = vocabulary.PredicateBook(run=lambda code, facts: (True, None))

    def refuse(predicate):
        raise PermissionError("trial refused")

    with pytest.raises(PermissionError, match="trial refused"):
        book.register("has-fill", "A fill occurred.", FIRST, facts={"fills": 1}, persist=refuse)
    assert book.get("has-fill") is None


def test_predicate_registration_is_private_to_its_book():
    book = vocabulary.PredicateBook(run=lambda code, facts: (True, None))
    book.register("has-fill", "A fill occurred.", FIRST, facts={"fills": 1}, persist=lambda p: None)
    assert vocabulary.PredicateBook().get("has-fill") is None


@pytest.mark.parametrize("changes,reason", [
    ({"id": []}, "slug"), ({"id": "wallet_up"}, "cannot be redefined"),
    ({"id": "return_paid_off"}, "cannot be redefined"),
    ({"description": ""}, "description"), ({"description": "a" * 501}, "description"),
    ({"code": None}, "string"), ({"code": " " * 8001}, "exceeds"),
    ({"code": "def resolve(:"}, "resolve"),
    ({"code": "async def resolve(facts): return True"}, "resolve"),
    ({"params": {}}, "fields"),
])
def test_malformed_predicate_proposals_return_reasons(changes, reason):
    accepted, rejected = parse({"kind": "predicate", "id": "has-fill",
                                "description": "A fill occurred.", "code": FIRST, **changes})
    assert not accepted
    assert reason in rejected[0].reason


def test_no_jail_rejects_a_predicate_proposal():
    accepted, rejected = parse({"kind": "predicate", "id": "has-fill",
                                "description": "A fill occurred.", "code": FIRST}, jail=False)
    assert not accepted
    assert rejected[0].reason == "no jail on this host"


def test_predicate_preflight_needs_a_closed_window_and_records_its_failure():
    book = vocabulary.PredicateBook(run=lambda code, facts: (None, "timeout"))
    committed, preflights = [], []
    with pytest.raises(ValueError, match="no closed window"):
        book.register("has-fill", "A fill occurred.", FIRST, facts=None, persist=committed.append)
    with pytest.raises(ValueError, match="timeout"):
        book.register("has-fill", "A fill occurred.", FIRST, facts={"fills": 0},
                      persist=committed.append,
                      preflight=lambda p, value, error: preflights.append(
                          (p.version, value, error)))
    assert not committed and not book.registered
    assert preflights == [(1, None, "timeout")]


def test_missing_resolution_facts_never_become_false():
    book = vocabulary.PredicateBook(run=lambda code, facts: (False, None))
    book.register("has-fill", "A fill occurred.", FIRST, facts={"fills": 0}, persist=lambda p: None)
    assert book.resolve("has-fill", {"horizon_events": 1}, None, version=1) == (
        None, "predicate facts unavailable")
    with pytest.raises(ValueError, match="unknown population predicate version"):
        book.resolve("has-fill", {"horizon_events": 1}, {"fills": 0}, version=2)


def test_predicate_history_survives_json_round_trip():
    book = vocabulary.PredicateBook(run=lambda code, facts: (True, None))
    predicate = book.register("has-fill", "A fill occurred.", FIRST, facts={"fills": 1},
                              persist=lambda p: None, provenance="private-handle")
    restored = vocabulary.PredicateBook(json.loads(json.dumps(book.registered)),
                                        run=lambda code, facts: (True, None))
    assert restored.get("has-fill", 1) == predicate
    assert "private-handle" not in json.dumps(restored.catalogue())
    assert restored.catalogue()[-1]["version"] == 1
    assert restored.resolve("has-fill", {"horizon_events": 1}, {"fills": 1}, version=1) == (
        True, None)


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


def test_population_parameters_need_a_known_definition_and_exact_horizon():
    book = vocabulary.PredicateBook(run=lambda code, facts: (True, None))
    predicate = book.register("has-fill", "A fill occurred.", FIRST, facts={"fills": 1},
                              persist=lambda p: None)
    vocabulary._validate_params("has-fill", {"horizon_events": 1}, predicate=predicate)
    with pytest.raises(ValueError, match="unknown predicate"):
        vocabulary._validate_params("has-fill", {"horizon_events": 1})
    for params in ({}, {"horizon_events": True}, {"horizon_events": 1, "answer": True}):
        with pytest.raises(ValueError):
            vocabulary._validate_params("has-fill", params, predicate=predicate)


def test_predicate_runner_uses_observation_jail_limits(monkeypatch):
    from factorylab.runtime import observations

    monkeypatch.setattr(sandbox, "jail_available", lambda: True)
    calls = []

    def run(code, **kwargs):
        calls.append((code, kwargs))
        return sandbox.SandboxResult('{"value": false}', "", 0, False)

    monkeypatch.setattr(sandbox, "run_python", run)
    runner = PredicateRunner()
    assert runner.run(FIRST, {"fills": 0}) == (False, None)
    assert calls[0][0].startswith(FIRST)
    assert calls[0][1] == {
        "stdin": '{"fills": 0}', "timeout_s": observations.OBSERVATION_TIMEOUT_S,
        "cpu_s": observations.OBSERVATION_CPU_S, "max_output_bytes": 2000,
    }


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


def test_predicate_runner_jail_returns_true_and_false():
    require_jail()
    runner = PredicateRunner()
    assert runner.run(FIRST, {"fills": 1}) == (True, None)
    assert runner.run(FIRST, {"fills": 0}) == (False, None)
    value, error = runner.run("def resolve(facts): return 1", {})
    assert value is None and error


def test_public_facts_for_a_claim_begin_where_the_claim_was_sealed():
    """What a predicate reads is the window after the forecast, never the window before it."""
    from factorylab.runtime.observations import window_cursor, window_facts, window_facts_since
    from factorylab.runtime.pricing import MeasureWindow

    window = MeasureWindow(index=3, equity_start_micro=500)
    window.fills = 2
    window.costs.append(10)
    window.mids.append({"coin": "BTC", "ts_ns": 1, "value": 10})
    cursor = window_cursor(window)
    assert window_facts_since(window, cursor)["fills"] == 0
    window.fills += 1
    window.costs.append(20)
    window.mids.append({"coin": "BTC", "ts_ns": 2, "value": 20})
    since = window_facts_since(window, cursor)
    assert (since["fills"], since["costs"], since["mids"]) == (1, [20], {"BTC": [[2, 20]]})
    # The window's identity is fixed when it opens, so it is carried, not differenced.
    assert (since["index"], since["equity_start_micro"]) == (3, 500)
    # A later window opened after the claim, so all of it is already after the claim.
    assert window_facts_since(window, {**cursor, "index": 2}) == window_facts(window)
    assert window_facts_since(window, None) == window_facts(window)
