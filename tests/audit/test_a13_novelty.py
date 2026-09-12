"""A13: the novelty reserve lasts as long as the experiment (seat 2, finding 5; seat 4)."""

from dataclasses import replace

import pytest

from factorylab.cortex.registration import AssemblyProposal
from factorylab.cortex.request import Return
from factorylab.kernel.queue import PropensityRecord, SettleStatus
from factorylab.runtime.worlds import load_manifest
from tests.runtime.test_fidelity import decision, runtime

EXPLORER = AssemblyProposal(
    id="new-explorer", model_id="fake-haiku", role="producer", accepts=("Tick",),
    system_prompt="Observe.", max_tokens=128, effort="medium",
)


def _registered_runtime():
    rt = runtime()
    rt._manage_reserve_window()
    rt._register("author", EXPLORER)
    decision(rt, "new-explorer", settled=True)  # a settled verdict: history, not a trial
    assert rt.queue.has_history("new-explorer")
    return rt


def _deliver(rt, assembly="new-explorer", *, parent=None):
    """One top-level (or child) return of the assembly whose consequence settles now."""
    actor = "test-router" if parent is None else f"composition:{parent}"
    handle = rt.queue.open(
        actor=actor, event_id="test", channel="verdict", deadline_ns=10**18,
        parent_handle=parent, cost_ceiling=0,
        propensity=PropensityRecord((assembly,), (1.0,), assembly, 0, actor, "state"),
    )
    rt.handle_to_assembly[handle] = assembly
    rt.consequences.start(handle, rt.n)
    rt.consequences.finish(handle, 0)
    rt._settle_due_forecasts()
    return handle


def test_seat_2_scenario_stays_unhistoried_while_its_consequence_is_pending():
    rt = _registered_runtime()
    # Two invocations and a continuation: five model calls, no consequence yet.
    for _ in range(5):
        handle = decision(rt, "new-explorer")
        rt._invoke_compute("new-explorer", rt._request(handle, "Observe.", {}, {}, 10**18,
                                                       "verdict"))
    assert rt.stats.invocations_by_assembly["new-explorer"] == 5
    pending = decision(rt, "new-explorer")
    rt.handle_to_assembly[pending] = "new-explorer"
    rt.consequences.start(pending, rt.n)  # the experiment is open: no cost, no outcome
    assert rt._unhistoried("new-explorer")
    assert rt._novelty_compute(pending, "model:fake-haiku")
    for i in range(rt.m.novelty.trials):
        assert rt._unhistoried("new-explorer")
        _deliver(rt)
        assert rt.stats.consequences_by_assembly["new-explorer"] == i + 1
    assert not rt._unhistoried("new-explorer")
    assert not rt._novelty_compute(pending, "model:fake-haiku")


def test_continuations_and_children_are_not_trials():
    rt = _registered_runtime()
    parent = _deliver(rt)  # one delivered consequence for the parent return
    for _ in range(3):
        _deliver(rt, parent=parent)  # children answer under the parent's liability
    assert rt.stats.consequences_by_assembly["new-explorer"] == 1
    assert rt._unhistoried("new-explorer")


def test_lifetime_windows_end_the_trial_and_learning_death_extends_it():
    rt = _registered_runtime()
    born = rt.stats.registered_window["new-explorer"]
    assert born == rt.stats.reserve_windows
    rt.stats.reserve_windows = born + rt.m.novelty.max_lifetime_windows - 1
    assert rt._unhistoried("new-explorer")
    rt.stats.reserve_windows = born + rt.m.novelty.max_lifetime_windows
    assert not rt._unhistoried("new-explorer")
    rt.stats.reserve_windows = born
    for _ in range(rt.m.novelty.trials):
        _deliver(rt)
    assert not rt._unhistoried("new-explorer")
    rt.stats.pathologies["learning_death"] = True  # the flag now has a responder
    assert rt._unhistoried("new-explorer")
    _deliver(rt)
    assert not rt._unhistoried("new-explorer")
    # Seed assemblies with history are never protected, whatever the flag says.
    decision(rt, "seed-decider", settled=True)
    assert not rt._unhistoried("seed-decider")


def test_a_refused_duplicate_proposal_returns_its_trial_to_the_window():
    rt = runtime()
    rt._manage_reserve_window()
    rt._register("author", EXPLORER)
    before = rt.reserve.remaining()
    # The same id again: the reserve issues a receipt (no history yet), the registry
    # refuses the duplicate, and the receipt goes back to the window.
    with pytest.raises(ValueError, match="version"):
        rt._register("author", EXPLORER)
    assert rt.reserve.remaining() == before
    release = next(i for i in rt.ledger._recovery_items() if i["kind"] == "novelty.release")
    assert release["refunded"] and release["amount"] == rt.ev.trial_amount_micro
    assert release["contract_id"] == "new-explorer"
    # Through the return path the refusal is fed back and the share is still whole.
    proposal = {"kind": "assembly", "id": "new-explorer", "model_id": "fake-haiku",
                "role": "producer", "accepts": ["Tick"], "system_prompt": "Observe."}
    rt._apply_registrations("author", Return("author", {"register": [proposal]}, 0, "ok"))
    assert rt.reserve.remaining() == before
    assert rt.registration_feedback[-1]["handle"] == "author"
    assert rt.stats.registrations_rejected == 1
    assert rt.wallet.check_conservation()


def test_the_population_reads_the_lifetime_contract_and_the_old_key_is_gone():
    rt = runtime()
    world = rt._world_block()
    assert world["reserve"]["trials"] == 3 and world["reserve"]["max_lifetime_windows"] == 6
    assert "trial_invocations" not in world["reserve"]
    assert "settled consequences" in world["scoring"]["novelty_reserve"]
    raw = {"name": "x", "initial_balance_usd": "1", "novelty": {"trial_invocations": 3}}
    from factorylab.runtime.worlds import manifest_from_dict

    with pytest.raises(ValueError, match="trial_invocations was replaced by novelty.trials"):
        manifest_from_dict({**raw, "models": [{"id": "m", "input_usd_per_mtok": "1",
                                                "output_usd_per_mtok": "1"}]})
    for name in ("scripted", "testnet", "edition1-example"):
        manifest = load_manifest(name)
        assert (manifest.novelty.trials, manifest.novelty.max_lifetime_windows) == (3, 6)
    bad = replace(load_manifest("scripted"),
                  novelty=replace(load_manifest("scripted").novelty, max_lifetime_windows=0))
    with pytest.raises(ValueError, match="max_lifetime_windows"):
        bad.validate()


def test_a_judges_settled_payoff_forecast_counts_as_its_consequence():
    from tests.runtime.test_loop import _consequence_judge, _consequence_produce

    rt = runtime()
    rt._manage_reserve_window()
    rt._register("author", replace(EXPLORER, id="new-judge", role="evaluator",
                                   accepts=("ProducerReturn",)))
    about, event = _consequence_produce(rt, "NOOP")
    _consequence_judge(rt, event, "new-judge")
    assert rt.queue.get(about).status is SettleStatus.SETTLED
    assert rt.stats.consequences_by_assembly.get("new-judge") == 1
