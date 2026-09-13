"""The essay's casts have observable purchase on live behaviour."""

from dataclasses import replace
from random import Random

import pytest

from factorylab.charter.charter import Charter, MetricCard
from factorylab.charter.windows import MetricWindow
from factorylab.kernel.queue import PropensityRecord, SettleStatus
from factorylab.kernel.wallet import Infeasible
from factorylab.runtime.loop import Runtime
from factorylab.runtime.pricing import MeasureWindow
from factorylab.runtime.worlds import load_manifest


def runtime(**changes):
    seed = load_manifest("scripted")
    # These controller tests explicitly select a single complete reserve window.
    charter = replace(seed.charter, cards=tuple(replace(c, window=MetricWindow("windows", 1, None))
                                               for c in seed.charter.cards))
    manifest = replace(seed, charter=charter, **changes)
    return Runtime(manifest, events=1, seed=1, initial_balance_micro=None,
                   ledger_path=None, drip=False, router_gamma=0.1)


def decision(rt, assembly, *, settled=False):
    handle = rt.queue.open(
        actor="test-router", event_id="test", channel="verdict", deadline_ns=10**18,
        parent_handle=None, cost_ceiling=rt.wallet.balance,
        propensity=PropensityRecord((assembly,), (1.0,), assembly, 0, "test-router", "state"),
    )
    rt.handle_to_assembly[handle] = assembly
    if settled:
        child = rt.queue.open(
            actor=assembly, event_id="consequence", channel="consequence", deadline_ns=10**18,
            parent_handle=handle, cost_ceiling=0,
            propensity=PropensityRecord((assembly,), (1.0,), assembly, 0, assembly, "test"),
        )
        rt.queue.settle(child, channel="consequence", score=0.5, status=SettleStatus.SETTLED,
                        definition_version="test", sampling_ref=None)
        rt.queue.settle(handle, channel="verdict", score=0.5, status=SettleStatus.SETTLED,
                        definition_version="test", sampling_ref=None)
    return handle


def close(rt, value, *, registrations=1):
    rt.n += 10
    rt.window = MeasureWindow(rt.n, rt.wallet.balance, invocations=10,
                              ok=int(value * 10), registrations=registrations)
    rt._close_price_window()


def test_population_authored_price_changes_a_settled_reward():
    """II.IV.a: a factory's speculative shadow price must affect what it earns."""
    rt = runtime()
    rt._manage_reserve_window()
    for assembly in rt.assemblies:
        for _ in range(5):
            decision(rt, assembly, settled=True)
    rt._propose_amendment("author", {
        "id": "population-quality",
                    "predicted_effect": {"card_id": "cost_per_return", "direction": "decrease",
                    "window": 1},
        "replace": [{"id": "well_formed_rate", "norm": rt.charter.norms[0],
                 "description": "Quality observed in this world.", "units": "fraction",
                 "window": {"kind": "windows", "n": 1, "per": None},
                    "acceptable_region": "at least 0.9",
                 "observation": "well_formed_rate", "answers_for": "producer", "lambda": 0.5}],
    })
    rt.clock.now_ns += (rt.m.timing.min_ratio * rt.ev.consequence_backstop_events
                        * rt.tick_clock.interval_ns)
    rt.n = rt.cadence.earliest_event()
    rt.cadence.advance(rt.n)
    rt._activate_charter_if_due()
    assert rt.charter.edition == 2
    rt.card_samples.values = {c.id: 0.5 for c in rt.charter.cards}
    handle = decision(rt, "seed-decider")
    rt._contribution(handle, "producer").update(invocations=1, ok=0)
    rt._settle_priced(handle, channel="verdict", score=0.8,
                      definition_version="test", sampling_ref=None, cards="producer")
    earned = rt.queue.returns_for("test-router")[-1].score
    # Runtime regions normalize the deficit by the card's own 0.9 bound.
    assert earned == pytest.approx(0.8 - 0.5 * (0.9 - 0.5) / 0.9)


def test_incumbent_cannot_spend_the_frontiers_compute():
    """II.II.b: compute usable only for unhistoried actions remains affordable to them."""
    rt = runtime()
    rt._manage_reserve_window()
    incumbent = decision(rt, "seed-decider", settled=True)
    protected = rt.reserve.remaining()
    ordinary = rt.wallet.reserve(rt.wallet.balance - protected, incumbent, "model:fake-opus")
    rt.wallet.commit(ordinary, ordinary.amount)
    with pytest.raises(Infeasible):
        rt.wallet.reserve(1, incumbent, "model:fake-opus")
    fresh = decision(rt, "seed-observer")
    hold = rt.wallet.reserve(protected, fresh, "model:fake-haiku")
    rt.wallet.commit(hold, protected // 2)
    assert rt.reserve.remaining() == protected - protected // 2
    assert rt.wallet.available == 0
    assert rt.wallet.check_conservation()


def test_stable_failure_ratchets_gain_live():
    """II.IV.b: persistent failure must ratchet up the available gain."""
    rt = runtime()
    rt._derive_regions()
    rt.controller.set_price("well_formed_rate", 0.2, amendment_id="test")
    router = rt.routers["Tick"][0]
    for _ in range(3):
        close(rt, 0.2)
    assert router.learner.gamma > 0.1
    assert rt._world_block()["pathologies"]["stable_failure"]


def test_thrash_lowers_gain_live_and_temporarily_increases_decay():
    """II.IV.b: excessive gain must not keep amplifying oscillation."""
    rt = runtime()
    rt._derive_regions()
    router = rt.routers["Tick"][0]
    router.learner.gamma = 0.5
    for i in range(8):
        close(rt, .2, registrations=3 * (i % 2))
    assert 0.1 <= router.learner.gamma < 0.5
    assert rt._world_block()["pathologies"]["thrash"]
    assert rt.controller.snapshot()["parameters"]["decay"] > rt.m.prices.decay


def test_registration_flood_does_not_change_committee_draw():
    """II.IV.a: a rotated delegation represents stakeholders, not empty registrations."""
    from factorylab.cortex.registration import AssemblyProposal

    rt = runtime()
    rt._manage_reserve_window()
    for assembly in rt.assemblies:
        for _ in range(5):
            decision(rt, assembly, settled=True)
    item = {"id": "before-flood", "tick_interval": "2s",
                    "predicted_effect": {"card_id": "cost_per_return", "direction": "decrease",
                    "window": 1}}
    rt.rng = Random(3)
    rt._propose_amendment("author", item)
    first = rt.charter_book._CharterBook__committees[item["id"]]
    for i in range(40):
        rt._register("author", AssemblyProposal(
            id=f"flood-{i}", model_id="fake-haiku", role="producer", accepts=("Tick",),
            system_prompt="Observe.", max_tokens=128, effort="medium",
        ))
    rt.rng = Random(3)
    rt._propose_amendment("author", {**item, "id": "after-flood"})
    second = rt.charter_book._CharterBook__committees["after-flood"]
    assert first.seats == second.seats


def test_role_field_is_required_and_validated_in_population_cards():
    rt = runtime()
    base = dict(vars(rt.charter.cards[1]), id="new-quality")
    for value in (None, True, "unknown", ""):
        card = {**base, "answers_for": value}
        with pytest.raises(ValueError, match="answers_for"):
            rt._propose_amendment("author", {"id": "bad-card", "add": [card],
                                             "predicted_effect": {"card_id": "cost_per_return",
                   "direction": "decrease",
                    "window": 1}})


def test_role_prices_are_not_card_id_conventions():
    rt = runtime()
    rt.charter = Charter(1, rt.charter.norms, tuple(
        MetricCard(f"card-{role}", rt.charter.norms[0], "d", "fraction",
                   MetricWindow("windows", 1, None),
                   "above 0.9", "well_formed_rate", answers_for=role)
        for role in ("producer", "evaluator", "meta", "all")
    ))
    rt._derive_regions()
    rt.card_samples.values = {c.id: 0.5 for c in rt.charter.cards}
    for card in rt.charter.cards:
        rt.controller.set_price(card.id, 0.25, amendment_id="test")
    for role in ("producer", "evaluator", "meta"):
        assert rt._penalty_for(role) == pytest.approx(2 * 0.25 * (0.9 - 0.5) / 0.9)


def test_new_assembly_keeps_protected_compute_until_its_consequences_settle():
    from factorylab.cortex.registration import AssemblyProposal

    rt = runtime()
    rt._manage_reserve_window()
    rt._register("author", AssemblyProposal(
        id="new-explorer", model_id="fake-haiku", role="producer", accepts=("Tick",),
        system_prompt="Observe.", max_tokens=128, effort="medium",
    ))
    decision(rt, "seed-decider", settled=True)
    incumbent = decision(rt, "seed-decider")
    hold = rt.wallet.reserve(rt.wallet.available, incumbent, "model:fake-opus")
    rt.wallet.commit(hold, hold.amount)
    assert rt.wallet.available == 0
    # An early settled verdict must not erase protection: trials are settled consequences.
    decision(rt, "new-explorer", settled=True)
    assert rt._is_feasible("new-explorer")[0]
    assert not rt._is_feasible("seed-decider")[0]
    for _ in range(5):  # invocations, continuations included, are not trials (A13)
        handle = decision(rt, "new-explorer")
        req = rt._request(handle, "Observe.", {}, {}, 10**18, "verdict")
        before = rt.reserve.remaining()
        result = rt._invoke("new-explorer", req, "producer")
        assert result.cost > 0
        assert rt.reserve.remaining() == before - result.cost
        assert rt.wallet.available == 0
    assert rt._is_feasible("new-explorer")[0]
    for _ in range(rt.m.novelty.trials):
        handle = decision(rt, "new-explorer")
        rt.handle_to_assembly[handle] = "new-explorer"
        rt.consequences.start(handle, rt.n)
        rt.consequences.finish(handle, 0)
        rt._settle_due_forecasts()
    assert rt.stats.consequences_by_assembly["new-explorer"] == rt.m.novelty.trials
    assert not rt._is_feasible("new-explorer")[0]
    handle = decision(rt, "new-explorer")
    req = rt._request(handle, "Observe.", {}, {}, 10**18, "verdict")
    assert rt._invoke("new-explorer", req, "producer").cost == 0
    assert rt.wallet.check_conservation()


def test_missing_proposal_role_is_rejected_before_spending_write_access():
    rt = runtime()
    rt._manage_reserve_window()
    card = dict(vars(rt.charter.cards[1]), id="new-quality")
    del card["answers_for"]
    before = rt.reserve.remaining()
    with pytest.raises(ValueError, match="answers_for"):
        rt._propose_amendment("author", {"id": "bad-card", "add": [card],
                                         "predicted_effect": {"card_id": "cost_per_return",
                   "direction": "decrease",
                    "window": 1}})
    assert rt.reserve.remaining() == before


def test_detection_needs_persistent_supported_failure_and_resets_on_compliance():
    rt = runtime()
    rt._derive_regions()
    for value in (0.2, 1, 0.2):
        close(rt, value)
    assert not rt.stats.pathologies["stable_failure"]
    for _ in range(3):
        close(rt, 1, registrations=0)
    assert rt.stats.pathologies["learning_death"]
    assert not rt.stats.pathologies["stable_failure"]
    before = rt.routers["Tick"][0].learner.gamma
    close(rt, 1, registrations=0)
    assert rt.routers["Tick"][0].learner.gamma == before


def test_immune_gain_is_bounded_and_decay_expires_after_one_window():
    rt = runtime()
    rt._derive_regions()
    for _ in range(30):
        close(rt, 0.2)
    assert all(st.learner.gamma == rt.m.immune.gamma_max for st in rt._all_router_states())
    for i in range(12):
        close(rt, .2, registrations=3 * (i % 2))
    assert rt.stats.pathologies["thrash"]
    # A constant compliant stretch clears sustained changes; extra decay is removed.
    for _ in range(12):
        close(rt, 1)
    assert not rt.stats.pathologies["thrash"]
    assert rt.controller.snapshot()["parameters"]["decay"] == rt.m.prices.decay
    assert all(st.learner.gamma >= st.seed_gamma for st in rt._all_router_states())


def test_live_immune_actions_are_ledger_first_and_do_not_read_diary(monkeypatch):
    rt = runtime()
    rt._derive_regions()
    for _ in range(2):
        close(rt, 0.2)
    append = rt.ledger.append
    entries = []

    def unreadable(*args, **kwargs):
        raise AssertionError("a live immune organ cannot read the diary")

    monkeypatch.setattr(rt.ledger, "decrypt_item", unreadable)

    def record(entry):
        if entry["kind"] == "immune.gain":
            router = next(r for r in rt._all_router_states() if r.learner.id == entry["router"])
            assert router.learner.gamma == entry["gamma_before"][0]
        entries.append(entry)
        return append(entry)

    monkeypatch.setattr(rt.ledger, "append", record)
    close(rt, 0.2)
    kinds = [e["kind"] for e in entries]
    assert kinds.index("pathology.stable_failure") < kinds.index("immune.gain")
    assert "immune.window" in kinds
    assert rt.ledger.verify()


def test_immune_and_novelty_state_survive_checkpoint_without_changing_future():
    from factorylab.runtime.resume import restore_runtime, runtime_state

    original = runtime()
    original._manage_reserve_window()
    for _ in range(3):
        close(original, 0.2)
    restored = runtime()
    restore_runtime(restored, runtime_state(original))
    for i in range(10):
        for rt in (original, restored):
            close(rt, .2, registrations=3 * (i % 2))
    assert original.stats == restored.stats
    assert original.controller.snapshot() == restored.controller.snapshot()
    assert [s.state() for s in original._all_router_states()] == [
        s.state() for s in restored._all_router_states()
    ]
    assert original.wallet.state() == restored.wallet.state()
    assert original.reserve.state() == restored.reserve.state()


def test_immune_adjusts_swap_router_rows_without_losing_delayed_decisions():
    from factorylab.runtime.immune import gamma

    rt = runtime()
    rt._derive_regions()
    router = rt._build_router("Tick", "blum_mansour", 0.1)
    router.learner.current_key = "pending-route"
    distribution = router.learner.distribution(router.universe)
    saved = router.learner.state()["inner"]["snapshots"]
    for _ in range(3):
        close(rt, 0.2)
    assert gamma(router.learner) > 0.1
    assert router.learner.state()["inner"]["snapshots"] == saved
    assert sum(distribution.values()) == pytest.approx(1)


@pytest.mark.parametrize("mode", ["direct", "market-tool", "limit-tool"])
def test_orders_cannot_commit_protected_novelty_collateral(mode):
    from factorylab.cortex.request import Return

    rt = runtime()
    rt._manage_reserve_window()
    incumbent = decision(rt, "seed-decider", settled=True)
    rt.consequences.start(incumbent, 0)
    hold = rt.wallet.reserve(rt.wallet.available, incumbent, "model:fake-opus")
    rt.wallet.commit(hold, hold.amount)
    before = rt.exchange.account().positions
    if mode == "direct":
        rt._execute_outputs(Return(incumbent, {
            "action": "order", "coin": "BTC", "side": "buy", "size": "0.0001",
        }, 0, "ok"))
        assert rt.stats.orders_rejected == 1
    else:
        args = {"coin": "BTC", "side": "buy", "size": "0.0001"}
        if mode == "limit-tool":
            args["price"] = str(rt.exchange.mids()["BTC"] / 2)
        result, cost = rt._run_tool("seed-decider", incumbent, {
            "tool": "venue.place_limit" if mode == "limit-tool" else "venue.place_market",
            "args": args,
        })
        assert result["status"] == "rejected" and cost == 0
    assert rt.exchange.account().positions == before
    assert rt.exchange.open_orders() == []
    assert rt.wallet.available == 0


def test_learning_death_cannot_be_hidden_by_naming_a_card_registrations():
    rt = runtime()
    rt.charter = Charter(1, rt.charter.norms, (
        MetricCard("registrations", rt.charter.norms[0], "quality", "fraction",
                   MetricWindow("windows", 1, None),
                   "above 0.9", "well_formed_rate", answers_for="all"),
    ))
    rt._derive_regions()
    for _ in range(3):
        close(rt, 0.2, registrations=0)
    assert rt.stats.pathologies["stable_failure"]
    assert rt.stats.pathologies["learning_death"]


def test_gamma_write_failure_leaves_router_unchanged(monkeypatch):
    rt = runtime()
    rt._derive_regions()
    for _ in range(2):
        close(rt, 0.2)
    append = rt.ledger.append
    before = [s.state() for s in rt._all_router_states()]

    def reject(entry):
        if entry["kind"] == "immune.gain":
            raise RuntimeError("ledger unavailable")
        return append(entry)

    monkeypatch.setattr(rt.ledger, "append", reject)
    with pytest.raises(RuntimeError, match="ledger unavailable"):
        close(rt, 0.2)
    assert before == [s.state() for s in rt._all_router_states()]


def test_sortition_threshold_counts_settled_decisions_only():
    rt = runtime()
    for _ in range(rt.m.committee.min_settled - 1):
        decision(rt, "seed-decider", settled=True)
    assert "seed-decider" not in rt._committee_eligible()
    for status in (SettleStatus.CENSORED, SettleStatus.INAPPLICABLE):
        handle = decision(rt, "seed-decider")
        rt.queue.settle(handle, channel="verdict", score=0.0, status=status,
                        definition_version="test", sampling_ref=None)
    decision(rt, "seed-decider")  # An outstanding decision is not experience either.
    assert "seed-decider" not in rt._committee_eligible()
    decision(rt, "seed-decider", settled=True)
    assert rt._committee_eligible() == {"seed-decider": "producer"}


def test_order_protection_uses_acknowledged_leverage_not_the_maximum():
    from decimal import Decimal

    rt = runtime()
    rt._manage_reserve_window()
    handle = decision(rt, "seed-decider", settled=True)
    rt.consequences.start(handle, 0)
    result, _ = rt._run_tool("seed-decider", handle, {
        "tool": "venue.set_leverage", "args": {"coin": "BTC", "leverage": 1},
    })
    assert result["status"] == "ok"
    # At 1x this exceeds ordinary collateral, although the same notional fits at 3x.
    notional = Decimal(rt.wallet.available + rt.reserve.remaining() // 2) / 1_000_000
    size = notional / rt.exchange.mids()["BTC"]
    result, _ = rt._run_tool("seed-decider", handle, {
        "tool": "venue.place_market", "args": {"coin": "BTC", "side": "buy", "size": str(size)},
    })
    assert result["status"] == "rejected"
    assert rt.exchange.account().positions == ()


def test_pending_protected_compute_hold_survives_checkpoint():
    from factorylab.runtime.resume import restore_runtime, runtime_state

    rt = runtime()
    rt._manage_reserve_window()
    handle = decision(rt, "seed-observer")
    protected = rt.reserve.remaining()
    hold = rt.wallet.reserve(protected, handle, "model:fake-haiku")
    restored = runtime()
    restore_runtime(restored, runtime_state(rt))
    restored_hold = restored.wallet._reservation_for_resume(hold.id)
    rt.wallet.commit(hold, 7)
    restored.wallet.commit(restored_hold, 7)
    assert rt.wallet.state() == restored.wallet.state()
    assert rt.reserve.state() == restored.reserve.state()
    assert rt.reserve.remaining() == protected - 7


def test_thrash_decay_reduces_next_windows_compliant_price():
    rt = runtime()
    rt._derive_regions()
    for i in range(8):
        close(rt, .2, registrations=3 * (i % 2))
    assert rt.stats.pathologies["thrash"]
    rt.controller.set_price("well_formed_rate", 0.8, amendment_id="test")
    close(rt, 1)
    assert rt.controller.price("well_formed_rate") == pytest.approx(
        0.8 - rt.m.prices.decay - rt.m.immune.decay_step
    )
