import hashlib
import json
import random
from dataclasses import FrozenInstanceError, replace

import pytest

from factorylab.kernel.events import Bus
from factorylab.kernel.ledger import Ledger
from factorylab.kernel.queue import SettleStatus
from factorylab.kernel.termination import Termination
from factorylab.settlement import ForecastBook, open_forecast_decision


@pytest.mark.parametrize("q", [0, 0.0, 0.5, 1.0, 1])
def test_forecast_accepts_probability_endpoints_and_a_future_due_event(q, forecast_factory):
    forecast = forecast_factory(q=q)
    assert forecast.q == q
    assert forecast.due_at_event > forecast.made_at_event
    assert forecast.seal == ""


@pytest.mark.parametrize("q", [-0.1, 1.1, float("nan"), float("inf"), True, "0.5", None])
def test_forecast_rejects_invalid_probability(q, forecast_factory):
    with pytest.raises(ValueError):
        forecast_factory(q=q)


@pytest.mark.parametrize(
    ("made", "due"), [(0, 0), (5, 4), (-1, 1), (0, -1), (0.0, 1), (0, 1.0), (False, 1)]
)
def test_forecast_requires_integer_event_indices_and_future_settlement(made, due, forecast_factory):
    with pytest.raises(ValueError):
        forecast_factory(made_at_event=made, due_at_event=due)


@pytest.mark.parametrize(
    "changes",
    [
        {"handle": ""},
        {"evaluator_id": None},
        {"about_handle": ""},
        {"predicate_id": "untrusted"},
        {"params": {}},
        {"params": {"horizon_events": 0}},
        {"seal": None},
    ],
)
def test_invalid_commitments_are_rejected_before_sealing(changes, forecast_factory):
    with pytest.raises(ValueError):
        forecast_factory(**changes)


def test_canonical_seal_covers_all_fields_except_seal_and_uses_ledger_clock(
    clock, ledger, book, forecast_factory
):
    params = {"horizon_events": 10, "fraction": 0.2}
    forecast = forecast_factory(
        predicate_id="drawdown_exceeds", params=params, evaluator_id="judgé"
    )
    sealed = book.seal(forecast)
    other_ledger = Ledger(clock_ns=clock)
    clock.now = 500
    other = ForecastBook(other_ledger).seal(
        replace(forecast, params=dict(reversed(list(params.items()))), seal="ignored input seal")
    )
    assert sealed == other
    assert forecast.seal == ""
    payload = {**vars(forecast), "params": params}
    payload.pop("seal")
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    assert sealed.seal == hashlib.sha256(encoded).hexdigest()
    for store, expected_ts in ((ledger, 100), (other_ledger, 500)):
        assert not store.seal_key_released()
        with pytest.raises(PermissionError):
            store.decrypt_item(0)
        Termination(ledger=store, bus=Bus(store), clock_ns=clock).kill("test audit")
        entry = store.decrypt_item(0)
        assert entry == {
            **payload,
            "kind": "forecast.seal",
            "seal": sealed.seal,
            "ts": expected_ts,
            **{key: entry[key] for key in ("seq", "prev_hash", "hash")},
        }
        assert store.verify()


@pytest.mark.parametrize(
    "changes",
    [
        {"handle": "different"},
        {"evaluator_id": "judge-b"},
        {"about_handle": "producer-2"},
        {"predicate_id": "fill_within"},
        {"params": {"horizon_events": 20}},
        {"q": 0.25},
        {"made_at_event": 1},
        {"due_at_event": 20},
    ],
)
def test_every_committed_field_changes_the_digest(changes, forecast_factory, clock):
    first = ForecastBook(Ledger(clock_ns=clock)).seal(forecast_factory())
    second = ForecastBook(Ledger(clock_ns=clock)).seal(forecast_factory(**changes))
    assert first.seal != second.seal


def test_forecast_params_and_sealed_returns_cannot_change(forecast_factory, book):
    params = {"horizon_events": 10}
    forecast = forecast_factory(params=params)
    sealed = book.seal(forecast)
    params["horizon_events"] = 1
    assert sealed.params["horizon_events"] == forecast.params["horizon_events"] == 10
    with pytest.raises(TypeError):
        sealed.params["horizon_events"] = 1
    with pytest.raises(FrozenInstanceError):
        sealed.q = 1.0
    returned = book.due(10)
    returned.clear()
    assert book.due(10) == [sealed]


def test_due_uses_seal_order_and_counts_all_requested_forecasts(book, forecast_factory):
    assert book.requested("unknown") == book.outstanding() == 0
    later = book.seal(forecast_factory(handle="later", due_at_event=20))
    earlier = book.seal(forecast_factory(handle="earlier", evaluator_id="judge-b"))
    last = book.seal(forecast_factory(handle="last"))
    assert book.due(9) == []
    assert book.due(10) == [earlier, last]
    assert book.due(20) == [later, earlier, last]
    assert book.outstanding() == 3
    assert book.requested("judge-a") == 2
    assert book.requested("judge-b") == 1
    book.mark_settled(earlier.handle)
    book.mark_settled(earlier.handle)
    assert book.due(20) == [later, last]
    assert book.outstanding() == 2
    assert book.requested("judge-b") == 1
    with pytest.raises(KeyError):
        book.mark_settled("unknown")


def test_seal_retries_are_idempotent_and_conflicting_handles_fail(book, ledger, forecast_factory):
    forecast = forecast_factory()
    sealed = book.seal(forecast)
    assert book.seal(forecast) is sealed
    assert book.seal(sealed) is sealed
    with pytest.raises(ValueError, match="different commitment"):
        book.seal(replace(forecast, q=0.9))
    book.mark_settled(sealed.handle)
    assert book.seal(sealed) is sealed
    assert book.outstanding() == 0
    assert book.requested(sealed.evaluator_id) == 1
    assert book.due(100) == []
    assert ledger.append({"kind": "test.marker"}) == 1


def test_failed_ledger_append_does_not_admit_or_count_forecast(
    monkeypatch, book, ledger, forecast_factory
):
    def fail(entry):
        assert entry["kind"] == "forecast.seal"
        assert book.outstanding() == book.requested("judge-a") == 0
        raise OSError("injected write failure")

    with monkeypatch.context() as patch:
        patch.setattr(ledger, "append", fail)
        with pytest.raises(OSError, match="injected write failure"):
            book.seal(forecast_factory())
    assert book.outstanding() == book.requested("judge-a") == 0
    assert book.due(100) == []
    book.seal(forecast_factory())
    assert book.outstanding() == 1


@pytest.mark.parametrize("q", [0.0, 0.04, 0.25, 0.66, 1.0])
def test_helper_opens_replayable_one_hot_consequence_decision(q, queue, clock):
    handle = open_forecast_decision(
        queue,
        evaluator_id="judge",
        event_id="event-2",
        q=q,
        deadline_ns=clock.now + 50,
        parent_handle=None,
        now_event=2,
        horizon=10,
    )
    decision = queue.get(handle)
    bucket = str(round(q, 1))
    propensity = decision.propensity
    propensity.validate()
    assert propensity.action_ids == (bucket,)
    assert propensity.probs == (1.0,)
    assert propensity.chosen == bucket
    assert propensity.rng_seed == 0
    assert propensity.learner_id == decision.actor == "judge"
    assert propensity.learner_state_hash == hashlib.sha256(bucket.encode()).hexdigest()
    assert (
        random.Random(propensity.rng_seed).choices(
            propensity.action_ids, weights=propensity.probs, k=1
        )[0]
        == bucket
    )
    assert decision.channel == "consequence"
    assert type(decision.cost_ceiling) is int and decision.cost_ceiling == 0
    assert decision.deadline_ns == clock.now + 50
    assert decision.event_id == "event-2"
    assert decision.parent_handle is None
    assert decision.status == SettleStatus.PENDING
    assert queue.history(handle) == ()
    child = open_forecast_decision(
        queue,
        evaluator_id="judge",
        event_id="child",
        q=q,
        deadline_ns=clock.now + 50,
        parent_handle=handle,
        now_event=2,
        horizon=10,
    )
    assert queue.get(child).parent_handle == handle


@pytest.mark.parametrize(
    "changes",
    [
        {"q": -0.1},
        {"q": float("nan")},
        {"q": True},
        {"horizon": 0},
        {"horizon": True},
        {"horizon": 1.5},
        {"now_event": -1},
        {"now_event": False},
        {"now_event": 1.5},
        {"deadline_ns": 99},
        {"parent_handle": "missing"},
    ],
)
def test_helper_rejects_invalid_input_without_opening_a_decision(changes, queue):
    arguments = dict(
        evaluator_id="judge",
        event_id="event-2",
        q=0.5,
        deadline_ns=200,
        parent_handle=None,
        now_event=2,
        horizon=10,
    )
    arguments.update(changes)
    with pytest.raises(ValueError):
        open_forecast_decision(queue, **arguments)
    assert queue.outstanding() == []
