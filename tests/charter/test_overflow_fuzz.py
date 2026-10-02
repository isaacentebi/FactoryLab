"""No price-loop input the charter admits drives a ledger row to infinity (Codex on #152).

The class, closed by a fuzz rather than one case at a time: a real ``PriceController``,
and the runtime's pricing window close, driven through many windows with extreme gains,
adopted prices up to 1e308, region scales down to the smallest subnormal, violations from
zero through subnormal to 1e308, holdouts at ``FLOAT_MAX``, ratchet steps near
``FLOAT_MAX``, redefinitions and spikes. Every call returns, and every ledger row the
run writes is JSON with no infinity or NaN (a ledger row must be recoverable).
"""

from __future__ import annotations

import json
import random

from factorylab.charter.controller import FLOAT_MAX, CardRegion, PriceController
from factorylab.kernel.ledger import Ledger

#: Magnitudes the fuzz draws from: zero, subnormal, ordinary, huge and the float limit.
EXTREMES = (0.0, 5e-324, 1e-300, 1e-9, 0.3, 1.0, 7.0, 1e9, 1e300, 1e308, FLOAT_MAX)
SCALES = (5e-324, 1e-300, 1e-9, 0.5, 1.0, 1e300, FLOAT_MAX)
WINDOWS = 50


def _draw(rng: random.Random, pool=EXTREMES) -> float:
    return rng.choice(pool)


def _region(rng: random.Random, card_id: str) -> CardRegion:
    kind = rng.choice(("max", "min", "band"))
    scale = _draw(rng, SCALES)
    if kind == "max":
        return CardRegion(card_id, "max", None, _draw(rng), scale)
    if kind == "min":
        return CardRegion(card_id, "min", _draw(rng), None, scale)
    lo, hi = sorted(rng.sample(EXTREMES, 2))
    return CardRegion(card_id, "band", lo, hi, scale)


def _value(rng: random.Random) -> float:
    return rng.choice((1, -1)) * _draw(rng)


def _controller(rng: random.Random, ledger: Ledger) -> PriceController:
    return PriceController(
        ledger, eta=rng.choice((1e-300, 1e-9, 0.05, 0.9, 1.0, 1e9, 1e300)),
        decay=rng.choice((5e-324, 1e-9, 0.1, 1.0, 1e300)),
        penalty_cap=rng.choice((1e-300, 0.1, 0.5, 0.9, 1 - 1e-16)),
        min_window_events=1,
        kp=rng.choice((0.0, 1e-300, 0.5, 1.0, 1e300, FLOAT_MAX)),
        kd=rng.choice((0.0, 0.25, 1e300, FLOAT_MAX)))


def _rows_are_json(ledger: Ledger) -> None:
    for row in ledger._recovery_items():
        json.dumps(row, allow_nan=False)


def test_no_controller_input_ledgers_an_infinity():
    for seed in range(40):
        rng = random.Random(seed)
        ledger = Ledger()
        prices = _controller(rng, ledger)
        cards = ["a", "b", "c"]
        for card_id in cards:
            prices.register(_region(rng, card_id))
        for window in range(WINDOWS):
            card_id = rng.choice(cards)
            move = rng.random()
            if move < 0.1:
                prices.set_price(card_id, _draw(rng), amendment_id=f"am-{window}")
            elif move < 0.2:
                prices.update_region(_region(rng, card_id))
            elif move < 0.25:
                prices.redefine(card_id, edition=window, window_end_event=window)
            for each in cards:
                prices.observe(each, _value(rng), window,
                               holdout=rng.choice((0.0, 1.0, 1e308, FLOAT_MAX)),
                               anticipated=rng.choice((None, 0.0, 1e308, -FLOAT_MAX)),
                               pressure=rng.choice((None, 0.0, 0.5, FLOAT_MAX)))
            if rng.random() < 0.5:
                prices.ratchet(card_id, window=window,
                               step=rng.choice((1e-300, 0.05, 1.0, 9e307, FLOAT_MAX)))
            elif rng.random() < 0.2:
                prices.end_failure(card_id, window=window)
            prices.penalty({each: _value(rng) for each in cards})
            prices.snapshot()
            prices.saturation(card_id)
        _rows_are_json(ledger)


def test_no_window_close_input_ledgers_an_infinity(monkeypatch):
    """The runtime's own close (measurement, holdouts, pressure, the controller, the
    immune organ and its ratchet, blame, unattributed parts) under the same extremes:
    adopted prices up to 1e308, subnormal region scales, holdouts at FLOAT_MAX, ratchet
    steps near FLOAT_MAX and identity-only redefinitions, with decisions priced on it."""
    from dataclasses import replace

    from factorylab.charter.charter import MetricCard
    from factorylab.charter.windows import MetricWindow
    from factorylab.kernel.queue import PropensityRecord
    from factorylab.runtime.loop import Runtime
    from factorylab.runtime.pricing import MeasureWindow
    from factorylab.runtime.worlds import load_manifest

    rng = random.Random(152)
    seed = load_manifest("scripted")
    cards = tuple(
        MetricCard(cid, "care with scarce resources", "A reading.", "fraction",
                   MetricWindow("windows", 1, None), region, "well_formed_rate", "all")
        for cid, region in (("floor", {"rule": "at least", "lo": 0.9}),
                            ("ceiling", {"rule": "at most", "hi": 0.1})))
    manifest = replace(seed, charter=replace(seed.charter, cards=cards),
                       immune=replace(seed.immune, price_step=9e307))
    rt = Runtime(manifest, events=1, seed=1, initial_balance_micro=None, ledger_path=None)
    rt._derive_regions()
    prop = PropensityRecord(("seed-decider",), (1.0,), "seed-decider", 0, "router:Tick", "t")
    for window in range(WINDOWS):
        for card in rt.charter.cards:
            if rng.random() < 0.2:
                rt.controller.set_price(card.id, _draw(rng), amendment_id=f"am-{window}")
            if rng.random() < 0.3:
                region = rt.regions[card.id]
                region = CardRegion(card.id, region.kind, region.lo, region.hi,
                                    _draw(rng, SCALES))
                rt.regions[card.id] = region
                rt.controller.update_region(region)
        if rng.random() < 0.05:  # an identity-only redefinition
            rt.charter = replace(rt.charter, cards=tuple(
                replace(c, answers_for=rng.choice(("all", "producer", "evaluator")))
                for c in rt.charter.cards))
            rt._derive_regions()
        holdout = rng.choice((0.0, 1e308, FLOAT_MAX))
        monkeypatch.setattr(rt, "_holdout_results", lambda values, v=holdout: {
            cid: {"results": {"h@1": False}, "violation": v} for cid in values})
        rt.n += 10
        rt.ticks_consumed += rt.m.timing.min_ratio
        rt.clockwork.force("immune", rt.ticks_consumed)
        rt.window = MeasureWindow(rt.n, rt.wallet.balance, invocations=10,
                                  # A steady failure (the ratchet's attractor),
                                  # with spikes.
                                  ok=10 if rng.random() < 0.1 else 1, registrations=1)
        handles = []
        for i in range(3):
            handle = rt.queue.open(actor="router:Tick", event_id=f"w{window}-{i}",
                                   propensity=prop, channel="verdict",
                                   deadline_ns=rt.clock.now_ns + 10**18,
                                   parent_handle=None, cost_ceiling=0)
            rt.handle_to_assembly[handle] = "seed-decider"
            sample = rt._contribution(handle, "producer")
            sample.update(invocations=1, ok=int(rng.random() < 0.5), cost=_draw(rng) > 1)
            handles.append(handle)
        rt._close_price_window()
        for handle in handles:
            for role in ("all", "producer"):
                assert 0.0 <= rt._penalty_for(role, handle) <= rt.m.prices.penalty_cap
    _rows_are_json(rt.ledger)
    kinds = {row["kind"] for row in rt.ledger._recovery_items()}
    assert {"price.update", "immune.price_ratchet", "price.redefined",
            "price.proposed"} <= kinds, kinds  # the extremes reached every path
