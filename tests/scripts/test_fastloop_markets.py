"""The scripted harness reaches the charter's markets (test plumbing, not a prompt)."""

from pathlib import Path

from scripts import fastloop

EDITION6 = Path(__file__).parents[2] / "worlds/edition6-testnet-rehearsal.toml"


def test_the_scripted_population_posts_lambda_proposes_motions_and_forecasts_both_branches():
    policy = fastloop.PolicyProvider(EDITION6)
    replies = {n: policy._markets(n) for n in range(1, 9)}
    assert replies[3]["shadow_prices"] == {"censorship-bound": 0.2}
    assert replies[3]["register"][0]["id"] == "market-enact"
    assert replies[6]["register"][0]["id"] == "market-reject"
    assert {(f["motion"], f["branch"]) for f in replies[4]["motion_forecasts"]} == {
        ("market-enact", "enact"), ("market-enact", "reject")}
    assert replies[1] == {}


def test_the_scorecard_reads_the_markets_from_the_diary():
    events = [
        {"kind": "lambda_post.posted"}, {"kind": "lambda_post.settled", "status": "settled",
                                         "score": 0.5, "realized": 0.2},
        {"kind": "policy.forecast"}, {"kind": "policy.void", "branch": "enact"},
        {"kind": "policy.outcome", "status": "settled", "forecast": True, "branch": "reject",
         "score": 0.64},
        {"kind": "policy.outcome", "status": "settled", "forecast": False, "branch": "reject",
         "score": 1.0},
        {"kind": "price.update", "f": 0.01, "anticipated": 0.02},
        {"kind": "price.update", "f": 0.0, "anticipated": 0.0}, {"kind": "price.update"},
    ]
    card = fastloop.charter_markets(events)
    assert card["lambda_posts"]["settled"] == 1 and card["lambda_posts"]["mean_score"] == 0.5
    assert card["lambda_posts"]["censored"] == 0 and card["margins"]["read"] == 0
    assert card["motions"]["graded"] == {"forecast:reject": {"n": 1, "score_sum": 0.64},
                                         "ballot:reject": {"n": 1, "score_sum": 1.0}}
    assert card["motions"]["void"] == {"enact": 1}
    assert card["feed_forward"] == {"price_updates": 2, "moved": 1, "f_min": 0.01,
                                    "f_max": 0.01}
