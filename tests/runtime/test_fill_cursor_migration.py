"""Fill checkpoint validation precedes destination mutation (§II.b, §III.b)."""

from decimal import Decimal
from pathlib import Path

import pytest

from factorylab.runtime.resume import ResumeError, decode, encode, restore_runtime, runtime_state
from factorylab.world.exchange import HyperliquidExchange
from factorylab.world.tape import Tape, TapeVenue
from tests.conftest import make_runtime


def _runtime(venue):
    rt = make_runtime(live=venue == "live")
    if venue == "live":
        # A real live adapter behind its recovery proxy, without credentials or I/O.
        exchange = object.__new__(HyperliquidExchange)
        exchange.name = "hyperliquid-testnet"
        rt.exchange.target = exchange
    elif venue == "tape":
        tape = Tape.load(Path(__file__).parents[1] / "fixtures/tape/live4-head.events.json")
        rt.exchange.target = TapeVenue(tape, coins=("BTC",), start_cash_usd=Decimal(100))
    return rt


@pytest.mark.parametrize("three_field", [True, False])
def test_live_legacy_cursor_refused_before_any_runtime_mutation(three_field):
    source = _runtime("live")
    source.clock.now_ns += 123
    state = runtime_state(source)
    components = decode(state["components"])
    saved = components["consequence_fills"]
    if three_field:
        components["consequence_fills"] = {key: saved[key] for key in
                                          ("since_ns", "seen", "through_ns")}
    else:
        del saved["orders"]
        saved["measured"] = False  # Saved cursor flags cannot bypass live identity.
    state["components"] = encode(components)
    target = _runtime("live")
    before = runtime_state(target)
    with pytest.raises(ResumeError) as caught:
        restore_runtime(target, state)
    assert caught.value.code == "legacy_live_cursor"
    assert str(caught.value) == (
        "this world's live fill cursor predates per-order accounting; start a new world")
    assert runtime_state(target) == before


@pytest.mark.parametrize("venue", ["fake", "tape"])
def test_genuine_three_field_legacy_cursor_keeps_simulated_constructor_defaults(venue):
    source = _runtime(venue)
    state = runtime_state(source)
    components = decode(state["components"])
    saved = components["consequence_fills"]
    components["consequence_fills"] = {key: saved[key] for key in
                                      ("since_ns", "seen", "through_ns")}
    state["components"] = encode(components)
    target = _runtime(venue)
    restore_runtime(target, state)
    cursor = target.consequence_fills
    assert cursor.since_ns == saved["since_ns"]
    assert cursor.seen == saved["seen"]
    assert cursor.through_ns == saved["through_ns"]
    assert cursor.launch_ns == saved["since_ns"]
    assert cursor.read_ns is None
    assert cursor.measured is False
    assert cursor.propagation_bound_ns is None
    assert cursor.observation_complete is True
    assert cursor.reconciliation_ns is None
    assert cursor.expected_positions is None
    assert cursor.expected_cash is None
    assert cursor.expected_fees is None
    assert cursor.recovery_span_ns == 0
    assert cursor.incomplete_since_ns is None
    assert cursor.last_residual is None


@pytest.mark.parametrize("field,value", [("since_ns", "bad"), ("seen", []),
                                         ("measured", 1), ("propagation_bound_ns", -1),
                                         ("expected_positions", {"perp:BTC": "NaN"}),
                                         ("expected_cash", {"perp": 1.5}),
                                         ("expected_fees", "1"), ("recovery_span_ns", -1)])
def test_invalid_fill_component_refuses_before_any_runtime_mutation(field, value):
    source = make_runtime(live=True)
    source.clock.now_ns += 123
    state = runtime_state(source)
    components = decode(state["components"])
    components["consequence_fills"][field] = value
    state["components"] = encode(components)
    target = make_runtime(live=True)
    before = runtime_state(target)
    with pytest.raises(ResumeError, match="fill cursor"):
        restore_runtime(target, state)
    assert runtime_state(target) == before
