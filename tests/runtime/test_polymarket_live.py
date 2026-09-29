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
from tests.world.fake_clob import FakeClob, _Wall, make_signer


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


def signed_s(rt, order_id):
    """A match time just after the order was signed, in the venue's seconds."""
    client_id = rt.polymarket.order_ids[order_id]
    order = rt.polymarket.intents[client_id]["order_identity"]["order"]
    return str(int(order["timestamp"]) // 1000 + 9)


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


def test_a_pot_above_its_principal_cap_takes_no_new_risk():
    rt, server = live_world(principal="40")  # the wallet holds 50
    handle = collateral_decision(rt)
    result = buy(rt, server, handle)
    assert result["status"] == "rejected" and "principal_usd" in result["error"]
    assert rt.polymarket.intents == {}
    polymarket.reconcile(rt)
    assert items(rt, "polymarket.principal_exceeded")


def test_a_manifest_pot_above_its_principal_cap_is_refused():
    with pytest.raises(ValueError, match="exceeds polymarket.principal_usd"):
        PolymarketSpec(enabled=True, collateral_micro=50_000_000, principal_micro=40_000_000)


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
    result = buy(rt, server, handle, price="0.45")  # crosses the 0.41 ask
    assert result["status"] == "filled"
    polymarket.tick(rt)
    assert items(rt, "polymarket.fill") == []  # MATCHED is not final
    server.settle()
    polymarket.tick(rt)
    polymarket.tick(rt)
    (fill,) = items(rt, "polymarket.fill")
    assert fill["size"] == "10" and fill["px"] == "0.41"
    order_id = result["order_id"]
    # The venue now reports a second execution of the same 10-token order: quarantined,
    # booked to the pot, owned by no decision.
    server.extra_fills = [{"id": "t-extra", "status": "CONFIRMED",
                           "match_time": signed_s(rt, order_id),
                           "taker_order_id": order_id, "size": "5", "price": "0.41",
                           "maker_orders": []}]
    before = dict(rt.venue_deltas.get(handle, {}))
    polymarket.tick(rt)
    assert items(rt, "polymarket.fill_quarantined")[0]["order_id"] == order_id
    assert items(rt, "consequence.quarantined")
    assert rt.venue_deltas.get(handle, {}) == before
    assert rt.polymarket.filled[order_id] == "10"


def test_unattributed_custody_is_what_no_return_owns_and_the_books_close():
    rt, server = live_world()
    handle = collateral_decision(rt)
    # fake-2 charges takers 5%: each fill books its fee to the pot.
    order_id = buy(rt, server, handle, size="5", price="0.75", market="fake-2")["order_id"]
    polymarket.tick(rt)
    server.extra_fills = [{"id": "t-extra", "status": "CONFIRMED",
                           "match_time": signed_s(rt, order_id),
                           "taker_order_id": order_id, "size": "5", "price": "0.71",
                           "fee_rate_bps": "500", "maker_orders": []}]
    polymarket.tick(rt)
    books = polymarket.custody_books(rt)
    assert books["claimed_micro"] + books["unattributed_micro"] == books["booked_micro"]
    rows = [i for i in items(rt, "venue.settled") if i["custody"] == "polymarket"]
    quarantined = [i for i in rows if i["handle"] is None]
    owned = [i for i in rows if i["handle"] == handle]
    # The quarantined fill's fee is the pot's, and no decision's: it never reaches the
    # owner's venue effects, and no claim can take it.
    assert len(quarantined) == 1 and len(owned) == 1
    assert rt.venue_deltas[handle]["polymarket"] == owned[0]["amount"]
    assert books["booked_micro"] == sum(i["amount"] for i in rows)
    # A claim can never take more than the decision's positions realised.
    assert polymarket.claim_share(rt, "seed-decider", handle, 10**9, "test") <= 0
    assert rt._summary()["polymarket_custody"] == polymarket.custody_books(rt)


def test_a_resolution_is_the_held_position_s_realized_consequence():
    fake = still_fake(resolutions={"fake-1": (10**15, 0)})
    rt, server = live_world(fake=fake)
    handle = collateral_decision(rt)
    buy(rt, server, handle, price="0.45")
    polymarket.tick(rt)
    rt.clock.now_ns = 10**15
    server.advance(10**15)
    polymarket.tick(rt)
    (resolution,) = items(rt, "polymarket.resolution")
    assert resolution["payout"] == "1" and resolution["size"] == "10"
    assert items(rt, "consequence.resolution")
    settled = [i for i in items(rt, "venue.settled") if i["reference"].startswith("resolution:")]
    assert settled[0]["amount"] == 5_900_000 and settled[0]["handle"] == handle
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
    rt, server = live_world(principal="40")
    facts = rt.institution_section("admission")["tools"]["polymarket_orders"]
    spec = rt.m.polymarket
    assert facts["principal_micro"] == spec.principal_micro == 40_000_000
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
    # fake-2 charges takers 5%: the crossing buy fills on arrival and books its fee.
    assert buy(rt, server, handle, size="5", price="0.75", market="fake-2")["status"] == (
        "uncertain")
    for _ in range(UNCERTAIN_ORDER_POLLS + 1):
        polymarket.tick(rt)
    client_id = f"{handle}:tool:0"
    assert rt.polymarket.intents[client_id]["unresolved"]
    polymarket.tick(rt)
    polymarket.tick(rt)
    intent = rt.polymarket.intents[client_id]
    assert rt.polymarket.order_ids.get(intent["order_hash"]) == client_id
    (fill,) = items(rt, "polymarket.fill")
    assert fill["order_id"] == intent["order_hash"] and fill["size"] == "5"
    owned = [i for i in items(rt, "venue.settled") if i["reference"] == f"fill:{fill['order_id']}"]
    assert owned and owned[0]["handle"] == handle
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
    buy(rt, server, handle, size="5", price="0.75", market="fake-2")
    for _ in range(UNCERTAIN_ORDER_POLLS + 2):
        polymarket.tick(rt)
    intent = rt.polymarket.intents[f"{handle}:tool:0"]
    assert rt.polymarket.order_ids.get(intent["order_hash"]) == f"{handle}:tool:0"
    (acknowledged,) = [i for i in items(rt, "polymarket.acknowledged")
                       if i["result"].get("evidence") == "confirmed trade"]
    assert acknowledged["handle"] == handle
    (fill,) = items(rt, "polymarket.fill")
    owned = [i for i in items(rt, "venue.settled") if i["reference"] == f"fill:{fill['order_id']}"]
    assert owned[0]["handle"] == handle


def test_a_fill_confirmed_after_its_market_resolved_is_booked_and_paid_once():
    """Astra P0 on #177: the order's lookup said MATCHED, its trade was not yet CONFIRMED
    when the market resolved; the order was retired, and the confirmed trade and its
    payout were never booked."""
    fake = still_fake(resolutions={"fake-1": (10**15, 0)})
    rt, server = live_world(fake=fake, confirm=False)
    handle = collateral_decision(rt)
    buy(rt, server, handle, price="0.45")  # crosses: MATCHED, not yet final
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
    assert fill["size"] == "10" and fill["px"] == "0.41"
    (resolution,) = items(rt, "polymarket.resolution")
    assert resolution["size"] == "10" and resolution["payout"] == "1"
    paid = [i for i in items(rt, "venue.settled") if i["reference"].startswith("resolution:")]
    assert [i["amount"] for i in paid] == [5_900_000] and paid[0]["handle"] == handle
    assert Decimal(polymarket.reconcile(rt)["drift"]) == 0


def test_a_fill_books_the_fee_its_execution_charged_not_the_admission_schedule():
    """Astra P0 on #177: admission read a 5% rate, the venue charged 10% at match time,
    and the pot booked 5%: the wallet moved by one amount and the books by another."""
    rt, server = live_world()
    handle = collateral_decision(rt)

    def raise_rate():
        server.fake._markets["fake-2"]["fee_rate"] = Decimal("0.10")

    server.on_post = raise_rate
    buy(rt, server, handle, size="5", price="0.75", market="fake-2")
    polymarket.tick(rt)
    (fill,) = items(rt, "polymarket.fill")
    assert Decimal(fill["fee_usd"]) == Decimal("0.10295")  # 5 x 0.10 x 0.71 x 0.29
    assert Decimal(polymarket.reconcile(rt)["drift"]) == 0


def _unstated_fee_fill(rt, server, handle):
    buy(rt, server, handle, size="5", price="0.75", market="fake-2")
    for trade in server.trades:
        trade.pop("fee_rate_bps")  # get-trades lists fee_rate_bps as optional


def test_a_fee_its_trade_does_not_state_is_never_taken_from_a_balance_change():
    """Codex P1 on #177: a fee inferred from the pot's drift cannot tell a fee from a
    withdrawal on the wallet: the withdrawal was booked to the order's owner as its fee."""
    rt, server = live_world()
    handle = collateral_decision(rt)
    _unstated_fee_fill(rt, server, handle)
    server.fake._cash -= Decimal(1)  # someone withdraws $1 from the wallet
    polymarket.tick(rt)
    (fill,) = items(rt, "polymarket.fill")
    assert fill["fee_usd"] == "0"
    (item,) = items(rt, "polymarket.fee_unresolved")
    assert item["handle"] == handle and len(rt.polymarket.open_fees) == 1
    assert not [i for i in items(rt, "venue.settled") if i["reference"].startswith("fee:")]
    assert rt.venue_deltas.get(handle, {}).get("polymarket", 0) == 0
    # The drift is its own signal, attributed to no one: $1 is more than any fee.
    assert items(rt, "polymarket.drift") and rt.polymarket.drifting


def test_the_exposure_cap_holds_while_the_venue_s_listings_lag():
    """Astra P1 on #177: a filled buy left the open orders before the positions listing
    showed it, so a second identical buy passed a $5 cap: $8.20 held against it."""
    rt, server = live_world(max_open_micro=5_000_000)
    polymarket.tick(rt)
    token_id = token(server)
    server.hidden_positions = {token_id}  # the Data API has not indexed the fill yet
    assert buy(rt, server, collateral_decision(rt), price="0.45")["status"] == "filled"
    second = buy(rt, server, collateral_decision(rt), price="0.45")
    assert second["status"] == "rejected" and "max_open_usd" in second["error"]
    polymarket.tick(rt)  # booked from its confirmed trade: still held at cost
    third = buy(rt, server, collateral_decision(rt), price="0.45")
    # The lagging listing also shows as drift now (the custodian's word is reconciled):
    # either refusal holds the cap.
    assert third["status"] == "rejected" and (
        "max_open_usd" in third["error"] or third["error"] == polymarket.DRIFT_REFUSAL)
    reserved, book = polymarket.local_commitments(rt.polymarket)
    assert reserved + book == Decimal("4.1")  # what the cap is weighed against


def test_the_principal_cap_counts_what_the_world_holds_while_the_listing_lags():
    """Astra P1 on #177: inventory absent from the positions listing (index lag or
    truncation) left principal under the cap in the check while it was over it."""
    rt, server = live_world(fake=still_fake(start_usdc=Decimal(35)), principal="40",
                            max_order_micro=30_000_000)
    polymarket.tick(rt)
    assert buy(rt, server, collateral_decision(rt), size="30",
               price="0.45")["status"] == "filled"  # $12.30 of the pot's $35 is tokens
    polymarket.tick(rt)
    server.hidden_positions = {token(server)}
    server.fake._cash += Decimal("10")  # a deposit: $45 of principal against a $40 cap
    polymarket.tick(rt)
    refused = buy(rt, server, collateral_decision(rt), size="5", price="0.30", market="fake-3")
    assert refused["status"] == "rejected" and refused["error"] in (
        polymarket.PRINCIPAL_REFUSAL, polymarket.DRIFT_REFUSAL)
    account = rt.polymarket.account(rt)
    assert polymarket.principal_excess(rt.polymarket, account) == polymarket.PRINCIPAL_REFUSAL


def test_a_positions_listing_is_read_to_its_end_or_the_pot_is_unavailable():
    rt, server = live_world()
    polymarket.tick(rt)
    buy(rt, server, collateral_decision(rt), price="0.45")
    buy(rt, server, collateral_decision(rt), price="0.75", market="fake-2", size="5")
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
    """Codex P1 on #177: the opening read failed, a fill whose fee its trade did not
    state settled first, and the opening then absorbed the fee: it closed at zero."""
    rt, server = live_world(opened=False)
    server.fail_balance = 1
    polymarket.tick(rt)  # the opening read fails
    assert rt.polymarket.opening is None
    refused = buy(rt, server, collateral_decision(rt), size="5", price="0.75",
                  market="fake-2")
    assert refused["status"] == "rejected" and refused["error"] == polymarket.OPENING_REFUSAL
    polymarket.tick(rt)  # the opening is read
    handle = collateral_decision(rt)
    buy(rt, server, handle, size="5", price="0.75", market="fake-2")
    for trade in server.trades:
        trade.pop("fee_rate_bps")
    polymarket.tick(rt)
    # The fee stays open, never closed from the balance.
    assert len(rt.polymarket.open_fees) == 1


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

    def spent(**kwargs):  # the budget is spent between the intent and the send
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
    buy(rt, server, handle, size="5", price="0.75", market="fake-2")

    def refuses(kind, payload, event):
        raise ValueError("spot sell exceeds long inventory")

    monkeypatch.setattr(rt.consequences, "observe", refuses)
    polymarket.tick(rt)  # does not raise
    (quarantined,) = items(rt, "polymarket.fill_quarantined")
    assert "exceeds long inventory" in quarantined["reason"]
    rows = [i for i in items(rt, "venue.settled") if i["reference"].startswith("fill:")]
    assert rows and all(i["handle"] is None for i in rows)

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
    buy(rt, server, collateral_decision(rt), price="0.45")  # fills on arrival
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
    buy(rt, server, collateral_decision(rt), price="0.45")  # the world's 10 tokens
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
    buy(rt, server, collateral_decision(rt), price="0.45")  # fake-1: held
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
    buy(rt, server, collateral_decision(rt), price="0.45")  # 10 at 0.41
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
    buy(rt, server, collateral_decision(rt), price="0.45")
    polymarket.tick(rt)
    rt.polymarket._account_memo = None
    report = polymarket.wind_down(rt)
    assert report["exposure_state"] == "wind_down_pending"
    assert [(u["size"], u["booked"]) for u in report["unsettled"]] == [("10", "0")]


def test_a_failed_trade_releases_its_matched_quantity_once_the_order_is_terminal():
    """Sol P2 on #177: a fully matched buy whose trade FAILED kept its $4.50 reserved,
    so under a $5 cap every later buy of the same size was refused for good."""
    rt, server = live_world(max_open_micro=5_000_000, confirm=False)
    buy(rt, server, collateral_decision(rt), price="0.45")  # matched, not yet final
    server.settle("FAILED")
    for _ in range(3):
        polymarket.tick(rt)
    assert polymarket.local_commitments(rt.polymarket)[0] == 0
    assert buy(rt, server, collateral_decision(rt), price="0.45")["status"] in (
        "filled", "resting")


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


def test_no_buy_is_taken_while_any_fee_is_unestablished():
    """Sol P1 on #177: the unstated-fee bound came from the admission-time schedule; the
    venue charged 10% where admission read 5%, a deposit of the difference cleared the
    drift, and a buy took principal past the cap. No bound is guessed any more: while a
    fee its trade did not state is open, no buy is taken."""
    rt, server = live_world(principal="50")

    def raise_rate():
        server.fake._markets["fake-2"]["fee_rate"] = Decimal("0.10")

    server.on_post = raise_rate
    _unstated_fee_fill(rt, server, collateral_decision(rt))
    polymarket.tick(rt)
    server.fake._cash += Decimal("0.05147")
    polymarket.tick(rt)
    refused = buy(rt, server, collateral_decision(rt), size="5", price="0.30",
                  market="fake-1")
    assert refused["status"] == "rejected" and refused["error"] == polymarket.FEE_OPEN_REFUSAL


def test_no_blanket_allowance_hides_a_small_unexplained_loss():
    """Sol P1 on #177: an allowance of 0.0001 a listed share absorbed a $0.001 withdrawal
    against 10 held shares (and $10 against 100,000). No allowance is made: a real
    rounding mismatch shows as drift too."""
    rt, server = live_world()
    buy(rt, server, collateral_decision(rt), price="0.45")  # 10 held, cost exact
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
    buy(rt, server, collateral_decision(rt), price="0.45")
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
    buy(rt, server, collateral_decision(rt), price="0.45")
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
