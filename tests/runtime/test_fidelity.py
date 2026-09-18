"""The essay's casts have observable purchase on live behaviour."""

from dataclasses import replace

import pytest

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


@pytest.mark.parametrize("mode", ["direct", "market-tool", "limit-tool"])
def test_an_empty_thinking_pot_does_not_stop_an_order_the_venue_can_carry(mode):
    """Rehearsal 3, defect 1: the protected novelty reserve is not order collateral.

    This test pinned the opposite until edition 3's live rehearsal, where a 0.005
    BTC short — about $383 of notional at 2x — was refused on a venue account
    holding $851 of perps cash, because the compute wallet net of the protected
    reserve had $107 left. C5 keeps the two pots apart by design: the wallet buys
    thoughts and the trading principal sits on the venue, so an empty wallet is not
    a reason to refuse an order the venue can collateralise. What refuses one now is
    the venue's own free collateral; the collateral tests in ``tests/audit/test_r3b_money`` pin
    that, and the leverage wall below is still the hard cast.
    """
    from factorylab.cortex.request import Return

    rt = runtime()
    rt._manage_reserve_window()
    incumbent = decision(rt, "seed-decider", settled=True)
    rt.consequences.start(incumbent, 0)
    hold = rt.wallet.reserve(rt.wallet.available, incumbent, "model:fake-opus")
    rt.wallet.commit(hold, hold.amount)
    assert rt.wallet.available == 0 and rt.reserve.remaining()
    before = rt.exchange.account().positions
    if mode == "direct":
        rt._execute_outputs(Return(incumbent, {
            "action": "order", "coin": "BTC", "side": "buy", "size": "0.0001",
        }, 0, "ok"))
        assert rt.stats.orders_placed == 1 and rt.stats.orders_rejected == 0
    else:
        args = {"coin": "BTC", "side": "buy", "size": "0.0001"}
        if mode == "limit-tool":
            args["price"] = str(rt.exchange.mids()["BTC"] / 2)
        result, _cost = rt._run_tool("seed-decider", incumbent, {
            "tool": "venue.place_limit" if mode == "limit-tool" else "venue.place_market",
            "args": args,
        })
        assert result["status"] in ("filled", "resting")
    # A market order takes the position; the limit rests below the mid and waits.
    assert (rt.exchange.account().positions != before) is (mode != "limit-tool")
    assert bool(rt.exchange.open_orders()) is (mode == "limit-tool")
    # The thinking pot is no gate on the order, and no longer fenced off from it
    # either: a filled order's fee settles against this wallet like every other
    # world-priced loss, so it ends below zero and into the protected share. The
    # gate never prevented that — a loss settles whatever the gate allowed — and
    # separating the two pots for real is the funded world's own question.
    assert rt.wallet.available <= 0


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


def test_order_protection_uses_acknowledged_leverage_not_the_maximum():
    """The leverage wall is unchanged by defect 1: only the pot it guards moved.

    The notional is sized from the venue's free collateral now rather than from the
    compute wallet, because that is what an order is collateralised by.
    """
    rt = runtime()
    rt._manage_reserve_window()
    handle = decision(rt, "seed-decider", settled=True)
    rt.consequences.start(handle, 0)
    result, _ = rt._run_tool("seed-decider", handle, {
        "tool": "venue.set_leverage", "args": {"coin": "BTC", "leverage": 1},
    })
    assert result["status"] == "ok"
    account = rt.exchange.account()
    # At 1x this exceeds the venue's free collateral, although the same notional
    # fits three times over at the venue's maximum 3x.
    notional = (account.equity_usd - account.margin_used_usd) * 2
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
