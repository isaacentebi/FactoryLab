"""Round three T22: registered observations can measure delivered world history."""

from factorylab.kernel.events import Event, EventKind
from factorylab.runtime.observations import window_facts
from tests.conftest import make_runtime


def test_window_facts_include_market_funding_wallet_and_tick_series():
    rt = make_runtime()
    rt._manage_reserve_window()
    for event in (
        Event("mid-1", EventKind.MARKET_MID, 10, {"coin": "BTC", "mid": "100.25"}, "test"),
        Event("mid-2", EventKind.MARKET_MID, 20, {"coin": "BTC", "mid": "101.50"}, "test"),
        Event("funding", EventKind.FUNDING, 25, {"coin": "BTC", "rate": "0.0001"}, "test"),
        Event("tick", EventKind.TICK, 30, {}, "test"),
    ):
        rt._observe_delivered_event(event)
    facts = window_facts(rt.window)
    assert facts["mids"]["BTC"] == [[10, 100_250_000], [20, 101_500_000]]
    assert facts["funding"]["BTC"] == [[25, 0.0001]]
    assert facts["wallet_balance_micro"][-1] == [30, rt.wallet.balance]
    assert facts["tick_timestamps_ns"] == [30]


def test_world_series_retain_only_the_latest_1024_samples():
    rt = make_runtime()
    rt._manage_reserve_window()
    for index in range(1_030):
        rt._observe_delivered_event(Event(f"tick-{index}", EventKind.TICK, index, {}, "test"))
    facts = window_facts(rt.window)
    assert facts["tick_timestamps_ns"] == list(range(6, 1_030))
    assert len(facts["wallet_balance_micro"]) == 1_024
    assert facts["wallet_balance_micro"][0] == [6, rt.wallet.balance]


def test_registered_multi_window_measurement_receives_bounded_world_series():
    from types import SimpleNamespace

    from factorylab.charter.charter import MetricCard
    from factorylab.charter.measurement import CardSamples, measure_cards
    from factorylab.runtime.pricing import MeasureWindow

    rt = make_runtime()
    rt._manage_reserve_window()
    rt.registered_observations["world-measure"] = {
        "description": "World measurement",
        "units": "fraction",
        "unit_range": [0, 1],
        "code": "def observe(facts): return 0.5",
        "version": 1,
    }
    seen = []
    rt.observation_runner = SimpleNamespace(
        run=lambda code, facts: (seen.append(facts), (0.5, None))[1]
    )
    card = MetricCard(
        "world",
        rt.charter.norms[2],
        "World",
        "fraction",
        {"kind": "windows", "n": 2, "per": None},
        "at least 0.1",
        "world-measure",
        "all",
    )
    samples = CardSamples()
    for index in range(2):
        rt.window = MeasureWindow(index, rt.wallet.balance)
        for tick in range(index * 600, (index + 1) * 600):
            rt._observe_delivered_event(
                Event(
                    f"mid-{tick}", EventKind.MARKET_MID, tick, {"coin": "BTC", "mid": "100"}, "test"
                )
            )
            rt._observe_delivered_event(Event(f"tick-{tick}", EventKind.TICK, tick, {}, "test"))
        result = measure_cards((card,), samples, rt.window, observations=rt.observations)
    assert result == {"world": 0.5}
    assert seen[-1]["mids"]["BTC"] == [[ts, 100_000_000] for ts in range(176, 1_200)]
    assert seen[-1]["tick_timestamps_ns"] == list(range(176, 1_200))
    assert "closed_shares" not in seen[-1]


def test_world_series_survive_checkpoint_with_integer_money():
    from factorylab.runtime.resume import restore_runtime, runtime_state

    rt = make_runtime()
    rt._manage_reserve_window()
    rt._observe_delivered_event(
        Event("mid", EventKind.MARKET_MID, 1, {"coin": "BTC", "mid": "123.456789"}, "test")
    )
    rt._observe_delivered_event(
        Event("funding", EventKind.FUNDING, 2, {"coin": "BTC", "rate": "-0.0001"}, "test")
    )
    rt._observe_delivered_event(Event("tick", EventKind.TICK, 3, {}, "test"))
    restored = make_runtime()
    restore_runtime(restored, runtime_state(rt))
    assert window_facts(restored.window) == window_facts(rt.window)
    facts = window_facts(restored.window)
    assert type(facts["mids"]["BTC"][0][1]) is int
    assert type(facts["wallet_balance_micro"][0][1]) is int


def test_a_funding_payment_event_is_not_a_funding_rate_sample():
    """Funding payments carry ``paid_usd``; only funding rate events feed the series."""
    from factorylab.kernel.events import Event, EventKind
    from tests.conftest import make_runtime

    rt = make_runtime()
    rt._observe_delivered_event(
        Event("f-pay", EventKind.FUNDING, 0, {"coin": "BTC", "paid_usd": ".9"}, "test")
    )
    assert rt.window.funding == []
    rt._observe_delivered_event(
        Event("f-rate", EventKind.FUNDING, 0, {"coin": "BTC", "rate": "0.0001"}, "test")
    )
    assert [s["value"] for s in rt.window.funding] == [0.0001]
