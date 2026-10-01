"""Polymarket orders on the live venue, through the running world.

Chapter II §II.b (physics is enforced): every order has a durable intent before the
network call and its hash in that intent, a lost answer is looked up and never resent, a
fill is booked once and never past its order, the pot never trades on principal above its
cap, and the pot's books reconcile ``claimed + unattributed == booked``. §III.b: a held
position's realized consequence is its resolution's payout, booked late into the pot. The
venue is ``LivePolymarket`` talking to ``FakeClob`` in process; nothing here touches a
network or a funded key.
"""

import hashlib
import json
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest

from factorylab.kernel.ledger import canonical
from factorylab.runtime import polymarket
from factorylab.runtime.resume import RecoveryJournal, encode
from factorylab.runtime.worlds import PolymarketSpec, load_manifest
from factorylab.world import polymarket_clob as clob
from tests.helpers import collateral_decision
from tests.runtime.test_loop import _consequence_runtime
from tests.runtime.test_polymarket_surface import still_fake
from tests.world.fake_clob import OTHER, FakeClob, _Wall, make_signer


def live_world(*, fake=None, principal="100", budget=60, confirm=True, wall=None,
               opened=True, **spec):
    signer = make_signer()
    manifest = replace(load_manifest("scripted"), polymarket=PolymarketSpec(
        enabled=True, venue="live", orders=True, funder=signer.address,
        principal_micro=int(Decimal(principal) * 1_000_000), order_requests_per_10s=budget,
        **spec))
    rt = _consequence_runtime(manifest=manifest)
    # A copy of every diary item as it is appended, so a test reads the diary without
    # killing the world (``_consequence_diary`` releases the seal by killing it).
    rt.seen_items = []
    append = rt.ledger.append

    def recording(entry):
        rt.seen_items.append(dict(entry))
        return append(entry)

    rt.ledger.append = recording
    rt._manage_reserve_window()
    assert isinstance(rt.polymarket.venue.target, clob.LivePolymarket)
    assert rt.polymarket.live and rt.polymarket.writes
    installed = rt.polymarket.venue.target
    server = FakeClob(fake if fake is not None else still_fake(), confirm=confirm)
    server.funder = signer.address
    venue = clob.LivePolymarket(funder=signer.address, signature_type=0, budget=budget,
                                signer=signer, send=server, identity=installed.identity,
                                wall=wall or _Wall(), nonce=lambda: 7,
                                get=lambda url: server("GET", url, {}, None))
    venue.intent_of = installed.intent_of  # the runtime's own intents, as installed
    rt.polymarket.venue.target = venue
    if opened:
        polymarket.tick(rt)  # a world's first tick reads the pot's opening, before any order
    return rt, server


def token(server, market="fake-1", side=0):
    return server.fake._markets[market]["tokens"][side]


def buy(rt, server, handle, *, size="10", price="0.30", slot="tool:0", side="buy",
        market="fake-1"):
    call = {"tool": "polymarket.place_limit",
            "args": {"token_id": token(server, market), "side": side, "size": size,
                     "price": price}}
    return rt._run_tool("seed-decider", handle, call, slot=slot)[0]


def maker_fill(rt, server, handle, *, price="0.40", market="fake-1", **kwargs):
    """A post-only buy that rests, then fills as a maker at its price when the market
    moves to it (the live venue never takes)."""
    result = buy(rt, server, handle, price=price, market=market, **kwargs)
    server.match()
    return result


def signed_s(rt, order_id):
    """A match time just after the order was signed, in the venue's seconds."""
    client_id = rt.polymarket.order_ids[order_id]
    order = rt.polymarket.intents[client_id]["order_identity"]["order"]
    return str(int(order["timestamp"]) // 1000 + 9)


def signed_s_of(intent):
    """A match time just after an intent's order was signed."""
    return str(int(intent["order_identity"]["order"]["timestamp"]) // 1000 + 9)


def items(rt, kind):
    return [i for i in rt.seen_items if i["kind"] == kind]


def test_the_intent_and_its_hash_are_durable_before_the_order_is_sent():
    rt, server = live_world()
    handle = collateral_decision(rt)
    seen = []
    send = server.__call__

    def watch(method, url, headers, body):
        if method == "POST" and url.endswith("/order"):
            seen.append([dict(i) for i in items(rt, "polymarket.intent")])
        return send(method, url, headers, body)

    rt.polymarket.venue.target.send = watch
    result = buy(rt, server, handle)
    (intents,) = seen
    (intent,) = intents
    assert intent["client_id"] == f"{handle}:tool:0" and intent["order_hash"].startswith("0x")
    assert result["status"] == "resting" and result["order_id"] == intent["order_hash"]
    assert rt.polymarket.order_ids == {intent["order_hash"]: f"{handle}:tool:0"}


def test_an_order_with_no_intent_is_refused_at_the_venue_boundary():
    rt, server = live_world()
    with pytest.raises(clob.PolymarketRefused, match="no durable intent"):
        rt.polymarket.venue.place(client_id="decision-9:tool:0", token_id=token(server),
                                  is_buy=True, size=Decimal(10), price=Decimal("0.30"))
    assert ("POST", "/order") not in server.calls


def test_a_price_off_the_tick_is_refused_before_any_intent():
    rt, server = live_world()
    handle = collateral_decision(rt)
    result = buy(rt, server, handle, price="0.305")
    assert result["status"] == "rejected" and "tick" in result["error"]
    assert rt.polymarket.intents == {} and ("POST", "/order") not in server.calls


def test_the_pot_cannot_be_spent_twice():
    rt, server = live_world(fake=still_fake(start_usdc=Decimal(5)))
    first, second = collateral_decision(rt), collateral_decision(rt)
    assert buy(rt, server, first, size="10", price="0.30")["status"] == "resting"
    # The first order's 3 USDC rests; 3 more do not fit the 2 left.
    refused = buy(rt, server, second, size="10", price="0.30")
    assert refused["status"] == "rejected" and "available USDC" in refused["error"]
    posts = [c for c in server.calls if c == ("POST", "/order")]
    assert len(posts) == 1


def test_a_simulated_pot_may_open_above_its_lifetime_commitment_cap():
    """Sol P2 (round 7) on #177: the cap bounds signed commitments, not what the pot
    holds, so a $50 simulated pot under a $3 cap loads, as the live one does."""
    spec = PolymarketSpec(enabled=True, collateral_micro=50_000_000, principal_micro=3_000_000)
    assert spec.collateral_micro > spec.principal_micro


def test_a_lost_answer_is_recovered_by_hash_and_never_resent():
    rt, server = live_world()
    handle = collateral_decision(rt)
    server.lose_answer = True
    result = buy(rt, server, handle)
    assert result["status"] == "resting"
    assert [c for c in server.calls if c == ("POST", "/order")] == [("POST", "/order")]
    kinds = [i["kind"] for i in rt.seen_items if i["kind"] in (
        "polymarket.intent", "polymarket.uncertain", "polymarket.acknowledged")]
    assert kinds[:3] == ["polymarket.intent", "polymarket.uncertain",
                         "polymarket.acknowledged"]
    # The same identity again reconciles; it is never a second order.
    assert buy(rt, server, handle) == result
    assert len(server.fake._all_orders) == 1


def test_a_resume_in_the_middle_of_an_order_completes_it_uncertain_never_resent():
    """A process death between ``polymarket.place``'s ``io.call`` and its ``io.result``
    resumes with the order uncertain for its intent to look up by hash; the replay never
    calls the venue again."""
    recorded = []

    def append(item):
        recorded.append(item)
        return len(recorded) - 1

    for name in ("polymarket.place", "polymarket.cancel"):
        recorded.clear()
        journal = RecoveryJournal(SimpleNamespace(append=append), lambda: 0)
        journal.active = journal.recovering = True
        fingerprint = hashlib.sha256(canonical(encode(((), {})))).hexdigest()
        journal.tail = [{"kind": "io.call", "name": name, "input_hash": fingerprint,
                         "seq": 0, "ts": 0}]
        result = journal.call(name, lambda: pytest.fail("order sent twice"), (), {})
        assert result == {"status": "uncertain"}
        assert recorded[-1] == {"kind": "io.result", "call": 0,
                                "result": encode({"status": "uncertain"})}


def test_an_uncertain_order_after_resume_is_looked_up_by_the_hash_its_intent_holds():
    rt, server = live_world()
    handle = collateral_decision(rt)
    live = rt.polymarket.venue.target
    real_place = live.place

    def dies(**kwargs):  # the order reaches the venue; the process dies before its answer
        real_place(**kwargs)
        return {"status": "uncertain"}  # what the journal completes it with on resume

    live.place = dies
    live.lookup = lambda *a, **k: {"status": "uncertain"}
    assert buy(rt, server, handle)["status"] == "uncertain"
    del live.lookup
    live.place = lambda **_: pytest.fail("an uncertain order is never resent")
    polymarket.tick(rt)
    intent = rt.polymarket.intents[f"{handle}:tool:0"]
    assert intent["result"]["status"] == "resting"
    assert rt.polymarket.order_ids == {intent["order_hash"]: f"{handle}:tool:0"}


def test_a_fill_is_booked_once_when_confirmed_and_never_past_its_order():
    rt, server = live_world(confirm=False)
    handle = collateral_decision(rt)
    result = maker_fill(rt, server, handle, price="0.40")  # rests, then fills as a maker
    assert result["status"] == "resting"
    polymarket.tick(rt)
    assert items(rt, "polymarket.fill") == []  # MATCHED is not final
    server.settle()
    polymarket.tick(rt)
    polymarket.tick(rt)
    (fill,) = items(rt, "polymarket.fill")
    assert fill["size"] == "10" and Decimal(fill["px"]) == Decimal("0.40")
    order_id = result["order_id"]
    # The venue now reports a second execution of the same 10-token order: past its
    # signed size, so the read is malformed (a visible stall), never booked
    # (architect's rule on Sol's round-8 review).
    server.extra_fills = [{"id": "t-extra", "status": "CONFIRMED",
                           "match_time": signed_s(rt, order_id),
                           "taker_order_id": OTHER, "side": "SELL", "size": "5", "price": "0.40",
                           "maker_orders": [{"order_id": order_id,
                                             "asset_id": token(server), "matched_amount": "5",
                                             "price": "0.40", "side": "BUY"}]}]
    before = dict(rt.venue_deltas.get(handle, {}))
    polymarket.tick(rt)
    assert not items(rt, "polymarket.fill_quarantined")
    assert [i["reason"] for i in items(rt, "polymarket.read_malformed")] == [
        "a leg takes its order past its signed size"]
    assert rt.venue_deltas.get(handle, {}) == before
    assert rt.polymarket.filled[order_id] == "10"


def test_unattributed_custody_is_what_no_return_owns_and_the_books_close():
    fake = still_fake(resolutions={"fake-1": (10**15, 0)})
    rt, server = live_world(fake=fake)
    handle = collateral_decision(rt)
    order_id = maker_fill(rt, server, handle)["order_id"]  # 10 at 0.40
    polymarket.tick(rt)
    server.extra_fills = [{"id": "t-extra", "status": "CONFIRMED",
                           "match_time": signed_s(rt, order_id), "taker_order_id": OTHER,
                           "side": "SELL",
                           "size": "5", "price": "0.40", "maker_orders": [
                               {"order_id": order_id,
                                "asset_id": token(server), "matched_amount": "5",
                                "price": "0.40", "side": "BUY"}]}]
    polymarket.tick(rt)  # past the order's 10: malformed, never booked
    rt.clock.now_ns = 10**15
    server.advance(10**15)
    polymarket.tick(rt)
    books = polymarket.custody_books(rt)
    assert books["claimed_micro"] + books["unattributed_micro"] == books["booked_micro"]
    rows = [i for i in items(rt, "venue.settled") if i["custody"] == "polymarket"]
    assert books["booked_micro"] == sum(i["amount"] for i in rows) != 0
    # A claim can never take more than the decision's positions realised.
    assert polymarket.claim_share(rt, "seed-decider", handle, 10**9, "test") <= 6_000_000
    assert rt._summary()["polymarket_custody"] == polymarket.custody_books(rt)


def test_a_resolution_is_the_held_position_s_realized_consequence():
    fake = still_fake(resolutions={"fake-1": (10**15, 0)})
    rt, server = live_world(fake=fake)
    handle = collateral_decision(rt)
    maker_fill(rt, server, handle, price="0.40")
    polymarket.tick(rt)
    rt.clock.now_ns = 10**15
    server.advance(10**15)
    polymarket.tick(rt)
    (resolution,) = items(rt, "polymarket.resolution")
    assert resolution["payout"] == "1" and resolution["size"] == "10"
    assert items(rt, "consequence.resolution")
    settled = [i for i in items(rt, "venue.settled") if i["reference"].startswith("resolution:")]
    assert settled[0]["amount"] == 6_000_000 and settled[0]["handle"] == handle
    # The pot reconciles: what it settled is what its custodian holds.
    assert Decimal(polymarket.reconcile(rt)["drift"]) == 0


def test_the_pot_s_requests_past_their_budget_are_not_sent():
    rt, server = live_world(budget=4, wall=lambda: 1_790_000_000_000_000_000)
    handle = collateral_decision(rt)
    for _ in range(3):
        polymarket.tick(rt)
    sent = len(server.calls)
    result = buy(rt, server, handle)
    assert result["status"] == "rejected"
    assert len(server.calls) == sent


def test_the_published_limits_are_the_enforced_ones():
    rt, server = live_world(principal="2.99")  # a buy of 10 at 0.30 may take 3
    facts = rt.institution_section("admission")["tools"]["polymarket_orders"]
    spec = rt.m.polymarket
    assert facts["principal_micro"] == spec.principal_micro == 2_990_000
    assert facts["order_requests_per_10s"] == spec.order_requests_per_10s
    assert facts["live_orders"] is True
    assert rt.polymarket.venue.target.budget.limit == facts["order_requests_per_10s"]
    handle = collateral_decision(rt)
    assert polymarket.PRINCIPAL_REFUSAL in buy(rt, server, handle)["error"]
    assert "principal_micro" in facts["rules"]["principal"]
    for tool in ("polymarket.open_orders", "polymarket.positions",
                 "polymarket.place_limit", "polymarket.cancel"):
        assert rt.tool_specs[tool]["price_micro_per_call"] == 0


def test_open_orders_and_positions_are_the_pot_s_own_reads():
    rt, server = live_world()
    handle = collateral_decision(rt)
    order_id = buy(rt, server, handle)["order_id"]
    answer, cost = rt._run_tool("seed-decider", handle,
                                {"tool": "polymarket.open_orders", "args": {}})
    assert cost == 0 and answer["status"] == "observed"
    assert [o["order_id"] for o in answer["open_orders"]] == [order_id]
    assert "usdc" not in answer
    pot, _ = rt._run_tool("seed-decider", handle, {"tool": "polymarket.positions", "args": {}})
    assert Decimal(pot["usdc_available"]) == Decimal(47)


def test_a_cancel_is_an_intent_and_releases_the_unfilled_order():
    rt, server = live_world()
    handle = collateral_decision(rt)
    order_id = buy(rt, server, handle)["order_id"]
    result = rt._run_tool("seed-decider", handle, {
        "tool": "polymarket.cancel", "args": {"order_id": order_id}}, slot="tool:1")[0]
    assert result["status"] == "cancelled"
    (cancel,) = [i for i in items(rt, "polymarket.intent")
                 if i["operation"] == "polymarket.cancel"]
    assert cancel["args"] == {"order_id": order_id}
    assert server.fake._orders == {}


def test_a_placement_whose_answer_and_lookups_all_failed_is_still_read_until_terminal():
    """Codex P1 on #177: the order reached the venue, its answer and every scheduled
    lookup failed, the intent was released unresolved, and its hash never entered the
    world's orders: the fill poll never read it, and its fills moved custody unbooked."""
    from factorylab.runtime.venue import UNCERTAIN_ORDER_POLLS

    rt, server = live_world()
    handle = collateral_decision(rt)
    server.lose_answer = True
    server.fail_lookups = UNCERTAIN_ORDER_POLLS + 1
    # The post-only buy reaches the venue and then fills there as a maker.
    assert maker_fill(rt, server, handle, size="5", price="0.70",
                      market="fake-2")["status"] == "uncertain"
    for _ in range(UNCERTAIN_ORDER_POLLS + 1):
        polymarket.tick(rt)
    client_id = f"{handle}:tool:0"
    # Its leg is read as this world's from the first poll (its durable hash), so the
    # confirmed trade binds it, released or not (Sol P1, round 13).
    polymarket.tick(rt)
    polymarket.tick(rt)
    intent = rt.polymarket.intents[client_id]
    assert rt.polymarket.order_ids.get(intent["order_hash"]) == client_id
    (fill,) = items(rt, "polymarket.fill")
    assert fill["order_id"] == intent["order_hash"] and fill["size"] == "5"
    assert rt._order_owner(fill["order_id"]) == handle
    assert [c for c in server.calls if c == ("POST", "/order")] == [("POST", "/order")]


def test_a_released_placement_the_venue_never_saw_is_read_and_never_resent():
    from factorylab.runtime.venue import UNCERTAIN_ORDER_POLLS

    rt, server = live_world()
    handle = collateral_decision(rt)
    live = rt.polymarket.venue.target

    def lost(**_kwargs):  # the order never reaches the venue, and nothing says so
        raise clob.PolymarketUnavailable("transport: TimeoutError")

    live.place = lost
    buy(rt, server, handle)
    for _ in range(UNCERTAIN_ORDER_POLLS + 3):
        polymarket.tick(rt)
    intent = rt.polymarket.intents[f"{handle}:tool:0"]
    assert intent["unresolved"] and intent["order_hash"] not in rt.polymarket.order_ids
    assert ("POST", "/order") not in server.calls


def test_a_released_placement_s_confirmed_trade_binds_it_while_lookups_still_fail():
    from factorylab.runtime.venue import UNCERTAIN_ORDER_POLLS

    rt, server = live_world()
    handle = collateral_decision(rt)
    server.lose_answer = True
    server.fail_lookups = 10**6  # the order status never answers again
    maker_fill(rt, server, handle, size="5", price="0.70", market="fake-2")
    for _ in range(UNCERTAIN_ORDER_POLLS + 2):
        polymarket.tick(rt)
    intent = rt.polymarket.intents[f"{handle}:tool:0"]
    assert rt.polymarket.order_ids.get(intent["order_hash"]) == f"{handle}:tool:0"
    (acknowledged,) = [i for i in items(rt, "polymarket.acknowledged")
                       if i["result"].get("evidence") == "confirmed trade"]
    assert acknowledged["handle"] == handle
    (fill,) = items(rt, "polymarket.fill")
    assert rt._order_owner(fill["order_id"]) == handle


def test_a_fill_confirmed_after_its_market_resolved_is_booked_and_paid_once():
    """Astra P0 on #177: the order's lookup said MATCHED, its trade was not yet CONFIRMED
    when the market resolved; the order was retired, and the confirmed trade and its
    payout were never booked."""
    fake = still_fake(resolutions={"fake-1": (10**15, 0)})
    rt, server = live_world(fake=fake, confirm=False)
    handle = collateral_decision(rt)
    maker_fill(rt, server, handle, price="0.40")  # matched as a maker, not yet final
    polymarket.tick(rt)
    assert items(rt, "polymarket.fill") == []
    rt.clock.now_ns = 10**15
    server.advance(10**15)
    polymarket.tick(rt)
    polymarket.tick(rt)
    server.settle("CONFIRMED")
    for _ in range(3):
        polymarket.tick(rt)
    (fill,) = items(rt, "polymarket.fill")
    assert fill["size"] == "10" and Decimal(fill["px"]) == Decimal("0.40")
    (resolution,) = items(rt, "polymarket.resolution")
    assert resolution["size"] == "10" and resolution["payout"] == "1"
    paid = [i for i in items(rt, "venue.settled") if i["reference"].startswith("resolution:")]
    assert [i["amount"] for i in paid] == [6_000_000] and paid[0]["handle"] == handle
    assert Decimal(polymarket.reconcile(rt)["drift"]) == 0


def test_the_exposure_cap_holds_while_the_venue_s_listings_lag():
    """Astra P1 on #177: a filled buy left the open orders before the positions listing
    showed it, so a second identical buy passed a $5 cap: $8.20 held against it."""
    rt, server = live_world(max_open_micro=5_000_000)
    polymarket.tick(rt)
    token_id = token(server)
    server.hidden_positions = {token_id}  # the Data API has not indexed the fill yet
    # It rests, and fills as a maker: the trade is out, not yet read.
    assert maker_fill(rt, server, collateral_decision(rt), price="0.40")["status"] == "resting"
    second = maker_fill(rt, server, collateral_decision(rt), price="0.40")
    assert second["status"] == "rejected" and "max_open_usd" in second["error"]
    polymarket.tick(rt)  # booked from its confirmed trade: still held at cost
    third = maker_fill(rt, server, collateral_decision(rt), price="0.40")
    # The lagging listing also shows as drift now (the custodian's word is reconciled):
    # either refusal holds the cap.
    assert third["status"] == "rejected" and (
        "max_open_usd" in third["error"] or third["error"] == polymarket.DRIFT_REFUSAL)
    reserved, book = polymarket.local_commitments(rt.polymarket)
    assert reserved + book == Decimal("4")  # what the cap is weighed against


def test_a_positions_listing_is_read_to_its_end_or_the_pot_is_unavailable():
    rt, server = live_world()
    polymarket.tick(rt)
    maker_fill(rt, server, collateral_decision(rt), price="0.40")
    maker_fill(rt, server, collateral_decision(rt), price="0.70", market="fake-2", size="5")
    server.positions_page = 1
    rt.polymarket._account_memo = None
    account = rt.polymarket.account(rt)
    assert len(account["positions"]) == 2


def test_money_gone_that_the_books_do_not_explain_stops_new_exposure_until_it_agrees():
    rt, server = live_world()
    polymarket.tick(rt)
    server.fake._cash -= Decimal(5)  # leaves the wallet, explained by nothing booked
    polymarket.tick(rt)
    refused = buy(rt, server, collateral_decision(rt))
    assert refused["status"] == "rejected" and refused["error"] == polymarket.DRIFT_REFUSAL
    server.fake._cash += Decimal(5)
    polymarket.tick(rt)
    assert buy(rt, server, collateral_decision(rt))["status"] == "resting"


def test_no_order_is_taken_before_the_pot_s_opening_is_read():
    """Codex P1 on #177: an order before the opening is read would put its own fill
    inside the baseline the pot is reconciled against."""
    rt, server = live_world(opened=False)
    server.fail_balance = 1
    polymarket.tick(rt)  # the opening read fails
    assert rt.polymarket.opening is None
    refused = buy(rt, server, collateral_decision(rt))
    assert refused["status"] == "rejected" and refused["error"] == polymarket.OPENING_REFUSAL
    assert ("POST", "/order") not in server.calls
    polymarket.tick(rt)  # the opening is read
    maker_fill(rt, server, collateral_decision(rt))
    polymarket.tick(rt)
    assert Decimal(polymarket.reconcile(rt)["drift"]) == 0


def test_a_cancelled_buy_releases_its_reservation():
    """Codex P1 on #177: a cancelled buy's placement stayed resting in the pot's own
    records, its notional reserved forever: place and cancel enough and every buy fails."""
    rt, server = live_world(max_open_micro=5_000_000)
    polymarket.tick(rt)
    for cycle in range(3):
        handle = collateral_decision(rt)
        order_id = buy(rt, server, handle)["order_id"]  # 10 x 0.30 rests: $3 of $5
        cancel = rt._run_tool("seed-decider", handle, {
            "tool": "polymarket.cancel", "args": {"order_id": order_id}}, slot="tool:1")[0]
        assert cancel["status"] == "cancelled", cycle
    assert polymarket.local_commitments(rt.polymarket) == (Decimal(0), Decimal(0))
    assert buy(rt, server, collateral_decision(rt))["status"] == "resting"


def test_a_placement_rejected_after_its_intent_is_not_polled():
    """Codex P2 on #177: a placement the pot's budget rejected entered the world's orders
    and was looked up and read for fills for the world's life."""
    rt, server = live_world(budget=12, wall=lambda: 1_790_000_000_000_000_000)
    polymarket.tick(rt)
    live = rt.polymarket.venue.target
    place = live.place

    def spent(**kwargs):  # no slot held (a resumed process, say) and none left
        live._reserved = 0
        live.budget.stamps = [1_790_000_000_000_000_000] * 12
        return place(**kwargs)

    live.place = spent
    result = buy(rt, server, collateral_decision(rt))
    assert result["status"] == "rejected" and "budget" in result["error"]
    assert result["order_id"] not in rt.polymarket.order_ids
    assert polymarket._live_orders(rt.polymarket) == {}
    assert polymarket.local_commitments(rt.polymarket) == (Decimal(0), Decimal(0))


def _foreign_resting_order(server, token_id):
    """An order someone else placed from the same wallet, by hand or another process."""
    placed = server.fake.place(client_id="0xforeign", token_id=token_id, is_buy=True,
                               size=Decimal(10), price=Decimal("0.20"))
    server.orders["0x" + "f0" * 32] = {"pm": placed["order_id"], "signed_s": 0}
    server.pm_to_hash[placed["order_id"]] = "0x" + "f0" * 32
    return placed["order_id"]


def test_a_fill_the_consequence_book_refuses_is_quarantined_never_raised(monkeypatch):
    rt, server = live_world()
    handle = collateral_decision(rt)
    maker_fill(rt, server, handle, size="5", price="0.70", market="fake-2")

    def refuses(kind, payload, event):
        raise ValueError("spot sell exceeds long inventory")

    monkeypatch.setattr(rt.consequences, "observe", refuses)
    polymarket.tick(rt)  # does not raise
    (quarantined,) = items(rt, "polymarket.fill_quarantined")
    assert "exceeds long inventory" in quarantined["reason"]
    assert rt.venue_deltas.get(handle, {}) == {}

def test_a_resume_within_ten_seconds_sends_no_second_allowance():
    """Codex P2 on #177: the pot's request stamps lived in memory, so a world resumed
    within 10 s could send another full allowance."""
    rt, server = live_world(budget=5, wall=lambda: 1_790_000_000_000_000_000)
    saved = rt.polymarket.state()
    rt.polymarket.restore(saved)  # what a resume does
    with pytest.raises(clob.BudgetSpent):
        rt.polymarket.venue.target.budget.take()


def _journaled_names(rt, server):
    """Every call a live world journals through its Polymarket venue, over the order
    path, its recoveries, the tick, a seat's reads and a kill."""
    names = []
    venue = rt.polymarket.venue
    call = venue.journal.call

    def recording(name, *args, **kwargs):
        names.append(name)
        return call(name, *args, **kwargs)

    venue.journal = SimpleNamespace(call=recording, recovering=False)
    handle = collateral_decision(rt)
    server.lose_answer = True
    order_id = buy(rt, server, handle)["order_id"]  # lost answer, recovered by lookup
    maker_fill(rt, server, collateral_decision(rt), price="0.40")  # fills on arrival
    polymarket.tick(rt)
    for tool, args in (("polymarket.search", {"query": "event"}),
                       ("polymarket.market", {"market_id": "fake-1"}),
                       ("polymarket.book", {"token_id": token(server)}),
                       ("polymarket.positions", {}), ("polymarket.open_orders", {})):
        rt._run_tool("seed-decider", handle, {"tool": tool, "args": args})
    rt._run_tool("seed-decider", handle, {"tool": "polymarket.cancel",
                                          "args": {"order_id": order_id}}, slot="tool:1")
    polymarket.wind_down(rt)
    return sorted(set(names))


def test_every_live_polymarket_call_resumes_from_an_interrupted_journal():
    """Codex P1 on #177: a crash between ``polymarket.drain_events``'s io.call and its
    io.result made every resume refuse it as an unacknowledged external write. No live
    call is journaled that a resume cannot complete: a read re-runs, an order or a
    cancel completes uncertain for its intent to look up, never resent."""
    rt, server = live_world()
    names = _journaled_names(rt, server)
    assert "polymarket.drain_events" not in names
    assert {"polymarket.place", "polymarket.cancel", "polymarket.poll",
            "polymarket.account", "polymarket.lookup"} <= set(names)
    recorded = []

    def append(item):
        recorded.append(item)
        return len(recorded) - 1

    fingerprint = hashlib.sha256(canonical(encode(((), {})))).hexdigest()
    for name in names:
        journal = RecoveryJournal(SimpleNamespace(append=append), lambda: 0)
        journal.active = journal.recovering = True
        journal.tail = [{"kind": "io.call", "name": name, "input_hash": fingerprint,
                         "seq": 0, "ts": 0}]
        if name in ("polymarket.place", "polymarket.cancel"):
            result = journal.call(name, lambda n=name: pytest.fail(f"{n} sent twice"), (), {})
            assert result == {"status": "uncertain"}, name
        else:
            assert journal.call(name, lambda n=name: {"read": n}, (), {}) == {"read": name}


def test_a_kill_after_resolution_reports_only_the_world_s_unredeemed_tokens():
    """Codex P2 on #177: with the funder holding the same token before this world, the
    wind-down reported the whole wallet position as the world's residual once the market
    resolved."""
    fake = still_fake(resolutions={"fake-1": (10**15, 0)})
    rt, server = live_world(fake=fake)
    maker_fill(rt, server, collateral_decision(rt), price="0.40")  # the world's 10 tokens
    polymarket.tick(rt)
    rt.clock.now_ns = 10**15
    server.advance(10**15)
    polymarket.tick(rt)
    assert items(rt, "polymarket.resolution")
    # On Polymarket the tokens stay in the wallet until redeemed; the funder holds 7 more.
    server.fake._positions[token(server)] = {"size": Decimal(17), "avg_px": Decimal("0.3")}
    rt.polymarket._account_memo = None
    report = polymarket.wind_down(rt)
    assert [p["size"] for p in report["residual"]] == ["10"]


def test_a_cancel_whose_answer_and_lookups_failed_is_settled_by_later_reads():
    """Codex P2 on #177: a cancel released unresolved was never looked at again, so the
    buy it cancelled kept its unfilled notional reserved forever."""
    from factorylab.runtime.venue import UNCERTAIN_ORDER_POLLS

    rt, server = live_world()
    handle = collateral_decision(rt)
    order_id = buy(rt, server, handle)["order_id"]  # 10 x 0.30 rests
    server.lose_cancel_answer = True
    server.fail_lookups = UNCERTAIN_ORDER_POLLS + 1
    rt._run_tool("seed-decider", handle, {"tool": "polymarket.cancel",
                                          "args": {"order_id": order_id}}, slot="tool:1")
    for _ in range(UNCERTAIN_ORDER_POLLS + 1):
        polymarket.tick(rt)
    assert rt.polymarket.intents[f"{handle}:tool:1"]["unresolved"]
    polymarket.tick(rt)
    polymarket.tick(rt)
    assert polymarket.local_commitments(rt.polymarket)[0] == 0
    placement = rt.polymarket.intents[f"{handle}:tool:0"]
    assert placement["result"]["status"] == "cancelled"
    order = next(o for o in rt.consequences.table.orders if o.order_id == order_id)
    assert order.remaining == 0  # the unfilled liability is released too


def test_resolution_reads_rotate_over_what_the_world_holds_or_has_resting_now():
    """Codex P2 on #177: tokens whose orders were all filled or cancelled stayed in the
    resolution rotation, so a held token's market was read ever more rarely."""
    rt, server = live_world()
    for market in ("fake-2", "fake-3"):
        handle = collateral_decision(rt)
        order_id = buy(rt, server, handle, market=market, price="0.10")["order_id"]
        rt._run_tool("seed-decider", handle, {"tool": "polymarket.cancel",
                                              "args": {"order_id": order_id}}, slot="tool:1")
    maker_fill(rt, server, collateral_decision(rt), price="0.40")  # fake-1: held
    polymarket.tick(rt)
    before = len(server.calls)
    for _ in range(3):
        polymarket.tick(rt)
    reads = [path for _method, path in server.calls[before:] if path.startswith("/markets/")]
    assert reads == ["/markets/fake-1"] * 3


def test_a_misbooked_cost_basis_shows_as_drift_never_hidden_by_the_larger_valuation():
    """Sol P0 on #177: reconciliation valued each token at the larger of the custodian's
    listing and the world's own book, so a cost basis booked too high (and its profit)
    reconciled to zero drift. The custodian's word is what the books are checked against."""
    rt, server = live_world()
    maker_fill(rt, server, collateral_decision(rt), price="0.40")  # 10 at 0.41
    polymarket.tick(rt)
    assert Decimal(polymarket.reconcile(rt)["drift"]) == 0
    held = rt.polymarket.cursor["book"][token(server)]
    rt.polymarket.cursor["book"][token(server)] = [held[0], "0.51"]  # $1.00 too high
    rt.polymarket.settled += Decimal(1)  # and the $1.00 of profit it would book
    assert Decimal(polymarket.reconcile(rt)["drift"]) == Decimal(-1)
    assert rt.polymarket.drifting


def test_a_kill_cancels_a_placement_known_only_by_its_durable_hash():
    """Sol P1 on #177: a placement whose answer was lost and whose lookups all failed was
    known only to its intent; the kill did not cancel it and reported flat."""
    from factorylab.runtime.venue import UNCERTAIN_ORDER_POLLS

    rt, server = live_world()
    handle = collateral_decision(rt)
    server.lose_answer = True
    server.fail_lookups = 10**6
    buy(rt, server, handle)
    for _ in range(UNCERTAIN_ORDER_POLLS + 1):
        polymarket.tick(rt)
    intent = rt.polymarket.intents[f"{handle}:tool:0"]
    assert intent["unresolved"] and intent["order_hash"] not in rt.polymarket.order_ids
    rt.polymarket._account_memo = None
    report = polymarket.wind_down(rt)
    assert ("DELETE", "/order") in server.calls and server.fake._orders == {}
    assert report["exposure_state"] != "flat"  # its cancel is not confirmed


def test_a_kill_with_matched_unconfirmed_quantity_is_not_flat():
    """Sol P1 on #177: a buy matched but not CONFIRMED shows in neither open orders nor
    positions; the kill reported flat while $4.50 was still committed."""
    rt, server = live_world(confirm=False)
    maker_fill(rt, server, collateral_decision(rt), price="0.40")
    polymarket.tick(rt)
    rt.polymarket._account_memo = None
    report = polymarket.wind_down(rt)
    # The order matched as a maker, unconfirmed: still the world's, never flat.
    assert report["exposure_state"] == "wind_down_pending"
    assert [(u["size"], u["booked"]) for u in report["unsettled"]] == [("10", "0")]


def test_a_failed_trade_releases_its_matched_quantity_once_the_order_is_terminal():
    """Sol P2 on #177: a fully matched buy whose trade FAILED kept its $4.50 reserved,
    so under a $5 cap every later buy of the same size was refused for good."""
    rt, server = live_world(max_open_micro=5_000_000, confirm=False)
    maker_fill(rt, server, collateral_decision(rt), price="0.40")  # matched, not yet final
    server.settle("FAILED")
    for _ in range(3):
        polymarket.tick(rt)
    assert polymarket.local_commitments(rt.polymarket)[0] == 0
    assert buy(rt, server, collateral_decision(rt), price="0.39")["status"] == "resting"


@pytest.mark.parametrize("live", [True, False])
def test_a_sell_is_refused_before_any_intent(live):
    """The venue takes BUY orders only (architect's decision on Sol's re-review of #177): a
    sale's cost basis would rest on an execution order the venue reveals only piecemeal.
    Sol's scenarios each need a sale; each is refused before any intent, alone, in a
    batch, and at the venue boundary, and nothing reaches the venue."""
    if live:
        rt, server = live_world()
        token_id = token(server)
    else:
        from tests.runtime.test_polymarket_surface import token as fake_token
        from tests.runtime.test_polymarket_surface import world

        rt, server = world(), None
        token_id = fake_token(rt)
    handle = collateral_decision(rt)
    sell = {"tool": "polymarket.place_limit",
            "args": {"token_id": token_id, "side": "sell", "size": "10", "price": "0.59"}}
    refused = rt._run_tool("seed-decider", handle, sell)[0]
    assert "invalid polymarket arguments" in refused["error"]
    assert polymarket.refusal(rt, rt.polymarket, "seed-decider", handle,
                              "polymarket.place_limit", sell["args"]) == (
        polymarket.BUY_ONLY_REFUSAL)
    batch = polymarket.batch_refusal(rt, "seed-decider", handle, [
        ("tool:0", "polymarket.place_limit", {**sell["args"], "side": "buy",
                                               "price": "0.30"}),
        ("tool:1", "polymarket.place_limit", sell["args"])])
    assert batch == (1, polymarket.BUY_ONLY_REFUSAL)
    assert rt.polymarket.intents == {}
    assert "sell" not in rt.tool_specs["polymarket.place_limit"]["args_schema"][
        "properties"]["side"]["enum"]
    assert "BUY orders only" in rt.tool_specs["polymarket.place_limit"]["description"]
    if live:
        assert ("POST", "/order") not in server.calls
        with pytest.raises(clob.PolymarketRefused, match="BUY orders only"):
            rt.polymarket.venue.target.order_identity(
                client_id="c", token_id=token_id, is_buy=False, size=Decimal(10),
                price=Decimal("0.59"), market={"tick_size": "0.01"})


def test_no_blanket_allowance_hides_a_small_unexplained_loss():
    """Sol P1 on #177: an allowance of 0.0001 a listed share absorbed a $0.001 withdrawal
    against 10 held shares (and $10 against 100,000). No allowance is made: a real
    rounding mismatch shows as drift too."""
    rt, server = live_world()
    maker_fill(rt, server, collateral_decision(rt), price="0.40")  # 10 held, cost exact
    polymarket.tick(rt)
    server.fake._cash -= Decimal("0.001")
    polymarket.tick(rt)
    assert rt.polymarket.drifting
    refused = buy(rt, server, collateral_decision(rt), size="5", price="0.30",
                  market="fake-2")
    assert refused["error"] == polymarket.DRIFT_REFUSAL


def test_a_kill_cancels_a_resting_order_the_listing_omits():
    """Sol P1 on #177: an acknowledged resting buy missing from the orders listing got no
    cancel, and the kill reported flat with a real order resting."""
    rt, server = live_world()
    buy(rt, server, collateral_decision(rt))  # rests
    server.orders_lag = True
    rt.polymarket._account_memo = None
    report = polymarket.wind_down(rt)
    assert ("DELETE", "/order") in server.calls and server.fake._orders == {}
    assert report["cancelled"] == 1


def test_a_kill_counts_what_the_world_holds_when_the_listing_omits_it():
    """Sol P1 on #177: a confirmed 10-share position missing from the positions listing
    left the kill reporting flat with no residual."""
    rt, server = live_world()
    maker_fill(rt, server, collateral_decision(rt), price="0.40")
    polymarket.tick(rt)
    server.hidden_positions = {token(server)}
    rt.polymarket._account_memo = None
    report = polymarket.wind_down(rt)
    assert report["exposure_state"] == "wind_down_pending"
    assert [p["size"] for p in report["residual"]] == ["10"]


def test_one_failed_read_or_cancel_never_stops_the_kill_reaching_every_order():
    """Sol P1 on #177: a failed balance read stopped the kill before any cancel, and a
    failed first cancel stopped the second."""
    rt, server = live_world()
    buy(rt, server, collateral_decision(rt))
    buy(rt, server, collateral_decision(rt), market="fake-2", price="0.50")
    server.fail_balance = 1
    rt.polymarket._account_memo = None
    live = rt.polymarket.venue.target
    cancel, attempts = live.cancel, []

    def first_fails(**kwargs):
        attempts.append(kwargs["order_id"])
        if len(attempts) == 1:
            raise clob.PolymarketUnavailable("transport: TimeoutError")
        return cancel(**kwargs)

    live.cancel = first_fails
    report = polymarket.wind_down(rt)
    assert len(attempts) == 2 and len(server.fake._orders) == 1
    assert report["exposure_state"] == "unknown"


def test_a_failed_trade_lets_its_order_s_account_close():
    """Sol P2 on #177: a fully FAILED buy released its collateral reservation, but its
    consequence order stayed remaining=10, unconfirmed, and its account could not close."""
    rt, server = live_world(confirm=False)
    maker_fill(rt, server, collateral_decision(rt), price="0.40")
    server.settle("FAILED")
    for _ in range(3):
        polymarket.tick(rt)
    (order,) = rt.consequences.table.orders
    assert order.remaining == 0 and order.executed == 0 and order.confirmed == 0


def test_a_released_placement_cancelled_by_the_kill_is_not_left_unanswered():
    """Sol P2 on #177: a released lost-answer placement the kill cancelled, with the
    venue's word cancelled and nothing matched, was still listed unanswered: unknown."""
    from factorylab.runtime.venue import UNCERTAIN_ORDER_POLLS

    rt, server = live_world()
    handle = collateral_decision(rt)
    server.lose_answer = True
    server.fail_lookups = 2 * UNCERTAIN_ORDER_POLLS + 1
    buy(rt, server, handle)
    for _ in range(UNCERTAIN_ORDER_POLLS + 1):
        polymarket.tick(rt)
    assert rt.polymarket.intents[f"{handle}:tool:0"]["unresolved"]
    server.fail_lookups = 0
    rt.polymarket._account_memo = None
    report = polymarket.wind_down(rt)
    assert report["cancelled"] == 1 and report["unanswered"] == []
    assert report["exposure_state"] == "flat"


def test_principal_at_risk_is_the_world_s_own_outlay_never_the_wallet():
    """Architect's decision on Sol's third review of #177: the principal is the world's
    own ledger (every buy that may have executed or may still execute, at its limit; a
    post-only order pays no fee), so no deposit, withdrawal or listing omission by anyone
    makes room or takes it away."""
    rt, server = live_world(principal="9.99")  # the wallet holds $50 of the funder's
    assert maker_fill(rt, server, collateral_decision(rt), price="0.40")["status"] == (
        "resting")
    polymarket.tick(rt)
    assert polymarket.principal_at_risk(rt.polymarket) == Decimal("4")  # 10 x 0.40
    server.hidden_positions = {token(server)}
    server.fake._cash += Decimal(100)  # a deposit makes no room
    rt.polymarket._account_memo = None
    rt.polymarket.drifting = False  # drift is its own halt, not tested here
    assert buy(rt, server, collateral_decision(rt), price="0.30", market="fake-2",
               slot="tool:1")["status"] == "resting"  # 10 x 0.30: 7 of 9.99
    third = buy(rt, server, collateral_decision(rt), price="0.30", market="fake-3")
    assert third["status"] == "rejected" and third["error"] == polymarket.PRINCIPAL_REFUSAL


def test_the_principal_cap_is_the_world_s_lifetime_outlay_never_released():
    """Architect's decision on Sol's round-4 review of #177: principal_usd is the world's
    lifetime Polymarket outlay. A resolution, a payout or a redemption gives no room
    back (Sol P1: resolution released it before any payment arrived); once the cap is
    used, buying stops for the world's life."""
    fake = still_fake(resolutions={"fake-1": (10**15, 0)})
    rt, server = live_world(fake=fake, principal="4.99")
    maker_fill(rt, server, collateral_decision(rt), price="0.40")  # 4 of 4.99
    polymarket.tick(rt)
    rt.clock.now_ns = 10**15
    server.advance(10**15)
    polymarket.tick(rt)
    assert items(rt, "polymarket.resolution")  # it paid 10 winning tokens
    refused = buy(rt, server, collateral_decision(rt), size="5", price="0.20",
                  market="fake-2")  # 1 more: 5 of 4.99
    assert refused["error"] == polymarket.PRINCIPAL_REFUSAL


def test_resolved_custody_stays_the_world_s_residual_until_it_is_redeemed():
    """Sol P1 on #177: ten resolved winning tokens, unredeemed, missing from the listing
    left the kill reporting flat. A listing's omission is no evidence of redemption."""
    fake = still_fake(resolutions={"fake-1": (10**15, 0)})
    rt, server = live_world(fake=fake)
    maker_fill(rt, server, collateral_decision(rt), price="0.40")
    polymarket.tick(rt)
    rt.clock.now_ns = 10**15
    server.advance(10**15)
    polymarket.tick(rt)
    server.hidden_positions = {token(server)}  # (the fake redeems at once; it is hidden)
    rt.polymarket._account_memo = None
    report = polymarket.wind_down(rt)
    assert report["exposure_state"] == "wind_down_pending"
    assert [p["size"] for p in report["residual"]] == ["10"]


def test_a_cancelled_order_s_failed_legs_are_netted_before_it_is_confirmed():
    """Sol P2 on #177: a resting buy matched 5 whose leg FAILED, then was cancelled; the
    cancel cleared its remaining liability, so the failed-leg netting was skipped and it
    was confirmed at the gross 5 matched against 0 executed: its account never closed."""
    rt, server = live_world()
    handle = collateral_decision(rt)
    order_id = buy(rt, server, handle)["order_id"]  # 10 at 0.30 rests
    pm = server.orders[order_id]["pm"]
    server.fake._all_orders[pm]["filled"] = Decimal(5)  # the venue matched 5 ...
    server.fake._all_orders[pm]["remaining"] = Decimal(5)
    server.extra_fills = [{"id": "t-failed", "status": "FAILED",  # ... and the leg failed
                           "match_time": signed_s(rt, order_id), "taker_order_id": OTHER,
                           "side": "SELL",
                           "size": "5", "price": "0.30", "maker_orders": [
                               {"order_id": order_id,
                                "asset_id": token(server), "matched_amount": "5",
                                "price": "0.30", "side": "BUY"}]}]
    polymarket.tick(rt)
    rt._run_tool("seed-decider", handle, {"tool": "polymarket.cancel",
                                          "args": {"order_id": order_id}}, slot="tool:1")
    for _ in range(3):
        polymarket.tick(rt)
    (order,) = rt.consequences.table.orders
    assert order.remaining == 0 and order.executed == 0 and order.confirmed == 0


def test_every_live_order_is_post_only_and_one_that_would_cross_is_refused_by_the_venue():
    """Architect's decision on Sol's round-4 review of #177: the venue charges takers
    only, and a post-only order that would cross is rejected, never filled
    (concepts/order-lifecycle, "Post-Only Orders"). Every live order is sent post-only,
    so no fee is ever charged: the fee machinery is gone."""
    rt, server = live_world()
    posted = []
    send = server.__call__

    def watch(method, url, headers, body):
        if method == "POST" and url.endswith("/order"):
            posted.append(json.loads(body))
        return send(method, url, headers, body)

    rt.polymarket.venue.target.send = watch
    crossing = buy(rt, server, collateral_decision(rt), price="0.45")  # the ask is 0.41
    assert crossing["status"] == "rejected" and "crosses the book" in crossing["error"]
    assert server.fake._all_orders == {}
    assert buy(rt, server, collateral_decision(rt), price="0.30")["status"] == "resting"
    assert [p["postOnly"] for p in posted] == [True, True]
    description = rt.tool_specs["polymarket.place_limit"]["description"]
    assert "post-only" in description and "no fee" in description


def test_the_simulated_and_live_venues_publish_one_order_physics():
    """Architect's decision on #177: the simulated venue has the live venue's physics, so
    a rehearsal shows the world a live run lives in, and published = enforced holds on
    both. Every Polymarket tool renders the same on both apart from the venue's name,
    and the world.read order section apart from the flag naming the venue kind."""
    from tests.runtime.test_polymarket_surface import world

    live, _server = live_world(principal="40", budget=7)
    sim = world(principal_micro=40_000_000, order_requests_per_10s=7)
    names = polymarket.VENUE_NAMES

    def rendered(rt, venue):
        return {tool: json.loads(json.dumps(spec).replace(names[venue], "<venue>"))
                for tool, spec in rt.tool_specs.items() if tool.startswith("polymarket.")}

    tools = rendered(live, "live")
    assert set(polymarket.WRITES) | {polymarket.ACCOUNT, polymarket.OPEN_ORDERS} <= set(tools)
    assert tools == rendered(sim, "fake")
    assert names["live"] in live.tool_specs["polymarket.place_limit"]["description"]
    assert names["fake"] in sim.tool_specs["polymarket.place_limit"]["description"]
    live_facts, sim_facts = (rt.institution_section("admission")["tools"]["polymarket_orders"]
                             for rt in (live, sim))
    assert (live_facts.pop("live_orders"), sim_facts.pop("live_orders")) == (True, False)
    assert live_facts == sim_facts
    assert "every venue kind" in live_facts["rules"]["maker"]


def test_a_simulated_crossing_order_is_rejected_before_it_executes():
    """The simulated venue is post-only too: an order at or above the ask is the venue's
    rejection, recorded as the intent's answer; nothing fills and no principal is used."""
    from tests.runtime.test_polymarket_surface import buy as sim_buy
    from tests.runtime.test_polymarket_surface import world

    rt = world(principal_micro=40_000_000)
    handle = collateral_decision(rt)
    result = sim_buy(rt, handle, price="0.41")  # fake-1's ask is 0.41
    assert result["status"] == "rejected"
    assert result["error"] == "invalid post-only order: order crosses book"
    assert rt.polymarket.venue.target.account()["positions"] == []
    assert polymarket.principal_at_risk(rt.polymarket) == 0
    polymarket.tick(rt)
    assert not [i for i in rt.ledger._recovery_items() if i.get("kind") == "polymarket.fill"]


def test_a_trade_that_contradicts_the_maker_only_venue_halts_buying():
    """A trade that reports this world's order as its taker, or a fee on it, contradicts
    the published fact: it is recorded as drift and halts buying; no fee is booked."""
    rt, server = live_world()
    order_id = buy(rt, server, collateral_decision(rt))["order_id"]
    server.extra_fills = [{"id": "t-x", "status": "CONFIRMED",
                           "match_time": signed_s(rt, order_id), "taker_order_id": order_id,
                           "asset_id": token(server),
                           "side": "BUY",
                           "size": "10", "price": "0.30", "fee_rate_bps": "500",
                           "maker_orders": []}]
    polymarket.tick(rt)
    (fill,) = items(rt, "polymarket.fill")
    assert fill["fee_usd"] == "0"
    assert [i for i in items(rt, "polymarket.drift") if i.get("reason")]
    refused = buy(rt, server, collateral_decision(rt), price="0.20", market="fake-2")
    assert refused["error"] == polymarket.MAKER_ONLY_REFUSAL


def test_a_discovered_partial_cancel_releases_its_unfilled_liability():
    """Sol P2 on #177: a released placement discovered cancelled with 5 of 10 confirmed
    and no failed legs stayed remaining=5, unconfirmed: the release required a failed
    leg. Terminal evidence that agrees with what is booked releases it."""
    from factorylab.runtime.venue import UNCERTAIN_ORDER_POLLS

    rt, server = live_world()
    handle = collateral_decision(rt)
    server.lose_answer = True
    server.fail_lookups = 10**6
    buy(rt, server, handle)  # 10 at 0.30, its answer lost
    for _ in range(UNCERTAIN_ORDER_POLLS + 1):
        polymarket.tick(rt)
    intent = rt.polymarket.intents[f"{handle}:tool:0"]
    order_id = intent["order_hash"]
    pm = server.orders[order_id]["pm"]
    server.fake._all_orders[pm].update(filled=Decimal(5), remaining=Decimal(5))
    server.trades.append({"id": "t-5", "status": "CONFIRMED",
                          "match_time": signed_s_of(intent), "taker_order_id": OTHER,
                          "side": "SELL",
                          "size": "5", "price": "0.30", "maker_orders": [
                              {"order_id": order_id,
                               "asset_id": token(server), "matched_amount": "5",
                               "price": "0.30", "side": "BUY"}]})
    polymarket.tick(rt)  # the confirmed trade binds the order; 5 are booked
    server.fake.cancel(client_id="by-hand", order_id=pm)  # the rest is cancelled
    server.fail_lookups = 0
    for _ in range(4):
        polymarket.tick(rt)
    (order,) = rt.consequences.table.orders
    assert (order.remaining, order.executed, order.confirmed) == (0, 5, 5)


def _partial(rt, server, order_id, *, status="CONFIRMED", trade_id="t-5", size="5"):
    """``size`` of an order's ten match as a maker in one trade of ``status``."""
    pm = server.orders[order_id]["pm"]
    server.fake._all_orders[pm].update(filled=Decimal(size),
                                       remaining=Decimal(10) - Decimal(size))
    row = {"id": trade_id, "status": status, "match_time": signed_s(rt, order_id),
           "taker_order_id": OTHER, "side": "SELL", "size": size, "price": "0.30", "maker_orders": [
               {"order_id": order_id,
                "asset_id": token(server), "matched_amount": size, "price": "0.30",
                "side": "BUY"}]}
    server.trades.append(row)
    return row


def test_a_terminal_read_back_without_its_matched_size_erases_no_outlay():
    """Sol P1 (round 5) on #177: five of a ten-share $0.30 buy fill ($1.50), the rest is
    cancelled, and the read-back omits size_matched. An unstated quantity is unknown,
    never 0: the order keeps its whole size counted, never less than what was booked,
    so under a $3 cap a second $3 buy is refused."""
    rt, server = live_world(principal="3")
    order_id = buy(rt, server, collateral_decision(rt))["order_id"]
    _partial(rt, server, order_id)
    # The custodian holds what the confirmed fill moved (the books reconcile).
    server.fake._cash -= Decimal("1.5")
    server.fake._positions[token(server)] = {"size": Decimal(5), "avg_px": Decimal("0.30")}
    polymarket.tick(rt)
    assert rt.polymarket.filled[order_id] == "5"
    server.order_answer = lambda answer: {k: v for k, v in answer.items()
                                          if k != "size_matched"}
    cancel = rt._run_tool("seed-decider", collateral_decision(rt), {
        "tool": "polymarket.cancel", "args": {"order_id": order_id}}, slot="tool:1")[0]
    assert cancel["status"] != "cancelled" or cancel.get("filled_size") is not None
    for _ in range(3):
        polymarket.tick(rt)
    assert polymarket.principal_at_risk(rt.polymarket) >= Decimal("1.5")
    assert polymarket.principal_at_risk(rt.polymarket) == Decimal(3)
    again = buy(rt, server, collateral_decision(rt), slot="tool:2", market="fake-2")
    assert again["error"] == polymarket.PRINCIPAL_REFUSAL


def test_the_cap_is_every_signed_commitment_but_an_outright_refusal():
    """Whatever became of a placement, its size x limit stays committed; only the
    venue's outright refusal of the submission frees it."""
    placed = {"operation": "polymarket.place_limit", "order_hash": "0x1",
              "args": {"side": "buy", "size": "10", "price": "0.30"},
              "result": {"status": "cancelled", "filled_size": "0"}}
    surface = SimpleNamespace(intents={"c": placed}, cursor={}, filled={})
    for result in ({"status": "cancelled", "filled_size": "0"}, {"status": "cancelled"},
                   {"status": "rejected"}, {"status": "uncertain"}):
        placed["result"] = result
        assert polymarket.principal_at_risk(surface) == Decimal(3)
    placed["result"] = {"status": "rejected", "venue_refused": True}
    assert polymarket.principal_at_risk(surface) == 0


@pytest.mark.parametrize("status", ["MATCHED", "MINED", "CONFIRMED", "FAILED"])
def test_a_taker_leg_at_any_trade_status_halts_buying(status):
    """Sol P1 (round 5) on #177: a trade that reports this world's post-only order as its
    taker contradicts the venue whatever its settlement status; it is recorded and halts
    buying, and money is still booked from CONFIRMED trades alone."""
    rt, server = live_world()
    order_id = buy(rt, server, collateral_decision(rt))["order_id"]
    server.trades.append({"id": "t-x", "status": status,
                          "match_time": signed_s(rt, order_id), "taker_order_id": order_id,
                          "asset_id": token(server),
                          "side": "BUY",
                          "size": "10", "price": "0.30", "maker_orders": []})
    polymarket.tick(rt)
    assert rt.polymarket.contradicted
    assert [i for i in items(rt, "polymarket.drift") if i.get("reason")]
    assert bool(items(rt, "polymarket.fill")) == (status == "CONFIRMED")
    refused = buy(rt, server, collateral_decision(rt), slot="tool:1", market="fake-2",
                  price="0.20")
    assert refused["error"] == polymarket.MAKER_ONLY_REFUSAL


def test_a_fee_disclosed_on_a_leg_already_booked_halts_buying():
    """A maker leg booked CONFIRMED with no fee, re-listed later with a fee on it: the
    disclosure is read on every sighting, independently of the fill's deduplication."""
    rt, server = live_world()
    order_id = buy(rt, server, collateral_decision(rt))["order_id"]
    row = _partial(rt, server, order_id)
    polymarket.tick(rt)
    assert rt.polymarket.filled[order_id] == "5" and not rt.polymarket.contradicted
    row["maker_orders"][0]["fee_rate_bps"] = "500"
    polymarket.tick(rt)
    assert rt.polymarket.contradicted and len(items(rt, "polymarket.fill")) == 1
    refused = buy(rt, server, collateral_decision(rt), slot="tool:1", market="fake-2",
                  price="0.20")
    assert refused["error"] == polymarket.MAKER_ONLY_REFUSAL
    from factorylab.runtime.resume import restore_runtime, runtime_state

    twin, _ = live_world(opened=False)
    restore_runtime(twin, runtime_state(rt))
    assert twin.polymarket.contradicted


def test_a_leg_that_fails_after_the_cancel_is_never_confirmed_executed():
    """Sol P2 (round 5) on #177: five match, the rest is cancelled, and the match later
    FAILS. The order is confirmed only once every matched leg is final, at what really
    executed: nothing."""
    rt, server = live_world()
    handle = collateral_decision(rt)
    order_id = buy(rt, server, handle)["order_id"]
    row = _partial(rt, server, order_id, status="MATCHED")
    polymarket.tick(rt)
    cancel = rt._run_tool("seed-decider", handle, {
        "tool": "polymarket.cancel", "args": {"order_id": order_id}}, slot="tool:1")[0]
    assert cancel["status"] == "cancelled" and cancel["filled_size"] == "5"
    polymarket.tick(rt)
    (order,) = rt.consequences.table.orders
    assert order.confirmed is None  # its matched leg is not final yet
    row["status"] = "FAILED"
    for _ in range(2):
        polymarket.tick(rt)
    (order,) = rt.consequences.table.orders
    assert (order.remaining, order.executed, order.confirmed) == (0, 0, 0)


def test_a_fully_filled_order_stops_pulling_the_fill_read_back():
    """Sol P2 (round 5) on #177: an order confirmed filled by its own read-back carries
    that into its placement, so the fill poll no longer reads for it."""
    rt, server = live_world()
    order_id = maker_fill(rt, server, collateral_decision(rt), price="0.30")["order_id"]
    for _ in range(2):
        polymarket.tick(rt)
    assert rt.polymarket.filled[order_id] == "10"
    (order,) = rt.consequences.table.orders
    assert order.confirmed is not None
    placement = rt.polymarket.intents[rt.polymarket.order_ids[order_id]]["result"]
    assert (placement["status"], placement["filled_size"]) == ("filled", "10")
    assert polymarket._live_orders(rt.polymarket)[order_id]["open"] is False


def test_a_venue_row_missing_a_quantity_is_unread_never_zero():
    """Sol P1 (round 5) on #177, swept: a trade leg with no id or time, an open order
    with no matched size and a position with no cost are each unknown, never 0: the read
    that carries one is unread (nothing booked, the pot unavailable), never smaller."""
    rt, server = live_world()
    order_id = buy(rt, server, collateral_decision(rt))["order_id"]
    row = _partial(rt, server, order_id)
    trade_id = row.pop("id")
    polymarket.tick(rt)
    assert order_id not in rt.polymarket.filled
    row["id"], match_time = trade_id, row.pop("match_time")
    polymarket.tick(rt)
    assert order_id not in rt.polymarket.filled
    row["match_time"] = match_time
    polymarket.tick(rt)
    assert rt.polymarket.filled[order_id] == "5"
    venue = rt.polymarket.venue.target
    server.order_answer = lambda answer: {k: v for k, v in answer.items()
                                          if k != "size_matched"}
    with pytest.raises(ValueError):
        venue.account()
    server.order_answer = None
    server.fake._positions[token(server)] = {"size": Decimal(5), "avg_px": Decimal("0.3")}
    server.position_row = lambda position: {k: v for k, v in position.items()
                                            if k != "avgPrice"}
    with pytest.raises(ValueError):
        venue.account()
    polymarket.tick(rt)  # a new tick reads the pot afresh
    refused = buy(rt, server, collateral_decision(rt), slot="tool:1", market="fake-2",
                  price="0.20")
    assert refused["error"].startswith("polymarket pot unavailable")


def test_the_cap_counts_every_signed_placement_whatever_became_of_it():
    """Architect's decision on Sol's round-6 review of #177: the cap bounds lifetime
    signed commitments, size x limit of every placement, forever. No cancel, read-back,
    matched size, failed leg, quarantine or resolution gives room back."""
    rt, server = live_world(principal="5")
    handle = collateral_decision(rt)
    order_id = buy(rt, server, handle)["order_id"]  # 10 at 0.30: 3 signed
    cancel = rt._run_tool("seed-decider", handle, {
        "tool": "polymarket.cancel", "args": {"order_id": order_id}}, slot="tool:1")[0]
    assert (cancel["status"], cancel["filled_size"]) == ("cancelled", "0")
    for _ in range(2):
        polymarket.tick(rt)
    assert polymarket.principal_at_risk(rt.polymarket) == Decimal(3)
    again = buy(rt, server, collateral_decision(rt), slot="tool:2", market="fake-2")
    assert again["error"] == polymarket.PRINCIPAL_REFUSAL


def test_only_an_explicit_venue_refusal_of_the_submission_frees_its_commitment():
    """A POST the venue answers with an explicit 4xx error body never became an order;
    a malformed acknowledgement, or a 4xx with no body, proves nothing: the order is
    uncertain, counted whole, and stays a cancellation target (Sol P1, round 6)."""
    rt, server = live_world(principal="10")
    crossing = buy(rt, server, collateral_decision(rt), price="0.45")  # the ask is 0.41
    assert crossing["status"] == "rejected"
    assert polymarket.principal_at_risk(rt.polymarket) == 0
    server.post_answer = lambda answer: {k: v for k, v in answer.items() if k != "success"}
    unsure = buy(rt, server, collateral_decision(rt), slot="tool:1")
    assert unsure["status"] != "rejected"
    server.post_answer = None
    (placed,) = [i for i in rt.polymarket.intents.values()
                 if i["operation"] == "polymarket.place_limit" and i["args"]["price"] == "0.30"]
    assert placed["order_hash"] in polymarket._live_targets(rt.polymarket)
    assert polymarket.principal_at_risk(rt.polymarket) == Decimal(3)
    def refuse_bare():
        raise clob.ClobHttpError(400)  # a 4xx whose body states nothing

    server.on_post = refuse_bare
    bare = buy(rt, server, collateral_decision(rt), slot="tool:2", market="fake-2",
               price="0.20")
    assert bare["status"] != "rejected"
    assert polymarket.principal_at_risk(rt.polymarket) == Decimal(5)


def test_a_malformed_row_never_discards_a_contradiction_found_beside_it():
    """Sol P1 (round 6) on #177: a fee on one leg of this world's is found before any
    row is parsed, and kept outside the poll's transactional cursor, so a malformed leg
    in the same read, which leaves the read unread, cannot erase the halt."""
    rt, server = live_world()
    order_id = buy(rt, server, collateral_decision(rt))["order_id"]
    row = _partial(rt, server, order_id)
    row["maker_orders"][0]["fee_rate_bps"] = "500"
    server.trades.append({"id": "t-bad", "status": "FAILED",
                          "match_time": signed_s(rt, order_id), "taker_order_id": OTHER,
                          "side": "SELL",
                          "size": "1", "price": "0.30", "maker_orders": [
                              {"order_id": order_id,
                               "asset_id": token(server), "matched_amount": "garbage",
                               "price": "0.30", "side": "BUY"}]})
    for _ in range(2):
        polymarket.tick(rt)
    assert order_id not in rt.polymarket.filled  # the read is unread: nothing booked
    assert rt.polymarket.contradicted
    refused = buy(rt, server, collateral_decision(rt), slot="tool:1", market="fake-2",
                  price="0.20")
    assert refused["error"] == polymarket.MAKER_ONLY_REFUSAL


def test_a_placement_the_budget_cannot_send_is_refused_before_it_is_signed():
    """Architect's decision on #177: the submission's request slot is reserved at
    admission, before the intent and the signature, so a spent budget refuses the
    placement there: nothing is signed, nothing is committed against the cap."""
    rt, server = live_world(principal="10")
    venue = rt.polymarket.venue.target
    signed = []
    sign = clob.order_signature

    def counting(*args, **kwargs):
        signed.append(1)
        return sign(*args, **kwargs)

    clob.order_signature = counting
    try:
        handle = collateral_decision(rt)
        account = rt.polymarket.account(rt)  # the tick's pot, read while there was room
        assert account is not None
        polymarket._write_market(rt, rt.polymarket, token(server))  # its market id known
        venue.budget.spend_all()
        venue.budget.stamps.pop()  # room for the write's market read, none to send it
        refused = buy(rt, server, handle)
        assert refused["error"] == "polymarket order request budget spent"
        assert signed == [] and not items(rt, "polymarket.intent")
        assert polymarket.principal_at_risk(rt.polymarket) == 0
    finally:
        clob.order_signature = sign


# --- Sol's round-7 review of #177, each scenario as a regression --------------------------


def test_a_refusal_mixing_a_duplicate_keeps_the_commitment_and_the_target():
    """#1: the venue accepts a $3 buy, then answers 400 with a duplicate beside a balance
    error. That proves nothing: the order is uncertain, counted, and a cancel target."""
    rt, server = live_world(principal="3")
    server.post_error = clob.ClobHttpError(
        400, {"error": "Duplicated", "detail": "not enough balance / allowance"})
    result = buy(rt, server, collateral_decision(rt))
    server.post_error = None
    assert result["status"] != "rejected" and "venue_refused" not in result
    assert polymarket.principal_at_risk(rt.polymarket) == Decimal(3)
    again = buy(rt, server, collateral_decision(rt), slot="tool:1", market="fake-2",
                price="0.20")
    assert again["error"] == polymarket.PRINCIPAL_REFUSAL
    (placed,) = [i for i in rt.polymarket.intents.values()
                 if i["operation"] == "polymarket.place_limit"]
    assert placed["order_hash"] in polymarket._live_targets(rt.polymarket)


@pytest.mark.parametrize("shape", ["null row", "null maker leg"])
def test_a_malformed_row_type_never_erases_a_contradiction_beside_it(shape):
    """#2: ``[null, charged own trade]`` or ``maker_orders=[null, charged leg]``: the read
    is unread, and the halt found before parsing stands."""
    rt, server = live_world()
    order_id = buy(rt, server, collateral_decision(rt))["order_id"]
    row = _partial(rt, server, order_id)
    row["maker_orders"][0]["fee_rate_bps"] = "500"
    if shape == "null row":
        server.trades_rows = lambda rows: [None, *rows]
    else:
        row["maker_orders"].insert(0, None)
    polymarket.tick(rt)
    assert rt.polymarket.contradicted and order_id not in rt.polymarket.filled
    refused = buy(rt, server, collateral_decision(rt), slot="tool:1", market="fake-2",
                  price="0.20")
    assert refused["error"] == polymarket.MAKER_ONLY_REFUSAL


@pytest.mark.parametrize("variant", ["upper taker", "upper charged maker", "feeRateBps"])
def test_hash_case_and_fee_spelling_never_hide_a_contradiction(variant):
    """#3: an upper-cased hash, or a fee under another key, still halts buying."""
    rt, server = live_world()
    order_id = buy(rt, server, collateral_decision(rt))["order_id"]
    upper = "0x" + order_id[2:].upper()
    if variant == "upper taker":
        server.trades.append({"id": "t-x", "status": "MATCHED",
                              "match_time": signed_s(rt, order_id), "taker_order_id": upper,
                              "asset_id": token(server),
                              "side": "BUY", "size": "10", "price": "0.30",
                              "maker_orders": []})
    else:
        row = _partial(rt, server, order_id)
        leg = row["maker_orders"][0]
        leg["order_id"] = upper if variant == "upper charged maker" else order_id
        leg["feeRateBps" if variant == "feeRateBps" else "fee_rate_bps"] = "500"
    polymarket.tick(rt)
    assert rt.polymarket.contradicted
    refused = buy(rt, server, collateral_decision(rt), slot="tool:1", market="fake-2",
                  price="0.20")
    assert refused["error"] == polymarket.MAKER_ONLY_REFUSAL


def test_a_contradictory_acknowledgement_keeps_its_order_a_cancel_target():
    """#4: ``success: false`` with a balance error, this order's id and ``status: live``,
    while the order rests. It is uncertain; the kill cancels it though the listing lags."""
    rt, server = live_world()
    server.post_answer = lambda answer: {**answer, "success": False,
                                         "errorMsg": "not enough balance / allowance"}
    result = buy(rt, server, collateral_decision(rt))
    server.post_answer = None
    assert result["status"] != "rejected"
    server.orders_lag = True
    rt.polymarket._account_memo = None
    polymarket.wind_down(rt)
    assert ("DELETE", "/order") in server.calls and server.fake._orders == {}


@pytest.mark.parametrize("paid", ["1.40", "0.50", "-0.30"])
def test_an_impossible_fill_price_is_never_booked(paid):
    """#5: a maker leg of this world's at $1.40, above its signed $0.30 limit, or below 0:
    the page is malformed, nothing is booked and the cursor stays."""
    rt, server = live_world()
    order_id = buy(rt, server, collateral_decision(rt))["order_id"]
    row = _partial(rt, server, order_id)
    row["maker_orders"][0]["price"] = paid
    before = json.loads(json.dumps(rt.polymarket.cursor))
    polymarket.tick(rt)
    assert order_id not in rt.polymarket.filled
    for key in ("seen", "book", "booked", "after", "failed"):
        assert rt.polymarket.cursor.get(key) == before.get(key), key


def test_money_the_books_do_not_explain_halts_buying_whichever_way_it_moved():
    """Architect's decision on Sol's round-7 review: unexplained money in either
    direction means the books are wrong."""
    rt, server = live_world()
    server.fake._cash += Decimal(10)  # money arrived that no fill or payout explains
    polymarket.tick(rt)
    assert rt.polymarket.drifting
    refused = buy(rt, server, collateral_decision(rt))
    assert refused["error"] == polymarket.DRIFT_REFUSAL


def test_a_stalled_read_is_visible_once_per_reason_per_window():
    """Architect's decision on #177: a malformed row stalls the trades read, and the
    stall is ledgered with its reason, at most once a reason per MALFORMED_LEDGER_TICKS."""
    rt, server = live_world()
    order_id = buy(rt, server, collateral_decision(rt))["order_id"]
    row = _partial(rt, server, order_id)
    row["maker_orders"][0]["price"] = "1.40"
    for _ in range(3):
        rt.ticks_consumed += 1
        polymarket.tick(rt)
    rows = items(rt, "polymarket.read_malformed")
    assert len(rows) == 1 and "maker price" in rows[0]["reason"]
    rt.ticks_consumed += polymarket.MALFORMED_LEDGER_TICKS
    polymarket.tick(rt)
    assert len(items(rt, "polymarket.read_malformed")) == 2
    assert order_id not in rt.polymarket.filled


# --- Sol's round-8 review of #177: a leg of ours is its signed order but for its size ------


def test_a_leg_reported_under_another_token_s_hash_is_never_booked():
    """#1: YES and NO buys rest at $0.30; only NO fills, but the venue reports YES's hash
    on the leg, with ``asset_id`` NO. The leg is not the order YES signed: the read is
    malformed and nothing is booked to YES."""
    rt, server = live_world()
    yes = buy(rt, server, collateral_decision(rt))["order_id"]
    buy(rt, server, collateral_decision(rt), slot="tool:1", market="fake-1")
    no = token(server, side=1)
    server.trades.append({"id": "t-no", "status": "CONFIRMED",
                          "match_time": signed_s(rt, yes), "taker_order_id": OTHER,
                          "side": "SELL", "size": "10", "price": "0.30",
                          "maker_orders": [{"order_id": yes, "asset_id": no,
                                            "matched_amount": "10", "price": "0.30",
                                            "side": "BUY"}]})
    polymarket.tick(rt)
    assert yes not in rt.polymarket.filled
    assert items(rt, "polymarket.read_malformed")


def test_a_maker_leg_below_its_limit_is_never_booked():
    """#2: a maker executes at its own price: a leg of the $0.30 order reported at $0.20
    would invent a dollar of profit. The read is malformed, never booked."""
    rt, server = live_world()
    order_id = buy(rt, server, collateral_decision(rt))["order_id"]
    row = _partial(rt, server, order_id)
    row["maker_orders"][0]["price"] = "0.20"
    polymarket.tick(rt)
    assert order_id not in rt.polymarket.filled
    assert items(rt, "polymarket.read_malformed")


def test_a_pending_uncertain_placement_s_charged_leg_halts_buying():
    """#3: a placement whose answer was lost and whose lookups fail is not yet released,
    so it is in no settlement set; a charged maker leg of it, read beside another order's
    trades, still halts buying: the scan reads every hash the world may have signed."""
    rt, server = live_world()
    other = buy(rt, server, collateral_decision(rt))["order_id"]  # acknowledged, read
    server.lose_answer = True
    server.fail_lookups = 10**6
    buy(rt, server, collateral_decision(rt), slot="tool:1", market="fake-2", price="0.20")
    (pending,) = [i for i in rt.polymarket.intents.values()
                  if i["args"]["price"] == "0.20"]
    assert pending["result"]["status"] == "uncertain" and not pending.get("unresolved")
    assert pending["order_hash"] not in polymarket._live_orders(rt.polymarket)
    server.trades.append({"id": "t-p", "status": "MATCHED",
                          "match_time": signed_s(rt, other), "taker_order_id": OTHER,
                          "side": "SELL", "size": "10", "price": "0.20",
                          "maker_orders": [{"order_id": pending["order_hash"],
                                            "asset_id": token(server, "fake-2"),
                                            "matched_amount": "10", "price": "0.20",
                                            "side": "BUY", "fee_rate_bps": "500"}]})
    polymarket.tick(rt)
    assert rt.polymarket.contradicted


def test_a_resolution_attributes_only_what_the_decision_s_lots_realised(monkeypatch):
    """#4: of a ten-share $0.30 order, five are booked to the decision's lot and five are
    quarantined (the consequence book refused them). At a YES resolution the venue
    realises $7; the decision owns the $3.50 its lot realised, and the other $3.50 stays
    in the pot, owned by no decision."""
    fake = still_fake(resolutions={"fake-1": (10**15, 0)})
    rt, server = live_world(fake=fake)
    handle = collateral_decision(rt)
    order_id = buy(rt, server, handle)["order_id"]
    _partial(rt, server, order_id)
    polymarket.tick(rt)  # 5 booked to the decision's lot
    observe = rt.consequences.observe

    def refuses(kind, payload, event):
        if kind == "Fill":
            raise ValueError("the consequence book refuses this fill")
        return observe(kind, payload, event)

    monkeypatch.setattr(rt.consequences, "observe", refuses)
    _partial(rt, server, order_id, trade_id="t-6")
    server.fake._all_orders[server.orders[order_id]["pm"]].update(
        filled=Decimal(10), remaining=Decimal(0))
    polymarket.tick(rt)  # 5 more: quarantined, owned by no decision
    monkeypatch.setattr(rt.consequences, "observe", observe)
    assert rt.polymarket.filled[order_id] == "10"
    before = rt.venue_deltas.get(handle, {}).get("polymarket", 0)
    rt.clock.now_ns = 10**15
    server.advance(10**15)
    for _ in range(3):
        polymarket.tick(rt)
    assert rt.venue_deltas[handle]["polymarket"] - before == 3_500_000
    rows = [i for i in items(rt, "venue.settled") if i["reason"] == "resolution"]
    assert sum(i["amount"] for i in rows) == 7_000_000
    assert [i["amount"] for i in rows if i["handle"] is None] == [3_500_000]


# --- Sol's round-9 review of #177 ----------------------------------------------------------


def test_a_market_listing_one_token_twice_is_never_settled_against():
    """#1: ten YES at $0.30 lose, but the resolution reply lists ``[YES, YES]`` with
    payouts ``[1, 0]``. A market must name each outcome token once: the read is
    malformed, and no payout is booked."""
    fake = still_fake(resolutions={"fake-1": (10**15, 1)})  # NO wins
    rt, server = live_world(fake=fake)
    order_id = maker_fill(rt, server, collateral_decision(rt), price="0.30")["order_id"]
    polymarket.tick(rt)
    assert rt.polymarket.filled[order_id] == "10"
    yes = token(server)

    def twice(row):
        return {**row, "clobTokenIds": json.dumps([yes, yes]),
                "outcomePrices": json.dumps(["1", "0"])} if row["closed"] else row

    server.market_row = twice
    rt.clock.now_ns = 10**15
    server.advance(10**15)
    for _ in range(3):
        polymarket.tick(rt)
    assert not items(rt, "polymarket.resolution")
    assert yes not in rt.polymarket.cursor.get("resolved", {})


def test_a_sole_pending_placement_is_read_and_scanned():
    """#2: the only placement lost its answer and its lookups fail, so no order is in
    the settlement set yet; its trades are read all the same, from its signing time,
    and a row naming it as taker halts buying."""
    rt, server = live_world()
    server.lose_answer = True
    server.fail_lookups = 10**6
    buy(rt, server, collateral_decision(rt))
    (pending,) = rt.polymarket.intents.values()
    assert pending["result"]["status"] == "uncertain"
    assert polymarket._live_orders(rt.polymarket) == {}
    server.trades.append({"id": "t-t", "status": "MATCHED",
                          "match_time": signed_s_of(pending),
                          "taker_order_id": pending["order_hash"],
                          "asset_id": token(server), "side": "BUY", "size": "10",
                          "price": "0.30", "maker_orders": []})
    reads = len([c for c in server.calls if c == ("GET", "/data/trades")])
    polymarket.tick(rt)
    assert len([c for c in server.calls if c == ("GET", "/data/trades")]) > reads
    assert rt.polymarket.contradicted


# --- Sol's round-10 review of #177 ----------------------------------------------------------


def test_a_token_id_aliased_by_a_leading_zero_is_never_settled_against():
    """#1: ``[YES, "0" + YES]`` with payouts ``[1, 0]`` names one on-chain token twice.
    Only canonical decimal token ids pass the door, so the market is malformed."""
    fake = still_fake(resolutions={"fake-1": (10**15, 1)})  # NO wins
    rt, server = live_world(fake=fake)
    maker_fill(rt, server, collateral_decision(rt), price="0.30")
    polymarket.tick(rt)
    yes = token(server)
    server.market_row = lambda row: {**row, "clobTokenIds": json.dumps([yes, "0" + yes]),
                                     "outcomePrices": json.dumps(["1", "0"])
                                     } if row["closed"] else row
    rt.clock.now_ns = 10**15
    server.advance(10**15)
    for _ in range(3):
        polymarket.tick(rt)
    assert not items(rt, "polymarket.resolution")


def test_a_terminal_underreport_never_erases_an_observed_pending_leg():
    """#2: a valid MATCHED ten-share maker leg at $0.40 is observed; the order is then
    read back CANCELED with nothing matched. The nonfinal leg stays liability until its
    own trade is CONFIRMED or FAILED: the order is not confirmed, exposure counts it, and
    the wind-down is not flat."""
    rt, server = live_world(max_open_micro=5_000_000)
    handle = collateral_decision(rt)
    order_id = buy(rt, server, handle, price="0.40")["order_id"]
    server.trades.append({"id": "t-m", "status": "MATCHED", "match_time": signed_s(rt, order_id),
                          "taker_order_id": OTHER, "side": "SELL", "size": "10",
                          "price": "0.40", "maker_orders": [
                              {"order_id": order_id, "asset_id": token(server),
                               "matched_amount": "10", "price": "0.40", "side": "BUY"}]})
    polymarket.tick(rt)
    cancel = rt._run_tool("seed-decider", handle, {
        "tool": "polymarket.cancel", "args": {"order_id": order_id}}, slot="tool:1")[0]
    assert (cancel["status"], cancel["filled_size"]) == ("cancelled", "0")
    for _ in range(2):
        polymarket.tick(rt)
    assert polymarket.local_commitments(rt.polymarket)[0] == Decimal(4)
    (order,) = rt.consequences.table.orders
    assert order.confirmed is None
    again = buy(rt, server, collateral_decision(rt), slot="tool:2", market="fake-2",
                price="0.40")
    assert "open exposure" in again["error"]
    assert polymarket.wind_down(rt)["exposure_state"] != "flat"


def test_a_book_for_another_token_is_never_this_token_s_mark():
    """#3: a valid NO book answered for our YES request is not YES's mark: the book is
    malformed, no mark is recorded and the token's book stream does not advance."""
    rt, server = live_world()
    maker_fill(rt, server, collateral_decision(rt), price="0.40")
    polymarket.tick(rt)
    yes, no = token(server), token(server, side=1)
    coin = polymarket.coin_of(yes)
    through = rt.polymarket.through.get(coin)
    server.book_of = lambda asked: no if asked == yes else asked
    rt.clock.now_ns += 10**9
    polymarket.mark(rt)
    assert coin not in rt.consequences.mids
    assert rt.polymarket.through.get(coin) == through


# --- Sol's round-11 review of #177 ----------------------------------------------------------


def test_an_invalid_read_back_never_erases_an_observed_pending_leg():
    """#1: a released placement's MATCHED leg is observed; its order then reads back
    INVALID with nothing matched. Any terminal status keeps the leg's liability and its
    polling until that leg's own trade is CONFIRMED or FAILED, which is then booked."""
    from factorylab.runtime.venue import UNCERTAIN_ORDER_POLLS

    rt, server = live_world(max_open_micro=5_000_000)
    handle = collateral_decision(rt)
    server.lose_answer = True
    server.fail_lookups = 10**6
    buy(rt, server, handle, price="0.40")
    for _ in range(UNCERTAIN_ORDER_POLLS + 1):
        polymarket.tick(rt)
    (intent,) = rt.polymarket.intents.values()
    assert intent.get("unresolved")
    order_id = intent["order_hash"]
    row = {"id": "t-m", "status": "MATCHED", "match_time": signed_s_of(intent),
           "taker_order_id": OTHER, "side": "SELL", "size": "10", "price": "0.40",
           "maker_orders": [{"order_id": order_id, "asset_id": token(server),
                             "matched_amount": "10", "price": "0.40", "side": "BUY"}]}
    server.trades.append(row)
    polymarket.tick(rt)
    assert polymarket._nonfinal(rt.polymarket) == {order_id: Decimal(10)}
    server.fail_lookups = 0
    server.order_answer = lambda answer: {**answer, "status": "INVALID", "size_matched": "0"}
    for _ in range(3):
        polymarket.tick(rt)
    assert polymarket.local_commitments(rt.polymarket)[0] == Decimal(4)
    again = buy(rt, server, collateral_decision(rt), slot="tool:1", market="fake-2",
                price="0.40")
    assert "open exposure" in again["error"]
    row["status"] = "CONFIRMED"
    polymarket.tick(rt)
    assert rt.polymarket.filled.get(order_id) == "10"
    assert polymarket._nonfinal(rt.polymarket) == {}


def test_a_market_row_for_another_market_never_supplies_a_payout():
    """#2: the market asked for resolved NO; the lookup answers a conforming resolved row
    of another market that names our token with payouts [1, 0]. A market reply must be
    the market asked for: nothing is settled against it."""
    fake = still_fake(resolutions={"fake-1": (10**15, 1)})  # NO wins
    rt, server = live_world(fake=fake)
    maker_fill(rt, server, collateral_decision(rt), price="0.40")
    polymarket.tick(rt)
    server.market_row = lambda row: {**row, "id": "foreign-market",
                                     "outcomePrices": json.dumps(["1", "0"])
                                     } if row["closed"] else row
    rt.clock.now_ns = 10**15
    server.advance(10**15)
    for _ in range(3):
        polymarket.tick(rt)
    assert not items(rt, "polymarket.resolution")


@pytest.mark.parametrize("paged", [False, True])
def test_a_position_listed_twice_is_never_counted_twice(paged):
    """#3: $96 and ten tokens at $0.40, the position listed twice (in one page, or across
    two): the listing is malformed, so no custody is published and no opening adopted."""
    rt, server = live_world(opened=False)
    yes = token(server)
    server.fake._cash = Decimal(96)
    server.fake._positions[yes] = {"size": Decimal(10), "avg_px": Decimal("0.40")}
    server.positions_rows = lambda rows: rows + rows
    if paged:
        server.positions_page = 1
    with pytest.raises(ValueError, match="listed twice"):
        rt.polymarket.venue.target.account()
    polymarket.tick(rt)
    assert rt.polymarket.opening is None


# --- Sol's round-12 review of #177: rules across replies -----------------------------------


def test_an_observed_matched_leg_never_shrinks():
    """#1: MATCHED 10, the order read CANCELED with 0, then the same trade's leg MATCHED
    1. The most ever observed (10) stays liability, and the smaller report contradicts
    the venue: buying stops."""
    rt, server = live_world(max_open_micro=5_000_000)
    handle = collateral_decision(rt)
    order_id = buy(rt, server, handle, price="0.40")["order_id"]
    leg = {"order_id": order_id, "asset_id": token(server), "matched_amount": "10",
           "price": "0.40", "side": "BUY"}
    server.trades.append({"id": "t-m", "status": "MATCHED", "match_time": signed_s(rt, order_id),
                          "taker_order_id": OTHER, "side": "SELL", "size": "10",
                          "price": "0.40", "maker_orders": [leg]})
    polymarket.tick(rt)
    rt._run_tool("seed-decider", handle, {"tool": "polymarket.cancel",
                                          "args": {"order_id": order_id}}, slot="tool:1")
    polymarket.tick(rt)
    leg["matched_amount"] = "1"
    polymarket.tick(rt)
    assert polymarket._nonfinal(rt.polymarket) == {order_id: Decimal(10)}
    assert polymarket.local_commitments(rt.polymarket)[0] == Decimal(4)
    assert rt.polymarket.contradicted
    refused = buy(rt, server, collateral_decision(rt), slot="tool:2", market="fake-2",
                  price="0.40")
    assert refused["error"] == polymarket.MAKER_ONLY_REFUSAL


def _unrelated(n, at):
    return {"id": f"u-{n}", "status": "CONFIRMED", "match_time": at,
            "taker_order_id": OTHER, "side": "SELL", "size": "1", "price": "0.5",
            "maker_orders": [{"order_id": f"0x{n:064x}", "asset_id": "7",
                              "matched_amount": "1", "price": "0.5", "side": "BUY"}]}


def test_a_trade_is_listed_once_across_a_paged_read_and_booked_once_ever():
    """#2: one-row pages list t1 with A's leg, four unrelated trades, then t1 again with
    B's leg. The read that resumes on a later poll still knows t1: the listing is
    malformed, a visible stall, and B is never booked."""
    rt, server = live_world()
    a = buy(rt, server, collateral_decision(rt), price="0.40")["order_id"]
    b = buy(rt, server, collateral_decision(rt), slot="tool:1", market="fake-2",
            price="0.30")["order_id"]

    def t1(order_id, market, price):
        return {"id": "t1", "status": "CONFIRMED", "match_time": signed_s(rt, a),
                "taker_order_id": OTHER, "side": "SELL", "size": "10", "price": price,
                "maker_orders": [{"order_id": order_id, "asset_id": token(server, market),
                                  "matched_amount": "10", "price": price, "side": "BUY"}]}

    server.trades = [t1(a, "fake-1", "0.40"), *[_unrelated(n, signed_s(rt, a)) for n in range(4)],
                     t1(b, "fake-2", "0.30")]
    server.page_size = 1
    for _ in range(3):
        polymarket.tick(rt)
    assert rt.polymarket.filled.get(a) == "10" and b not in rt.polymarket.filled
    assert any("listed twice" in i["reason"] for i in items(rt, "polymarket.read_malformed"))


def test_a_trade_once_booked_is_never_booked_again():
    """#2 (b): t1 books A's leg in a complete listing; a later listing names t1 with B's
    leg. A booked trade is remembered durably: B is never booked, and buying stops."""
    rt, server = live_world()
    a = buy(rt, server, collateral_decision(rt), price="0.40")["order_id"]
    b = buy(rt, server, collateral_decision(rt), slot="tool:1", market="fake-2",
            price="0.30")["order_id"]

    def t1(order_id, market, price):
        return {"id": "t1", "status": "CONFIRMED", "match_time": signed_s(rt, a),
                "taker_order_id": OTHER, "side": "SELL", "size": "10", "price": price,
                "maker_orders": [{"order_id": order_id, "asset_id": token(server, market),
                                  "matched_amount": "10", "price": price, "side": "BUY"}]}

    server.trades = [t1(a, "fake-1", "0.40")]
    polymarket.tick(rt)
    assert rt.polymarket.filled.get(a) == "10"
    server.trades = [t1(b, "fake-2", "0.30")]
    polymarket.tick(rt)
    assert b not in rt.polymarket.filled and rt.polymarket.contradicted


def test_a_position_that_names_no_owner_is_never_this_pot_s():
    """#3: $50 and no positions; a row with no ``proxyWallet`` reports ten tokens. A
    position is bound to no hash this world signed, so its owner is required: the
    listing is malformed and no opening is adopted."""
    rt, server = live_world(opened=False)
    server.fake._cash = Decimal(50)
    foreign = {"asset": token(server), "size": "10", "avgPrice": "0.4", "outcomeIndex": 0,
               "outcome": "Yes"}
    server.positions_rows = lambda rows: [*rows, foreign]
    polymarket.tick(rt)
    assert rt.polymarket.opening is None


@pytest.mark.parametrize("answer", ["two markets", "another market"])
def test_a_token_s_market_once_bound_is_never_rebound(answer):
    """#4: YES is bound to fake-1 by its order. A claim's lookup answers two markets
    naming YES (resolved fake-2 first), or only fake-2: the first is malformed at the
    door, the second contradicts the binding. Either way YES stays fake-1's."""
    rt, server = live_world()
    maker_fill(rt, server, collateral_decision(rt), price="0.40")
    polymarket.tick(rt)
    yes = token(server)
    assert rt.polymarket.token_markets[yes] == "fake-1"
    one, two = (server._raw_market(server.fake._markets[m]) for m in ("fake-1", "fake-2"))
    foreign = {**two, "clobTokenIds": one["clobTokenIds"], "closed": True,
               "umaResolutionStatus": "resolved", "outcomePrices": json.dumps(["1", "0"])}
    rows = [foreign, one] if answer == "two markets" else [foreign]
    real = server._gamma
    server._gamma = lambda path, query: rows if path == "/markets" else real(path, query)
    listed = None
    try:
        listed = rt.polymarket.venue.market_of_token(yes)
    except Exception:  # noqa: BLE001 - the door refuses two markets naming one token
        pass
    if listed is not None:
        assert not polymarket.bind_market(rt, rt.polymarket, yes, listed)
        assert rt.polymarket.contradicted
    else:
        assert answer == "two markets"
    assert rt.polymarket.token_markets[yes] == "fake-1"


# --- Sol's round-14 review of #177: more facts first sight binds ----------------------------


def _row(rt, server, order_id, status, size, trade_id="t1", market="fake-1", price="0.40"):
    return {"id": trade_id, "status": status, "match_time": signed_s(rt, order_id),
            "taker_order_id": OTHER, "side": "SELL", "size": size, "price": price,
            "maker_orders": [{"order_id": order_id, "asset_id": token(server, market),
                              "matched_amount": size, "price": price, "side": "BUY"}]}


def test_a_leg_failed_then_confirmed_is_never_silently_lost():
    """R14 #1: the venue filled ten (custody: $46 and ten tokens); its trade first reads
    FAILED, then CONFIRMED. The leg's settlement was bound FAILED: the later CONFIRMED
    contradicts it and halts buying, and the custodian's ten tokens the books do not
    hold show as drift, never hidden by an equal value at cost."""
    rt, server = live_world()
    order_id = buy(rt, server, collateral_decision(rt), price="0.40")["order_id"]
    server.trades = [_row(rt, server, order_id, "FAILED", "10")]
    polymarket.tick(rt)
    server.trades = [_row(rt, server, order_id, "CONFIRMED", "10")]
    server.fake._cash -= Decimal(4)
    server.fake._positions[token(server)] = {"size": Decimal(10), "avg_px": Decimal("0.40")}
    server.fake._orders.clear()
    polymarket.tick(rt)
    assert rt.polymarket.contradicted and rt.polymarket.drifting


def test_a_confirmed_quantity_never_changes():
    """R14 #2: t1 CONFIRMED 2 is booked; the same trade later says 3. A confirmed leg's
    quantity is bound exactly: the change halts buying, and nothing more is booked."""
    rt, server = live_world()
    order_id = buy(rt, server, collateral_decision(rt), price="0.40")["order_id"]
    server.trades = [_row(rt, server, order_id, "CONFIRMED", "2")]
    polymarket.tick(rt)
    assert rt.polymarket.filled[order_id] == "2"
    server.trades = [_row(rt, server, order_id, "CONFIRMED", "3")]
    polymarket.tick(rt)
    assert rt.polymarket.contradicted and rt.polymarket.filled[order_id] == "2"


def test_a_cancel_underreport_never_erases_an_order_read_s_matched_floor():
    """R14 #3: an order read says MATCHED 10 (its trade not yet read), reserving $4; the
    cancel answer then says CANCELED with 0. The order's matched floor stays 10, the $4
    stays reserved, and the lower report halts buying."""
    rt, server = live_world(max_open_micro=5_000_000)
    handle = collateral_decision(rt)
    order_id = buy(rt, server, handle, price="0.40")["order_id"]
    server.order_answer = lambda answer: {**answer, "status": "MATCHED", "size_matched": "10"}
    for _ in range(2):
        polymarket.tick(rt)
    assert polymarket.local_commitments(rt.polymarket)[0] == Decimal(4)
    server.order_answer = lambda answer: {**answer, "status": "CANCELED", "size_matched": "0"}
    rt._run_tool("seed-decider", handle, {"tool": "polymarket.cancel",
                                          "args": {"order_id": order_id}}, slot="tool:1")
    polymarket.tick(rt)
    assert polymarket.local_commitments(rt.polymarket)[0] == Decimal(4)
    assert rt.polymarket.contradicted


def test_a_bound_trade_that_loses_its_own_legs_halts_buying():
    """R14 #4: t1 is bound to this world's leg; it comes back naming only another
    party's. Losing its own legs disagrees with the binding."""
    rt, server = live_world()
    order_id = buy(rt, server, collateral_decision(rt), price="0.40")["order_id"]
    row = _row(rt, server, order_id, "MATCHED", "10")
    server.trades = [row]
    polymarket.tick(rt)
    row["maker_orders"] = [{**row["maker_orders"][0], "order_id": "0x" + "aa" * 32,
                            "asset_id": "7"}]
    polymarket.tick(rt)
    assert rt.polymarket.contradicted


# --- Sol's round-13 review of #177: first sight binds, forever ------------------------------


def test_a_confirmation_below_the_observed_floor_is_never_booked():
    """#1: MATCHED 10, a terminal read of 0, then the same trade CONFIRMED 1. The leg's
    floor is 10: the confirmation below it contradicts the venue, buying halts, nothing
    of it is booked, and the ten stay liability."""
    rt, server = live_world()
    handle = collateral_decision(rt)
    order_id = buy(rt, server, handle, price="0.40")["order_id"]
    row = {"id": "t-m", "status": "MATCHED", "match_time": signed_s(rt, order_id),
           "taker_order_id": OTHER, "side": "SELL", "size": "10", "price": "0.40",
           "maker_orders": [{"order_id": order_id, "asset_id": token(server),
                             "matched_amount": "10", "price": "0.40", "side": "BUY"}]}
    server.trades.append(row)
    polymarket.tick(rt)
    rt._run_tool("seed-decider", handle, {"tool": "polymarket.cancel",
                                          "args": {"order_id": order_id}}, slot="tool:1")
    polymarket.tick(rt)
    row.update(status="CONFIRMED", size="1")
    row["maker_orders"][0]["matched_amount"] = "1"
    polymarket.tick(rt)
    assert rt.polymarket.contradicted and order_id not in rt.polymarket.filled
    assert polymarket.local_commitments(rt.polymarket)[0] == Decimal(4)


def test_a_trade_first_seen_on_one_order_never_books_another():
    """#2: t1 is first seen MATCHED against A; it is later CONFIRMED against B. The trade
    was bound to A's leg at first sight: buying halts and B books nothing."""
    rt, server = live_world()
    a = buy(rt, server, collateral_decision(rt), price="0.40")["order_id"]
    b = buy(rt, server, collateral_decision(rt), slot="tool:1", market="fake-2",
            price="0.30")["order_id"]

    def t1(status, order_id, market, price):
        return {"id": "t1", "status": status, "match_time": signed_s(rt, a),
                "taker_order_id": OTHER, "side": "SELL", "size": "10", "price": price,
                "maker_orders": [{"order_id": order_id, "asset_id": token(server, market),
                                  "matched_amount": "10", "price": price, "side": "BUY"}]}

    server.trades = [t1("MATCHED", a, "fake-1", "0.40")]
    polymarket.tick(rt)
    server.trades = [t1("CONFIRMED", b, "fake-2", "0.30")]
    polymarket.tick(rt)
    assert rt.polymarket.contradicted and b not in rt.polymarket.filled


def test_a_market_that_moves_a_held_token_to_the_other_outcome_never_pays_it():
    """#3: YES is bound at index 0 of fake-1; the closed reply reverses only the token
    ids, keeping ["Yes", "No"] and [1, 0]. The token's bound place disagrees: buying
    halts and no payout is booked."""
    fake = still_fake(resolutions={"fake-1": (10**15, 0)})  # YES wins
    rt, server = live_world(fake=fake)
    maker_fill(rt, server, collateral_decision(rt), price="0.40")
    polymarket.tick(rt)
    server.market_row = lambda row: {**row, "clobTokenIds": json.dumps(list(reversed(
        json.loads(row["clobTokenIds"]))))} if row["closed"] else row
    rt.clock.now_ns = 10**15
    server.advance(10**15)
    for _ in range(3):
        polymarket.tick(rt)
    assert rt.polymarket.contradicted and not items(rt, "polymarket.resolution")


@pytest.mark.parametrize("kind", ["trade", "leg", "token", "market", "settled",
                                  "confirmed quantity", "order", "own legs lost"])
def test_a_disagreement_with_any_bound_fact_halts_buying(kind):
    """The owner's rule: first sight binds, forever. One disagreement in each kind of
    bound fact (a trade's legs, a leg's floor, a token's place, a market's tokens, a
    leg's settlement and its confirmed quantity, an order's matched floor, a trade's own
    legs at all) halts buying through the contradiction path."""
    rt, server = live_world()
    order_id = buy(rt, server, collateral_decision(rt), price="0.40")["order_id"]
    leg = {"order_id": order_id, "asset_id": token(server), "matched_amount": "10",
           "price": "0.40", "side": "BUY"}
    row = {"id": "t-k", "status": "MATCHED", "match_time": signed_s(rt, order_id),
           "taker_order_id": OTHER, "side": "SELL", "size": "10", "price": "0.40",
           "maker_orders": [leg]}
    server.trades.append(row)
    polymarket.tick(rt)
    assert not rt.polymarket.contradicted
    market = rt.polymarket.venue.target.write_market("fake-1")
    if kind == "trade":
        other = buy(rt, server, collateral_decision(rt), slot="tool:2", market="fake-3",
                    price="0.10")["order_id"]
        row["maker_orders"].append({"order_id": other, "asset_id": token(server, "fake-3"),
                                    "matched_amount": "10", "price": "0.10",
                                    "side": "BUY"})  # the trade now names two legs
        polymarket.tick(rt)
    elif kind == "leg":
        leg["matched_amount"] = "4"
        polymarket.tick(rt)
    elif kind == "token":
        moved = {**market, "outcomes": [dict(o) for o in market["outcomes"]]}
        moved["outcomes"][0]["outcome"] = "No"
        polymarket.bind_market(rt, rt.polymarket, token(server), moved)
    elif kind == "market":
        moved = {**market, "outcomes": [dict(o) for o in market["outcomes"]]}
        moved["outcomes"][1]["token_id"] = "7"
        polymarket.bind_market(rt, rt.polymarket, token(server), moved)
    elif kind == "settled":
        row["status"] = "FAILED"
        polymarket.tick(rt)
        row["status"] = "CONFIRMED"
        polymarket.tick(rt)
    elif kind == "confirmed quantity":
        server.trades = [_row(rt, server, order_id, "CONFIRMED", "2", trade_id="t-q")]
        polymarket.tick(rt)  # booked at 2
        server.trades = [_row(rt, server, order_id, "CONFIRMED", "3", trade_id="t-q")]
        polymarket.tick(rt)
    elif kind == "order":
        polymarket.observe_matched(rt, rt.polymarket, order_id, "10")
        polymarket.observe_matched(rt, rt.polymarket, order_id, "3")
    else:
        row["maker_orders"] = [{**leg, "order_id": "0x" + "aa" * 32, "asset_id": "7"}]
        polymarket.tick(rt)
    assert rt.polymarket.contradicted
    refused = buy(rt, server, collateral_decision(rt), slot="tool:1", market="fake-2",
                  price="0.20")
    assert refused["error"] == polymarket.MAKER_ONLY_REFUSAL
