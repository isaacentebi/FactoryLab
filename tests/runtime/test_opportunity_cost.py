"""The priced road not taken is a world measurement (ruling R2), opportunity-cost-v2.

The architect's ruling on the #128 review: y for a hold is a symmetric, monotone
function of the named declined trade's gross return over the horizon, excluding
fees, y = 0.5 - 0.5 * tanh(gross_bps / s). Without directional skill a hold earns
0.5 in expectation whatever trade it names, so naming a dull coin buys nothing.
"""

import math

import pytest

from factorylab.runtime.grounded import declined_trade, opportunity_cost

BUY, SELL = {"coin": "BTC", "side": "buy"}, {"coin": "BTC", "side": "sell"}


def test_naming_a_flat_coin_earns_the_neutral_half():
    """The v1 steer: any named trade that did not beat its fees scored about 1."""
    flat = opportunity_cost([("BTC", "100")], [("BTC", "100")], 50, BUY)
    assert flat["score"] == 0.5 and flat["gross_bps"] == "0.00"
    # A move smaller than a round trip's fees scores in proportion, never a near-certain 1.
    dull = opportunity_cost([("BTC", "100")], [("BTC", "100.05")], 50, SELL)
    assert 0.5 < dull["score"] < 0.6


def test_the_sign_follows_the_move_and_the_function_is_symmetric():
    rally = [("BTC", "100")], [("BTC", "101")]
    missed = opportunity_cost(*rally, 50, BUY)  # declined a buy into a rally: wrong
    right = opportunity_cost(*rally, 50, SELL)  # declined a sell into a rally: right
    assert missed["score"] < 0.5 < right["score"]
    assert missed["score"] + right["score"] == pytest.approx(1.0)
    assert missed["gross_bps"] == "100.00" and right["gross_bps"] == "-100.00"
    assert missed["score"] == pytest.approx(0.5 - 0.5 * math.tanh(100 / 50), abs=1e-6)
    # Monotone: a larger move the trade would have caught scores the hold lower.
    bigger = opportunity_cost([("BTC", "100")], [("BTC", "102")], 50, BUY)
    assert bigger["score"] < missed["score"]


def test_without_directional_skill_a_hold_earns_one_half_in_expectation():
    """Symmetric moves either way average to 0.5 whatever side or coin is named."""
    for side in (BUY, SELL):
        up = opportunity_cost([("BTC", "100")], [("BTC", "100.7")], 50, side)["score"]
        down = opportunity_cost([("BTC", "100")], [("BTC", "99.3")], 50, side)["score"]
        assert (up + down) / 2 == pytest.approx(0.5, abs=1e-6)


def test_a_bare_hold_or_missing_prices_mean_the_world_did_not_speak():
    assert opportunity_cost([("BTC", "100")], [("BTC", "103")], 50, None) is None
    assert opportunity_cost([("BTC", "100")], [("ETH", "10")], 50, BUY) is None
    assert opportunity_cost([], [], 50, BUY) is None
    assert opportunity_cost([("BTC", "100")], [("BTC", "101")], 50,
                            {"coin": "ETH", "side": "buy"}) is None


def test_declined_trade_is_read_only_from_what_the_decision_said():
    assert declined_trade({"counterfactual": {"coin": "btc", "side": "BUY"}}) == {
        "coin": "BTC", "side": "buy"}
    assert declined_trade({"counterfactual": "sell:ETH"}) == {"coin": "ETH", "side": "sell"}
    assert declined_trade({"counterfactual": "would have bought"}) is None
    assert declined_trade({"action": "hold"}) is None


def test_the_scale_is_a_hashed_manifest_key_with_a_stated_default():
    from factorylab.runtime.worlds import load_manifest, manifest_from_dict
    from tests.runtime.test_manifests import _base

    assert load_manifest("scripted").evaluation.opportunity_scale_bps == 50.0
    default = manifest_from_dict(_base())
    raw = _base()
    raw["evaluation"] = {"opportunity_scale_bps": 80}
    scaled = manifest_from_dict(raw)
    assert scaled.evaluation.opportunity_scale_bps == 80
    assert scaled.manifest_hash() != default.manifest_hash()
    for bad in (0, -5, "50", float("inf")):
        raw["evaluation"] = {"opportunity_scale_bps": bad}
        with pytest.raises(ValueError, match="opportunity_scale_bps"):
            manifest_from_dict(raw)
