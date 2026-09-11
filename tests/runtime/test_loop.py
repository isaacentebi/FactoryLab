from factorylab.runtime.loop import run_world
from factorylab.runtime.worlds import load_manifest


def test_scripted_world_closes_the_loop_spec_condition_2() -> None:
    m = load_manifest("scripted")
    s = run_world(m, events=200, seed=1)
    st = s["stats"]
    assert s["terminated"] is False
    assert st["invocations"] >= 1 and s["stats"]["sample_propensity"] is not None
    assert st["consequence_settlements"] >= 1
    assert st["max_settlement_latency_events"] >= 10
    assert s["wallet_conservation"] is True and s["ledger_verify"] is True
    agg = s["aggregates"]
    assert sum(agg["invocations_by_assembly"]["counts"].values()) == st["invocations"]
    assert (
        agg["settlement_latency"]["count"] == st["fast_settlements"] + st["consequence_settlements"]
    )
    # every invocation was metered: spend recorded against chosen capabilities
    assert sum(agg["spend_by_capability"]["spend"].values()) > 0


def test_scripted_world_starves_but_cannot_die_from_compute_alone() -> None:
    m = load_manifest("scripted")
    s = run_world(m, events=600, seed=2, initial_balance_micro=200_000, drip=False)
    # metering never reserves beyond the balance: the wallet starves toward zero
    assert s["terminated"] is False and 0 < s["wallet_balance_micro"] < 200_000
    assert s["stats"]["exclusions"] > 0  # assemblies became infeasible and were logged as such


def test_crash_world_dies_and_releases_seal_spec_condition_3() -> None:
    m = load_manifest("scripted-crash")
    s = run_world(m, events=2000, seed=2)
    assert s["terminated"] is True and s["termination_reason"] == "balance_zero"
    assert s["seal_key_released"] is True
    assert s["wallet_balance_micro"] <= 0
    assert s["wallet_conservation"] is True
    assert s["stats"]["events"] < 2000 + 600  # died well before the run ended


def test_determinism_same_seed_same_summary() -> None:
    m = load_manifest("scripted")
    a = run_world(m, events=60, seed=7)
    b = run_world(m, events=60, seed=7)
    a.pop("aggregates", None)
    b.pop("aggregates", None)
    assert a == b
