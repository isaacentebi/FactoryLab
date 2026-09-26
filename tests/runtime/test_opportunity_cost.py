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


@pytest.mark.parametrize("side", [BUY, SELL])
@pytest.mark.parametrize("bps", ["0.01", "1", "4.5", "8.99", "9"])
def test_a_move_the_round_trip_would_have_eaten_is_declining_right(side, bps):
    """Money sign: every 0 < g <= fee gives y = 1. The v2 tanh read such moves as the
    hold being wrong, on trades that would have lost money after the venue's fee."""
    priced = opportunity_cost(*_price(side, bps), TAKER, TAKER, side)
    assert Decimal(priced["gross_bps"]) > 0
    assert priced["round_trip_fee_bps"] == "9"
    assert priced["score"] == 1.0


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
    exact = opportunity_cost(*path, TAKER, TAKER, side, ["0.0003" if side is BUY else "-0.0003"])
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
        assert attempted["score"] == (1.0 if Decimal(bps) > 9 else 0.0)
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
