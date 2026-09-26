"""The priced road not taken is a money fact (ruling R2; wave 16, D1).

y for a return that executed nothing is binary and net of the venue's own fee and
funding: 1 when the named trade would not have beaten the venue's round-trip taker
fee over the horizon (declining was right in money), 0 otherwise. The attempted
form, for a refused answer order, is its complement. Every term is a fact the
venue states: the move, the taker rate and the funding rate. No scale is the
architect's, so none exists to set.
"""

from decimal import Decimal

import pytest

from factorylab.runtime.grounded import (
    ATTEMPTED_DEFINITION,
    OPPORTUNITY_DEFINITION,
    RETIRED_DEFINITIONS,
    attempted_cost,
    declined_trade,
    opportunity_cost,
)

BUY, SELL = {"coin": "BTC", "side": "buy"}, {"coin": "BTC", "side": "sell"}
#: 4.5 bp taker, the rate every fill of the 6-hour run paid: a 9 bp round trip.
TAKER = "0.00045"


def _price(side, bps):
    """A BTC path from 10,000 that moves ``bps`` basis points for ``side``."""
    move = Decimal(bps) if side is BUY else -Decimal(bps)
    return [("BTC", "10000")], [("BTC", str(Decimal(10_000) + move))]


def _round_trip(side, bps, entry=TAKER, exit_=TAKER):
    """The venue's round trip in bp of the entry notional (D7, Codex on #152): the entry
    leg on the entry notional, the exit leg on the exit notional, after / before of it."""
    (_, before), = _price(side, bps)[0]
    (_, after), = _price(side, bps)[1]
    return (Decimal(entry) + Decimal(exit_) * Decimal(after) / Decimal(before)) * 10_000


@pytest.mark.parametrize("side", [BUY, SELL])
@pytest.mark.parametrize("bps", ["0.01", "1", "4.5", "8.99", "9"])
def test_a_move_the_round_trip_would_have_eaten_is_declining_right(side, bps):
    """Money sign: every 0 < g <= fee gives y = 1. The v2 tanh read such moves as the
    hold being wrong, on trades that would have lost money after the venue's fee. The
    fee is the round trip on each leg's own notional: 9 bp and 4.5 bp times the move on
    the exit leg, so a 9 bp short's exit costs a little less and beats it."""
    priced = opportunity_cost(*_price(side, bps), TAKER, TAKER, side)
    fee = _round_trip(side, bps)
    assert Decimal(priced["gross_bps"]) > 0
    assert Decimal(priced["round_trip_fee_bps"]) == fee.quantize(Decimal("0.0001"))
    assert priced["score"] == (1.0 if Decimal(bps) <= fee else 0.0)
    assert priced["score"] == (0.0 if (side is SELL and bps == "9") else 1.0)


@pytest.mark.parametrize("side", [BUY, SELL])
def test_a_trade_that_beats_the_round_trip_makes_declining_wrong(side):
    assert opportunity_cost(*_price(side, "9.01"), TAKER, TAKER, side)["score"] == 0.0
    assert opportunity_cost(*_price(side, "-50"), TAKER, TAKER, side)["score"] == 1.0


def test_the_fee_is_the_venues_and_changing_it_changes_y():
    """The same move is right to decline at one venue's rate and wrong at another's:
    the fee is read from the venue, never a module constant."""
    path = _price(BUY, "6")
    assert opportunity_cost(*path, "0.00045", "0.00045", BUY)["score"] == 1.0
    assert opportunity_cost(*path, "0.00025", "0.00025", BUY)["score"] == 0.0
    assert opportunity_cost(*path, "0", "0", BUY)["score"] == 0.0
    # An unread rate is never a number: no rate, no y.
    assert opportunity_cost(*path, None, TAKER, BUY) is None
    assert opportunity_cost(*path, TAKER, None, BUY) is None
    import factorylab.runtime.grounded as grounded

    constants = [v for k, v in vars(grounded).items()
                 if k.isupper() and isinstance(v, int | float | Decimal)]
    assert constants == []


@pytest.mark.parametrize("side,rate,flips", [(BUY, "0.0002", True), (SELL, "-0.0002", True),
                                            (BUY, "-0.0002", False),
                                            (SELL, "0.0002", False)])
def test_a_funding_payment_inside_the_window_flips_y_where_its_term_crosses_zero(
        side, rate, flips):
    """Longs pay a positive rate. A 12 bp move beats the 9 bp round trip by 3 bp; a
    2 bp funding payment the named side would have paid leaves it beating by 1 bp; a
    4 bp one makes it lose. A payment the named side would have received never flips
    a winner."""
    path = _price(side, "12")
    assert opportunity_cost(*path, TAKER, TAKER, side)["score"] == 0.0
    assert opportunity_cost(*path, TAKER, TAKER, side, [rate])["score"] == 0.0
    doubled = opportunity_cost(*path, TAKER, TAKER, side, [rate, rate])
    assert doubled["score"] == (1.0 if flips else 0.0)
    assert doubled["funding_payments"] == 2
    # The rate term alone decides the flip: at a net of exactly zero declining is right.
    sign = 1 if side is BUY else -1
    zero = sign * (Decimal("12") - _round_trip(side, "12")) / 10_000
    exact = opportunity_cost(*path, TAKER, TAKER, side, [str(zero)])
    assert Decimal(exact["net_bps"]) == 0 and exact["score"] == 1.0


def test_no_hold_y_is_ever_outside_zero_and_one():
    for bps in ("-500", "-9", "-0.5", "0", "0.5", "8.9", "9", "9.1", "500"):
        for side in (BUY, SELL):
            for rates in ((), ["0.0001"], ["-0.0003", "0.0001"]):
                for priced in (opportunity_cost(*_price(side, bps), TAKER, TAKER, side, rates),
                               attempted_cost(*_price(side, bps), TAKER, TAKER, side, rates)):
                    assert priced["score"] in (0.0, 1.0)


@pytest.mark.parametrize("side", [BUY, SELL])
def test_the_attempted_trade_is_one_when_it_would_have_beaten_the_round_trip(side):
    """The complement of the declined form on the same named trade and money terms."""
    for bps in ("-20", "0", "5", "9", "9.01", "40"):
        attempted = attempted_cost(*_price(side, bps), TAKER, TAKER, side)
        declined = opportunity_cost(*_price(side, bps), TAKER, TAKER, side)
        assert attempted["score"] == (1.0 if Decimal(bps) > _round_trip(side, bps) else 0.0)
        assert attempted["score"] + declined["score"] == 1.0
        assert attempted["attempted"] == side and attempted["net_bps"] == declined["net_bps"]


def test_a_bare_hold_or_missing_prices_mean_the_world_did_not_speak():
    assert opportunity_cost([("BTC", "100")], [("BTC", "103")], TAKER, TAKER, None) is None
    assert opportunity_cost([("BTC", "100")], [("ETH", "10")], TAKER, TAKER, BUY) is None
    assert opportunity_cost([], [], TAKER, TAKER, BUY) is None
    assert opportunity_cost([("BTC", "100")], [("BTC", "101")], TAKER, TAKER,
                            {"coin": "ETH", "side": "buy"}) is None
    assert attempted_cost([("BTC", "100")], [("BTC", "101")], None, TAKER, BUY) is None
    assert attempted_cost([("BTC", "100")], [("BTC", "101")], TAKER, None, BUY) is None


def test_the_definitions_are_new_and_the_old_ones_are_names_only():
    assert (OPPORTUNITY_DEFINITION, ATTEMPTED_DEFINITION) == (
        "declined-trade-net-v1", "attempted-trade-net-v1")
    assert set(RETIRED_DEFINITIONS) == {"opportunity-cost-v2", "attempted-trade-v1"}


def test_declined_trade_is_read_only_from_what_the_decision_said():
    assert declined_trade({"counterfactual": {"coin": "btc", "side": "BUY"}}) == {
        "coin": "BTC", "side": "buy"}
    assert declined_trade({"counterfactual": "sell:ETH"}) == {"coin": "ETH", "side": "sell"}
    assert declined_trade({"counterfactual": "would have bought"}) is None
    assert declined_trade({"action": "hold"}) is None


def test_a_manifest_that_names_a_scale_is_refused():
    """``opportunity_scale_bps`` was the architect's number with no world referent."""
    from factorylab.runtime.worlds import load_manifest, manifest_from_dict
    from tests.runtime.test_manifests import _base

    assert not hasattr(load_manifest("scripted").evaluation, "opportunity_scale_bps")
    raw = _base()
    raw["evaluation"] = {"opportunity_scale_bps": 50}
    with pytest.raises(ValueError, match="opportunity_scale_bps was removed"):
        manifest_from_dict(raw)


def _schedule_runtime(perp, spot):
    from types import SimpleNamespace

    from factorylab.runtime.venue import VenueMixin

    class Venue:
        def instruments(self):
            return {"perp": [{"coin": "BTC", "taker_fee_rate": perp}],
                    "spot": [{"coin": "PURR/USDC", "taker_fee_rate": spot}]}

    class Rt(VenueMixin):
        pass

    rt = Rt()
    rows = []
    rt.exchange, rt.fee_schedule = Venue(), None
    rt.clock = SimpleNamespace(now_ns=5)
    rt.ledger = SimpleNamespace(append=rows.append)
    rt.m = SimpleNamespace(timing=SimpleNamespace(world_repricing_ns=100))
    return rt, rows


def test_a_spot_coin_is_priced_at_the_venues_spot_taker_rate():
    """Ruling R-I: spot coins use the venue's spot schedule, read separately."""
    rt, rows = _schedule_runtime("0.00045", "0.0007")
    assert rt._fee_schedule_due()
    rt._read_fee_schedule()
    assert rt._taker_rate("BTC") == "0.00045"
    assert rt._taker_rate("PURR/USDC") == "0.0007"
    assert rows[0]["kind"] == "venue.fee_schedule"
    assert rows[0]["rates"] == {"BTC": "0.00045", "PURR/USDC": "0.0007"}
    # Read once per repricing period, and ledgered again only when it changed.
    assert not rt._fee_schedule_due()
    rt.clock.now_ns = 105
    assert rt._fee_schedule_due()
    rt._read_fee_schedule()
    assert len(rows) == 1


def test_a_listing_that_states_no_rate_prices_nothing():
    rt, _rows = _schedule_runtime(None, None)
    rt._read_fee_schedule()
    assert rt._taker_rate("BTC") is None and rt._taker_rate("PURR/USDC") is None


# --- D7: the road not taken nets exactly what the same acting lot nets -----------------


def _acting_net_micro(side, before, after, size, entry_rate, exit_rate):
    """The net micro-USD of an acting lot opened at ``before`` (paying the entry leg on
    its fill notional) and marked at ``after`` at the horizon (the exit leg on the mark's
    notional, ``LotTable.resolve``), at the same instants and size."""
    from factorylab.settlement.lots import LotTable

    fee = Decimal(entry_rate) * Decimal(before) * Decimal(size)
    table = LotTable().start("acting", 1, ns=1_000).finish("acting", 0).order(
        "1", "acting", size)
    table = table.fill(order_id="1", coin="BTC", is_buy=side is BUY, size=size, px=before,
                       fee_usd=str(fee))
    table = table.resolve(2, 20, {"BTC": after}, now_ns=1_060, horizon_ns=60,
                          exit_rates={"perp": exit_rate})
    return table.account("acting").payoff.net_micro


def _counterfactual_micro(side, before, after, size, entry_rate, exit_rate):
    """The named trade's exact net, in micro-USD of the same size."""
    from factorylab.runtime.grounded import _net

    priced = _net([("BTC", before)], [("BTC", after)], entry_rate, exit_rate, side, ())
    return priced["_net"] / 10_000 * Decimal(before) * Decimal(size) * 1_000_000


def test_codex_case_a_move_that_beats_the_entry_notional_round_trip_is_still_a_loss():
    """Codex on #152: 100 -> 102.005 at 1% each way. On the entry notional the round
    trip is 200 bp and the move 200.5 bp, a win; the exit leg is paid on 102.005, so the
    round trip is 202.005 bp and the trade loses, as the same acting lot does."""
    priced = opportunity_cost([("BTC", "100")], [("BTC", "102.005")], "0.01", "0.01", BUY)
    assert Decimal(priced["exit_fee_bps"]) == Decimal("102.005")
    assert Decimal(priced["net_bps"]) == Decimal("-1.505")
    assert priced["score"] == 1.0  # declining was right
    assert attempted_cost([("BTC", "100")], [("BTC", "102.005")], "0.01", "0.01",
                          BUY)["score"] == 0.0
    acting = _acting_net_micro(BUY, "100", "102.005", "1", "0.01", "0.01")
    assert acting == -15_050 and (acting < 0) == (Decimal(priced["net_bps"]) < 0)


def test_the_counterfactual_net_is_the_acting_lots_net_to_the_micro_usd():
    """D7 as a property: over random entry and exit mids, sizes, sides and fee rates on
    each leg, the named trade's net equals the net of an acting lot opened and marked at
    the same instants and size, to the micro-USD (the lot's outcome is whole micro-USD,
    rounded down)."""
    import random

    rng = random.Random(152)
    for _ in range(400):
        side = rng.choice((BUY, SELL))
        before = Decimal(rng.randint(1, 10_000_000)) / 100
        after = (before * Decimal(rng.randint(8_000, 12_000)) / 10_000).quantize(
            Decimal("0.0001"))
        size = str(Decimal(rng.randint(1, 100_000)) / 1_000)
        entry, exit_ = (str(Decimal(rng.randint(0, 100)) / 10_000) for _ in range(2))
        counterfactual = _counterfactual_micro(side, str(before), str(after), size, entry,
                                               exit_)
        acting = _acting_net_micro(side, str(before), str(after), size, entry, exit_)
        assert 0 <= counterfactual - acting < 1, (side, before, after, size, entry, exit_)
