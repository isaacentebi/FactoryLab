import pytest

from factorylab.charter.controller import CardRegion, PriceController, violation
from factorylab.kernel.ledger import Ledger
from factorylab.versioning import summary
from factorylab.versioning.versions import slope


def test_stable_failure_only(diary):
    region = {"kind": "max", "lo": None, "hi": 1.0, "scale": 2.0}
    rows = [{"verdict": 0.2, "cards": {"cost": 5}, "regions": {"cost": region}} for _ in range(8)]
    flags = summary(diary(rows))["pathologies"]
    assert [flag["kind"] for flag in flags] == ["stable_failure"]
    assert (flags[0]["start_window"], flags[0]["end_window"]) == (2, 7)
    assert flags[0]["evidence"]["windows"][0]["gap_bound"] == 1
    assert all(e["violated_cards"] == ["cost"] for e in flags[0]["evidence"]["windows"])
    rows[0]["regions"] = {"cost": dict(region, hi=5)}
    for row in rows[1:]:
        row.pop("regions")
    assert summary(diary(rows))["pathologies"] == []


def test_learning_death_only_and_registrations_split_runs(diary):
    rows = [{"verdict": 0.5, "registrations": 0} for _ in range(9)]
    flags = summary(diary(rows))["pathologies"]
    assert [flag["kind"] for flag in flags] == ["learning_death"]
    assert (flags[0]["start_window"], flags[0]["end_window"]) == (2, 8)
    rows[4]["registrations"] = 1
    flags = summary(diary(rows))["pathologies"]
    assert [(flag["start_window"], flag["end_window"]) for flag in flags] == [(2, 3), (7, 8)]


def test_thrash_only_and_stops_when_cells_stop_changing(diary):
    # A card supplies cells without adding a score slope that could flag divergence.
    rows = [{"cards": {"activity": i % 2},
             "regions": {"activity": {"kind": "max", "lo": None, "hi": -1, "scale": 1}}}
            for i in range(14)]
    flags = summary(diary(rows), k=3, tv_threshold=0.2)["pathologies"]
    assert [flag["kind"] for flag in flags] == ["thrash"]
    assert (flags[0]["start_window"], flags[0]["end_window"]) == (3, 13)
    assert all(all(e["changes"]) for e in flags[0]["evidence"]["windows"])
    report = summary(diary(rows + [{"cards": {"activity": 1}}] * 4), k=3, tv_threshold=0.2)
    assert [flag["end_window"] for flag in report["pathologies"] if flag["kind"] == "thrash"] == [
        13
    ]


@pytest.mark.parametrize("outcome", ["forecast_skill", "consequence"])
def test_overfitting_divergence_only(diary, outcome):
    rows = [
        {"verdict": i / 10, "cards": {"forecast_skill": 1 - i / 10}}
        if outcome == "forecast_skill"
        else {"verdict": i / 10, "consequence": 1 - i / 10}
        for i in range(9)
    ]
    report = summary(diary(rows))
    flags = report["pathologies"]
    assert [flag["kind"] for flag in flags] == ["overfitting_divergence"]
    assert flags[0]["evidence"]["verdict_slope"] == pytest.approx(0.1)
    assert flags[0]["evidence"]["outcome_slope"] == pytest.approx(-0.1)
    assert flags[0]["evidence"]["outcome_series"] == outcome


def test_partial_forecast_support_does_not_fabricate_a_slope(diary):
    rows = [{"verdict": i, "consequence": -i} for i in range(5)]
    rows[0]["cards"] = {"forecast_skill": 0.5}
    assert summary(diary(rows))["pathologies"] == []


@pytest.mark.parametrize(
    "kind,lo,hi,values",
    [
        ("max", -99, 2, [1, 2, 4]),
        ("min", 2, 99, [0, 2, 4]),
        ("band", 2, 4, [0, 2, 3, 4, 6]),
    ],
)
def test_violation_matches_controller(kind, lo, hi, values):
    controller = PriceController(Ledger(), eta=0.1, decay=0.1, lambda_max=1, min_window_events=1)
    controller.register(CardRegion("x", kind, lo, hi, 2))
    region = {"kind": kind, "lo": lo, "hi": hi, "scale": 2}
    for value in values:
        region_value = violation(CardRegion(**dict(region, card_id="x")), value)
        assert region_value == controller.violation("x", value)


def test_version_boundaries_are_detection_windows(diary):
    region = {"kind": "max", "hi": 0, "lo": None, "scale": 1}
    rows = [{"verdict": v, "cards": {"quality": v}, "regions": {"quality": region}}
            for v in [0] * 6 + [1] * 6]
    report = summary(diary(rows), k=3, tv_threshold=.5)
    spans = report["versions"]
    assert [span["start_window"] for span in spans] == [0, 7, 8, 9]
    assert sum(span["duration"] for span in spans) == 12
    assert spans[0]["dominant_cells"][0]["share"] == pytest.approx(6 / 7)
    assert [entry["end_window"] for entry in report["ews"]] == [6, 7, 8, 11]


def test_ews_known_values_and_missing_time_positions(diary):
    report = summary(
        diary([{"verdict": i, "balance": 100, "consequence": 2} for i in range(12)]), k=3
    )
    signals = report["ews"][-1]["series"]
    assert [s["span"] for s in signals["verdict"]] == [3, 6, 12]
    assert signals["verdict"][0]["variance"] == pytest.approx(2 / 3)
    assert signals["verdict"][0]["autocorrelation"] == 0
    assert signals["balance"][0]["variance"] == 0
    assert signals["balance"][0]["autocorrelation"] is None
    assert signals["disagreement"][0]["variance"] is None
    rows = [{"verdict": i} for i in range(12)]
    rows[-2] = {}
    missing = summary(diary(rows))["ews"][-1]["series"]["verdict"][0]
    assert missing["supported"] == 2 and missing["variance"] is None
    assert slope([0, None, 2]) == 1
    assert slope([None, 1]) is None


def test_settling_after_activation_and_unsettled_tail(diary):
    activation = {"kind": "charter.activate", "amendment_id": "a", "edition": 2}
    rows = [{"verdict": 0} for _ in range(3)]
    rows += [{"verdict": 1, "before": [activation]}, {"verdict": 1}]
    rows += [{"verdict": 0} for _ in range(4)]
    for row in rows:
        row["cards"] = {"quality": row["verdict"]}
        row["regions"] = {"quality": {"kind": "max", "hi": 0, "lo": None, "scale": 1}}
    items = diary(rows)
    report = summary(items, k=2)
    evidence = report["settling"][0]
    assert evidence["activation_window"] == 3
    assert evidence["settled_window"] == 6
    assert evidence["windows_after_activation"] == 4 and evidence["tv"] == 0
    assert summary(diary(rows[:5]), k=2)["settling"][0]["windows_after_activation"] is None
    tail = dict(activation, amendment_id="tail", edition=3, seq=len(items))
    assert summary(items + [tail], k=2)["settling"][-1]["activation_window"] is None


def test_activation_inside_window_excludes_mixed_window(diary):
    rows = [{"verdict": 0} for _ in range(6)]
    rows[2]["extra"] = [{"kind": "charter.activate", "amendment_id": "a", "edition": 2}]
    report = summary(diary(rows), k=2)
    assert report["settling"][0]["settled_window"] == 4
    assert report["settling"][0]["windows_after_activation"] == 3
    assert report["windows"][2]["charter_edition"] == 1
    assert report["windows"][3]["charter_edition"] == 2
    early = [{"before": [{"kind": "charter.activate", "amendment_id": "b", "edition": 2}]}]
    assert summary(diary(early), k=2)["settling"][0]["windows_after_activation"] is None
