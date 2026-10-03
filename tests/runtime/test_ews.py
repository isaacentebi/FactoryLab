"""Early warning at its owner: variance, autocorrelation and ensemble disagreement.

Essay II.III.a: "multiscale attentiveness to a few important metrics: variance,
autocorrelation, and ensemble disagreement". Ruling R3 makes the statistics live and
evaluations M2 publishes them to the evaluators: at every window close the runtime
reads them over k, 2k and 4k windows (``runtime.ews``, ``Pricing._early_warning_*``).
That only evaluators are shown the table is proved on a world, beside the multi-judge
checks in ``test_evaluation_layer``.
"""

from __future__ import annotations

import pytest

from factorylab.runtime import ews
from factorylab.runtime.loop import Runtime
from factorylab.runtime.pricing import MeasureWindow
from factorylab.runtime.worlds import load_manifest


def _history(profiles, k):
    history = []
    for window, profile in enumerate(profiles):
        history = ews.history_with(history, window, profile, k)
    return history


def test_the_history_keeps_the_longest_span_and_one_row_per_window():
    history = _history([{"verdict": float(w)} for w in range(10)], k=2)
    assert [row["window"] for row in history] == list(range(2, 10))  # 4k windows
    profile = {"verdict": 0.5}
    again = ews.history_with(history, 9, profile, k=2)  # the same window, read again
    assert [row["window"] for row in again] == list(range(2, 10))
    assert again[-1]["profile"] == {"verdict": 0.5}
    profile["verdict"] = 0.9  # the history holds its own copy
    assert again[-1]["profile"] == {"verdict": 0.5}


def test_the_table_reads_variance_and_lag_one_autocorrelation_over_k_2k_and_4k():
    k = 2
    history = _history([{"verdict": float(w % 2), "conformity": 0.5, "consequence":
                         None if w == 4 else 0.25, "balance": 10.0 * w}
                        for w in range(4 * k)], k)
    series = ews.table(history, ["card:c"], k)
    assert {"verdict", "conformity", "consequence", "disagreement", "balance",
            "card:c"} <= set(series)
    # 0,1,0,1,...: population variance 1/4 at every span; the centred lag-one
    # autocorrelation is -(n-1)/n over n points: -1/2, -3/4, -7/8.
    verdict = series["verdict"]
    assert [s["span"] for s in verdict] == [2, 4, 8]
    assert [s["variance"] for s in verdict] == pytest.approx([0.25, 0.25, 0.25])
    assert [s["autocorrelation"] for s in verdict] == pytest.approx([-0.5, -0.75, -0.875])
    # A constant span has a variance of zero and no autocorrelation, never zero.
    assert [(s["variance"], s["autocorrelation"]) for s in series["conformity"]] == [
        (0.0, None)] * 3
    # A span with a missing value is unsupported, never compressed across the gap.
    consequence = series["consequence"]
    assert consequence[0]["variance"] == 0.0 and consequence[0]["supported"] == 2
    assert [s["variance"] for s in consequence[1:]] == [None, None]
    assert [s["supported"] for s in consequence[1:]] == [3, 7]
    # A series no window carried, and a history shorter than a span, are unsupported.
    assert all(s["variance"] is None for s in series["disagreement"] + series["card:c"])
    short = ews.table(history[:3], [], k)
    assert [s["variance"] is None for s in short["verdict"]] == [False, True, True]
    assert ews.table([], [], k) == {}


def test_the_summary_is_the_largest_score_statistic_at_its_shortest_supported_span():
    k = 2
    history = _history([{"verdict": float(w % 2), "conformity": 0.5, "consequence":
                         0.1 * (w // 4), "balance": 1000.0 * w} for w in range(4 * k)], k)
    series = ews.table(history, [], k)
    # verdict: 0.25 and -1/2 at span 2; conformity: 0 and None; consequence at span 2
    # reads (0.1, 0.1): 0 and None. The balance swings far more and is not a score.
    variance, autocorrelation = ews.summary(series)
    assert variance == pytest.approx(0.25) and autocorrelation == pytest.approx(-0.5)
    assert ews.summary(ews.table(history[:1], [], k)) == (None, None)


def test_an_evaluator_is_shown_the_last_close_and_nothing_else():
    assert ews.view({}) == {"window": None, "series": {}}
    table = {"window": 4, "spans_windows": [1, 2, 4], "summary": {"ews_variance": 0.1},
             "series": {"verdict": []}, "history": ["private"]}
    assert ews.view(table) == {key: v for key, v in table.items() if key != "history"}


def test_every_close_publishes_the_table_to_evaluators_and_ledgers_it():
    """The runtime's own close, window after window: each close ledgers ``ews.window``
    and replaces the evaluators' table; its statistics are withheld from the public
    observations a producer reads."""
    rt = Runtime(load_manifest("scripted"), events=0, seed=1, initial_balance_micro=None,
                 ledger_path=None)
    rt._derive_regions()
    k = rt.m.immune.k
    for close in range(4 * k):
        rt.n += 10
        rt.ticks_consumed += rt.m.timing.min_ratio
        rt.window = MeasureWindow(rt.n, rt.wallet.balance, invocations=10, ok=10,
                                  verdicts={f"r{close}": {"j1": [float(close % 2)],
                                                          "j2": [0.5]}})
        index = rt.window.index
        rt._close_price_window()
        table = rt.stats.early_warning
        assert table["window"] == index and table["spans_windows"] == [k, 2 * k, 4 * k]
    closes = [i for i in rt.ledger._recovery_items() if i["kind"] == "ews.window"]
    assert len(closes) == 4 * k
    cards = {f"card:{c.id}" for c in rt.charter.cards}
    assert {"verdict", "conformity", "consequence", "disagreement", "balance"} | cards <= set(
        table["series"])
    assert all(s["variance"] is not None for s in table["series"]["verdict"])
    assert all(s["variance"] is not None for s in table["series"]["disagreement"])
    assert table["summary"]["ews_variance"] is not None
    assert "verdict" in closes[-1]["supported_series"]
    assert rt._early_warning_view()["window"] == table["window"]
    assert not set(rt._public_observations()["last_closed_window_values"]) & (
        ews.EWS_OBSERVATIONS)
