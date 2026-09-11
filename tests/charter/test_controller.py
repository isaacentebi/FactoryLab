from dataclasses import FrozenInstanceError, dataclass, replace

import pytest

from factorylab.charter.controller import CardRegion, PriceController
from factorylab.kernel.events import Bus
from factorylab.kernel.ledger import Ledger
from factorylab.kernel.termination import Termination
from factorylab.kernel.timing import TimingRegistry


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


def test_region_is_frozen_and_accepts_integer_bounds():
    card = CardRegion("band", "band", -2, 3, 2)
    assert (card.lo, card.hi, card.scale) == (-2.0, 3.0, 2.0)
    with pytest.raises(FrozenInstanceError):
        card.scale = 0


@pytest.mark.parametrize("parameter", ["eta", "decay", "lambda_max"])
@pytest.mark.parametrize(
    "value", [0, -1, True, False, None, "1", float("nan"), float("inf"), -float("inf")]
)
def test_positive_finite_controller_parameters(ledger, parameter, value):
    with pytest.raises(ValueError):
        controller(ledger, **{parameter: value})


@pytest.mark.parametrize("value", [0, -1, True, False, 1.0, 1.5, "3", None])
def test_window_requires_positive_integer(ledger, value):
    with pytest.raises(ValueError):
        controller(ledger, min_window_events=value)


def test_integer_parameters_are_valid(ledger):
    prices = controller(ledger, eta=1, decay=1, lambda_max=2, min_window_events=1)
    prices.register(region())
    prices.observe("cost", 12, 0)
    assert prices.price("cost") == 1.0


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
    prices = controller(ledger, eta=1, decay=1, lambda_max=2, min_window_events=1)
    prices.register(region())
    # Exact hits are not saturation; attempts to go beyond a bound are.
    for event, value in enumerate([14, 12, 10, 10, 10, 100, 100]):
        prices.observe("cost", value, event)
    assert prices.snapshot()["cards"]["cost"] == {
        "lambda": 2.0, "updates": 7, "saturations": 4, "max_step": 2.0,
        "last_window_end_event": 6,
    }
    entries = evidence(ledger)
    assert [item["lambda_after"] for item in entries] == [2, 2, 1, 0, 0, 2, 2]
    assert [item["saturated"] for item in entries] == [False, True, False, False, True, True, True]
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


def test_snapshot_shape_defaults_and_detachment(ledger):
    prices = controller(ledger)
    assert prices.snapshot() == {
        "parameters": {"eta": 0.5, "decay": 0.25, "lambda_max": 2.0, "min_window_events": 3},
        "cards": {},
    }
    prices.register(region())
    assert prices.snapshot()["cards"]["cost"] == {
        "lambda": 0.0, "updates": 0, "saturations": 0, "max_step": 0.0,
        "last_window_end_event": None,
    }
    snapshot = prices.snapshot()
    snapshot["parameters"]["eta"] = 100
    snapshot["cards"]["cost"]["lambda"] = 100
    snapshot["cards"].clear()
    prices.observe("cost", 12, 0)
    assert prices.price("cost") == 0.5


def test_timing_records_only_accepted_strictly_increasing_event_indices(ledger, monkeypatch):
    timing = TimingRegistry()
    timing.register_loop("judgment", [])
    timing.register_loop("price:cost", ["judgment"])
    prices = controller(ledger, timing=timing)
    prices.register(region())
    prices.register(region(card_id="other"))
    assert timing.governed("price:cost") == ("judgment",)
    assert timing.governed("price:other") == ()
    closures = []
    record = timing.record_closure

    def capture(loop_id, ts):
        record(loop_id, ts)
        closures.append((loop_id, ts))

    monkeypatch.setattr(timing, "record_closure", capture)
    for event in (0, 0, 2, 3, 1, 6, 20):
        prices.observe("cost", 12, event)
    prices.observe("other", 12, 0)
    assert closures == [("price:cost", ts) for ts in (0, 3, 6, 20)] + [("price:other", 0)]
    assert timing.closure_count("price:cost") == 4
    assert timing.estimated_period("price:cost") == 6
    with pytest.raises(ValueError, match="already registered"):
        prices.register(region())
    timing.register_loop("price:started", [])
    timing.record_closure("price:started", 100)
    with pytest.raises(ValueError, match="prior closures"):
        prices.register(region(card_id="started"))
    assert "started" not in prices.snapshot()["cards"]


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


@pytest.mark.parametrize("event", [-1, True, 1.0, "1", None])
def test_invalid_event_indices_do_not_change_state_or_append(ledger, event):
    prices = controller(ledger)
    prices.register(region())
    before = prices.snapshot()
    with pytest.raises(ValueError):
        prices.observe("cost", 12, event)
    assert prices.snapshot() == before
    assert evidence(ledger) == []


def test_update_is_appended_before_price_counters_or_timing_change(ledger, monkeypatch):
    timing = TimingRegistry()
    prices = controller(ledger, timing=timing)
    prices.register(region())
    before = prices.snapshot()
    append = ledger.append

    def inspect(entry):
        assert prices.snapshot() == before
        assert timing.closure_count("price:cost") == 0
        result = append(entry)
        assert prices.snapshot() == before
        assert timing.closure_count("price:cost") == 0
        return result

    with monkeypatch.context() as patch:
        patch.setattr(ledger, "append", inspect)
        prices.observe("cost", 12, 7)
    assert prices.price("cost") == 0.5
    assert timing.closure_count("price:cost") == 1
    with pytest.raises(PermissionError):
        ledger.decrypt_item(0)
    item, = evidence(ledger)
    assert {key: value for key, value in item.items() if key not in {
        "seq", "prev_hash", "hash",
    }} == {
        "kind": "price.update", "card_id": "cost", "value": 12.0,
        "violation": 1.0, "lambda_before": 0.0, "lambda_after": 0.5,
        "saturated": False, "window_end_event": 7, "ts": 100,
    }


@pytest.mark.parametrize("event", [0, 3])
def test_failed_ledger_append_leaves_update_or_skip_state_unchanged(ledger, clock, event):
    timing = TimingRegistry()
    prices = controller(ledger, timing=timing)
    prices.register(region())
    prices.observe("cost", 12, 0)
    before = prices.snapshot()
    clock.fail = True
    with pytest.raises(RuntimeError, match="clock unavailable"):
        prices.observe("cost", 100, event)
    assert prices.snapshot() == before
    assert timing.closure_count("price:cost") == 1
    clock.fail = False
    prices.observe("cost", 100, 3)
    assert prices.price("cost") == 2.0
    assert timing.closure_count("price:cost") == 2
    assert len(evidence(ledger)) == 2


def test_nonfinite_violation_is_rejected_before_ledger_or_state_changes(ledger):
    prices = controller(ledger)
    prices.register(region(hi=-1e308))
    before = prices.snapshot()
    with pytest.raises(ValueError, match="violation"):
        prices.observe("cost", 1e308, 0)
    assert prices.snapshot() == before
    assert evidence(ledger) == []


def test_update_region_keeps_price_counts_and_timing_but_moves_the_bounds(ledger):
    timing = TimingRegistry()
    prices = controller(ledger, timing=timing)
    prices.register(region())
    prices.observe("cost", 12, 0)
    before = prices.snapshot()
    assert before["cards"]["cost"]["lambda"] == 0.5
    prices.update_region(region(hi=20.0, scale=4.0))
    assert prices.snapshot() == before  # nothing but the bounds changed
    assert prices.violation("cost", 12) == 0.0 and prices.violation("cost", 28) == 2.0
    assert timing.closure_count("price:cost") == 1
    with pytest.raises(KeyError):
        prices.update_region(region(card_id="unknown"))
    with pytest.raises(ValueError):
        prices.update_region("cost")
    assert len(evidence(ledger)) == 1  # a region change is not a price revision
