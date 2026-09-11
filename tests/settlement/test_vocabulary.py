from dataclasses import FrozenInstanceError

import pytest

from factorylab.kernel.events import EventKind
from factorylab.settlement import SEED_VOCABULARY, Observer, Predicate, WindowFacts


def test_seed_vocabulary_has_exactly_the_five_fixed_schemas():
    assert isinstance(SEED_VOCABULARY, tuple)
    assert tuple(predicate.id for predicate in SEED_VOCABULARY) == (
        "wallet_up",
        "fill_within",
        "rejected_within",
        "liquidated_within",
        "drawdown_exceeds",
    )
    for predicate in SEED_VOCABULARY:
        assert predicate.description
        assert predicate.horizon_param == "horizon_events"
        schema = predicate.param_schema
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert schema["properties"]["horizon_events"] == {"type": "integer", "minimum": 1}
        expected = {"horizon_events"}
        if predicate.id == "drawdown_exceeds":
            expected.add("fraction")
            assert schema["properties"]["fraction"] == {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
            }
        assert set(schema["required"]) == set(schema["properties"]) == expected


@pytest.mark.parametrize(
    ("predicate", "params", "positive", "negative"),
    [
        (
            "wallet_up",
            {"horizon_events": 2},
            WindowFacts(100, 101, 90, ()),
            WindowFacts(100, 100, 90, ()),
        ),
        (
            "fill_within",
            {"horizon_events": 2},
            WindowFacts(100, 100, 100, ({"kind": EventKind.FILL, "payload": {}},)),
            WindowFacts(100, 100, 100, ({"kind": "OrderRejected", "payload": {}},)),
        ),
        (
            "rejected_within",
            {"horizon_events": 2},
            WindowFacts(100, 100, 100, ({"kind": "OrderRejected", "payload": {}},)),
            WindowFacts(100, 100, 100, ({"kind": "Fill", "payload": {}},)),
        ),
        (
            "liquidated_within",
            {"horizon_events": 2},
            WindowFacts(100, 100, 100, ({"kind": "Fill", "payload": {"liquidation": True}},)),
            WindowFacts(
                100,
                100,
                100,
                (
                    {"kind": "Fill", "payload": {"liquidation": False}},
                    {"kind": "Fill", "payload": {"liquidation": 1}},
                    {"kind": "Fill", "payload": {"liquidation": "true"}},
                    {"kind": "OrderRejected", "payload": {"liquidation": True}},
                ),
            ),
        ),
        (
            "drawdown_exceeds",
            {"horizon_events": 2, "fraction": 0.2},
            WindowFacts(100, 110, 79, ()),
            WindowFacts(100, 110, 80, ()),
        ),
    ],
)
def test_every_predicate_has_both_binary_outcomes(predicate, params, positive, negative):
    observer = Observer()
    for facts, expected in ((positive, 1), (negative, 0)):
        actual = observer.observe(predicate, params, facts)
        assert type(actual) is int and actual == expected


@pytest.mark.parametrize("predicate", ["fill_within", "rejected_within", "liquidated_within"])
def test_non_events_are_zero_and_verdict_contents_are_not_facts(predicate):
    observer = Observer()
    for events in ((), ({"kind": "Verdict", "payload": {"kind": "Fill", "liquidation": True}},)):
        assert observer.observe(predicate, {"horizon_events": 1}, WindowFacts(1, 1, 1, events)) == 0


@pytest.mark.parametrize(
    ("fraction", "minimum", "expected"),
    [(0, 100, 0), (0, 99, 1), (1, 0, 0), (1, -1, 1)],
)
def test_drawdown_endpoints_are_strict(fraction, minimum, expected):
    assert (
        Observer().observe(
            "drawdown_exceeds",
            {"fraction": fraction, "horizon_events": 1},
            WindowFacts(100, 100, minimum, ()),
        )
        == expected
    )


def test_drawdown_preserves_micro_usd_precision_above_float_range():
    initial = 10**100
    threshold = initial * 9 // 10
    observer = Observer()
    params = {"fraction": 0.1, "horizon_events": 10}
    assert (
        observer.observe("drawdown_exceeds", params, WindowFacts(initial, initial, threshold, ()))
        == 0
    )
    assert (
        observer.observe(
            "drawdown_exceeds", params, WindowFacts(initial, initial, threshold - 1, ())
        )
        == 1
    )


@pytest.mark.parametrize("horizon", [0, -1, 1.5, True, "1", None])
def test_invalid_horizons_raise_value_error(horizon):
    for predicate in SEED_VOCABULARY:
        params = {"horizon_events": horizon}
        if predicate.id == "drawdown_exceeds":
            params["fraction"] = 0.1
        with pytest.raises(ValueError):
            Observer().observe(predicate.id, params, WindowFacts(1, 1, 1, ()))


@pytest.mark.parametrize("fraction", [-0.1, 1.1, float("nan"), float("inf"), True, "0.1", None])
def test_invalid_fractions_raise_value_error(fraction):
    with pytest.raises(ValueError):
        Observer().observe(
            "drawdown_exceeds",
            {"horizon_events": 1, "fraction": fraction},
            WindowFacts(1, 1, 1, ()),
        )


@pytest.mark.parametrize(
    ("predicate", "params"),
    [
        ("unknown", {"horizon_events": 1}),
        (None, {}),
        ([], {}),
        ("wallet_up", {}),
        ("wallet_up", None),
        ("wallet_up", []),
        ("wallet_up", {"horizon_events": 1, "fraction": 0.1}),
        ("drawdown_exceeds", {"horizon_events": 1}),
        ("drawdown_exceeds", {"horizon_events": 1, "fraction": 0.1, "extra": 1}),
    ],
)
def test_unknown_predicates_and_malformed_params_raise_value_error(predicate, params):
    with pytest.raises(ValueError):
        Observer().observe(predicate, params, WindowFacts(1, 1, 1, ()))


def test_frozen_records_detach_nested_caller_data():
    schema = {"properties": {"horizon_events": {"minimum": 1}}}
    predicate = Predicate("test", "Test predicate.", schema, "horizon_events")
    schema["properties"]["horizon_events"]["minimum"] = 0
    assert predicate.param_schema["properties"]["horizon_events"]["minimum"] == 1
    with pytest.raises(TypeError):
        SEED_VOCABULARY[0].param_schema["properties"]["horizon_events"]["minimum"] = 0
    with pytest.raises(FrozenInstanceError):
        predicate.id = "changed"
    events = [{"kind": "Fill", "payload": {"liquidation": True}}]
    facts = WindowFacts(100, 90, 80, events)
    events[0]["payload"]["liquidation"] = False
    events.clear()
    assert facts.events[0]["payload"]["liquidation"] is True
    with pytest.raises(TypeError):
        facts.events[0]["payload"]["liquidation"] = False
    with pytest.raises(FrozenInstanceError):
        facts.balance_at_forecast = 0


@pytest.mark.parametrize(
    "field", ["balance_at_forecast", "balance_at_settlement", "min_balance_in_window"]
)
@pytest.mark.parametrize("value", [1.0, True])
def test_window_money_cannot_be_float_or_bool(field, value):
    arguments = dict(balance_at_forecast=1, balance_at_settlement=1, min_balance_in_window=1)
    arguments[field] = value
    with pytest.raises(TypeError):
        WindowFacts(**arguments, events=())


@pytest.mark.parametrize(
    "event",
    [{}, {"kind": "Fill"}, {"kind": "", "payload": {}}, {"kind": "Fill", "payload": None}, None],
)
def test_window_events_require_kind_and_payload(event):
    with pytest.raises(ValueError):
        WindowFacts(1, 1, 1, (event,))
