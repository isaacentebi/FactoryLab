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


def live_world(*, fake=None, principal="100", budget=60, confirm=True, wall=None, **spec):
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
                                wall=wall or _Wall(), nonce=lambda: 7)
    venue.intent_of = installed.intent_of  # the runtime's own intents, as installed
    rt.polymarket.venue.target = venue
    return rt, server


def token(server, market="fake-1", side=0):
    return server.fake._markets[market]["tokens"][side]


def buy(rt, server, handle, *, size="10", price="0.30", slot="tool:0", side="buy",
        market="fake-1"):
    call = {"tool": "polymarket.place_limit",
            "args": {"token_id": token(server, market), "side": side, "size": size,
                     "price": price}}
    return rt._run_tool("seed-decider", handle, call, slot=slot)[0]


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
    kinds = [i["kind"] for i in rt.seen_items if i["kind"].startswith("polymarket.")]
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
    server.extra_fills = [{"id": "t-extra", "status": "CONFIRMED", "match_time": "9",
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
    server.extra_fills = [{"id": "t-extra", "status": "CONFIRMED", "match_time": "9",
                           "taker_order_id": order_id, "size": "5", "price": "0.71",
                           "maker_orders": []}]
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
