from dataclasses import dataclass, replace

import pytest

from factorylab.charter.controller import CardRegion, PriceController
from factorylab.kernel.events import Bus
from factorylab.kernel.ledger import Ledger
from factorylab.kernel.termination import Termination


@dataclass
class Clock:
    now: int = 100
    fail: bool = False

    def __call__(self):
        if self.fail:
            raise RuntimeError("clock unavailable")
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def ledger(clock):
    return Ledger(clock_ns=clock)


def controller(ledger, **changes):
    parameters = dict(eta=0.25, decay=0.125, penalty_cap=0.9, min_window_events=3)
    parameters.update(changes)
    return PriceController(ledger, **parameters)


def region(**changes):
    return replace(CardRegion("cost", "max", None, 10.0, 2.0), **changes)


def evidence(ledger):
    termination = Termination(ledger=ledger, bus=Bus(ledger), clock_ns=lambda: 1000)
    termination.kill("test audit")
    items = []
    while True:
        item = ledger.decrypt_item(len(items))
        if item["kind"] == "event" and item["event"]["kind"] == "Terminated":
            break
        items.append(item)
    assert ledger.verify()
    return items


@pytest.mark.parametrize(
    "changes",
    [
        {"card_id": ""}, {"card_id": " \n"}, {"card_id": 1},
        {"kind": "other"}, {"kind": None}, {"kind": True},
        {"hi": None}, {"kind": "min"}, {"kind": "band"},
        {"kind": "band", "lo": 10}, {"kind": "band", "lo": 11},
        {"kind": "band", "lo": 0, "hi": None},
        {"scale": 0}, {"scale": -1}, {"scale": None},
    ],
)
def test_region_rejects_invalid_shape(changes):
    with pytest.raises(ValueError):
        region(**changes)


@pytest.mark.parametrize("field", ["lo", "hi", "scale"])
@pytest.mark.parametrize("value", [True, False, "1", float("nan"), float("inf"), -float("inf")])
def test_region_requires_finite_numbers_without_booleans(field, value):
    with pytest.raises(ValueError):
        region(**{field: value})


@pytest.mark.parametrize(
    ("card", "values", "expected"),
    [
        (CardRegion("max", "max", None, 10, 2), [-4, 10, 12, 16], [0, 0, 1, 3]),
        (CardRegion("min", "min", 10, None, 4), [2, 6, 10, 20], [2, 1, 0, 0]),
        (CardRegion("band", "band", -2, 4, 2), [-6, -2, 0, 4, 10], [2, 0, 0, 0, 3]),
        (CardRegion("max", "max", 8, 10, 2), [0, 12], [0, 1]),
        (CardRegion("min", "min", 10, 12, 2), [8, 20], [1, 0]),
    ],
)
def test_violation_uses_inclusive_region_and_scale(ledger, card, values, expected):
    prices = controller(ledger)
    prices.register(card)
    assert [prices.violation(card.card_id, value) for value in values] == expected


def test_registration_is_unique_and_unknown_price_is_zero(ledger):
    prices = controller(ledger)
    assert prices.price("missing") == 0.0
    assert prices.price(None) == 0.0
    assert prices.price([]) == 0.0
    prices.register(region())
    prices.observe("cost", 12, 0)
    before = prices.snapshot()
    for card in (region(), region(hi=30)):
        with pytest.raises(ValueError, match="already registered"):
            prices.register(card)
    with pytest.raises(ValueError, match="CardRegion"):
        prices.register("cost")
    with pytest.raises(KeyError):
        prices.violation("missing", 1)
    with pytest.raises(KeyError):
        prices.observe("missing", 1, 0)
    assert prices.snapshot() == before


def test_prices_rise_on_violation_and_decay_inside_region(ledger):
    prices = controller(ledger)
    prices.register(region())
    observed = []
    for event, value in [(0, 12), (3, 12), (6, 12), (9, 10), (12, -100)]:
        prices.observe("cost", value, event)
        observed.append(prices.price("cost"))
    assert observed == [0.25, 0.5, 0.75, 0.625, 0.5]
    assert prices.snapshot()["cards"]["cost"]["updates"] == 5


def test_clipping_counts_only_attempts_beyond_bounds_and_tracks_actual_steps(ledger):
    """Wave 16, ruling R-E: the one bound is penalty_cap / v, the price at which the
    card's own penalty takes the whole cap. An exact hit is not saturation; an attempt
    beyond the bound is."""
    prices = controller(ledger, eta=0.45, decay=1, min_window_events=1)
    prices.register(region())
    for event, value in enumerate([12, 12, 12, 10]):  # violation 1: bound 0.9
        prices.observe("cost", value, event)
    entries = evidence(ledger)
    assert [item["lambda_after"] for item in entries] == pytest.approx([0.45, 0.9, 0.9, 0])
    assert [item["saturated"] for item in entries] == [False] * 4
    # At the bound the integrator is frozen (anti-windup): the third window holds.
    assert [item["integrator_frozen"] for item in entries] == [False, False, True, False]
    assert [item["at_cap"] for item in entries] == [False, True, True, False]
    assert prices.saturation("cost") == {"bound": None, "windows_at_bound": 2,
                                         "saturated_windows": 0, "violation_windows": 0}
    assert prices.saturation("unregistered") == {"bound": None, "windows_at_bound": 0,
                                                 "saturated_windows": 0,
                                                 "violation_windows": 0}


def test_an_attempt_beyond_the_bound_is_saturation_and_the_bound_moves_with_v(ledger):
    prices = controller(ledger, eta=1, decay=1, min_window_events=1, kp=1)
    prices.register(region())
    for event, value in enumerate([14, 100, 100]):
        prices.observe("cost", value, event)
    card = prices.snapshot()["cards"]["cost"]
    assert card == pytest.approx({
        "lambda": 0.02, "updates": 3, "saturations": 3, "max_step": 0.45,
        "last_window_end_event": 2, "integral": 0.0, "episode_bound": 0.45,
        "failing_windows": 0,
        "bound": 0.02, "windows_at_bound": 3, "saturated_windows": 3,
        "violation_windows": 3,
    })
    entries = evidence(ledger)
    assert [item["lambda_after"] for item in entries] == pytest.approx([0.45, 0.02, 0.02])
    assert entries[1]["violation"] == 45.0  # Unclipped observation retained.
    # lambda * v never exceeds the cap: the penalty is bounded, not the price.
    assert all(item["lambda_after"] * item["violation"] <= 0.9 + 1e-12 for item in entries)


def test_window_skips_duplicates_old_and_early_observations_without_moving_boundary(ledger):
    prices = controller(ledger)
    prices.register(region())
    prices.register(region(card_id="other"))
    prices.observe("cost", 12, 10)
    before = prices.snapshot()
    for event in (10, 9, 0, 12):
        prices.observe("cost", 100, event)
        assert prices.snapshot() == before
    prices.observe("other", 12, 10)  # Cadence is independent per card.
    prices.observe("cost", 12, 13)  # Inclusive eligibility, based on last accepted window.
    assert prices.price("cost") == 0.5
    assert prices.price("other") == 0.25
    entries = evidence(ledger)
    assert [item["kind"] for item in entries] == [
        "price.update", *["price.skipped"] * 4, "price.update", "price.update",
    ]
    assert [item["window_end_event"] for item in entries[1:5]] == [10, 9, 0, 12]
    assert all(item["last_window_end_event"] == 10 for item in entries[1:5])
    assert all(item["card_id"] == "cost" and item["value"] == 100 for item in entries[1:5])


def test_penalty_sums_known_cards_without_clipping_or_mutating(ledger):
    prices = controller(ledger, eta=1)
    for card in (region(), CardRegion("rate", "min", 4, None, 1)):
        prices.register(card)
    prices.observe("cost", 14, 0)
    prices.observe("rate", 3, 0)
    before = prices.snapshot()
    # Each card's pressure saturates at the cap (0.45 * 3 and 0.9 * 2 both exceed 0.9):
    # finite for any adopted price; the sum over cards is not clipped.
    assert prices.penalty({"cost": 16, "rate": 2, "unknown": float("nan")}) == pytest.approx(
        0.9 + 0.9)
    assert prices.penalty({"cost": 11}) == pytest.approx(0.45 * 0.5)  # below: the product
    assert prices.penalty({"cost": 10, "rate": 5}) == 0.0
    assert prices.penalty({}) == prices.penalty({"unknown": 1000}) == 0.0
    assert prices.snapshot() == before
    assert len(evidence(ledger)) == 2


@pytest.mark.parametrize("value", [True, None, "12", float("nan"), float("inf")])
def test_invalid_observations_do_not_change_state_or_append(ledger, value):
    prices = controller(ledger)
    prices.register(region())
    before = prices.snapshot()
    with pytest.raises(ValueError):
        prices.observe("cost", value, 0)
    with pytest.raises(ValueError):
        prices.violation("cost", value)
    assert prices.snapshot() == before
    assert evidence(ledger) == []


def test_update_is_appended_before_price_or_counters_change(ledger, monkeypatch):
    prices = controller(ledger)
    prices.register(region())
    before = prices.snapshot()
    append = ledger.append

    def inspect(entry):
        assert prices.snapshot() == before
        result = append(entry)
        assert prices.snapshot() == before
        return result

    with monkeypatch.context() as patch:
        patch.setattr(ledger, "append", inspect)
        prices.observe("cost", 12, 7)
    assert prices.price("cost") == 0.25
    with pytest.raises(PermissionError):
        ledger.decrypt_item(0)
    item, = evidence(ledger)
    assert {key: value for key, value in item.items() if key not in {
        "seq", "prev_hash", "hash",
    }} == {
        "kind": "price.update", "card_id": "cost", "value": 12.0,
        "violation": 1.0, "lambda_before": 0.0, "lambda_after": 0.25,
        "previous_violation": 0.0, "p": 0.0, "i": 0.25, "d": 0.0,
        "saturated": False, "window_end_event": 7, "ts": 100,
        "bound": 0.9, "at_cap": False, "integrator_frozen": False,
    }


@pytest.mark.parametrize("event", [0, 3])
def test_failed_ledger_append_leaves_update_or_skip_state_unchanged(ledger, clock, event):
    prices = controller(ledger)
    prices.register(region())
    prices.observe("cost", 12, 0)
    before = prices.snapshot()
    clock.fail = True
    with pytest.raises(RuntimeError, match="clock unavailable"):
        prices.observe("cost", 100, event)
    assert prices.snapshot() == before
    clock.fail = False
    prices.observe("cost", 100, 3)
    assert prices.price("cost") == pytest.approx(0.9 / 45)  # the bound at violation 45
    assert len(evidence(ledger)) == 2


def test_an_overflowing_violation_is_held_at_the_largest_float_and_ledgered(ledger):
    """A distance across the whole float range overflows. It is held at the largest
    float (Codex on #152: a refusal here aborted the window close), never infinity,
    so the row is ledgerable and the card is priced at its bound for it."""
    import sys

    prices = controller(ledger)
    prices.register(region(hi=-1e308))
    prices.observe("cost", 1e308, 0)
    (row,) = evidence(ledger)
    assert row["violation"] == sys.float_info.max / 2  # the distance, held, over scale 2
    assert row["bound"] == 0.9 / (sys.float_info.max / 2)


def test_skipped_observations_and_failed_updates_do_not_replace_violation_history(ledger,
                                                                                    clock):
    prices = controller(ledger, eta=0.05)
    prices.register(region())
    prices.observe("cost", 18, 0)
    prices.observe("cost", 100, 1)  # skipped
    clock.fail = True
    with pytest.raises(RuntimeError):
        prices.observe("cost", 16, 3)
    clock.fail = False
    prices.observe("cost", 14, 3)
    assert prices.price("cost") == pytest.approx(0.3)  # the integral: 0.05 * 4, + 0.05 * 2
    assert evidence(ledger)[-1]["previous_violation"] == 4


@pytest.mark.parametrize("scale,value", [(1.0, 5e-324), (5e-324, 1.0), (1e-310, 1e10)])
def test_a_subnormal_violation_or_scale_holds_every_bound_at_the_largest_float(
        ledger, scale, value):
    """Codex on #152: a finite subnormal violation (5e-324) made ``cap / v`` infinity,
    which reached ``episode_bound`` and the ``price.update`` row's bound, and the ledger's
    canonical JSON refused it, aborting the window close. Every bound, violation and
    term is held at the largest float instead: ledgerable, and still effectively
    unbounded. A subnormal scale overflows the violation itself the same way."""
    import sys

    from factorylab.kernel.ledger import canonical

    prices = controller(ledger, eta=1, decay=1, min_window_events=1, kp=2.0, kd=2.0)
    prices.register(CardRegion("cost", "max", None, 0.0, scale))
    for event, observed in enumerate([value, value * 2, value]):
        prices.observe("cost", observed, event, anticipated=1.0)
    card = prices.snapshot()["cards"]["cost"]
    canonical(prices.snapshot())
    rows = evidence(ledger)
    for row in rows:
        canonical(row)
        for key in ("bound", "violation", "p", "i", "d", "f", "lambda_after"):
            assert row[key] is None or row[key] <= sys.float_info.max, (key, row)
    bounds = [row["bound"] for row in rows]
    if value / scale < 1e-300:  # a subnormal violation: the bound overflows
        assert bounds == [sys.float_info.max] * 3
        assert card["episode_bound"] == sys.float_info.max
        assert prices.saturation("cost")["bound"] == sys.float_info.max
    else:  # a subnormal scale: the violation overflows and the bound is tiny
        assert rows[0]["violation"] == sys.float_info.max
        assert 0 < bounds[0] < 1e-300


def test_what_is_computed_from_an_overflowing_violation_stays_finite():
    """The sweep behind Codex's finding on #152: every division by, and sum of, a
    measured violation, scale or step stays finite. A blame share is a share of the
    exact total where the float sum of two largest-float violations overflows; a
    margin whose violations spread past a float identifies no slope; a branch's
    expected violation and a holdout's added violation are held at the largest float."""
    import sys

    from factorylab.charter.charter import holdout_violation
    from factorylab.charter.controller import part, ratio
    from factorylab.charter.market import branch_violation, margin

    top = sys.float_info.max
    assert ratio(1.0, 5e-324) == top and ratio(-1.0, 5e-324) == -top
    assert ratio(1.0, 4.0) == 0.25
    assert part(top, [top, top]) == 0.5 and part(1.0, [1.0, 3.0]) == 0.25
    assert part(0.0, [0.0, 0.0]) == 0.0
    points = [{"v": v, "consequence": 0.5, "micro_usd": 1.0} for v in (0.0, top, top / 2)]
    assert margin(points)["slope"] is None
    assert branch_violation(top, 1.0, 1, top) == top
    assert holdout_violation([False, False], top) == top
