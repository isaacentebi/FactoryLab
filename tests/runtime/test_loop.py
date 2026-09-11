from factorylab.runtime.loop import run_world
from factorylab.runtime.worlds import load_manifest


def _covered_evaluators(standing: dict) -> list[str]:
    per = standing.get("evaluators", standing)
    return [e for e, v in per.items() if isinstance(v, dict) and v.get("settled", 0) > 0]


def test_scripted_world_phase2_spec_condition_2() -> None:
    m = load_manifest("scripted")
    s = run_world(m, events=400, seed=1)
    st = s["stats"]
    assert s["terminated"] is False
    assert s["wallet_conservation"] is True and s["ledger_verify"] is True
    # producers are judged, evaluators are judged, forecasts are sealed and settled
    assert st["verdicts"] >= 20 and st["conformities"] >= 10
    assert st["forecasts_sealed"] >= 20 and st["forecasts_settled"] >= 20
    assert st["max_settlement_latency_events"] >= 1
    # consequence standing exists with coverage for at least two evaluators
    assert len(_covered_evaluators(s["standing"])) >= 2
    # a scripted proposal registered a new assembly, opened an epoch, and it was invoked
    assert st["registrations_accepted"] >= 1 and st["epochs"] >= 1
    assert st["registrations_rejected"] >= 1  # the model proposal has no catalogue here
    counts = s["aggregates"]["invocations_by_assembly"]["counts"]
    assert counts.get("funding-watcher", 0) >= 1
    assert st["routers_replaced"] >= 1
    assert s["evaluation_boundary"] == "producer → evaluator → meta"
    assert st["sample_propensity"] is not None
    assert sum(s["aggregates"]["spend_by_capability"]["spend"].values()) > 0


def test_every_producer_decision_is_judged_or_censored() -> None:
    m = load_manifest("scripted")
    s = run_world(m, events=120, seed=4)
    st = s["stats"]
    judged = st["verdicts"] + st["censored"]
    assert judged + s["outstanding_decisions"] >= st["producer_returns"]


def test_scripted_world_starves_but_cannot_die_from_compute_alone() -> None:
    m = load_manifest("scripted")
    s = run_world(m, events=600, seed=2, initial_balance_micro=200_000, drip=False)
    assert s["terminated"] is False and 0 < s["wallet_balance_micro"] < 200_000
    assert s["stats"]["exclusions"] > 0


def test_crash_world_dies_and_releases_seal_spec_condition_3() -> None:
    m = load_manifest("scripted-crash")
    s = run_world(m, events=2000, seed=2)
    assert s["terminated"] is True and s["termination_reason"] == "balance_zero"
    assert s["seal_key_released"] is True
    assert s["wallet_balance_micro"] <= 0
    assert s["wallet_conservation"] is True


def test_determinism_same_seed_same_summary() -> None:
    m = load_manifest("scripted")
    a = run_world(m, events=60, seed=7)
    b = run_world(m, events=60, seed=7)
    a.pop("aggregates", None)
    b.pop("aggregates", None)
    assert a == b
