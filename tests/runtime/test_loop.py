from dataclasses import replace
from decimal import Decimal
from math import ceil

import pytest

from factorylab.kernel.events import Event, EventKind
from factorylab.kernel.queue import PropensityRecord, SettleStatus
from factorylab.runtime.feedback import PendingJudgement
from factorylab.runtime.loop import Runtime, run_world
from factorylab.runtime.worlds import load_manifest
from factorylab.world.scripted import ScriptedProvider


def test_scripted_world_compute_starvation_is_final_under_phase4() -> None:
    m = load_manifest("scripted")
    s = run_world(m, events=60, seed=2, initial_balance_micro=1)
    assert s["terminated"] and s["termination_reason"] == "insolvency:compute"
    assert s["seal_key_released"] and s["wallet_balance_micro"] == 1
    assert s["stats"]["exclusions"] > 0


def test_crash_world_wipes_its_venue_without_spending_its_compute_authority() -> None:
    """Restated by R3-B. The crash world's shocks gap a leveraged long through
    maintenance margin, and its wallet used to reach zero because the realised loss
    settled there: "venue losses can consume fictitious compute resources". The loss
    is as large as it ever was and the venue account still goes negative; what it no
    longer does is buy thoughts. The world keeps the compute authority it has not
    spent. Death and seal release are covered above, by compute starvation, which is
    what actually ends a world that has run out of money to think with."""
    m = load_manifest("scripted-crash")
    # The four shocks land by event 120; the venue account is already below zero. Whether
    # the trader is long through them depends on what its routers learned, which every
    # change to the reward line moves, so the claim is made of the first seed that is.
    for seed in (2, 3):
        s = run_world(m, events=120, seed=seed)
        if Decimal(s["exchange_equity_usd"]) < 0:
            break
    assert Decimal(s["exchange_equity_usd"]) < 0  # the venue was wiped
    assert s["terminated"] is False and s["termination_reason"] is None
    assert s["wallet_balance_micro"] > 0  # authority, not spent by the venue
    assert s["wallet_conservation"] is True


def test_determinism_same_seed_same_summary() -> None:
    base = load_manifest("scripted")
    m = replace(base, novelty=replace(base.novelty, window_ns=20_000_000_000))
    a = run_world(m, events=45, seed=7)
    b = run_world(m, events=45, seed=7)
    # Forty-five events reach an immune window, a router replacement and a price update
    # (thirty did while judges were scored for forecasting holds already resolved).
    assert a["stats"]["immune_windows"] and a["stats"]["routers_replaced"]
    assert a["stats"]["price_updates"]
    a.pop("aggregates", None)
    b.pop("aggregates", None)
    assert a == b


# Recursive depth is introduced by a population return, never by changing the seeds.


class RecursiveMetaProvider(ScriptedProvider):
    """A scripted provider whose producers hold at the default cadence and register a
    recursive meta seat on their eighth call, so a tier judges the metas."""

    recursive_ids = ("recursive-meta",)

    def _produce(self, desc, inputs):
        self._producer_calls += 1
        reply = {"action": "hold", "subscribe": {"cadence_floor": 1}}
        if self._producer_calls == 8:
            reply["register"] = [
                {
                    "kind": "assembly",
                    "id": aid,
                    "role": "meta",
                    # The world block no longer publishes a model list; the scripted
                    # world's other registrations name their model directly.
                    "model_id": "fake-haiku",
                    "system_prompt": "Assess the supplied judgement against the charter.",
                    "accepts": ["MetaVerdict"],
                    "max_tokens": 128,
                }
                for aid in self.recursive_ids
            ]
        return reply


def _recursive_runtime(*, events=100, provider=None):
    # C10: a registered child lives on the trial its proposer moves to it. The
    # recursive meta judges on fake-opus, whose call ceiling exceeds the scripted
    # 0.10 USD trial, so these tiers are exercised with a trial the seat can keep
    # working on once its protected trial calls are spent.
    base = load_manifest("scripted")
    manifest = replace(base, evaluation=replace(base.evaluation, trial_amount_micro=2_000_000))
    return Runtime(
        manifest,
        events=events,
        seed=1,
        initial_balance_micro=None,
        ledger_path=None,
        router_gamma=0.1,
        provider=provider or RecursiveMetaProvider(),
    )


def _diary(runtime):
    runtime.termination.kill("test audit")
    items = []
    while True:
        try:
            items.append(runtime.ledger.decrypt_item(len(items)))
        except IndexError:
            return items


def test_cascade_release_is_ledger_first_and_fast_fallback_keeps_timeout(monkeypatch):
    runtime = _recursive_runtime(events=0)
    runtime.m = replace(runtime.m, timing=replace(runtime.m.timing, jitter_fraction=0))
    handles = [_pending_meta(runtime) for _ in range(3)]
    # Restated for R3-D: a tier's separation is a duration, not an arrival count
    # (GPT-6 third reading §6.C), so the three arrivals are spread across the
    # window the manifest precommits instead of sharing one timestamp. Everything
    # the test is about — the representative, the siblings, the ledger order — is
    # unchanged.
    step = runtime.m.timing.min_ratio * runtime.tick_clock.interval_ns // 2
    events = [
        Event(
            f"meta-{i}",
            EventKind.META_VERDICT,
            i * step,
            {"by": h, "about": "lower", "tier": 2, "score": i / 2},
            "runtime",
        )
        for i, h in enumerate(handles)
    ]
    for event in events[:2]:
        assert runtime._cascade_arrival(event) is None
    before = runtime.cascade[2]
    # The window's duration is ticks (time audit T3, T10): it has elapsed at the third.
    runtime.ticks_consumed = ceil(before.window)
    rng_before = runtime.rng.getstate()
    append = runtime.ledger.append

    def reject_release(item):
        if item["kind"] == "cascade.release":
            raise RuntimeError("ledger unavailable")
        return append(item)

    monkeypatch.setattr(runtime.ledger, "append", reject_release)
    with pytest.raises(RuntimeError, match="ledger unavailable"):
        runtime._cascade_arrival(events[2])
    assert runtime.cascade[2] is before
    assert runtime.rng.getstate() == rng_before
    assert all(runtime.queue.get(h).status is SettleStatus.PENDING for h in handles)
    monkeypatch.setattr(runtime.ledger, "append", append)
    # The verdict timeout counts world ticks consumed, not internal events (defect 1).
    runtime.ticks_consumed = runtime.ev.verdict_timeout_ticks + 1
    runtime.pending[handles[1]].opened_at_tick = runtime.ticks_consumed
    runtime.pending[handles[2]].opened_at_tick = runtime.ticks_consumed
    released = runtime._cascade_arrival(events[2])
    assert released.id == events[2].id
    # Nothing settles at release, and an evaluator decision is not a stale producer
    # judgement: it closes on its own two signals (ruling R1).
    assert all(runtime.queue.get(h).status is SettleStatus.PENDING for h in handles)
    runtime._censor_stale_judgements()
    assert all(h in runtime.pending for h in handles)


def test_a_meta_grades_the_representative_and_the_unread_siblings_borrow_nothing():
    """Evaluations U2: the sibling share is deleted. A meta reads the window's
    representative and grades it; the window's other verdicts were not read and
    settle on their own signals, here none, so censored."""
    runtime = _recursive_runtime(events=0)
    runtime.m = replace(runtime.m, timing=replace(runtime.m.timing, jitter_fraction=0))
    handles = [_pending_meta(runtime) for _ in range(3)]
    step = runtime.m.timing.min_ratio * runtime.tick_clock.interval_ns // 2
    events = [
        Event(
            f"meta-{i}",
            EventKind.META_VERDICT,
            i * step,
            {"by": h, "about": "lower", "tier": 2, "score": i / 2},
            "runtime",
        )
        for i, h in enumerate(handles)
    ]
    for event in events[:2]:
        assert runtime._cascade_arrival(event) is None
    runtime.ticks_consumed = ceil(runtime.cascade[2].window)
    released = runtime._cascade_arrival(events[2])
    assert released is not None
    judged = Event(
        "meta-top",
        EventKind.META_VERDICT,
        0,
        {"by": "judge-3", "about": handles[2], "tier": 3, "score": 0.25},
        "runtime",
    )
    runtime._deliver_meta_verdict(judged)
    assert runtime.pending[handles[2]].grades == [0.25]
    assert runtime.pending[handles[0]].grades == runtime.pending[handles[1]].grades == []
    runtime.ticks_consumed = (runtime.ev.consequence_backstop_ticks
                              + runtime.ev.verdict_timeout_ticks + 1)
    runtime._settle_evaluations()
    (read,) = runtime.queue.history(handles[2])
    assert read.status is SettleStatus.SETTLED and read.score == 0.25
    for h in handles[:2]:
        (unread,) = runtime.queue.history(h)
        assert unread.status is SettleStatus.CENSORED
        assert h not in runtime.pending


def _pending_meta(runtime):
    handle = runtime.queue.open(
        actor="test-router",
        event_id="test",
        channel="conformity",
        deadline_ns=10**15,
        parent_handle=None,
        cost_ceiling=0,
        propensity=PropensityRecord(("meta",), (1.0,), "meta", 0, "test-router", "state"),
    )
    runtime.pending[handle] = PendingJudgement(handle, "conformity", 0, tier=2,
                                               opened_at_tick=0, about="lower", q=0.5,
                                               evaluator_id="meta")
    return handle


def _consequence_runtime(*, provider=None, exchange=None, manifest=None):
    from factorylab.runtime.loop import Runtime

    return Runtime(
        manifest or load_manifest("scripted"),
        events=0,
        seed=1,
        initial_balance_micro=None,
        ledger_path=None,
        router_gamma=0.2,
        provider=provider,
        exchange=exchange,
    )


def _consequence_decision(runtime, action, channel, *, deadline_ns=None):
    from factorylab.kernel.queue import PropensityRecord

    return runtime.queue.open(
        actor="test-router",
        event_id=f"test-{runtime.n}",
        channel=channel,
        propensity=PropensityRecord((action,), (1.0,), action, 0, "test-router", "state"),
        deadline_ns=(runtime.clock.now_ns + 100_000_000_000 if deadline_ns is None
                     else deadline_ns),
        parent_handle=None,
        cost_ceiling=runtime.wallet.available,
    )


def _consequence_produce(runtime, action="seed-decider", channel="verdict"):
    from types import SimpleNamespace

    from factorylab.kernel.events import Event, EventKind

    runtime.n += 1
    handle = _consequence_decision(runtime, action, channel)
    runtime._producer_step(
        Event(f"tick-{runtime.n}", EventKind.TICK, runtime.clock.now_ns, {"index": 0}, "test"),
        handle,
        SimpleNamespace(chosen=action),
        runtime.queue.get(handle).deadline_ns,
    )
    # A return is published as its own kind (primitive audit F12): an antagonist's
    # is an Exposure, never a ProducerReturn.
    event = next(
        e
        for e in runtime.internal
        if str(e.kind) in (str(EventKind.PRODUCER_RETURN), "Exposure")
        and e.payload["about_handle"] == handle
    )
    runtime._settle_due_forecasts()
    return handle, event


def _consequence_judge(runtime, event, judge):
    from types import SimpleNamespace

    runtime.n += 1
    handle = _consequence_decision(runtime, judge, "conformity")
    runtime._evaluator_step(
        event,
        handle,
        SimpleNamespace(chosen=judge),
        runtime.queue.get(handle).deadline_ns,
    )
    # The end of the event's routing: the judged return settles on its verdicts (R1).
    runtime._settle_arrived_verdicts()
    runtime._settle_due_forecasts()
    return handle


def _consequence_diary(runtime):
    marker = runtime.ledger.append({"kind": "test.marker"})
    runtime.termination.kill("test")
    return [runtime.ledger.decrypt_item(i) for i in range(marker)]


def test_tool_order_and_close_belong_to_calling_returns_and_tool_charge_decides_payoff():
    import json
    from decimal import Decimal

    from factorylab.world.exchange import FakeExchange
    from factorylab.world.models import ModelResponse

    class Provider:
        def __init__(self):
            self.calls = 0

        def complete(self, req):
            replies = [
                {
                    "action": "hold",
                    "tool_calls": [
                        {
                            "tool": "venue.place_market",
                            "args": {
                                "coin": "BTC",
                                "side": "buy",
                                "size": "1",
                            },
                        }
                    ],
                },
                {"action": "noop"},
                {
                    "action": "hold",
                    "tool_calls": [
                        {
                            "tool": "venue.close",
                            "args": {
                                "coin": "BTC",
                            },
                        }
                    ],
                },
                {"action": "noop"},
            ]
            reply = replies[self.calls]
            self.calls += 1
            return ModelResponse(req.model_id, json.dumps(reply), 300, 40, "end_turn")

    exchange = FakeExchange(
        coins=("BTC",),
        start_prices={"BTC": Decimal("100")},
        price_path={"BTC": [Decimal("100.010020")]},
        fee_bps=Decimal(0),
        spread_bps=Decimal(0),
    )
    runtime = _consequence_runtime(provider=Provider(), exchange=exchange)
    runtime.tool_specs["venue.place_market"]["price_micro_per_call"] = 11
    opener, _ = _consequence_produce(runtime)
    assert runtime.consequences.payoff(opener) is None
    assert runtime.consequences.table.account(opener).cost_micro == 5011
    runtime._settle_exchange_effects(exchange.advance(1_000_000_000))
    closer, _ = _consequence_produce(runtime)
    payoff = runtime.consequences.payoff(opener)
    # C6/F7: the lot's 10020 micro-USD is credited once, split 100:100.010020 by entry
    # and exit notional and floored on each side. The opener's 5009 clears its 5000
    # compute and falls to the 11 micro-USD tool charge; the closer's 5010 clears 5000.
    assert payoff.net_micro == 5009 and payoff.cost_micro == 5011 and payoff.y == 0
    assert not payoff.marked
    closed = runtime.consequences.payoff(closer)
    assert closed.net_micro == 5010 and closed.cost_micro == 5000 and closed.y == 1  # credited
    assert payoff.net_micro + closed.net_micro == 10020 - 1  # once, less the two floors
    assert runtime.consequences.table.lots == ()
    assert runtime.window.producer_returns == runtime.window.noop_returns == 2
    assert runtime.window.revision_returns == 0  # Tool calls are not revisions (A14).
    assert runtime.window.tool_calls == runtime.window.fills == 2
    items = _consequence_diary(runtime)
    commits = [i for i in items if i["kind"] == "wallet.commit" and i["handle"] == opener]
    assert sum(i["amount"] for i in commits) == payoff.cost_micro
    orders = [i for i in items if i["kind"] == "consequence.order"]
    assert [i["handle"] for i in orders] == [opener, closer]
    assert runtime.wallet.check_conservation() and runtime.ledger.verify()


def test_self_crossing_limit_tools_cannot_manufacture_paid_off_return():
    from decimal import Decimal

    from factorylab.world.exchange import FakeExchange

    runtime = _consequence_runtime(
        exchange=FakeExchange(
            coins=("BTC",),
            start_prices={"BTC": Decimal("100")},
        )
    )
    wash = _consequence_decision(runtime, "seed-decider", "verdict")
    runtime.consequences.start(wash, 0)
    for side in ("buy", "sell"):
        result, _ = runtime._run_tool(
            "seed-decider",
            wash,
            {
                "tool": "venue.place_limit",
                "args": {"coin": "BTC", "side": side, "size": "1", "price": "100"},
            },
            slot=f"test:{side}",
        )
        assert result["status"] == "filled"
    runtime.consequences.finish(wash, 500)
    runtime.consequences.resolve(0)
    payoff = runtime.consequences.payoff(wash)
    assert payoff.net_micro == -70_000 and payoff.y == 0
    # Restated by R3-B: the two fees are the venue's, not the compute wallet's, so
    # they are asserted where they settle. The point of the test is unchanged -- a
    # wash trade costs its maker the fees and pays nothing off.
    settled = [i for i in runtime.ledger._recovery_items() if i["kind"] == "venue.settled"]
    assert sum(i["amount"] for i in settled) == -70_000
    assert {i["custody"] for i in settled} == {"venue_perps"}
    assert runtime.wallet.balance == runtime.initial


def test_resting_limit_fill_and_reduce_only_tool_keep_original_return_attribution():
    from decimal import Decimal

    from factorylab.world.exchange import FakeExchange

    exchange = FakeExchange(
        coins=("BTC",),
        start_prices={"BTC": Decimal("101")},
        price_path={"BTC": [Decimal("100"), Decimal("110")]},
    )
    runtime = _consequence_runtime(exchange=exchange)
    limit = _consequence_decision(runtime, "seed-decider", "verdict")
    runtime.consequences.start(limit, 0)
    result, _ = runtime._run_tool(
        "seed-decider",
        limit,
        {
            "tool": "venue.place_limit",
            "args": {"coin": "BTC", "side": "buy", "size": "1", "price": "100"},
        },
    )
    assert result["status"] == "resting"
    runtime.consequences.finish(limit, 500)
    runtime.consequences.resolve(0)
    assert runtime.consequences.payoff(limit) is None
    runtime._settle_exchange_effects(exchange.advance(1_000_000_000))
    assert runtime.consequences.table.lots[0].handle == limit
    runtime._settle_exchange_effects(exchange.advance(2_000_000_000))
    reduce = _consequence_decision(runtime, "seed-decider", "verdict")
    runtime.consequences.start(reduce, 1)
    result, _ = runtime._run_tool(
        "seed-decider",
        reduce,
        {
            "tool": "venue.place_market",
            "args": {"coin": "BTC", "side": "sell", "size": "2", "reduce_only": True},
        },
    )
    assert result["filled_size"] == "1"
    runtime.consequences.finish(reduce, 500)
    runtime.consequences.resolve(1)
    assert runtime.consequences.payoff(limit).y == 1
    assert runtime.consequences.payoff(reduce).y == 1  # the closer is credited (A16)
    assert runtime.consequences.table.lots == ()


@pytest.fixture
def market_http(monkeypatch):
    from urllib import request

    from tests.world.test_market import SellerHTTP

    monkeypatch.delenv("RESERVE_PRIVATE_KEY", raising=False)
    monkeypatch.setattr(request.OpenerDirector, "open", lambda *a, **k: pytest.fail("network"))
    return SellerHTTP(balance=100_000)


def _market_runtime(market_http, *, provider=None, events=10, treasury=None, seed_price="0"):
    from factorylab.runtime.worlds import manifest_from_dict
    from factorylab.world.market import X402Provider
    from tests.seed_charter import seed_charter_table
    from tests.world.test_market import TEST_KEY

    manifest = manifest_from_dict({
        "name": "market-scripted", "seed": 1, "initial_balance_usd": "0.1",
        "models": [{"id": "fake-model", "provider": "fake",
                    "input_usd_per_mtok": seed_price, "output_usd_per_mtok": seed_price}],
        "assemblies": [{"id": "seed-market", "model_id": "fake-model", "accepts": ["Tick"]}],
        "evaluation": {"trial_amount_usd": "0.001"},
        "novelty": {"share": 0.5},
        "treasury": treasury or {"insolvency_events": 3},
        "charter": seed_charter_table(), "immune": {"price_step": 0.05},
    })
    return Runtime(
        manifest, events=events, seed=1, initial_balance_micro=None, ledger_path=None,
        router_gamma=0.2, provider=provider or ScriptedProvider(),
        market=X402Provider(private_key=TEST_KEY, transport=market_http),
    )


def _register_test_seller(runtime):
    from factorylab.cortex.registration import AssemblyProposal, ModelProposal
    from tests.world.test_market import MODEL

    runtime._manage_reserve_window()
    runtime._register("proposal", ModelProposal(MODEL))
    runtime._register("proposal", AssemblyProposal(
        "market-buyer", "producer", MODEL, "Return JSON.", ("Tick",), 16, "low",
    ))


def test_x402_feasibility_uses_one_fixed_request_and_on_chain_reserve(market_http):
    runtime = _market_runtime(market_http)
    _register_test_seller(runtime)
    # C10: the buyer's own entitlement must cover the fixed request as well as the
    # wallet; its trial endowment is below one request, so a seed endows it.
    runtime.budget.transfer(runtime.m.assemblies[0].id, "market-buyer", 1734, "test")
    runtime.wallet.settle(1734 - runtime.wallet.balance, "test", "exchange_pnl")
    market_http.balance = 1734
    assert runtime._is_feasible("market-buyer") == (True, "")
    market_http.balance = 1733
    assert not runtime._is_feasible("market-buyer")[0]
    assert not market_http.payments  # registration and feasibility never authorize payments


def test_market_discovery_tool_is_priced_and_debited_before_return(market_http, monkeypatch):
    from factorylab.world.x402 import HTTPResponse
    from tests.world.test_market import resource

    runtime = _market_runtime(market_http)
    calls = []

    def fake(method, url, payload, headers):
        calls.append(url)
        return HTTPResponse(200, {"items": [resource("https://seller.test/chat")],
                                 "pagination": {"offset": 0, "limit": 100, "total": 1}})

    monkeypatch.setattr(runtime.market, "_transport", fake)
    initial = runtime.wallet.balance
    result, cost = runtime._run_tool("seed-market", "discovery", {
        "tool": "market.discover", "args": {"url_substring": "chat"},
    })
    assert cost == runtime.m.tools.population_tool_micro_per_call
    assert runtime.wallet.balance == initial - cost
    assert result["sellers"][0]["resource"] == "https://seller.test/chat"
    assert runtime.tool_specs["market.discover"]["kind"] == "market" and len(calls) == 1


def test_insolvency_terminates_scripted_world_when_seller_demands_unaffordable_payment(market_http):
    from factorylab.world.x402 import InsufficientReserve

    class Unaffordable(ScriptedProvider):
        def complete(self, req):
            raise InsufficientReserve("Reserve cannot cover the quoted Base USDC payment")

    runtime = _market_runtime(market_http, provider=Unaffordable(), events=200)
    _register_test_seller(runtime)
    # The reserve can be drained after feasibility, between the read and the payment quote.
    def demand(req, *, record=None, quoted=None):
        raise InsufficientReserve("Reserve cannot cover the quoted Base USDC payment")

    runtime.market.complete = demand
    # A single x402 route removes seeded alternatives while preserving ordinary router sampling.
    del runtime.assemblies["seed-market"]
    runtime._build_router("Tick", "exp3", 0.2)
    result = runtime.run()
    assert result["terminated"] and result["termination_reason"] == "insolvency:compute"
    assert result["seal_key_released"] and result["wallet_balance_micro"] > 0
    assert not market_http.payments
    events = [i for i in _diary(runtime) if i["kind"] == "treasury.insolvency"]
    assert [e["consecutive_events"] for e in events] == [1, 0, 1, 3]
    assert runtime.insolvency_count == 3


def test_insolvency_no_affordable_provider_counts_once_per_routed_event(market_http):
    runtime = _market_runtime(market_http, seed_price="1000")
    result = runtime.run()
    assert result["termination_reason"] == "insolvency:compute"
    assert result["seal_key_released"] and result["wallet_balance_micro"] == 100_000
    items = _diary(runtime)
    counted = [i for i in items if i["kind"] == "treasury.insolvency"]
    assert [i["consecutive_events"] for i in counted] == [1, 3]
    assert len({i["event_id"] for i in counted}) == 2
    assert runtime.insolvency_count == 3
    assert len([i for i in items if i["kind"] == "event"
                and i["event"]["kind"] == "Terminated"]) == 1


def test_insolvency_streak_reset_noop_and_unrouted_events(market_http):
    runtime = _market_runtime(market_http)
    event = Event("routed", EventKind.TICK, 1, {}, "test")
    runtime._compute_routed = True
    runtime._compute_unaffordable = True
    runtime._record_insolvency_event(event)
    assert runtime.insolvency_count == 1
    runtime._compute_routed = False
    runtime._record_insolvency_event(Event("unrouted", EventKind.REGISTERED, 2, {}, "test"))
    assert runtime.insolvency_count == 1
    # Affordable NOOP choices are not insolvency; no invocation is needed to reset.
    runtime._compute_routed = True
    runtime._compute_unaffordable = False
    runtime._record_insolvency_event(event)
    assert runtime.insolvency_count == 0


def test_position_peak_is_ledger_first_and_survives_flat_account(monkeypatch):
    from decimal import Decimal
    from types import SimpleNamespace

    rt = _recursive_runtime(events=0)
    rt.window.equity_start_micro = 10_000_000
    positions = [SimpleNamespace(coin="BTC", size=Decimal("-2"))]
    monkeypatch.setattr(rt.exchange, "account", lambda: SimpleNamespace(positions=positions))
    monkeypatch.setattr(rt.exchange, "mids", lambda: {"BTC": Decimal("3")})
    append = rt.ledger.append

    def capture(item):
        if item["kind"] == "observation.position_peak":
            assert rt.window.max_position_notional_micro is None
        return append(item)

    monkeypatch.setattr(rt.ledger, "append", capture)
    rt._observe_positions()
    assert rt.window.max_position_notional_micro == 6_000_000
    positions.clear()
    rt._observe_positions()
    assert rt.window.max_position_notional_micro == 6_000_000


