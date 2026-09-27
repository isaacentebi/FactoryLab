"""Founder-selected assembly endowments stay exact, funded and replayable."""

import json
from dataclasses import replace

import pytest

from factorylab.cortex.registration import AssemblyProposal, parse_proposals
from factorylab.cortex.request import ChildRequest
from factorylab.kernel.queue import PropensityRecord
from factorylab.kernel.wallet import Infeasible
from factorylab.runtime.loop import Runtime
from factorylab.runtime.resume import restore_runtime, runtime_state
from factorylab.runtime.shared import CH_VERDICT
from factorylab.runtime.worlds import load_manifest
from factorylab.world.exchange import FakeExchange
from factorylab.world.models import ModelResponse
from factorylab.world.scripted import ScriptedProvider
from tests.conftest import make_runtime
from tests.runtime.test_loop import lists_nothing

FOUNDER = "seed-decider"


def _handle(rt, *, seat=FOUNDER, event="founder-endowment", actor=None):
    actor = seat if actor is None else actor
    handle = rt.queue.open(
        actor=actor, event_id=event,
        propensity=PropensityRecord((seat,), (1.0,), seat, 0, actor, "fixed"),
        channel=CH_VERDICT, deadline_ns=10**18, parent_handle=None, cost_ceiling=20_000_000,
    )
    rt.handle_to_assembly[handle] = seat
    return handle


def _proposal(aid="founder-child", *, endowment_micro=None):
    return AssemblyProposal(
        aid, "producer", "fake-haiku", "Reply with JSON.", ("Tick",), 128, "low",
        endowment_micro=endowment_micro,
    )


def _runtime(*, novelty_share=None):
    if novelty_share is None:
        rt = make_runtime()
    else:
        manifest = load_manifest("scripted")
        manifest = replace(
            manifest, novelty=replace(manifest.novelty, share=novelty_share))
        rt = Runtime(
            manifest, events=0, seed=1, initial_balance_micro=100_000_000,
            ledger_path=None, router_gamma=.1,
            exchange=FakeExchange(), provider=ScriptedProvider(),
        )
    rt._manage_reserve_window()
    return rt


def test_exact_endowment_conserves_budget_and_ledgers_choice():
    rt = _runtime()
    handle = _handle(rt)
    amount = 1_234_567
    founder_before = rt.budget.entitlement(FOUNDER)
    child_before = rt.budget.entitlement("founder-child")
    rt._register(handle, _proposal(endowment_micro=amount))

    assert rt.budget.entitlement(FOUNDER) == founder_before - amount
    assert rt.budget.entitlement("founder-child") == child_before + amount
    assert rt.budget.lineage("founder-child") == rt.budget.lineage(FOUNDER)
    assert rt.budget.check_invariant()
    transfers = [row for row in rt.ledger._recovery_items()
                 if row["kind"] == "budget" and row["op"] == "transfer"
                 and row["dst"] == "founder-child"]
    assert transfers and transfers[-1]["amount"] == amount


def test_omitted_endowment_keeps_the_existing_trial_amount():
    rt = _runtime()
    handle = _handle(rt)
    founder_before = rt.budget.entitlement(FOUNDER)
    rt._register(handle, _proposal())
    assert rt.budget.entitlement(FOUNDER) == founder_before - rt.ev.trial_amount_micro
    assert rt.budget.entitlement("founder-child") == rt.ev.trial_amount_micro


def test_large_founder_endowment_uses_only_fixed_novelty_trial():
    rt = _runtime()
    handle = _handle(rt)
    trial = rt.ev.trial_amount_micro
    reserve_before = rt.reserve.remaining()
    amount = reserve_before + 1
    top_up = max(0, amount - rt.budget.entitlement(FOUNDER))
    if top_up:
        rt.budget.grant(FOUNDER, top_up, "test:founder-capital")
    assert amount > reserve_before
    rt._register(handle, _proposal(endowment_micro=amount))

    assert rt.budget.entitlement("founder-child") == amount
    assert rt.reserve.remaining() == reserve_before - trial
    assert rt.budget.check_invariant()


@pytest.mark.parametrize("value", [None, 0, -1, 1.5, True])
def test_endowment_requires_a_positive_exact_integer(value):
    rt = _runtime()
    item = {
        "kind": "assembly", "id": "founder-child", "model_id": "fake-haiku",
        "system_prompt": "Reply with JSON.", "accepts": ["Tick"],
        "endowment_micro": value,
    }
    accepted, rejected = parse_proposals(
        {"register": [item]}, event_kinds=rt._event_kinds(),
        known_models=frozenset(rt.prices.prices), known_assemblies=frozenset(rt.assemblies),
    )
    assert accepted == [] and rejected and "endowment_micro" in rejected[0].reason


def test_explicit_endowment_refuses_overspend_and_open_hold_without_mutation():
    rt = _runtime()
    handle = _handle(rt)
    before_registry = rt.registry.state()
    before_reserve = rt.reserve.state()
    with pytest.raises(Infeasible):
        rt._register(handle, _proposal(endowment_micro=rt.budget.entitlement(FOUNDER) + 1))
    assert rt.registry.state() == before_registry and rt.reserve.state() == before_reserve

    hold = rt._seat_wallet(FOUNDER).reserve(1_000, "founder-hold", "test:hold")
    try:
        with pytest.raises(Infeasible):
            rt._register(handle, _proposal(aid="held-child",
                                            endowment_micro=rt.budget.entitlement(FOUNDER) + 1))
        assert "held-child" not in rt.assemblies
        assert rt.budget.check_invariant()
    finally:
        rt._seat_wallet(FOUNDER).release(hold)


def test_full_novelty_reserve_does_not_block_backed_founder_endowment():
    from fractions import Fraction

    rt = _runtime(novelty_share=1)
    # A whole flow period's share accrued (time audit T6): the reserve holds everything.
    rt.clock.now_ns += 1
    rt.reserve.open_window(rt.clock.now_ns, max(0, rt.wallet.unlocked), accrued=Fraction(1))
    handle = _handle(rt)
    amount = 1_000
    founder_before = rt.budget.entitlement(FOUNDER)
    balance_before = rt.wallet.balance
    reserve_before = rt.reserve.remaining()
    assert rt.wallet.available == 0
    assert founder_before >= amount

    rt._register(handle, _proposal(endowment_micro=amount))

    assert rt.wallet.balance == balance_before
    assert rt.budget.entitlement(FOUNDER) == founder_before - amount
    assert rt.budget.entitlement("founder-child") == amount
    assert rt.reserve.remaining() == reserve_before - rt.ev.trial_amount_micro
    assert rt.budget.check_invariant()


def test_explicit_endowment_requires_known_founder_and_rejects_self_or_live_id():
    rt = _runtime()
    unknown = _handle(rt, seat="unknown-founder", event="unknown-founder", actor=FOUNDER)
    rt.handle_to_assembly.pop(unknown)
    before = rt.registry.state()
    with pytest.raises(Infeasible, match="known founder"):
        rt._register(unknown, _proposal(endowment_micro=1_000))
    assert rt.registry.state() == before

    founder = _handle(rt, event="self-founder")
    with pytest.raises(ValueError, match="cannot endow itself"):
        rt._register(founder, _proposal(aid=FOUNDER, endowment_micro=1_000))
    with pytest.raises(ValueError, match="live assembly"):
        rt._register(founder, _proposal(aid="eval-a", endowment_micro=1_000))


def test_endowment_replays_and_child_calls_charge_the_right_seat(monkeypatch):
    rt = lists_nothing(_runtime())
    handle = _handle(rt)
    amount = 2_000_000
    rt._register(handle, _proposal(endowment_micro=amount))
    state = runtime_state(rt)

    restored = lists_nothing(make_runtime())
    restore_runtime(restored, state)
    assert restored.budget.state() == rt.budget.state()
    assert restored.budget.check_invariant()
    duplicate = _handle(restored, event="duplicate-founder")
    founder_before = restored.budget.entitlement(FOUNDER)
    child_before = restored.budget.entitlement("founder-child")
    reserve_before = restored.reserve.state()
    with pytest.raises(ValueError, match="live assembly"):
        restored._register(duplicate, _proposal(endowment_micro=amount))
    assert restored.budget.entitlement(FOUNDER) == founder_before
    assert restored.budget.entitlement("founder-child") == child_before
    reserve_after = restored.reserve.state()
    assert reserve_after["remaining"] == reserve_before["remaining"]
    assert reserve_after["receipts"] == reserve_before["receipts"]

    parent = _handle(rt, event="parent-call")
    parent_before = rt.budget.entitlement(FOUNDER)
    child_before = rt.budget.entitlement("founder-child")
    monkeypatch.setattr(rt.provider.target, "complete", lambda request: ModelResponse(
        request.model_id, json.dumps({"action": "hold"}), 1, 1, "stop"))
    # A request names a kind (primitive audit F5). With the observer retired, the
    # founder's child is the one contract besides the founder that emits it.
    rt._retire_assembly("seed-observer", "test")
    child_result, child_cost = rt._invoke_child(
        FOUNDER, rt._request(parent, "child task", {}, {"type": "object"}, 10**18, CH_VERDICT),
        ChildRequest("ProducerReturn", "child task", {}, {"type": "object"}), 10**18)
    assert child_result["result"]["status"] == "ok" and child_cost > 0
    assert rt.budget.entitlement(FOUNDER) < parent_before
    assert rt.budget.entitlement("founder-child") == child_before
    commit = [row for row in rt.ledger._recovery_items()
              if row["kind"] == "budget" and row["op"] == "commit"]
    assert commit and commit[-1]["assembly_id"] == FOUNDER

    independent = _handle(rt, seat="founder-child", event="independent-child")
    child_before = rt.budget.entitlement("founder-child")
    rt._invoke("founder-child", rt._request(
        independent, "independent task", {}, {"type": "object"}, 10**18, CH_VERDICT), "producer")
    assert rt.budget.entitlement("founder-child") < child_before
    commit = [row for row in rt.ledger._recovery_items()
              if row["kind"] == "budget" and row["op"] == "commit"]
    assert commit[-1]["assembly_id"] == "founder-child"


def test_funded_child_has_more_than_one_configured_independent_reservation():
    rt = _runtime()
    handle = _handle(rt)
    rt._register(handle, _proposal(endowment_micro=5_000_000))
    child_handle = _handle(rt, seat="founder-child", event="child-reservation")
    request = rt._request(child_handle, "independent task", {}, {"type": "object"},
                          10**18, CH_VERDICT)
    reservation = rt.assemblies["founder-child"].model.ceiling(
        rt.assemblies["founder-child"].build_model_request(request))
    assert reservation > 0
    assert rt.budget.entitlement("founder-child") >= 2 * reservation


def test_same_lineage_children_do_not_add_a_commons_release_head():
    base = _runtime()
    base_grants = base.budget.commons_release("test:base")

    rt = _runtime()
    handle = _handle(rt)
    rt._register(handle, _proposal(aid="founder-child", endowment_micro=1_000))
    rt._register(handle, _proposal(aid="founder-child-two", endowment_micro=1_000))
    grants = rt.budget.commons_release("test:children")

    assert len(grants) == len(base_grants) == 9
    assert grants[FOUNDER] == base_grants[FOUNDER]
    lineage = rt.budget.lineages()[rt.budget.lineage(FOUNDER)]
    assert lineage["seats"] == [FOUNDER, "founder-child", "founder-child-two"]
