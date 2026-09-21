"""The predicate contract publishes the actual preflight shape and readiness signal."""

from factorylab.runtime.observations import window_facts
from factorylab.runtime.pricing import MeasureWindow
from factorylab.runtime.shared import work_disclosure


def test_predicate_disclosure_matches_closed_window_facts_contract():
    work = work_disclosure({}, [])

    assert work["predicate_readiness"] == {
        "requires_closed_window": True,
        "live_signal": "WORLD UPDATE public_observations.last_closed_window_values",
        "not_ready": "an empty last_closed_window_values means registration will be rejected "
                     "because there is no closed window to preflight",
    }
    facts = work["predicate_facts_example"]
    actual = window_facts(MeasureWindow(
        index=1,
        equity_start_micro=300_000_000,
        mids=[
            {"coin": "BTC", "ts_ns": 1_710_000_000_000_000_000,
             "value": 81_234_500_000},
            {"coin": "BTC", "ts_ns": 1_710_000_060_000_000_000,
             "value": 81_310_000_000},
        ],
        funding=[{"coin": "BTC", "ts_ns": 1_710_000_000_000_000_000,
                  "value": 0.0001}],
        wallet_balance_micro=[
            [1_710_000_000_000_000_000, 300_000_000],
            [1_710_000_060_000_000_000, 299_999_000],
        ],
        tick_timestamps_ns=[1_710_000_000_000_000_000,
                            1_710_000_060_000_000_000],
    ))
    assert {key: actual[key] for key in facts} == facts
    assert "ordered [timestamp_ns, value] pairs, not objects" in work["predicate_contract"]
