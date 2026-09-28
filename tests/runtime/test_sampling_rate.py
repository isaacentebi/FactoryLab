"""§IV.b: observed divergence and support loss buy more ordinary evaluation draws."""

from dataclasses import replace

import pytest

from tests.gauntlet import populations as P

pytestmark = pytest.mark.gate


def _world(mode, *, cap=0.7, base=0.3):
    def judge(view):
        if mode in {"gap", "never"} and (mode == "never" or view.tick >= 20):
            return P.decline(view)
        if mode == "divergence":
            return P.rising_verdict(0.55, 0.95, 80)(view)
        return P.verdict(0.6)(view)

    seats = [P.producer("a", P.hold), P.producer("b", P.hold),
             *(P.judge(f"j{i}", judge) for i in range(4)),
             *(P.meta(f"m{i}", P.conformity(0.8)) for i in range(2))]
    cards = [P.UPTAKE, P.WELL_FORMED]
    # A calm no-trigger control has no sparse verdict card: stochastic routing can
    # leave a real support gap even when every invoked judge returns a verdict.
    if mode not in {"divergence", "calm"}:
        cards.append(P.card("grade", "verdict_mean", "at least 0.5",
                            answers_for="evaluator"))
    manifest = P.world(seats, cards=cards, changes={
        "timing": {"world_repricing": "9s"},
        "evaluation": {"sampling_cap": cap, "multi_judge_share": base}})
    return P.run(manifest, P.Population(seats), events=100 if mode == "divergence" else 64,
                 instrument=False)


@pytest.fixture(scope="module")
def divergence():
    return _world("divergence")


@pytest.fixture(scope="module")
def gap():
    return _world("gap")


def test_divergence_increases_actual_paid_evaluation_sampling(divergence):
    raises = divergence.rows("sampling.rate_raise")
    assert raises
    for row in raises:
        assert not row["gaps"]
        assert row["verdict_slope"] > 0 > row["outcome_slope"]
        assert row["rate_after"] == pytest.approx(min(0.7, row["rate_before"] + 0.1))
    _paid_draws(divergence, raises[0])


def _paid_draws(run, change):
    rate = 0.3
    for row in run.events:
        if row["kind"] in {"sampling.rate_raise", "sampling.rate_lower"}:
            assert row["rate_before"] == rate
            rate = row["rate_after"]
        elif row["kind"] == "route.multi_judge":
            assert row["share"] == rate
    assert run.rt.multi_judge_share == rate
    routes = [r for r in run.rows("route.multi_judge")
              if r["seq"] > change["seq"] and r["share"] > 0.3]
    assert routes
    assert all(r["sample"] < r["share"] for r in routes)
    assert any(r["sample"] >= 0.3 for r in routes)  # The base rate would skip this draw.
    paid = []
    for route in routes:
        if route["sample"] < 0.3:
            continue  # Attribute the bill to draws the baseline would not have bought.
        decisions = [r for r in run.rows("decision.open")
                     if r.get("event_id") == route["event_id"] and r["seq"] > route["seq"]]
        for decision in decisions:
            chosen = decision["propensity"]["chosen"]
            paid.extend(r for r in run.rows("wallet.commit")
                        if r["handle"] == decision["handle"]
                        and r["reason"] == f"model:fake-{chosen}")
    assert paid
    assert all(r["amount"] == 500 for r in paid)
    assert all(0.3 < r["share"] <= 0.7 for r in routes)


def test_observed_support_loss_raises_rate_without_fabricating_support(gap):
    raises = [r for r in gap.rows("sampling.rate_raise") if r["gaps"]]
    assert raises
    windows = {r["window"]: r for r in gap.rows("price.window")}
    for row in raises:
        fact = row["gaps"]["grade"]
        assert fact["unmeasured_windows"] > 0
        assert "grade" in windows[fact["last_measured_window"]]["values"]
        assert "grade" not in windows[row["window"]]["values"]
        assert row["rate_after"] == pytest.approx(min(0.7, row["rate_before"] + 0.1))
    assert gap.rows("sampling.blind")
    assert not gap.rows("sampling.lower", "sampling.rate_lower")
    _paid_draws(gap, raises[0])


def test_live_rate_and_measurement_support_survive_checkpoint(gap):
    from factorylab.runtime.loop import Runtime
    from factorylab.runtime.resume import restore_runtime, runtime_state

    state = runtime_state(gap.rt)
    twin = Runtime(gap.rt.m, ledger_path=None, **state["config"])
    try:
        restore_runtime(twin, state)
        assert twin.multi_judge_share == gap.rt.multi_judge_share > 0.3
        assert twin._sampling_gaps() == gap.rt._sampling_gaps()
        assert "grade" in twin._sampling_gaps()
    finally:
        twin._ledger_lock.close()


def test_removed_or_redefined_metric_cannot_reuse_old_support(gap):
    rt = gap.rt
    charter, support = rt.charter, dict(rt.sampling_card_support)
    try:
        card = next(c for c in charter.cards if c.id == "grade")
        assert "grade" in rt._sampling_gaps()
        for cards in (
            tuple(c for c in charter.cards if c.id != "grade"),
            tuple(replace(c, answers_for="producer") if c == card else c
                  for c in charter.cards),
        ):
            rt.sampling_card_support = dict(support)
            rt.charter = replace(charter, cards=cards)
            assert "grade" not in rt._sampling_gaps()
            assert "grade" not in rt.sampling_card_support
    finally:
        rt.charter, rt.sampling_card_support = charter, support


@pytest.mark.parametrize("mode,base", [("never", 0.3), ("calm", 0.3), ("gap", 0.8)])
def test_absent_trigger_or_base_above_cap_does_not_raise(mode, base):
    run = _world(mode, base=base)
    assert not run.rows("sampling.rate_raise")
    assert run.rt.multi_judge_share == base
    if mode == "calm":
        assert len(run.rt.sampling_history) == run.rt.m.immune.k
        assert all(row["consequence"] is not None for row in run.rt.sampling_history)
        assert not run.rt._sampling_gaps()
    assert all(r["share"] == base for r in run.rows("route.multi_judge"))
