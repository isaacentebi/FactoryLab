import copy
import json

import pytest

from factorylab.versioning.report import render, summary
from factorylab.versioning.series import windows


def test_profiles_follow_runtime_shapes(diary):
    extra = [
        {"kind": "decision.open", "propensity": {"chosen": "NOOP"}},
        {"kind": "decision.open", "propensity": {"chosen": "producer"}},
        {
            "kind": "decision.settle",
            "return": {"channel": "verdict", "score": 0.2, "status": "settled"},
        },
        {
            "kind": "decision.settle",
            "return": {"channel": "verdict", "score": 99, "status": "censored"},
        },
        {
            "kind": "decision.settle",
            "return": {"channel": "verdict", "score": 99, "status": "historical"},
            "original_status": "settled",
        },
        {"kind": "wallet.reserve", "balance_after": 7_123_456},
    ]
    for about, evaluator, score in [("a", "e1", 0), ("a", "e2", 1), ("b", "e1", 0.9)]:
        extra.append(
            {
                "kind": "event",
                "event": {
                    "kind": "Verdict",
                    "payload": {
                        "about_handle": about,
                        "evaluator_handle": evaluator,
                        "verdict": score,
                    },
                },
            }
        )
    report = summary(diary([{"verdict": 0.8, "extra": extra, "cards": {"turnover": 5}}]))
    p = report["windows"][0]["profile"]
    assert p["verdict"] == pytest.approx(0.5)
    assert p["consequence"] is None
    assert p["noop_share"] == 0.5 and p["registrations"] == 1.0
    assert p["disagreement"] == 0.5 and p["balance"] == 7_123_456.0
    assert p["turnover"] == 5.0
    assert all(value is None or type(value) is float for value in p.values())


def test_disagreement_deduplicates_evaluators_and_omits_singletons(diary):
    def verdict(about, evaluator, score):
        return {
            "kind": "event",
            "event": {
                "kind": "Verdict",
                "payload": {
                    "about_handle": about,
                    "evaluator_handle": evaluator,
                    "verdict": score,
                },
            },
        }

    report = summary(
        diary(
            [
                {
                    "extra": [
                        verdict("a", "e1", 0),
                        verdict("a", "e1", 1),
                        verdict("b", "e1", 0),
                        verdict("b", "e2", 0.4),
                        verdict("c", "e1", 0),
                        verdict("c", "e2", 0.8),
                    ]
                },
                {"extra": [verdict("a", "e2", 0)]},
            ]
        )
    )
    assert report["windows"][0]["profile"]["disagreement"] == pytest.approx(0.3)
    assert report["windows"][1]["profile"]["disagreement"] is None


def test_windows_close_inclusively_by_seq_and_drop_only_tail(diary):
    items = diary([{"verdict": 1}, {"verdict": 2}, {"verdict": 3}])
    boundary_seq = [item["seq"] for item in items if item["kind"] == "price.window"]
    items.append(
        {
            "seq": len(items),
            "kind": "decision.settle",
            "return": {
                "channel": "verdict",
                "status": "settled",
                "score": 999,
            },
        }
    )
    groups = windows(items, window_items=1)
    assert len(groups) == 3
    assert [w["profile"]["verdict"] for w in groups] == [1, 2, 3]
    assert [w["end_seq"] for w in groups] == boundary_seq
    retained = [i for w in groups for i in range(w["start_seq"], w["end_seq"] + 1)]
    assert retained == list(range(len(items) - 1))
    assert groups[0]["start_event"] is None
    assert groups[1]["start_event"] == groups[0]["end_event"] == 120


def test_fallback_blocks_keep_complete_last_block():
    items = [{"kind": "wallet.settle", "seq": i, "balance_after": i} for i in range(7)]
    assert [w["profile"]["balance"] for w in windows(items, window_items=3)] == [2, 5]
    assert len(windows(items[:6], window_items=3)) == 2
    assert windows(items[:2], window_items=3) == []


def test_regions_and_activation_use_real_code_shapes(diary):
    max_region = {"kind": "max", "lo": None, "hi": 2, "scale": 1}
    replacement = dict(max_region, hi=9)
    items = diary(
        [
            {"regions": {"cost": max_region}, "cards": {"cost": 3}},
            {
                "before": [{"kind": "charter.activate", "edition": 2, "amendment_id": "a"}],
                "regions": {"cost": replacement},
                "cards": {"cost": 3},
            },
            {"cards": {"cost": 3}},
        ]
    )
    groups = windows(items)
    assert [w["charter_edition"] for w in groups] == [1, 2, 2]
    assert groups[0]["regions"]["cost"]["hi"] == 2
    assert [w["regions"]["cost"]["hi"] for w in groups[1:]] == [9, 9]


def test_input_and_outputs_are_detached_and_json_serialisable(diary):
    items = diary(
        [
            {
                "verdict": i,
                "cards": {"cost": i},
                "regions": {
                    "cost": {"kind": "max", "lo": None, "hi": 3, "scale": 2},
                },
            }
            for i in range(12)
        ]
    )
    original = copy.deepcopy(items)
    report = summary(items)
    assert report == summary(list(reversed(items))) == summary(items)
    assert json.loads(json.dumps(report, allow_nan=False)) == report
    text = render(report)
    assert "Spectral gap lower bound (Dobrushin)" in text
    assert text.count("EWS ") == 6
    assert render(report) == text
    assert items == original
    report["windows"][0]["regions"]["cost"]["hi"] = 999
    assert items == original
    assert report["windows"][1]["regions"]["cost"]["hi"] == 3


def test_empty_summary_is_explicit(immune_params):
    report = summary([], **immune_params)
    for field in ("windows", "versions", "pathologies", "ews", "settling"):
        assert report[field] == []
    assert report["operator"]["gap_bound"] is None
    assert "unsupported" in render(report)


@pytest.mark.parametrize(
    "params",
    [
        {"window_items": 0},
        {"bins": 0},
        {"k": 0},
        {"k": True},
        {"bins": 1.5},
        {"tv_threshold": float("nan")},
        {"gap_threshold": 1.1},
        {"tv_threshold": -1},
    ],
)
def test_invalid_parameters_fail_even_without_data(params, immune_params):
    with pytest.raises(ValueError):
        summary([], **(immune_params | params))


def test_ambiguous_sequence_windows_and_names_fail(diary):
    with pytest.raises(ValueError, match="sequence"):
        summary([{"seq": 0}, {"seq": 0}])
    with pytest.raises(ValueError, match="seq"):
        summary([{}])
    items = diary([{}, {}])
    items[-1]["window_end_event"] = 120
    with pytest.raises(ValueError, match="increase"):
        summary(items)
    with pytest.raises(ValueError, match="collides"):
        summary(diary([{"cards": {"verdict": 3}}]))
