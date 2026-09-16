"""A13: the novelty reserve lasts as long as the experiment (seat 2, finding 5; seat 4)."""

from dataclasses import replace

import pytest

from factorylab.cortex.registration import AssemblyProposal
from factorylab.cortex.request import Return
from factorylab.kernel.queue import PropensityRecord, SettleStatus
from factorylab.runtime.pricing import MeasureWindow
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


def _boundary(rt, *, registrations=0):
    """Cross one reserve-window boundary; the window that closes shows the given activity."""
    rt.n += 10
    rt.window = MeasureWindow(rt.stats.reserve_windows, rt.wallet.balance, invocations=10,
                              ok=10, registrations=registrations)
    rt.clock.now_ns += rt.m.novelty.window_ns
    rt._manage_reserve_window()


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
    for _ in range(rt.m.immune.k):  # k quiet windows: learning death, and a responder
        _boundary(rt)
    assert rt.stats.pathologies["learning_death"]
    assert rt._unhistoried("new-explorer")
    _deliver(rt)
    assert not rt._unhistoried("new-explorer")
    # Seed assemblies with history are never protected, whatever the flag says.
    decision(rt, "seed-decider", settled=True)
    assert not rt._unhistoried("seed-decider")


def test_learning_death_grant_is_spent_once_per_window_and_reissued_only_by_the_flag():
    rt = _registered_runtime()
    for _ in range(rt.m.novelty.trials):
        _deliver(rt)
    assert not rt._unhistoried("new-explorer")
    for _ in range(rt.m.immune.k - 1):
        _boundary(rt)
        assert not rt.stats.pathologies["learning_death"]
        assert rt.novelty_grant == {"window": None, "consumed": []}
    _boundary(rt)  # the k-th quiet window flags learning death at this boundary
    assert rt.stats.pathologies["learning_death"]
    granted = rt.stats.reserve_windows
    assert rt.novelty_grant == {"window": granted, "consumed": []}
    assert rt._unhistoried("new-explorer")
    _deliver(rt)  # the one extra trial
    assert rt.novelty_grant == {"window": granted, "consumed": ["new-explorer"]}
    assert not rt._unhistoried("new-explorer")
    _deliver(rt)  # nothing further to spend in this window
    assert not rt._unhistoried("new-explorer")
    items = rt.ledger._recovery_items()
    assert [i["window"] for i in items if i["kind"] == "novelty.grant"] == [granted]
    consumed = [i for i in items if i["kind"] == "novelty.grant_consumed"]
    assert [(i["assembly"], i["window"]) for i in consumed] == [("new-explorer", granted)]
    # Still flagged at the next boundary: the grant is issued again, unspent.
    _boundary(rt)
    assert rt.stats.pathologies["learning_death"]
    assert rt.novelty_grant == {"window": granted + 1, "consumed": []}
    assert rt._unhistoried("new-explorer")
    assert rt.stats.consequences_by_assembly["new-explorer"] == rt.m.novelty.trials + 2


def test_an_unused_learning_death_grant_expires_at_the_next_boundary():
    rt = _registered_runtime()
    for _ in range(rt.m.novelty.trials):
        _deliver(rt)
    for _ in range(rt.m.immune.k):
        _boundary(rt)
    granted = rt.stats.reserve_windows
    assert rt.novelty_grant["window"] == granted and rt._unhistoried("new-explorer")
    # A window with a registration clears the flag; the unspent grant lapses with it.
    _boundary(rt, registrations=1)
    assert not rt.stats.pathologies["learning_death"]
    assert rt.novelty_grant == {"window": None, "consumed": []}
    assert not rt._unhistoried("new-explorer")
    assert rt.stats.consequences_by_assembly["new-explorer"] == rt.m.novelty.trials
    assert not [i for i in rt.ledger._recovery_items() if i["kind"] == "novelty.grant_consumed"]
    # The grant is not a permanent base + 1: it is not carried over the boundary.
    _deliver(rt)
    assert not rt._unhistoried("new-explorer")
    # Persisted through the resume codec.
    from factorylab.runtime.resume import _RUNTIME_FIELDS

    assert "novelty_grant" in _RUNTIME_FIELDS


def test_a_refused_duplicate_proposal_returns_its_trial_to_the_window():
    rt = runtime()
    rt._manage_reserve_window()
    rt._register("author", EXPLORER)
    before = rt.reserve.remaining()
    # The same id again: the reserve issues a receipt (no history yet), the duplicate is
    # refused (A1 re-registers an id only after retirement), and the receipt goes back.
    with pytest.raises(ValueError, match="already registered"):
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
    # A8: the public feedback carries the reason, never the proposing handle.
    assert "handle" not in rt.registration_feedback[-1]
    assert rt.registration_feedback[-1]["reason"]
    rejected = [i for i in rt.ledger._recovery_items() if i["kind"] == "registration.rejected"]
    assert rejected[-1]["handle"] == "author"
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
    # Restated for R3-D: the subject has to have committed to something, or the
    # judgement of it concludes unmeasured and settles no consequence at all
    # (GPT-6 third reading §6.B). The router's own abstention commits to nothing.
    about, event = _consequence_produce(rt)
    _consequence_judge(rt, event, "new-judge")
    assert rt.queue.get(about).status is SettleStatus.SETTLED
    assert rt.stats.consequences_by_assembly.get("new-judge") == 1


def test_an_assembly_that_never_settles_anything_still_ends_at_its_lifetime():
    """Codex finding: the no-history early return came before the lifetime check, so a
    registered assembly that never settled anything drew protected compute for ever.
    Absence of history is the start of a trial, not an exemption from its end."""
    rt = runtime()
    rt._manage_reserve_window()
    rt._register("author", EXPLORER)
    born = rt.stats.registered_window["new-explorer"]
    assert not rt.queue.has_history("new-explorer") and rt._unhistoried("new-explorer")
    rt.stats.reserve_windows = born + rt.m.novelty.max_lifetime_windows + 1
    assert not rt.queue.has_history("new-explorer")
    assert not rt._unhistoried("new-explorer")
    pending = decision(rt, "new-explorer")
    assert not rt._novelty_compute(pending, "model:fake-haiku")
    assert rt.wallet.available_for(pending, "model:fake-haiku") == rt.wallet.available
    # A seed assembly has no registration window: it is protected until its first record.
    assert rt._unhistoried("seed-decider")
    decision(rt, "seed-decider", settled=True)
    assert not rt._unhistoried("seed-decider")
