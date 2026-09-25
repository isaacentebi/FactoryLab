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


def _runtime(**prices):
    """The scripted world, its price gains overridden by ``prices``: a test of the
    duration price needs gains that leave it room below the cap (``gain_headroom``)."""
    seed = load_manifest("scripted")
    seed = replace(seed, prices=replace(seed.prices, **prices))
    charter = replace(seed.charter, cards=tuple(replace(c, window=MetricWindow("windows", 1, None))
                                               for c in seed.charter.cards))
    rt = Runtime(replace(seed, charter=charter), events=1, seed=1, initial_balance_micro=None,
                 ledger_path=None, router_gamma=0.1)
    rt._derive_regions()
    return rt


def _close(rt, well_formed: float, registrations: int = 1, *, organ_due: bool = True):
    """Close one window, a price-loop period of ticks after the last.

    ``organ_due`` makes the immune organ's own loop due at this close (versioning P5:
    left to itself it acts once every min_ratio price periods or more).
    """
    rt.n += 10
    rt.ticks_consumed += rt.m.timing.min_ratio
    if organ_due:
        rt.clockwork.force("immune", rt.ticks_consumed)
    rt.window = MeasureWindow(rt.n, rt.wallet.balance, invocations=10,
                              ok=int(well_formed * 10), registrations=registrations)
    rt._close_price_window()


def _items(rt, kind):
    return [i for i in rt.ledger._recovery_items() if i["kind"] == kind]


def test_stable_failure_raises_violated_price_with_duration_and_never_halves_it():
    rt = _runtime(eta=0.01)  # the PID alone takes many windows to reach the cap
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
        rt.m.immune.price_step * r["duration"] for r in ratchets]
    assert all(r["lambda_after"] >= r["lambda_before"] for r in ratchets)
    # Bounded by the one bound (wave 16, R-E): the card's penalty never passes the cap.
    bound = rt.controller.saturation("well_formed_rate")["bound"]
    assert prices == sorted(prices) and prices[-1] <= bound


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


def test_the_organ_diagnoses_every_window_and_acts_on_its_own_slower_loop():
    """Versioning P5, time audit T2: the organ acts once every min_ratio price periods or
    more, jittered, and diagnoses every window in between."""
    rt = _runtime()
    rt.clockwork.fire("price", rt.ticks_consumed, 1)
    rt.clockwork.fire("immune", rt.ticks_consumed, rt.clockwork.period("price"))
    for _ in range(12):
        _close(rt, 0.2, organ_due=False)
    windows = _items(rt, "immune.window")
    acted = [w["window"] for w in windows if w["acts"]]
    assert len(windows) == 12 and 1 <= len(acted) <= 12 // rt.m.timing.min_ratio
    loops = [i for i in _items(rt, "clock.loop") if i["loop"] == "immune"]
    assert loops and all(i["period_ticks"] >= rt.m.timing.min_ratio * i["inner_ticks"]
                         for i in loops)
    # Only a window the organ acted in carries a gain step or a ratchet.
    assert {i["window"] for i in _items(rt, "immune.price_ratchet")} <= set(acted)


def test_price_step_alone_sets_the_ratchet_and_is_part_of_the_identity():
    seed = load_manifest("scripted")
    assert '"price_step":0.05' in seed.canonical_json()
    stepped = replace(seed, immune=replace(seed.immune, price_step=0.2))
    stepped.validate()
    assert stepped.canonical_json() != seed.canonical_json()
    for bad in (None, 0.0, -0.1, float("nan"), float("inf"), True):
        with pytest.raises(ValueError):
            replace(seed, immune=replace(seed.immune, price_step=bad)).validate()

    rt = _runtime(eta=0.01)
    rt.m = replace(rt.m, immune=replace(rt.m.immune, price_step=0.02))
    for _ in range(6):
        _close(rt, 0.2)
    ratchets = _items(rt, "immune.price_ratchet")
    assert ratchets and [r["step"] for r in ratchets] == pytest.approx(
        [0.02 * r["duration"] for r in ratchets])
