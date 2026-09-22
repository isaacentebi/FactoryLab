"""Stable failure is priced by its duration; the exploration ratchet unwinds once it clears.

Essay II.II.b: "In the case of stable failure, one should price the duration of
failure, ratcheting up penalties the longer the factory spends in a wide-spectral-gap
attractor that is failing its input-output target." The organ used to halve the
violated cards' price for a window instead, and its exploration raise never came
back down unless thrash was diagnosed.
"""

from dataclasses import replace

import pytest

from factorylab.charter.windows import MetricWindow
from factorylab.runtime.immune import gamma
from factorylab.runtime.loop import Runtime
from factorylab.runtime.pricing import MeasureWindow
from factorylab.runtime.worlds import load_manifest


def _runtime(lambda_max=1.0):
    seed = load_manifest("scripted")
    seed = replace(seed, prices=replace(seed.prices, lambda_max=lambda_max))
    charter = replace(seed.charter, cards=tuple(replace(c, window=MetricWindow("windows", 1, None))
                                               for c in seed.charter.cards))
    rt = Runtime(replace(seed, charter=charter), events=1, seed=1, initial_balance_micro=None,
                 ledger_path=None, drip=False, router_gamma=0.1)
    rt._derive_regions()
    return rt


def _close(rt, well_formed: float, registrations: int = 1):
    rt.n += 10
    rt.window = MeasureWindow(rt.n, rt.wallet.balance, invocations=10,
                              ok=int(well_formed * 10), registrations=registrations)
    rt._close_price_window()


def _items(rt, kind):
    return [i for i in rt.ledger._recovery_items() if i["kind"] == kind]


def test_stable_failure_raises_violated_price_with_duration_and_never_halves_it():
    rt = _runtime(lambda_max=10.0)
    prices = [0.0]
    for _ in range(6):
        _close(rt, 0.2)  # the same failing cell, window after window
        prices.append(rt.controller.price("well_formed_rate"))
    ratchets = _items(rt, "immune.price_ratchet")
    assert len(ratchets) >= 3 and _items(rt, "pathology.stable_failure")
    # Each window's rise is the controller's own step plus the ratchet, and the
    # ratchet's part grows with how long the attractor has held.
    steps = [b - a for a, b in zip(prices, prices[1:], strict=False)]
    assert steps[-1] - steps[0] > 0
    assert steps[-1] - steps[-2] > 0
    assert [r["duration"] for r in ratchets] == list(range(1, len(ratchets) + 1))
    assert [r["step"] for r in ratchets] == [
        rt.m.immune.gain_step * r["duration"] for r in ratchets]
    assert all(r["lambda_after"] >= r["lambda_before"] for r in ratchets)
    assert prices == sorted(prices) and prices[-1] <= rt.m.prices.lambda_max


def test_leaving_the_attractor_ends_the_ratchet_and_unwinds_exploration():
    rt = _runtime()
    for _ in range(4):
        _close(rt, 0.2)
    raised = [gamma(r.learner) for r in rt._all_router_states()]
    seeds = [r.seed_gamma for r in rt._all_router_states()]
    assert any(g > s for g, s in zip(raised, seeds, strict=True))
    assert rt.controller.snapshot()["cards"]["well_formed_rate"]["failing_windows"] > 0
    for i in range(12):
        _close(rt, 1.0, registrations=3 * (i % 2))  # compliant and active: no pathology
    assert rt.controller.snapshot()["cards"]["well_formed_rate"]["failing_windows"] == 0
    assert _items(rt, "immune.price_ratchet_ended")
    assert [gamma(r.learner) for r in rt._all_router_states()] == seeds
    cleared = [i for i in _items(rt, "immune.gain") if i["pathology"] == "cleared"]
    assert cleared and all(max(i["gamma_after"]) < max(i["gamma_before"]) for i in cleared)


def test_price_step_sets_the_ratchet_apart_from_the_gain_step_and_hashes_absent():
    seed = load_manifest("scripted")
    explicit = replace(seed, immune=replace(seed.immune, price_step=None))
    assert explicit.canonical_json() == seed.canonical_json()
    assert "price_step" not in seed.canonical_json()
    stepped = replace(seed, immune=replace(seed.immune, price_step=0.2))
    stepped.validate()
    assert stepped.canonical_json() != seed.canonical_json()
    for bad in (0.0, -0.1, float("nan"), seed.prices.lambda_max * 2, True):
        with pytest.raises(ValueError):
            replace(seed, immune=replace(seed.immune, price_step=bad)).validate()

    rt = _runtime(lambda_max=10.0)
    rt.m = replace(rt.m, immune=replace(rt.m.immune, price_step=0.2))
    for _ in range(6):
        _close(rt, 0.2)
    ratchets = _items(rt, "immune.price_ratchet")
    assert ratchets and [r["step"] for r in ratchets] == pytest.approx(
        [0.2 * r["duration"] for r in ratchets])
