"""A9: no judge trades what it judges, and nothing judges its own output (seat 6, finding 5)."""

from dataclasses import replace
from decimal import Decimal

from factorylab.cortex.request import ChildRequest, Return
from factorylab.kernel.events import Event, EventKind
from factorylab.kernel.queue import PropensityRecord, SettleStatus
from factorylab.runtime.shared import CH_CONFORMITY, CH_VERDICT
from factorylab.world.exchange import FakeExchange
from tests.runtime.test_loop import _consequence_decision, _consequence_runtime


def _judge_handle(runtime, judge="eval-a", *, deadline_ns=None):
    return _consequence_decision(runtime, judge, CH_CONFORMITY, deadline_ns=deadline_ns)


def test_a_judge_cannot_close_against_a_producers_lot():
    exchange = FakeExchange(coins=("BTC",), start_prices={"BTC": Decimal("100")},
                            fee_bps=Decimal(0), spread_bps=Decimal(0))
    runtime = _consequence_runtime(exchange=exchange)
    producer = _consequence_decision(runtime, "seed-decider", CH_VERDICT)
    runtime.consequences.start(producer, 0)
    result, _ = runtime._run_tool("seed-decider", producer, {
        "tool": "venue.place_market", "args": {"coin": "BTC", "side": "buy", "size": "1"},
    })
    assert result["status"] == "filled"
    assert [lot.handle for lot in runtime.consequences.table.lots] == [producer]
    judge = _judge_handle(runtime)
    runtime.handle_to_assembly[judge] = "eval-a"
    for tool, args in (
        ("venue.close", {"coin": "BTC"}),
        ("venue.place_market", {"coin": "BTC", "side": "sell", "size": "1"}),
        ("venue.place_limit", {"coin": "BTC", "side": "sell", "size": "1", "price": "90"}),
        ("venue.cancel", {"coin": "BTC", "order_id": "1"}),
        ("venue.set_leverage", {"coin": "BTC", "leverage": 1}),
        ("treasury.transfer", {"direction": "to_reserve", "usd": 5}),
    ):
        result, cost = runtime._run_tool("eval-a", judge, {"tool": tool, "args": args})
        assert "open consequence account" in result["error"] and cost == 0
    refusals = [i for i in runtime.ledger._recovery_items() if i["kind"] == "tool.refused"]
    assert [r["tool"] for r in refusals] == [
        "venue.close", "venue.place_market", "venue.place_limit", "venue.cancel",
        "venue.set_leverage", "treasury.transfer",
    ]
    assert all(r["handle"] == judge and r["assembly_id"] == "eval-a" for r in refusals)
    # The producer's lot is untouched and the venue saw no judge order.
    assert [lot.handle for lot in runtime.consequences.table.lots] == [producer]
    assert runtime.exchange.account().positions[0].size == Decimal("1")
    # Reads stay open to everyone: schematics are public.
    result, _ = runtime._run_tool("eval-a", judge, {"tool": "venue.positions", "args": {}})
    assert "error" not in result


def test_a_fill_for_an_order_nobody_with_an_account_placed_never_enters_the_fifo():
    exchange = FakeExchange(coins=("BTC",), start_prices={"BTC": Decimal("100")},
                            fee_bps=Decimal(0), spread_bps=Decimal(0))
    runtime = _consequence_runtime(exchange=exchange)
    producer = _consequence_decision(runtime, "seed-decider", CH_VERDICT)
    runtime.consequences.start(producer, 0)
    runtime._run_tool("seed-decider", producer, {
        "tool": "venue.place_market", "args": {"coin": "BTC", "side": "buy", "size": "1"},
    })
    # Suppose a judge's sell somehow reached the venue: its fill is refused by the book, so
    # the producer's lot is not closed by it and the producer keeps its own consequence.
    runtime.consequences.observe("Fill", {
        "order_id": "judge-order", "coin": "BTC", "is_buy": False, "size": "1", "px": "110",
        "fee_usd": "0", "realized_usd": "10",
    }, runtime.n)
    assert [lot.handle for lot in runtime.consequences.table.lots] == [producer]
    refused = next(i for i in runtime.ledger._recovery_items()
                   if i["kind"] == "consequence.refused")
    assert refused["order_id"] == "judge-order"
    # Nor can a judge handle bind an order to the book.
    runtime.consequences.order_result("judge-handle", {"order_id": "j1", "status": "resting"},
                                      {"size": "1"}, runtime.n)
    assert all(o.handle == producer for o in runtime.consequences.table.orders)


def test_a_judge_is_never_routed_to_its_own_childs_return_nor_to_its_own_output():
    runtime = _consequence_runtime()
    spec = runtime.assemblies["eval-a"].spec
    # A judge that also accepts producer returns as a child target, and a parent producer.
    runtime._instantiate(replace(spec, id="eval-child", role="evaluator",
                                 accepts=frozenset({"ProducerReturn", "Tick"}),
                                 emits=("ProducerReturn",)))
    parent = _consequence_decision(runtime, "seed-decider", CH_VERDICT)
    runtime.handle_to_assembly[parent] = "seed-decider"
    runtime.consequences.start(parent, 0)
    request = runtime._request(parent, "parent", {}, {"type": "object"}, 10**18, CH_VERDICT)
    child_item = ChildRequest("eval-child", "child task", {}, {"type": "object"})
    child_handle = None

    def invoke(target, req, role, *, child=False):
        nonlocal child_handle
        child_handle = req.handle
        return Return(req.handle, {"answer": 1}, 0, "ok")

    runtime._invoke = invoke
    runtime._invoke_child("seed-decider", request, child_item, 1000)
    child_return = next(e for e in runtime.internal if e.kind is EventKind.PRODUCER_RETURN
                        and e.payload["about_handle"] == child_handle)
    # A1 permits producing on one's own event. A judging contract still cannot judge it.
    assert "eval-child" in runtime._universe_for("ProducerReturn", child_return)
    runtime._instantiate(replace(runtime.assemblies["eval-child"].spec, emits=("Verdict",)))
    universe = runtime._universe_for("ProducerReturn", child_return)
    assert "eval-child" not in universe  # the child cannot judge its own return
    assert "seed-decider" not in universe  # the parent cannot judge the child it requested
    assert "eval-a" in universe
    # Routing the child's return ledgers the exclusion of the judge that authored it.
    state = runtime.routers["ProducerReturn"][0]
    runtime._open_epoch("ProducerReturn")
    state = runtime.routers["ProducerReturn"][0]
    assert "eval-child" in state.universe
    runtime.n += 1
    runtime._route_with(state, child_return)
    excluded = [i for i in runtime.ledger._recovery_items() if i["kind"] == "route.excluded"]
    assert {i["assembly_id"] for i in excluded} == {"eval-child"}
    assert all(i["reason"] == "self-judgement" for i in excluded)
    # A judge's own verdict and a meta's own meta verdict are excluded on every kind.
    judge = _judge_handle(runtime)
    runtime.handle_to_assembly[judge] = "eval-a"
    verdict = Event("v", EventKind.VERDICT, 0, {"about_handle": parent,
                                                "evaluator_handle": judge}, "runtime")
    assert "eval-a" not in runtime._universe_for("Verdict", verdict)
    assert "eval-a" in runtime._universe_for("ProducerReturn", None)


def test_child_target_and_parent_are_excluded_for_meta_and_producer_kinds_alike():
    runtime = _consequence_runtime()
    parent = _consequence_decision(runtime, "seed-decider", CH_VERDICT)
    runtime.handle_to_assembly[parent] = "seed-decider"
    actor = f"composition:{parent}"
    child = runtime.queue.open(
        actor=actor, event_id="child",
        propensity=PropensityRecord(("meta-a",), (1.,), "meta-a", 0, actor, "parent-selected"),
        channel=CH_VERDICT, deadline_ns=10**18, parent_handle=parent, cost_ceiling=0,
    )
    runtime.handle_to_assembly[child] = "meta-a"
    event = Event("r", EventKind.PRODUCER_RETURN, 0, {"about_handle": child}, "runtime")
    assert runtime._subject_authors("ProducerReturn", event) == {"meta-a", "seed-decider"}
    assert runtime._subject_authors("Tick", Event("t", EventKind.TICK, 0, {}, "w")) == set()


def test_a_chosen_target_whose_consequence_is_fixed_seals_no_payoff_forecast():
    """A9: a judge that picks its own target cannot pick one whose outcome already exists."""
    from types import SimpleNamespace

    from tests.runtime.test_loop import _consequence_produce

    runtime = _consequence_runtime()
    stale, _stale_event = _consequence_produce(runtime, "NOOP")
    assert runtime.consequences.payoff(stale) is not None  # resolved before any judge saw it
    _fresh, event = _consequence_produce(runtime, "NOOP")

    runtime.n += 1
    handle = _judge_handle(runtime)
    hindsight = Return(handle, {"verdict": 0.8, "payoff": 1.0, "rationale": "after the fact",
                                "forecasts": [], "about_handle": stale}, 0, "ok")
    runtime._evaluator_step(event, handle, SimpleNamespace(chosen="eval-a"),
                            runtime.queue.get(handle).deadline_ns, returned=hindsight)
    refused = [i for i in runtime.ledger._recovery_items() if i["kind"] == "return.refused"]
    assert [(i["handle"], i["about_handle"]) for i in refused] == [(handle, stale)]
    assert "consequence is still open" in refused[0]["reason"]
    # Nothing was sealed, the stale return keeps its own outcome, and the judge is
    # settled as non-conforming rather than rewarded for the hindsight.
    assert runtime.stats.forecasts_sealed == 0 and not list(runtime.book.pending())
    assert runtime.queue.get(handle).status is SettleStatus.SETTLED
    assert runtime.queue.history(handle)[0].score == 0.0
    assert runtime.decision_subjects.get(handle) is None


def test_a_chosen_target_with_an_open_consequence_is_judged_and_sealed():
    """The refusal is about the fixed outcome, not about choosing a target at all."""
    from types import SimpleNamespace

    from tests.runtime.test_loop import _consequence_produce

    runtime = _consequence_runtime()
    _about, event = _consequence_produce(runtime, "NOOP")
    # A second return whose consequence has not been resolved yet.
    runtime.n += 1
    open_handle = _consequence_decision(runtime, "NOOP", CH_VERDICT)
    runtime._producer_step(
        Event(f"tick-{runtime.n}", EventKind.TICK, runtime.clock.now_ns, {"index": 0}, "test"),
        open_handle, SimpleNamespace(chosen="NOOP"), runtime.queue.get(open_handle).deadline_ns)
    assert runtime.consequences.payoff(open_handle) is None

    runtime.n += 1
    # The judgement must also settle inside the chosen target's consequence backstop, so its
    # deadline is a handful of ticks rather than the helper's default hundred seconds.
    handle = _judge_handle(
        runtime, deadline_ns=runtime.clock.now_ns + 5 * runtime.tick_clock.interval_ns)
    chosen = Return(handle, {"verdict": 0.8, "payoff": 0.4, "rationale": "on time",
                             "forecasts": [], "about_handle": open_handle}, 0, "ok")
    runtime._evaluator_step(event, handle, SimpleNamespace(chosen="eval-a"),
                            runtime.queue.get(handle).deadline_ns, returned=chosen)
    assert not [i for i in runtime.ledger._recovery_items() if i["kind"] == "return.refused"]
    assert runtime.stats.forecasts_sealed == 1
    assert [f.about_handle for f in runtime.book.pending()] == [open_handle]
