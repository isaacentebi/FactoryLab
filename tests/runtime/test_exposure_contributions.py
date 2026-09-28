"""Known-handle outcome evidence survives into charter pricing (§II.b/§IV.a)."""

import pytest

from factorylab.cortex.request import Return
from factorylab.kernel.queue import SettleStatus
from factorylab.runtime.shared import CH_EXPOSURE
from tests.runtime.test_evaluation_layer import _adversarial_runtime, _counter, _produce_hold
from tests.runtime.test_loop import _consequence_produce, lists_nothing
from tests.runtime.test_reward_chain import _horizon, _judge, _mids, _runtime


def _facts(rt, handle):
    # Isolate evidence ownership from the independent unhistoried-niche exemption.
    rt.window.decisions[handle].pop("niche", None)
    rt.card_samples.closed(rt.window)
    return rt._holdout_decision_facts()[handle]

@pytest.mark.parametrize(("verdict", "won"), [(0.9, 1), (0.1, 0)])
@pytest.mark.gate
def test_real_exposure_records_its_own_settlement_and_win(verdict, won):
    rt = _runtime(counterfactual={"coin": "BTC", "side": "buy"}, verdicts=(verdict,))
    _mids(rt, BTC="100")
    handle, event = _consequence_produce(rt, "antagonist-a", CH_EXPOSURE)
    _judge(rt, event)
    rt._settle_arrived_verdicts()
    _horizon(rt, BTC="101")
    assert rt.queue.history(handle)[0].status is SettleStatus.SETTLED
    assert rt.window.exposures_settled == 1
    assert rt.window.exposures_won == won
    sample = rt.window.decisions[handle]
    assert sample.get("exposures_settled") == 1
    assert sample.get("exposures_won", 0) == won
    rt._settle_exposures()
    assert sample["exposures_settled"] == 1
    facts = _facts(rt, handle)
    assert facts["exposures_settled"] == 1
    assert facts["exposures_won"] == won


@pytest.mark.parametrize(("seat", "channel"), [
    ("seed-decider", "verdict"), ("antagonist-a", CH_EXPOSURE),
])
@pytest.mark.gate
def test_real_unjudged_return_records_censored_outcome_once(seat, channel):
    rt = lists_nothing(_runtime())
    handle, _event = _consequence_produce(rt, seat, channel)
    rt.ticks_consumed += rt.ev.verdict_timeout_ticks + 1
    rt._settle_exposures()
    rt._censor_stale_judgements()
    assert rt.queue.history(handle)[0].status is SettleStatus.CENSORED
    assert rt.window.outcomes == rt.window.censored == 1
    sample = rt.window.decisions[handle]
    assert sample.get("supplemental", {}).get("outcomes") == 1
    assert sample.get("supplemental", {}).get("censored") == 1
    rt._settle_exposures()
    rt._censor_stale_judgements()
    assert sample["supplemental"] == {"outcomes": 1, "censored": 1}
    facts = _facts(rt, handle)
    assert facts["outcomes"] == facts["censored"] == 1


@pytest.mark.gate
def test_real_malformed_judgement_records_censored_outcome_once():
    rt = lists_nothing(_runtime())
    _producer, event = _consequence_produce(rt)
    handle = _judge(rt, event, returned=Return("ignored", {}, 0, "ok"))
    assert rt.queue.history(handle)[0].status is SettleStatus.CENSORED
    assert rt.window.outcomes == rt.window.censored == 1
    sample = rt.window.decisions.get(handle, {})
    assert sample.get("supplemental") == {"outcomes": 1, "censored": 1}
    rt._censor_judgement(handle, "already closed")
    assert sample["supplemental"] == {"outcomes": 1, "censored": 1}
    facts = _facts(rt, handle)
    assert facts["outcomes"] == facts["censored"] == 1


@pytest.mark.gate
def test_real_unscored_evaluator_records_censored_outcome():
    rt = lists_nothing(_runtime(verdicts=(0.9,)))
    _producer, event = _consequence_produce(rt)
    handle = _judge(rt, event)
    rt._settle_arrived_verdicts()
    rt.ticks_consumed += rt.ev.verdict_timeout_ticks + 1
    rt._settle_evaluations()
    assert rt.queue.history(handle)[0].status is SettleStatus.CENSORED
    assert rt.window.decisions[handle].get("supplemental") == {"outcomes": 1, "censored": 1}
    # Compatibility with retained same-handle forecast evidence: neither the
    # derived commitment nor the separately censored decision may overwrite the other.
    rt.card_samples.forecasts.append({
        "handle": handle, "owner_handle": handle, "assembly": "eval-a", "role": "evaluator",
        "subject_handle": _producer, "subject_assembly": "seed-decider",
        "subject_role": "producer", "window": rt.window.index, "skill": None,
        "predicate": "wallet_up", "y": None, "status": "censored",
        "verdict": None, "excluded": None,
    })
    facts = _facts(rt, handle)
    assert facts["outcomes"] == facts["censored"] == 2


@pytest.mark.gate
def test_real_counter_with_expired_world_measurement_records_censorship():
    rt = _adversarial_runtime(counterfactual={"coin": "BTC", "side": "buy"},
                              verdicts=(0.9,))
    _mids(rt, BTC="100")
    _producer, event = _produce_hold(rt)
    judge = _judge(rt, event, "eval-c")
    rt._settle_arrived_verdicts()
    handle = _counter(rt, judge, 0.1)
    assert handle in rt.pending_counters
    # No later market print arrives before the counter's world-measurement patience.
    rt.clock.now_ns += rt._patience_ns() + 1
    rt._settle_counters()
    assert rt.queue.history(handle)[0].status is SettleStatus.CENSORED
    assert rt.window.decisions[handle]["supplemental"] == {"outcomes": 1, "censored": 1}
    rt._settle_counters()
    facts = _facts(rt, handle)
    assert facts["outcomes"] == facts["censored"] == 1
