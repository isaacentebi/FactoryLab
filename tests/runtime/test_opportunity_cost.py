"""The priced road not taken is a world measurement (ruling R2).

What a declined trade would have netted over the horizon, net of a round trip's
fees, priced on the trade the decision itself named and never in hindsight. It
grades the verdicts on that decision (tests/runtime/test_reward_chain.py); these
cases pin the measurement itself.
"""

from decimal import Decimal

from factorylab.runtime.grounded import opportunity_cost


def test_a_bare_hold_has_zero_consequence_and_settles_neutral():
    priced = opportunity_cost([("BTC", "100")], [("BTC", "103")], Decimal("9"))
    assert priced["score"] == 0.5 and priced["declined"] is None


def test_a_named_declined_trade_is_priced_ex_ante_never_in_hindsight():
    # Declined a buy; the market rallied 1%: 91 bp passed up against a 9 bp round trip.
    missed = opportunity_cost([("BTC", "100")], [("BTC", "101")], Decimal("9"),
                              {"coin": "BTC", "side": "buy"})
    assert missed["regret_bps"] == "91.00" and missed["score"] == round(9 / 100, 4)
    # Declined a sell into the same rally: declining it was right.
    right = opportunity_cost([("BTC", "100")], [("BTC", "101")], Decimal("9"),
                             {"coin": "BTC", "side": "sell"})
    assert right["regret_bps"] == "0.00" and right["score"] == 1.0
    # A move smaller than the fees vindicates any declined trade.
    flat = opportunity_cost([("BTC", "100")], [("BTC", "100.05")], Decimal("9"),
                            {"coin": "BTC", "side": "buy"})
    assert flat["score"] == 1.0


def test_no_shared_prices_means_the_world_did_not_speak():
    assert opportunity_cost([("BTC", "100")], [("ETH", "10")], Decimal("9")) is None
    assert opportunity_cost([], [], Decimal("9")) is None
    assert opportunity_cost([("BTC", "100")], [("BTC", "101")], Decimal("9"),
                            {"coin": "ETH", "side": "buy"}) is None


def test_declined_trade_is_read_only_from_what_the_decision_said():
    from factorylab.runtime.grounded import declined_trade

    assert declined_trade({"counterfactual": {"coin": "btc", "side": "BUY"}}) == {
        "coin": "BTC", "side": "buy"}
    assert declined_trade({"counterfactual": "sell:ETH"}) == {"coin": "ETH", "side": "sell"}
    assert declined_trade({"counterfactual": "would have bought"}) is None
    assert declined_trade({"action": "hold"}) is None
