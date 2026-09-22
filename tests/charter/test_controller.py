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
    parameters = dict(eta=0.5, decay=0.25, lambda_max=2.0, min_window_events=3)
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
    assert observed == [0.5, 1.0, 1.5, 1.25, 1.0]
    assert prices.snapshot()["cards"]["cost"]["updates"] == 5


def test_clipping_counts_only_attempts_beyond_bounds_and_tracks_actual_steps(ledger):
    prices = controller(ledger, eta=1, decay=1, lambda_max=2, min_window_events=1, kp=1)
    prices.register(region())
    # Exact hits are not saturation; attempts to go beyond a bound are.
    for event, value in enumerate([14, 12, 10, 10, 10, 100, 100]):
        prices.observe("cost", value, event)
    assert prices.snapshot()["cards"]["cost"] == {
        "lambda": 2.0, "updates": 7, "saturations": 2, "max_step": 2.0,
        "last_window_end_event": 6,
        # The integral held while P alone saturated a growing violation; no stable
        # failure ratcheted it.
        "integral": 2.0, "failing_windows": 0,
        # Charter audit M7: four windows closed at lambda_max; the violation that
        # ended at event 2 reset the run, and the current one has lasted two windows.
        "windows_at_lambda_max": 4, "violation_windows": 2,
    }
    entries = evidence(ledger)
    assert [item["lambda_after"] for item in entries] == [2, 2, 0, 0, 0, 2, 2]
    assert [item["saturated"] for item in entries] == [False] * 5 + [True, True]
    assert prices.saturation("cost") == {"windows_at_lambda_max": 4, "violation_windows": 2}
    assert prices.saturation("unregistered") == {"windows_at_lambda_max": 0,
                                                 "violation_windows": 0}
    assert entries[5]["violation"] == 45.0  # Unclipped observation retained.


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
    assert prices.price("cost") == 1.0
    assert prices.price("other") == 0.5
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
    assert prices.penalty({"cost": 16, "rate": 2, "unknown": float("nan")}) == 8.0
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
    assert prices.price("cost") == 0.5
    with pytest.raises(PermissionError):
        ledger.decrypt_item(0)
    item, = evidence(ledger)
    assert {key: value for key, value in item.items() if key not in {
        "seq", "prev_hash", "hash",
    }} == {
        "kind": "price.update", "card_id": "cost", "value": 12.0,
        "violation": 1.0, "lambda_before": 0.0, "lambda_after": 0.5,
        "previous_violation": 0.0, "p": 0.0, "i": 0.5, "d": 0.0,
        "saturated": False, "window_end_event": 7, "ts": 100,
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
    assert prices.price("cost") == 2.0
    assert len(evidence(ledger)) == 2


def test_nonfinite_violation_is_rejected_before_ledger_or_state_changes(ledger):
    prices = controller(ledger)
    prices.register(region(hi=-1e308))
    before = prices.snapshot()
    with pytest.raises(ValueError, match="violation"):
        prices.observe("cost", 1e308, 0)
    assert prices.snapshot() == before
    assert evidence(ledger) == []


def test_skipped_observations_and_failed_updates_do_not_replace_violation_history(ledger,
                                                                                    clock):
    prices = controller(ledger, lambda_max=100)
    prices.register(region())
    prices.observe("cost", 18, 0)
    prices.observe("cost", 100, 1)  # skipped
    clock.fail = True
    with pytest.raises(RuntimeError):
        prices.observe("cost", 16, 3)
    clock.fail = False
    prices.observe("cost", 14, 3)
    assert prices.price("cost") == 3.0  # the integral: 0.5 * 4, then + 0.5 * 2
    assert evidence(ledger)[-1]["previous_violation"] == 4
