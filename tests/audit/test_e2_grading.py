"""Edition 2, contract C6: the grading defects of the GPT-6 cold audit (F3–F7), closed.

Each test reproduces the reviewer's scenario in the intended direction: the
defect it names would fail the assertion on the edition 1 code.
"""

from dataclasses import replace
from fractions import Fraction

import pytest

from factorylab.charter.charter import MetricCard
from factorylab.charter.controller import CardRegion
from factorylab.charter.measurement import CardSamples, measure_card, measurement_catalogue
from factorylab.charter.windows import MetricWindow
from factorylab.cortex.request import Return
from factorylab.kernel.ledger import Ledger
from factorylab.kernel.queue import DecisionQueue
from factorylab.runtime.worlds import load_manifest
from factorylab.settlement import (
    ConsequenceStanding,
    ForecastBook,
    Observer,
    PrevalenceBaseline,
    Settler,
)
from factorylab.settlement.lots import LotTable
from tests.audit.test_a4_prices import decision, runtime


def _settler():
    clock = lambda: 100  # noqa: E731
    ledger = Ledger(clock_ns=clock)
    baseline = PrevalenceBaseline()
    return Settler(ForecastBook(ledger), DecisionQueue(ledger, clock_ns=clock),
                   ConsequenceStanding(0.75), baseline, Observer()), baseline


# --- F3: fractional verdict outcome, fractional prevalence baseline ---------------------

def test_f3_a_constant_judge_under_constant_fractional_blame_has_zero_excess_skill():
    """Every return carries 10% of its window's blame; a judge always answers 0.9.

    The scored target is always 0.9. The baseline used to learn "never unblamed"
    (target 0) and the judge showed 0.81 of invented skill; now the baseline
    learns 0.9, the same quantity, and the excess is zero from the second
    verdict on.
    """
    settler, baseline = _settler()
    excess = []
    for i in range(12):
        verdict = settler.settle_verdict(evaluator_id="judge", about_handle=f"r{i}", q=0.9,
                                         share=0.1)
        assert verdict.outcome == pytest.approx(0.9)
        excess.append(verdict.brier - verdict.baseline_brier)
    assert excess[0] == pytest.approx(0.16)  # the uninformed 0.5 prior, once
    assert all(e == pytest.approx(0.0) for e in excess[1:])
    assert baseline.baseline_q("verdict_not_blamed") == pytest.approx(0.9)


def test_f3_one_percent_blame_and_a_verdict_of_one_shows_no_spurious_skill():
    settler, _ = _settler()
    last = None
    for i in range(5):
        last = settler.settle_verdict(evaluator_id="judge", about_handle=f"r{i}", q=1.0,
                                      share=0.01)
    # Before: 0.98 of apparent excess skill. Now the base rate holds the same 0.99 target
    # and the over-endorsing judge scores fractionally below it, never above.
    assert last.brier - last.baseline_brier == pytest.approx(-1e-4)


def test_f3_zero_blame_and_full_blame_controls_keep_their_matched_targets():
    settler, baseline = _settler()
    clean = settler.settle_verdict(evaluator_id="judge", about_handle="clean", q=1.0, share=0.0)
    assert clean.outcome == 1.0 and clean.brier == 1.0
    blamed = settler.settle_verdict(evaluator_id="judge", about_handle="blamed", q=0.0, share=1.0)
    assert blamed.outcome == 0.0 and blamed.brier == 1.0
    assert baseline.baseline_q("verdict_not_blamed") == pytest.approx(0.5)


def test_f3_a_return_enters_the_base_rate_once_however_many_judges_it_has():
    settler, baseline = _settler()
    settler.settle_verdict(evaluator_id="a", about_handle="r", q=0.5, share=0.3)
    settler.settle_verdict(evaluator_id="b", about_handle="r", q=0.5, share=0.3)
    assert baseline.baseline_q("verdict_not_blamed") == pytest.approx(0.7)
    later = settler.settle_verdict(evaluator_id="c", about_handle="r", q=0.5, share=0.3)
    assert later.baseline_brier == pytest.approx(1 - (0.5 - 0.7) ** 2)  # the pre-outcome prior


# --- F4: cost per attempt counts tolerated failures -------------------------------------

def _samples(costs_ok: list[tuple[int, bool]], *, tool_calls: int = 0) -> CardSamples:
    samples = CardSamples()
    for i, (cost, ok) in enumerate(costs_ok):
        ret = Return(f"h{i}", {}, cost, "ok" if ok else "failed",
                     tool_calls=tuple({"tool": "t", "args": {}} for _ in range(tool_calls)))
        samples.returned(handle=f"h{i}", assembly="asm", role="producer", window=1, ret=ret)
    return samples


def _card(observation: str, region: str) -> MetricCard:
    return MetricCard("cap", "care with scarce resources", "cost", "micro-USD",
                      MetricWindow("returns", 10, "role"), region, observation, "producer")


def test_f4_nine_cheap_successes_and_one_expensive_failure_fail_the_attempt_cap():
    samples = _samples([(1_000, True)] * 9 + [(100_000, False)])
    per_return = measure_card(_card("cost_per_return", "at most 5000"), samples)
    per_attempt = measure_card(_card("cost_per_attempt", "at most 5000"), samples)
    assert per_return == {"producer": pytest.approx(1_000)}  # passes the cap, as before
    assert per_attempt == {"producer": pytest.approx(10_900)}  # fails it
    assert per_attempt["producer"] > 5_000 > per_return["producer"]


def test_f4_rent_adds_to_the_attempt_cost_and_is_never_an_attempt():
    samples = _samples([(1_000, True)] * 4 + [(1_000, False)])
    samples.stored(handle="rent", assembly="asm", role="producer", window=1, cost=5_000)
    card = replace(_card("cost_per_attempt", "at most 5000"), window=MetricWindow("returns", 5,
                                                                                    "role"))
    assert measure_card(card, samples) == {"producer": pytest.approx((5_000 + 5_000) / 5)}


def test_f4_cost_per_return_keeps_its_old_meaning_for_old_charters():
    manifest = load_manifest("worlds/edition1-example.toml")
    card = next(c for c in manifest.charter.cards if c.id == "model_cost_efficiency")
    assert card.observation == "cost_per_return"
    samples = _samples([(1_000, True)] * 9 + [(100_000, False)])
    assert measure_card(replace(card, window=MetricWindow("returns", 10, "role")),
                        samples) == {"producer": pytest.approx(1_000)}


def test_f4_cost_per_attempt_is_in_the_catalogue_the_charter_loader_reads():
    row = next(r for r in measurement_catalogue() if r["id"] == "cost_per_attempt")
    assert row["window_kinds"] == ["windows", "returns"] and row["groupable"]


# --- F5: tool discipline is calls per return ---------------------------------------------

def test_f5_ten_returns_with_one_call_each_measure_one_not_ten():
    samples = _samples([(1, True)] * 10, tool_calls=1)
    card = MetricCard("tool-discipline", "care with scarce resources", "calls", "count",
                      MetricWindow("returns", 10, "assembly"), "at most 2", "tool_calls",
                      "producer")
    assert measure_card(card, samples) == {"asm": pytest.approx(1.0)}


# --- F6: generic blame has a floor ------------------------------------------------------

def test_f6_ten_and_twenty_equal_contributors_each_carry_at_least_the_floor():
    rt = runtime()
    assert rt.m.prices.min_blame_share == 0.1 and rt.m.prices.penalty_cap == 0.5
    region = CardRegion("skill", "min", 0.0, None, 1)
    handles = [decision(rt, 100) for _ in range(10)]
    shares = [rt._decision_share(rt.window, h, "forecast_skill", "producer", region, -0.5)
              for h in handles]
    assert all(s == pytest.approx(0.1) for s in shares)
    handles += [decision(rt, 100) for _ in range(10)]
    shares = [rt._decision_share(rt.window, h, "forecast_skill", "producer", region, -0.5)
              for h in handles]
    assert all(s >= 0.1 for s in shares) and shares[0] == pytest.approx(0.1)  # not 1/20
    # Under the 0.5 cap a fully priced violation still costs each decision at least 0.05.
    assert min(1.0, rt.m.prices.penalty_cap) * min(shares) >= 0.05


def test_f6_the_floor_is_a_manifest_price_and_a_small_committee_is_not_floored():
    rt = runtime()
    rt.m = replace(rt.m, prices=replace(rt.m.prices, min_blame_share=0.25))
    region = CardRegion("skill", "min", 0.0, None, 1)
    handles = [decision(rt, 100) for _ in range(2)]
    assert rt._decision_share(rt.window, handles[0], "forecast_skill", "producer", region,
                              -0.5) == pytest.approx(0.5)
    handles += [decision(rt, 100) for _ in range(6)]
    assert rt._decision_share(rt.window, handles[0], "forecast_skill", "producer", region,
                              -0.5) == pytest.approx(0.25)


def test_f6_cost_per_attempt_blame_is_owned_by_every_attempt_failed_ones_included():
    rt = runtime()
    cheap = decision(rt, 1_000)
    failed = decision(rt, 9_000, ok=False)
    region = CardRegion("cap", "max", None, 5_000, 1)
    assert rt._decision_share(rt.window, failed, "cost_per_attempt", "producer", region,
                              5_000) == pytest.approx(0.9)
    assert rt._decision_share(rt.window, cheap, "cost_per_attempt", "producer", region,
                              5_000) == pytest.approx(0.1)
    # Per successful return the failure owns nothing, as its cost is not measured there.
    assert rt._decision_share(rt.window, failed, "cost_per_return", "producer", region,
                              5_000) == 0.0


# --- F7: lot credit is conserved ---------------------------------------------------------

def _fill(table, owner, oid, *, size="1", px="100", buy=True, fee="0"):
    table = table.order(oid, owner, size)
    return table.fill(order_id=oid, coin="BTC", is_buy=buy, size=size, px=px, fee_usd=fee)


def test_f7_opener_and_closer_credits_sum_to_the_lots_pnl():
    """The reviewer's zero-fee trade: $0.10 made, each side cost $0.06.

    Before, both sides were credited the whole $0.10 and both paid off while
    the pair spent $0.12 to make $0.10. Now the $0.10 is split 100:110 by the
    notional each contributed, the credits sum to the P&L, and neither side
    pays off on money the pair never made.
    """
    table = LotTable().start("opener", 1).finish("opener", 60_000)
    table = table.start("closer", 2).finish("closer", 60_000)
    table = _fill(table, "opener", "open", size="0.01", px="100")
    table = _fill(table, "closer", "close", size="0.01", px="110", buy=False)
    opener = table.account("opener").realized_micro
    closer = table.account("closer").realized_micro
    assert opener + closer == Fraction(100_000)  # $0.10, once
    assert opener == Fraction(100_000) * 100 / 210 and closer == Fraction(100_000) * 110 / 210
    resolved = table.resolve(3, 200, {})
    assert resolved.account("opener").payoff.y == 0
    assert resolved.account("closer").payoff.y == 0


def test_f7_a_self_close_and_a_liquidation_still_credit_the_whole_pnl_once():
    table = LotTable().start("self", 1).finish("self", 0)
    table = _fill(table, "self", "open", px="100")
    table = _fill(table, "self", "close", px="120", buy=False, fee="1")
    assert table.account("self").realized_micro == Fraction(19_000_000)
    table = LotTable().start("victim", 1).finish("victim", 0)
    table = _fill(table, "victim", "open", px="100")
    table = table.fill(order_id="liq", coin="BTC", is_buy=False, size="1", px="90",
                       fee_usd="1", liquidation=True)
    assert table.account("victim").realized_micro == Fraction(-11_000_000)
