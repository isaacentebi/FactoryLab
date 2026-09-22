"""Polymarket event markets as a world surface: tools, jail, intents, custody, settlement.

Every test runs the seeded simulated venue (``FakePolymarket``); nothing here
reads the network or signs anything.
"""

import json
from dataclasses import replace
from decimal import Decimal
from fractions import Fraction

import pytest

from factorylab.runtime import polymarket
from factorylab.runtime.worlds import KillSpec, PolymarketSpec, load_manifest, manifest_from_dict
from factorylab.world.exchange import FakeExchange
from factorylab.world.models import ModelResponse
from factorylab.world.polymarket import DEFAULT_FAKE_MARKETS, FakePolymarket
from tests.helpers import collateral_decision
from tests.runtime.test_loop import _consequence_diary, _consequence_produce, _consequence_runtime


class Scripted:
    """Replies in order, one per provider call."""

    def __init__(self, *replies):
        self.replies = list(replies)

    def complete(self, req):
        return ModelResponse(req.model_id, json.dumps(self.replies.pop(0)), 300, 40, "end_turn")


def still_fake(**changes):
    """The seeded venue with prices that do not walk and no scripted resolution."""
    markets = tuple({**m, "resolves_after_s": None} for m in DEFAULT_FAKE_MARKETS)
    return FakePolymarket(**{"start_usdc": Decimal(50), "markets": markets, "step_ticks": 0,
                             **changes})


def world(*, provider=None, exchange=None, fake=None, realized=False, kill=False, **spec):
    manifest = load_manifest("scripted")
    manifest = replace(manifest, polymarket=PolymarketSpec(enabled=True, **spec))
    if realized:
        manifest = replace(manifest, evaluation=replace(manifest.evaluation,
                                                        producer_feedback="realized"))
    if kill:
        manifest = replace(manifest, kill=KillSpec(wind_down=True))
    rt = _consequence_runtime(provider=provider, exchange=exchange, manifest=manifest)
    rt._manage_reserve_window()
    rt.polymarket.venue.target = fake if fake is not None else still_fake()
    return rt


def token(rt, market="fake-1", side=0):
    return rt.polymarket.venue.target.market(market)["outcomes"][side]["token_id"]


def buy(rt, handle, *, size="10", price="0.45", slot="tool:0", market="fake-1", side="buy"):
    call = {"tool": "polymarket.place_limit",
            "args": {"token_id": token(rt, market), "side": side, "size": size,
                     "price": price}}
    return rt._run_tool("seed-decider", handle, call, slot=slot)[0]


def kinds(rt):
    return [item["kind"] for item in _consequence_diary(rt)]


# --- the manifest ------------------------------------------------------------------------

def test_a_world_without_the_block_has_no_surface_and_its_identity_is_unchanged():
    rt = _consequence_runtime()
    assert not hasattr(rt, "polymarket")
    assert not any(tool.startswith("polymarket.") for tool in rt.tool_specs)
    import tomllib
    from pathlib import Path

    base = load_manifest("scripted")
    raw = tomllib.loads(Path(__file__).parents[2].joinpath("worlds/scripted.toml").read_text())
    assert manifest_from_dict(raw).manifest_hash() == base.manifest_hash()
    off = manifest_from_dict({**raw, "polymarket": {"enabled": False}})
    assert off.manifest_hash() == manifest_from_dict(raw).manifest_hash()
    on = manifest_from_dict({**raw, "polymarket": {"enabled": True, "max_order_usd": "5"}})
    assert on.manifest_hash() != off.manifest_hash()
    assert on.polymarket.max_order_micro == 5_000_000
    assert base.polymarket == PolymarketSpec()
    with pytest.raises(ValueError, match="unknown polymarket"):
        manifest_from_dict({**raw, "polymarket": {"enabled": True, "leverage": 3}})
    with pytest.raises(ValueError, match="simulated venue"):
        manifest_from_dict({**raw, "polymarket": {"enabled": True, "venue": "live",
                                                  "collateral_usd": "5"}})


def test_no_world_in_the_repository_enables_event_markets():
    from pathlib import Path

    for path in sorted(Path(__file__).parents[2].joinpath("worlds").glob("*.toml")):
        assert load_manifest(str(path)).polymarket.enabled is False, path.name


def test_published_tools_state_what_they_do_and_cost_and_carry_valid_examples():
    from factorylab.cortex.assembly import validate_schema

    rt = world(read_price_micro=2000)
    specs = {k: v for k, v in rt.tool_specs.items() if k.startswith("polymarket.")}
    assert set(specs) == {*polymarket.READS, polymarket.ACCOUNT, *polymarket.WRITES}
    for tool_id, spec in specs.items():
        for example in spec["args_schema"]["examples"]:
            validate_schema(example, spec["args_schema"])
        text = spec["description"].lower()
        assert "$0.002" in text if tool_id in polymarket.READS else "free" in text
        # A surface, never a suggestion (AGENTS.md: physics is enforced, not announced).
        assert not any(word in text for word in ("should", "profit", "opportunit", "edge",
                                                 "recommend", "consider", "worth", "better"))
    live = world(venue="live")
    assert not any(t in live.tool_specs for t in (*polymarket.WRITES, polymarket.ACCOUNT))


# --- reads and the jail ----------------------------------------------------------------------

def test_reads_are_priced_and_their_prose_is_kept_off_durable_surfaces():
    rt = world(read_price_micro=1500)
    handle = collateral_decision(rt)
    result, cost = rt._run_tool("seed-decider", handle,
                                {"tool": "polymarket.search", "args": {"query": "event A"}})
    assert cost == 1500
    [market] = result["markets"]
    question = market["question"]
    assert len(question) >= 32
    assert rt.ledger.without_connector_bodies({"q": question}) != {"q": question}
    # Ids stay repeatable facts: the answer that trades a token may name it.
    tid = market["outcomes"][0]["token_id"]
    assert rt.ledger.without_connector_bodies(tid) == tid
    book, _ = rt._run_tool("seed-decider", handle,
                           {"tool": "polymarket.book", "args": {"token_id": tid, "depth": 2}})
    assert [lvl["price"] for lvl in book["book"]["asks"]] == ["0.41", "0.42"]
    bad, cost = rt._run_tool("seed-decider", handle,
                             {"tool": "polymarket.book", "args": {"token_id": "BTC"}})
    assert "format" in bad["error"]


def test_a_round_that_read_market_text_cannot_trade_in_the_same_wake():
    place = {"tool": "polymarket.place_limit",
             "args": {"token_id": "100000000000000000000", "side": "buy", "size": "10",
                      "price": "0.45"}}
    provider = Scripted(
        {"action": "investigate",
         "tool_calls": [{"tool": "polymarket.search", "args": {"query": "event A"}}]},
        {"action": "hold", "tool_calls": [place]},
    )
    rt = world(provider=provider)
    handle, event = _consequence_produce(rt)
    assert rt.polymarket.writes_of(handle) == []
    assert rt.polymarket.venue.target.account()["positions"] == []
    assert "tool.calls_ignored" in kinds(rt)


def test_market_text_repeated_verbatim_in_an_answer_voids_it():
    question = still_fake().market("fake-1")["question"]
    provider = Scripted(
        {"action": "investigate",
         "tool_calls": [{"tool": "polymarket.market", "args": {"market_id": "fake-1"}}]},
        {"action": "hold", "rationale": question},
    )
    rt = world(provider=provider)
    _, event = _consequence_produce(rt)
    assert event.payload["status"] == "malformed"


# --- writes --------------------------------------------------------------------------------

def test_an_order_through_the_tool_is_the_decisions_act_and_is_reported_not_repeated():
    place = {"tool": "polymarket.place_limit",
             "args": {"token_id": "100000000000000000000", "side": "buy", "size": "10",
                      "price": "0.45"}}
    provider = Scripted({"action": "order", "tool_calls": [place]},
                        {"action": "order", "coin": "BTC", "side": "buy", "size": "0.01"})
    exchange = FakeExchange(coins=("BTC", "ETH"))
    rt = world(provider=provider, exchange=exchange)
    handle, event = _consequence_produce(rt)
    assert event.payload["status"] == "ok"
    executed = event.payload["executed_operations"]
    assert [(e["operation"], e["status"]) for e in executed] == [
        ("polymarket.place_limit", "filled")]
    # The answer's "order" names the trade the tool made; no Hyperliquid order follows.
    assert rt.order_intents == {} and not exchange.account().positions
    assert "order.reported" in kinds(rt)


def test_one_client_identity_submits_once_whatever_repeats_it():
    rt = world()
    handle = collateral_decision(rt)
    first = buy(rt, handle)
    again = buy(rt, handle)
    assert first == again and first["status"] == "filled"
    assert rt.polymarket.venue.target.account()["positions"][0]["size"] == "10"
    intents = [i for i in _consequence_diary(rt) if i["kind"] == "polymarket.intent"]
    assert len(intents) == 1 and intents[0]["client_id"] == f"{handle}:tool:0"


def test_a_lost_acknowledgement_is_recovered_by_identity_never_resubmitted():
    rt = world()
    fake = rt.polymarket.venue.target
    submitted = []

    def lossy(**kwargs):
        submitted.append(kwargs["client_id"])
        FakePolymarket.place(fake, **kwargs)
        raise TimeoutError("acknowledgement lost")

    fake.place = lossy
    handle = collateral_decision(rt)
    result = buy(rt, handle)
    assert result["status"] == "filled" and submitted == [f"{handle}:tool:0"]
    diary = _consequence_diary(rt)
    assert [i["kind"] for i in diary if i["kind"].startswith("polymarket.")][:3] == [
        "polymarket.intent", "polymarket.uncertain", "polymarket.acknowledged"]


def test_an_unanswered_intent_is_polled_on_the_bounded_schedule_then_released():
    from factorylab.runtime.venue import UNCERTAIN_ORDER_POLLS

    rt = world()
    fake = rt.polymarket.venue.target

    def silent(**_kwargs):
        raise TimeoutError("no answer")

    fake.place = fake.lookup = silent
    handle = collateral_decision(rt)
    assert buy(rt, handle)["status"] == "uncertain"
    assert rt.consequences.pending_orders  # every economic outcome waits on it
    for _ in range(UNCERTAIN_ORDER_POLLS + 2):
        polymarket.tick(rt)
    assert not rt.consequences.pending_orders
    assert kinds(rt).count("polymarket.unresolved") == 1


@pytest.mark.parametrize(("change", "reason"), [
    ({"size": "20", "price": "0.45"}, "available USDC"),
    ({"size": "10", "price": "0.45", "side": "sell"}, "tokens the polymarket pot holds"),
    ({"size": "30", "price": "0.45"}, "max_order_usd"),
    ({"size": "10", "price": "1.2"}, "strictly between 0 and 1"),
])
def test_writes_the_pot_or_the_caps_cannot_carry_are_refused_before_any_intent(change, reason):
    rt = world(max_order_micro=10_000_000, fake=still_fake(start_usdc=Decimal(8)))
    handle = collateral_decision(rt)
    result = buy(rt, handle, **change)
    assert result["status"] == "rejected" and reason in result["error"]
    assert rt.polymarket.intents == {} and rt.consequences.pending_orders == {}
    assert "polymarket.refused" in kinds(rt)


def test_collateral_is_the_polymarket_pot_never_the_hyperliquid_account():
    rich_venue = FakeExchange(start_cash_usd=Decimal(1_000_000))
    rt = world(exchange=rich_venue, fake=still_fake(start_usdc=Decimal(0)))
    handle = collateral_decision(rt)
    result = buy(rt, handle)
    assert result["status"] == "rejected" and "polymarket pot" in result["error"]
    assert rich_venue.account().cash_usd == Decimal(1_000_000)


def test_window_cap_unlisted_tokens_and_foreign_cancels_are_refused():
    rt = world(max_orders_per_window=1)
    handle = collateral_decision(rt)
    unlisted = rt._run_tool("seed-decider", handle, {"tool": "polymarket.place_limit", "args": {
        "token_id": "42", "side": "buy", "size": "10", "price": "0.5"}}, slot="tool:2")[0]
    assert unlisted["error"] == "token is not listed"
    assert buy(rt, handle, price="0.30", slot="tool:0")["status"] == "resting"
    capped = buy(rt, handle, price="0.31", slot="tool:1")
    assert "window cap" in capped["error"]
    foreign = rt._run_tool("seed-decider", handle, {"tool": "polymarket.cancel",
                                                    "args": {"order_id": "pm-999"}},
                            slot="tool:3")[0]
    assert "placed by this world" in foreign["error"]


def test_an_identical_resting_order_from_the_same_seat_is_refused():
    rt = world()
    first = collateral_decision(rt)
    assert buy(rt, first, price="0.30")["status"] == "resting"
    second = collateral_decision(rt)
    repeat = buy(rt, second, price="0.30")
    assert "already resting" in repeat["error"]


def test_a_judge_cannot_trade_event_markets():
    rt = world()
    handle = collateral_decision(rt)
    rt.return_kinds[handle] = "Verdict"
    result = buy(rt, handle)
    assert result["error"] == rt.WRITE_REFUSAL
    assert rt.polymarket.intents == {}


def test_a_batch_is_weighed_whole_across_both_venues():
    from factorylab.cortex.request import Return

    exchange = FakeExchange(coins=("BTC", "ETH"))
    rt = world(exchange=exchange, fake=still_fake(start_usdc=Decimal(8)))
    handle = collateral_decision(rt)
    tid = token(rt)
    fits = {"tool": "polymarket.place_limit",
            "args": {"token_id": tid, "side": "buy", "size": "10", "price": "0.45"}}
    # Each buy fits the $8 pot alone; together they do not.
    other = {**fits, "args": {**fits["args"], "price": "0.40"}}
    hedge = {"tool": "venue.place_market", "args": {"coin": "BTC", "side": "sell",
                                                   "size": "0.001"}}
    ret = Return(handle, {"action": "order"}, 0, "ok", tool_calls=(hedge, fits, other))
    weighed = rt._weigh_venue_batch("seed-decider", handle, ret, 0)
    assert all(call.get("invalid") for call in weighed.tool_calls)
    assert "available USDC" in weighed.tool_calls[0]["invalid"]
    alone = Return(handle, {"action": "order"}, 0, "ok", tool_calls=(hedge, fits))
    assert not any(c.get("invalid")
                   for c in rt._weigh_venue_batch("seed-decider", handle, alone, 0).tool_calls)


# --- custody and settlement --------------------------------------------------------------

def test_the_pot_is_its_own_custody_account_beside_the_others():
    from factorylab.runtime.custody import custody_view

    rt = world()
    handle = collateral_decision(rt)
    before = rt.wallet.pots()
    buy(rt, handle, price="0.45")  # fills at the ask, 0.41
    view = custody_view(rt)["polymarket"]
    assert view["status"] == "observed" and Decimal(view["usdc"]) == Decimal("45.9")
    assert view["positions"][0]["size"] == "10"
    pots = rt.wallet.pots()
    assert pots["polymarket"] == 45_900_000 == before["polymarket"] - 4_100_000
    assert pots["polymarket_tokens"] == [{"token_id": token(rt), "size": "10"}]
    if before["complete"]:
        assert pots["total_micro"] == before["total_micro"] - 4_100_000
    assert rt._venue_accounts(custody_view(rt))["polymarket"] == view


def test_a_position_stays_pending_past_the_backstop_until_its_market_resolves():
    from factorylab.runtime.grounded import observed_evidence_refs, public_evidence

    rt = world(fake=still_fake(resolutions={"fake-1": (10**12, 0)}))
    handle = collateral_decision(rt)
    assert buy(rt, handle)["status"] == "filled"
    rt.consequences.finish(handle, 1_000)
    rt.ticks_consumed += rt.ev.consequence_backstop_ticks + 5
    rt.consequences.observe("MarketMid", {"coin": polymarket.coin_of(token(rt)),
                                          "mid": "0.99"}, rt.n)
    assert rt.consequences.resolve(rt.n) == []  # no mark, however tempting
    assert rt.consequences.payoff(handle) is None
    assert polymarket.awaiting_resolution(rt, handle)
    rt.clock.now_ns = 10**12
    polymarket.tick(rt)
    [payoff] = rt.consequences.resolve(rt.n)
    # Ten YES tokens bought at 0.41 redeemed at 1: +5.90, settled, not marked.
    assert (payoff.handle, payoff.net_micro, payoff.marked, payoff.y) == (
        handle, 5_900_000, False, 1)
    evidence = public_evidence(rt, _contract(rt, handle))
    refs = {row["ref"]: row for row in evidence}
    resolution = [r for r in evidence if r["kind"] == "ExecutionReceipt:resolution"]
    assert resolution and resolution[0]["payload"]["facts"]["payout"] == "1"
    assert any(ref.startswith("economic-outcome:") for ref in observed_evidence_refs(evidence))
    assert refs and not polymarket.awaiting_resolution(rt, handle)
    settled = [i for i in _consequence_diary(rt) if i["kind"] == "venue.settled"]
    assert [(i["custody"], i["amount"], i["handle"]) for i in settled] == [
        ("polymarket", 5_900_000, handle)]


def _contract(rt, handle):
    from factorylab.runtime.grounded import freeze_contract

    return replace(freeze_contract(rt, handle, "seed-decider", {"action": "order"}),
                   event_cursor=0, receipt_cursor=0)


def test_a_resting_order_holds_its_decision_open_and_a_losing_resolution_realizes_a_loss():
    rt = world(fake=still_fake(resolutions={"fake-1": (10**12, 1)}))
    handle = collateral_decision(rt)
    assert buy(rt, handle, price="0.30")["status"] == "resting"
    rt.consequences.finish(handle, 0)
    rt.ticks_consumed += rt.ev.consequence_backstop_ticks + 5
    assert polymarket.held(rt) == (handle,)
    assert rt.consequences.resolve(rt.n) == []
    rt.polymarket.venue.target._markets["fake-1"]["mid"] = Decimal("0.28")
    rt.clock.now_ns = 10**9
    polymarket.tick(rt)  # the book walks through the resting bid: a maker fill
    assert rt.consequences.table.lots[0].px == Fraction(3, 10)
    rt.clock.now_ns = 10**12
    polymarket.tick(rt)
    [payoff] = rt.consequences.resolve(rt.n)
    assert (payoff.net_micro, payoff.y, payoff.marked) == (-3_000_000, 0, False)


def test_the_grounded_horizon_follows_an_open_position_to_its_resolution():
    rt = world(realized=True, fake=still_fake(resolutions={"fake-1": (10**12, 0)}))
    handle = collateral_decision(rt)
    buy(rt, handle)
    rt.consequences.finish(handle, 0)
    contract = _contract(rt, handle)
    rt.grounded_pending[handle] = contract
    rt.ticks_consumed = contract.close_tick + 3
    rt._settle_due_grounded()
    deferred = rt.grounded_pending[handle]
    assert handle not in rt.grounded_closed and not deferred.final_requested
    assert deferred.close_tick > rt.ticks_consumed and deferred.criteria == contract.criteria
    rt.clock.now_ns = 10**12
    polymarket.tick(rt)
    rt._settle_due_forecasts()
    rt.ticks_consumed += 1
    rt._settle_due_grounded()
    assert rt.grounded_pending[handle].final_requested
    diary = _consequence_diary(rt)
    assert [i["kind"] for i in diary].count("consequence.awaiting_resolution") == 1
    [request] = [i for i in diary if i["kind"] == "consequence.final_requested"]
    assert any(ref.startswith("economic-outcome:") for ref in request["evidence_refs"])


def test_the_surface_survives_a_checkpoint():
    from factorylab.runtime.resume import restore_runtime, runtime_state

    rt = world()
    handle = collateral_decision(rt)
    buy(rt, handle, price="0.30")
    state = runtime_state(rt)
    twin = world()
    restore_runtime(twin, state)
    assert twin.polymarket.intents == rt.polymarket.intents
    assert twin.polymarket.order_ids == rt.polymarket.order_ids
    assert twin.polymarket.account() == rt.polymarket.account()
    # A world without the block writes no polymarket key at all.
    assert "polymarket" not in runtime_state(_consequence_runtime())


def test_a_kill_winds_the_pot_down_and_counts_what_it_leaves():
    rt = world(kill=True)
    handle = collateral_decision(rt)
    buy(rt, handle, price="0.45", slot="tool:0")  # filled: ten tokens held
    buy(rt, handle, price="0.30", slot="tool:1")  # resting
    report = rt.kill("test")
    assert report["polymarket"]["cancelled"] == 1 and report["polymarket"]["sold"] == 1
    assert report["polymarket"]["exposure_state"] == "flat"
    account = rt.polymarket.account()
    assert account["positions"] == [] and account["open_orders"] == []
