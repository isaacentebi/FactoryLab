"""R3-B acceptance: three quantities, typed custody, and no fabricated facts.

`docs/plans/edition3-r3.md`, R3-B, from GPT-6 Pro's third reading §2 and §3. Five
acceptance sentences, one section each:

* a venue loss leaves the compute authority untouched and provider inventory unchanged;
* a duplicate receipt books once and a conflicting one refuses;
* a confirmed Venice purchase moves reserve to venice_credit exactly;
* the collateral check admits an order the venue can carry and refuses one it
  cannot, from the collateral view, with spot and perp separate;
* the custody view shows every account and never a fabricated one.

No network, no venue, no key: the scripted world's fake venue and scripted rail
throughout.
"""

from decimal import Decimal

import pytest

from factorylab.runtime.custody import custody_view
from factorylab.runtime.loop import Runtime
from factorylab.runtime.worlds import load_manifest
from factorylab.world.exchange import FakeExchange, Order, VenueUnavailable
from factorylab.world.scripted import ScriptedProvider
from tests.helpers import place, venue_runtime
from tests.helpers import spot_producer as _producer
from tests.helpers import spot_venue as _venue
from tests.runtime.test_connectors import ledger_items
from tests.runtime.test_fidelity import runtime as scripted_runtime


def _spot_runtime(exchange):
    return Runtime(load_manifest("scripted"), events=0, seed=1, initial_balance_micro=None,
                   ledger_path=None, drip=False, router_gamma=.1, provider=ScriptedProvider(),
                   exchange=exchange)


def settled(rt):
    return ledger_items(rt, "venue.settled")


# --- 1. a venue loss leaves compute authority and provider inventory untouched ----------

def test_a_venue_loss_leaves_compute_authority_and_provider_inventory_untouched():
    """The acceptance sentence, through the ordinary order path.

    A position is opened and closed into a fallen price. The loss is real, it is
    ledgered, and it is the venue's: the compute wallet, which buys thoughts and
    collateralises nothing, is exactly where it was, and so is every provider
    credit balance. Before R3-B this loss ran through ``wallet.settle_batch``.
    """
    rt = venue_runtime(venue_usd="1000", wallet_micro=50_000_000)
    pots_before = rt.treasury.pots()
    authority_before = rt.wallet.balance
    venue_before = pots_before["venue"]
    assert place(rt, "0.04")["status"] == "filled"
    mids = rt.exchange.target._mids
    mids["BTC"] = mids["BTC"] * Decimal("0.9")  # the price moves against the position
    assert place(rt, "0.04", side="sell", reduce_only=True)["status"] == "filled"
    assert rt.realized_to_date < 0

    pots_after = rt.treasury.pots()
    assert rt.wallet.balance == authority_before  # authority, not cash
    assert pots_after["seed"] == pots_before["seed"]
    assert pots_after["sellers"] == pots_before["sellers"]
    assert pots_after["venue"] < venue_before  # the loss landed where it happened
    losses = [item for item in settled(rt) if item["amount"] < 0]
    assert losses and all(item["custody"] == "venue_perps" for item in losses)
    assert all(item["reason"] in ("exchange_pnl", "funding") for item in settled(rt))
    # No wallet settlement of venue effects exists any more, in either direction.
    assert not [i for i in rt.ledger._recovery_items()
                if i.get("kind") == "wallet.settle"
                and i.get("reason") in ("exchange_pnl", "funding")]


# --- 2. a duplicate receipt books once, a conflicting one refuses ------------------------

def test_a_duplicate_receipt_books_once_and_a_conflicting_one_refuses():
    rt = scripted_runtime()
    treasury = rt.treasury
    identity = dict(payer="0xbuyer", chain="base", asset="USDC", log_index=3,
                    recipient="0xreserve")
    first = treasury.earn("oracle", 1_000, "0xFEED", **identity)
    assert first is not None and treasury.income["earned_micro"] == 1_000
    # The same payment, spelled differently, is the same payment.
    assert treasury.earn("oracle", 1_000, "0xfeed", **identity) is None
    assert treasury.income["earned_micro"] == 1_000
    # A different log index in the same transaction is a different transfer.
    assert treasury.earn("oracle", 1_000, "0xFEED", **{**identity, "log_index": 4}) is not None
    assert treasury.income["earned_micro"] == 2_000
    # A conflicting fact about one identity fails closed and books nothing.
    with pytest.raises(ValueError, match="conflicting payment"):
        treasury.earn("oracle", 9_000, "0xFEED", **identity)
    assert treasury.income["earned_micro"] == 2_000
    conflicts = ledger_items(rt, "income.conflict")
    assert len(conflicts) == 1 and conflicts[0]["presented"]["micro"] == 9_000


def test_a_spooled_receipt_is_a_claim_until_the_chain_read_confirms_it(tmp_path):
    """The spool is the wake host's word. Income is the chain's."""
    rt = scripted_runtime()
    treasury = rt.treasury
    spool = tmp_path / "income.jsonl"
    spool.write_text('{"service":"oracle","micro":2500,"tx":"0xabc","payer":"0xbuyer"}\n')
    treasury.income_spool = str(spool)
    treasury.collect_income()
    assert treasury.income["earned_micro"] == 0
    assert treasury.pots()["claimed_micro"] == 2500
    assert len(ledger_items(rt, "income.claimed")) == 1
    booked = treasury.verify_receipts()
    assert [item["micro"] for item in booked] == [2500]
    assert treasury.income["earned_micro"] == 2500 and treasury.pots()["claimed_micro"] == 0
    # A second pass over the same spool offset books nothing at all.
    treasury.collect_income()
    assert treasury.verify_receipts() == [] and treasury.income["earned_micro"] == 2500


def test_a_chain_that_contradicts_a_claim_books_nothing(monkeypatch):
    rt = scripted_runtime()
    treasury = rt.treasury
    treasury.earn("oracle", 4_000, "0xdead", claim=True, payer="0xbuyer")
    monkeypatch.setattr(treasury.rail.target, "verify_receipt",
                        lambda receipt: {"confirmed": False, "reason": "no such transfer"})
    assert treasury.verify_receipts() == []
    assert treasury.income["earned_micro"] == 0 and treasury.income["claims"] == {}
    assert ledger_items(rt, "income.conflict")[0]["reason"] == "no such transfer"


def test_an_unreadable_chain_leaves_the_claim_standing(monkeypatch):
    rt = scripted_runtime()
    treasury = rt.treasury
    treasury.earn("oracle", 4_000, "0xdead", claim=True, payer="0xbuyer")
    monkeypatch.setattr(treasury.rail.target, "verify_receipt", lambda receipt: None)
    assert treasury.verify_receipts() == []
    assert treasury.income["earned_micro"] == 0
    assert treasury.pots()["claimed_micro"] == 4_000  # still a claim, not a refusal


# --- 3. a confirmed Venice purchase moves reserve to venice_credit exactly ---------------

def test_a_confirmed_venice_purchase_moves_reserve_to_venice_credit_exactly():
    rt = scripted_runtime()
    treasury = rt.treasury
    treasury.open_window(1)
    treasury.transfer("to_reserve", "20", handle="fund-reserve", now_ns=1)
    treasury.tick(2)
    before = treasury.pots()
    openrouter = before["seed"]
    assert treasury.transfer("to_venice", "5", handle="population", now_ns=3)["status"] == (
        "submitted")

    # In flight: a held source and a pending claim, never money in two places.
    pending = custody_view(rt)["pending_conversions"]["transfers"]
    assert len(pending) == 1
    assert (pending[0]["source"], pending[0]["destination"]) == ("base_reserve", "venice_credit")
    assert pending[0]["held_micro"] == 5_000_000 and pending[0]["class"] == "financing"

    assert treasury.tick(4)[0]["status"] == "confirmed"
    after = treasury.pots()
    assert after["reserve"] == before["reserve"] - 5_000_000
    assert after["sellers"]["venice"] == before["sellers"]["venice"] + 5_000_000
    assert after["seed"] == openrouter  # the Venice route replenishes nothing else
    assert after["converted_from_principal_micro"] == 5_000_000
    assert after["earned_micro"] == 0  # principal converted is financing, never income
    financing = ledger_items(rt, "treasury.financing")
    assert len(financing) == 1
    assert financing[0]["source"] == "base_reserve"
    assert financing[0]["destination"] == "venice_credit"
    assert financing[0]["implies_openrouter_replenishment"] is False
    assert not custody_view(rt)["pending_conversions"]["transfers"]


# --- 4. collateral from the view, spot and perp separate ---------------------------------

def test_the_collateral_view_is_the_pot_the_venue_would_actually_charge():
    rt = venue_runtime(venue_usd="1000", wallet_micro=1_000_000)
    view = rt.exchange.collateral_view("BTC")
    assert view["collateral_asset"] == "USDC" and view["account_mode"] == "cross"
    assert view["eligible_equity_usd"] == Decimal(1000)
    assert view["holds_included_in_margin_used"] is False
    assert view["observed_at_ns"] is not None


def test_the_check_admits_what_the_venue_can_carry_and_refuses_what_it_cannot():
    rt = venue_runtime(venue_usd="1000", wallet_micro=1_000_000)
    assert place(rt, "0.02")["status"] == "filled"  # $400 of margin at 3x
    result = place(rt, "0.06")  # $1,200 more, on a $1,000 account
    assert result["status"] == "rejected"
    assert result["error"] == "order collateral exceeds venue free collateral"
    assert len(ledger_items(rt, "order.infeasible")) == 1


def test_precommitted_headroom_is_collateral_the_order_may_not_use():
    """``[venue] collateral_headroom_usd``, declared before the orders exist."""
    from dataclasses import replace as _replace

    manifest = load_manifest("scripted")
    manifest = _replace(manifest, exchange=_replace(
        manifest.exchange, collateral_headroom_usd="900"))
    rt = Runtime(manifest, events=0, seed=1, initial_balance_micro=1_000_000,
                 ledger_path=None, drip=False, router_gamma=.1, provider=ScriptedProvider(),
                 exchange=FakeExchange(start_cash_usd=Decimal("1000")))
    rt._manage_reserve_window()
    # $400 of margin fits $1,000 of equity, but not $1,000 less $900 of headroom.
    assert place(rt, "0.02")["status"] == "rejected"
    assert rt.m.exchange.collateral_headroom_usd == "900"


def test_spot_and_perp_are_checked_separately_against_their_own_balances():
    exchange = _venue()
    rt = _spot_runtime(exchange)
    try:
        handle = _producer(rt)
        # No spot USDC yet: the perps account's equity does not buy spot.
        result, _ = rt._run_tool("seed-decider", handle, {
            "tool": "venue.place_market",
            "args": {"coin": "BTC/USDC", "market": "spot", "side": "buy", "size": "0.2"}})
        assert result["status"] == "rejected"
        assert result["error"] == "spot buy exceeds venue USDC balance"
        # A sell of a coin the venue does not hold is refused on its own balance.
        assert rt._order_collateral("h", "BTC/USDC", Decimal("0.2"), False) == (
            "spot sell exceeds venue base balance")
        # Funded, the same buy is admitted, and then the sell is too.
        exchange.class_transfer(Decimal(50), False)
        handle = _producer(rt)
        result, _ = rt._run_tool("seed-decider", handle, {
            "tool": "venue.place_market",
            "args": {"coin": "BTC/USDC", "market": "spot", "side": "buy", "size": "0.2"}})
        assert result["status"] == "filled"
        assert rt._order_collateral("h", "BTC/USDC", Decimal("0.2"), False) is None
        spot_settled = [i for i in settled(rt) if i["custody"] == "venue_spot"]
        assert all(i["reason"] == "exchange_pnl" for i in spot_settled)
    finally:
        rt._ledger_lock.close()


def test_unknown_collateral_blocks_new_risk_and_never_a_reduction(monkeypatch):
    rt = venue_runtime(venue_usd="1000", wallet_micro=1_000_000)
    assert place(rt, "0.02")["status"] == "filled"

    def unavailable(*a, **kw):
        raise VenueUnavailable("account read failed")

    monkeypatch.setattr(rt.exchange.target, "collateral_view", unavailable)
    assert rt._order_collateral("h", "BTC", Decimal("0.01"), True) == (
        "order collateral unavailable: VenueUnavailable")
    # A reduction is never blocked by collateral it is about to release.
    assert rt._order_collateral("h", "BTC", Decimal("0.01"), False, reduce_only=True) is None


def test_a_stale_collateral_observation_blocks_new_risk():
    """A live venue's last complete snapshot is not this tick's collateral."""
    rt = venue_runtime(venue_usd="1000", wallet_micro=1_000_000)
    stale = {"eligible_equity_usd": Decimal(1000), "margin_used_usd": Decimal(0),
             "open_order_holds_usd": Decimal(0), "holds_included_in_margin_used": False,
             "leverage_for_instrument": Decimal(3), "position_size": Decimal(0),
             "spot_available": {"USDC": Decimal(0)},
             "observed_at_ns": rt.clock.now_ns - rt.m.tick_interval_ns * 5}
    rt.exchange.deterministic = False
    rt.exchange.target.collateral_view = lambda coin, market="perp": stale
    try:
        assert rt._order_collateral("h", "BTC", Decimal("0.01"), True) == (
            "order collateral is stale: venue account older than one tick")
    finally:
        del rt.exchange.target.collateral_view


# --- 5. the custody view shows every account and never a fabricated one ------------------


def test_a_failed_venue_read_is_unavailable_and_never_the_wallet_balance(monkeypatch):
    rt = venue_runtime(venue_usd="1000", wallet_micro=50_000_000)

    def unavailable():
        raise VenueUnavailable("user_state unreachable")

    monkeypatch.setattr(rt.exchange.target, "account", unavailable)
    rt.ticks_consumed += 1  # a new tick, so the memo is re-read and fails
    view = custody_view(rt)
    for name in ("venue_perps", "venue_spot"):
        assert view[name]["status"] == "unavailable"
        assert "VenueUnavailable" in view[name]["reason"]
        assert "equity_usd" not in view[name] and "positions" not in view[name]
    block = rt._world_block()
    assert block["account"]["status"] == "unavailable"
    assert "equity_usd" not in block["account"]
    # The wallet appears once, labelled authority, and never as venue equity.
    assert str(rt.wallet.balance) not in str(block["account"]["custody"]["venue_perps"])
    assert block["account"]["custody"]["authority"]["kind"] == "authority"
    assert block["world_resources"]["trading_equity_usd"] is None


def test_the_custody_view_reads_the_venue_the_orders_actually_reached():
    exchange = _venue()
    rt = _spot_runtime(exchange)
    try:
        exchange.class_transfer(Decimal(50), False)
        exchange.place(Order("BTC/USDC", True, Decimal("0.2"), market="spot"))
        exchange.drain_events()
        rt.ticks_consumed += 1
        view = custody_view(rt)
        balances = {row["coin"]: row for row in view["venue_spot"]["balances"]}
        assert balances["BTC"]["total"] == "0.2"
        assert view["venue_perps"]["equity_usd"] == str(exchange._perp_equity())
        assert view["base_reserve"]["status"] == "observed"
    finally:
        rt._ledger_lock.close()
