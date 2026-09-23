"""The forensic report replays the live versioning and the live predicate (wave 5b).

Essay II.II: versions are behavioural; stable failure is "a robust version with a
wide spectral gap" failing its input; thrash never settles; learning death is the
frontier "no longer being invoked". The report reaches exactly what the organ read.
"""

import pytest

from factorylab.charter.controller import CardRegion, PriceController, violation
from factorylab.kernel.ledger import Ledger
from factorylab.versioning.report import summary
from factorylab.versioning.versions import frontier_evidence, slope

REGION = {"kind": "max", "lo": None, "hi": 1.0, "scale": 2.0}


def _failing(n, **extra):
    return [{"verdict": 0.5, "registrations": 0, "cards": {"cost": 5}, "regions": {"cost": REGION},
             **extra} for _ in range(n)]


def test_stable_failure_is_a_persistent_violation_in_a_wide_attractor(diary):
    flags = summary(diary(_failing(8)))["pathologies"]
    assert [flag["kind"] for flag in flags] == ["stable_failure"]
    assert (flags[0]["start_window"], flags[0]["end_window"]) == (2, 7)
    evidence = flags[0]["evidence"]["windows"]
    assert evidence[0]["card_gap"] == 1
    assert all(e["violated_cards"] == ["cost"] for e in evidence)
    rows = _failing(8)
    rows[0]["regions"] = {"cost": dict(REGION, hi=5)}  # compliant: nothing fails
    for row in rows[1:]:
        row.pop("regions")
    assert summary(diary(rows))["pathologies"] == []


def test_a_registration_does_not_reset_the_failing_attractor(diary):
    """Versioning P3: the attractor is the persistent violated-card set and the gap over
    the cards; activity, which the organ's raised gain invites, never enters it."""
    rows = _failing(10)
    rows[4]["registrations"] = 3
    rows[6]["registrations"] = 3
    flags = [f for f in summary(diary(rows))["pathologies"] if f["kind"] == "stable_failure"]
    assert [(f["start_window"], f["end_window"]) for f in flags] == [(2, 9)]


def test_an_unmeasured_reading_is_missing_evidence_not_compliance(diary):
    rows = _failing(8)
    rows[4]["cards"] = {}
    flags = [f for f in summary(diary(rows))["pathologies"] if f["kind"] == "stable_failure"]
    assert [(f["start_window"], f["end_window"]) for f in flags] == [(2, 7)]


def _immune(rows, frontier):
    """Diary rows that carry the organ's own window records, with the routers' draws."""
    for i, row in enumerate(rows):
        row["extra"] = [{"kind": "immune.window", "window": i + 1, "charter_edition": 1,
                         "profile": {"verdict": row.get("verdict"), "card:cost": 5,
                                     "registrations": row.get("registrations", 0),
                                     "revision": 0.0},
                         "regions": {"card:cost": REGION}, "tick": 10 * (i + 1),
                         "frontier_invocation": frontier(i)}]
    return rows


def test_learning_death_is_read_from_frontier_invocation_never_from_cards(diary):
    """Versioning P1: the routers' draws, quiet and a single cell; compliance never enters."""
    parked = [{"router": "router:Tick", "uninvoked": True, "core": False}]
    invoked = [{"router": "router:Tick", "uninvoked": False, "core": False}]
    rows = _immune(_failing(8), lambda i: parked)
    kinds = {f["kind"] for f in summary(diary(rows))["pathologies"]}
    assert "learning_death" in kinds
    rows = _immune(_failing(8), lambda i: invoked)
    assert "learning_death" not in {f["kind"] for f in summary(diary(rows))["pathologies"]}
    # A core (no-swap-regret) router parked at NOOP is not the frontier.
    rows = _immune(_failing(8), lambda i: [dict(parked[0], core=True)])
    assert "learning_death" not in {f["kind"] for f in summary(diary(rows))["pathologies"]}
    # Registrations break the quiet, so they split the diagnosis.
    rows = _immune(_failing(9), lambda i: parked)
    rows[4]["registrations"] = 1
    rows = _immune(rows, lambda i: parked)
    spans = [(f["start_window"], f["end_window"]) for f in summary(diary(rows))["pathologies"]
             if f["kind"] == "learning_death"]
    assert spans == [(2, 3), (7, 8)]


def test_a_quarantined_frontier_is_offered_unhistoried_seats_and_never_draws_them():
    window = {"profile": {"registrations": 0, "revision": 0}, "regions": {},
              "frontier_invocation": [{"router": "r", "uninvoked": False,
                                       "unhistoried_offered": 4, "unhistoried_mass": 0.0}]}
    evidence = frontier_evidence([window, window, window])
    assert evidence["gone"] and evidence["unhistoried"] == {"offered": 12, "mass": 0.0}
    window["frontier_invocation"][0]["unhistoried_mass"] = 0.2
    assert not frontier_evidence([window, window, window])["gone"]
    # Without the routers' record (an older diary) nothing is diagnosed gone.
    assert not frontier_evidence([{"profile": {}, "regions": {}}])["gone"]


def test_oscillation_is_thrash_and_a_steady_series_is_not(diary):
    """Essay II.IV.b: "We can think of thrash as oscillation"; its versions are cut
    before they ever settle."""
    region = {"kind": "max", "lo": None, "hi": 0, "scale": 1}
    rows = [{"cards": {"activity": i % 2}, "regions": {"activity": region}} for i in range(14)]
    report = summary(diary(rows), k=3, tv_threshold=0.2)
    thrash = [f for f in report["pathologies"] if f["kind"] == "thrash"]
    assert thrash and all(not e["settled"] for f in thrash for e in f["evidence"]["windows"])
    steady = [{"cards": {"activity": 1}, "regions": {"activity": region}} for _ in range(14)]
    assert not [f for f in summary(diary(steady), k=3)["pathologies"] if f["kind"] == "thrash"]


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


def test_versions_are_debounced_detections_each_with_its_own_gap(diary):
    """Versioning M2, P6: a boundary needs two complete k-blocks inside the version, and
    each version's operator is counted over its own span."""
    region = {"kind": "max", "hi": 0, "lo": None, "scale": 1}
    rows = [{"verdict": v, "cards": {"quality": v}, "regions": {"quality": region}}
            for v in [0] * 6 + [1] * 6]
    spans = summary(diary(rows), k=3, tv_threshold=.5)["versions"]
    assert [(s["start_window"], s["cause"]) for s in spans] == [(0, "launch"), (7, "behaviour")]
    assert sum(span["duration"] for span in spans) == 12
    assert spans[0]["dominant_cells"][0]["share"] == pytest.approx(6 / 7)
    assert spans[1]["gap"] == 1 and spans[1]["durable"]


def test_a_card_becoming_measurable_opens_no_version(diary):
    """Versioning P6: the unsupported reading is missing, not a cell of its own."""
    region = {"kind": "max", "hi": 0, "lo": None, "scale": 1}
    rows = [{"cards": {} if i < 4 else {"quality": 1}, "regions": {"quality": region}}
            for i in range(12)]
    assert [s["cause"] for s in summary(diary(rows), k=3)["versions"]] == ["launch"]


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


def test_an_activation_opens_a_version_and_its_settling_is_read(diary):
    """Essay II.II: "If a revision to the input at the level of the charter happens, the
    version has changed"; II.IV.c: its settling time is what governance waits on."""
    activation = {"kind": "charter.activate", "amendment_id": "a", "edition": 2}
    region = {"kind": "max", "hi": 0, "lo": None, "scale": 1}
    rows = [{"cards": {"quality": 0}, "regions": {"quality": region}} for _ in range(4)]
    rows += [{"cards": {"quality": 1}, "regions": {"quality": region}, "before": [activation]}]
    rows += [{"cards": {"quality": 1}, "regions": {"quality": region}} for _ in range(6)]
    report = summary(diary(rows), k=2)
    assert [(s["start_window"], s["cause"]) for s in report["versions"]] == [
        (0, "launch"), (4, "charter")]
    charter = [r for r in report["settling"] if r["cause"] == "charter"]
    assert charter and charter[0]["settled"] and charter[0]["ticks"] > 0
    assert report["versions"][1]["settling_ticks"] == charter[0]["ticks"]
