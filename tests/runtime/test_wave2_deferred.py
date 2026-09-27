"""The Wave 2 review's deferred items, closed in Wave 5a.

Item 8b: an abstention priced on each role its draw could have woken is measured in
that role's scope, floor and attribution alike. Item 9: an old checkpoint carrying
the fields of deleted mechanisms (the grounded final judge, the fidelity queue, the
charter's weight on the outside signal, the sibling share) restores and drops them;
the state Wave 5a added defaults on its absence.
"""

from __future__ import annotations

import json

import pytest

from factorylab.runtime.feedback import exposure_score
from factorylab.runtime.shared import CH_CONFORMITY, CH_EXPOSURE
from factorylab.runtime.worlds import load_manifest
from tests.runtime.test_loop import _consequence_runtime
from tests.runtime.test_reward_chain import Population, _mids, _rows


def test_an_abstention_is_priced_in_each_menu_role_under_that_roles_scope(monkeypatch):
    """Item 8b: a less-weighted role's floor and attribution are that role's, never the
    role the window filed the abstention under."""
    from factorylab.runtime import pricing
    from tests.runtime.test_abstention_price import _abstention
    from tests.runtime.test_attributable_blame import _card, _commitments, _decision, _runtime

    monkeypatch.setattr(pricing, "close_window", lambda *_a: None)
    rt = _runtime(_card(per="role"))
    for seat in ("eval-a", "eval-b"):
        _decision(rt, seat)
    noop = _abstention(rt, role="producer")
    rt.window.decisions[noop]["menu_roles"] = {"producer": 0.75, "evaluator": 0.25}
    _commitments(rt, "eval-a", censored=4)
    rt._close_price_window()
    as_evaluator = rt._penalty_for("evaluator", noop, as_role="evaluator")
    filed = rt._penalty_for("evaluator", noop)
    assert as_evaluator > 0 and filed == 0.0
    charged = rt._priced_abstention(noop)
    assert charged == pytest.approx(0.75 * rt._penalty_for("producer", noop, as_role="producer")
                                    + 0.25 * as_evaluator)


def test_an_old_checkpoint_with_retired_fields_restores_and_drops_them():
    """Item 9: grounded pending, the weights' ``weight_sum``, the sibling share's
    runtime fields, retired pending channels and retired records, on an old checkpoint."""
    from factorylab.runtime.loop import Runtime
    from factorylab.runtime.resume import encode, restore_runtime, runtime_state

    rt = Runtime(load_manifest("scripted"), events=30, seed=1, initial_balance_micro=None,
                 ledger_path=None, router_gamma=0.1)
    rt.run()
    state = runtime_state(rt)
    runtime = state["runtime"]["$map"]
    # Retired runtime fields: the grounded judge, the fidelity queue, the sibling share.
    runtime.extend([["grounded_pending", encode({"decision-1": {"x": 1}})],
                    ["grounded_closed", encode(["decision-2"])],
                    ["open_adjudications", encode({})],
                    ["verdicts_graded", encode({"a": 1})],
                    ["pending_meta", encode({})]])
    # A pre-wave-5a exposure record (a bare float per judge) and no wave-5a fields.
    names = {k for k, _v in runtime}
    assert {"pending_counters", "judge_ordinary", "chaos_tick"} <= names
    runtime[:] = [[k, v] for k, v in runtime
                  if k not in ("pending_counters", "judge_ordinary", "chaos_tick")]
    legacy = {"$record": "PendingJudgement", "fields": {
        "handle": "decision-999", "channel": "verdict.norm", "opened_at_event": 1, "tier": 1,
        "opened_at_tick": 1, "judge": "x", "cards": "producer", "window": 1,
        "payoff_beat": None, "awaits_payoff": False, "verdict_closed": False,
        "verdict_beat": None, "graded": False, "unmeasured": False}}
    judge_wait = json.loads(json.dumps(legacy))
    judge_wait["fields"].update(handle="decision-998", channel="conformity")
    for key, value in runtime:
        if key == "pending":
            value["$map"].extend([["decision-999", legacy], ["decision-998", judge_wait]])
    # A retired receipt record decodes to nothing and is dropped where it sits.
    book = state["receipts"]["$map"][0][1]
    book.append({"$record": "Adjudication", "fields": {}})
    # The charter's weight on the outside signal (settlement.weights, ruling R1).
    standing = json.dumps(state["components"])
    assert "weight_sum" not in standing

    twin = Runtime(rt.m, ledger_path=None, **state["config"])
    restore_runtime(twin, state)
    for retired in ("grounded_pending", "grounded_closed", "open_adjudications",
                    "verdicts_graded", "pending_meta"):
        assert not hasattr(twin, retired), retired
    assert "decision-999" not in twin.pending  # a retired channel is dropped
    # An old judge wait carries no prediction: it is never scored, only censored.
    kept = twin.pending["decision-998"]
    assert kept.q is None and not kept.evaluation
    # New state defaults on absence.
    assert twin.pending_counters == {} and twin.judge_ordinary == {} and twin.chaos_tick == {}


def test_an_old_standing_with_weight_sum_restores_without_it():
    from factorylab.runtime.resume import decode

    record = {"$record": "_Standing", "fields": {"n": 2, "sum_brier": 1.5, "weight_sum": 3.0}}
    decoded = decode(record)
    assert (decoded.n, decoded.sum_brier) == (2, 1.5)
    assert not hasattr(decoded, "weight_sum")


def test_a_pre_centring_exposure_score_still_settles():
    rt = _consequence_runtime(provider=Population(counterfactual={"coin": "BTC", "side": "buy"},
                                                  verdicts=(0.9,)))
    rt._manage_reserve_window()
    _mids(rt, BTC="100")
    from tests.runtime.test_loop import _consequence_produce

    antagonist, _event = _consequence_produce(rt, "antagonist-a", CH_EXPOSURE)
    rt.exposure_scores[antagonist] = [0.3]  # a bare float, as a checkpoint before 5a held it
    rt.ticks_consumed += rt.ev.verdict_timeout_ticks + 1
    rt._settle_exposures()
    (row,) = _rows(rt, "exposure.settled", handle=antagonist)
    assert row["score"] == pytest.approx(exposure_score([0.3], [0.5]))
    assert CH_CONFORMITY  # the judges' channel is untouched by the antagonist's
