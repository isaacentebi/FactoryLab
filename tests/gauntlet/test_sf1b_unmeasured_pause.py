"""SF-1b retains duration across an explicitly unmeasured pause (§II.b, §IV.c).

The fixture projects real rows from scripted-20260927-210633-s1/events.json:
price registration/removal, ratchet rows and immune closes through window 223.
Price-window values/regions retain the three
ratcheted cards' measured readings. Original seq and duration values are kept.
No world, venue or model is run.
"""

import json
from pathlib import Path

import pytest

from scripts import gauntlet as g


@pytest.fixture
def rows():
    path = Path(__file__).resolve().parents[1] / "fixtures/scripted_s1_sf1b_rows.json"
    return json.loads(path.read_text())


def test_real_unmeasured_pauses_resume_without_reset(rows):
    result = g.sf1b_ratchet_cadence(rows, {})
    assert result.status == g.PASS, result.evidence
    assert result.evidence["problems"] == []


@pytest.mark.parametrize("window,previous", [(163, 35), (223, 50)])
def test_resetting_a_paused_duration_is_still_a_failure(rows, window, previous):
    for row in rows:
        if (row["kind"] == "immune.price_ratchet_saturated"
                and row["card_id"] == "forecast-skill-floor" and row["window"] == window):
            row["duration"] = 1
    result = g.sf1b_ratchet_cadence(rows, {})
    assert result.status == g.FAIL
    assert {"card": "forecast-skill-floor", "window": window,
            "duration_reset": [previous, 1]} in result.evidence["problems"]


@pytest.mark.parametrize("window,duration", [(163, 36), (223, 16)])
def test_measured_card_really_exited_and_must_reset(rows, window, duration):
    # The real uptake card was not unmeasured_held: its ended row at 158/215
    # records a genuine exit on the acting grid, unlike the forecast card.
    for row in rows:
        if (row["kind"] == "immune.price_ratchet_saturated"
                and row["card_id"] == "independent-uptake" and row["window"] == window):
            row["duration"] = duration
    result = g.sf1b_ratchet_cadence(rows, {})
    assert result.status == g.FAIL
    assert {"card": "independent-uptake", "window": window,
            "missed_reset": [0, duration]} in result.evidence["problems"]


def test_unmeasured_still_owes_ratchet_when_flagged(rows):
    rows = [r for r in rows if not (r["kind"] == "immune.price_ratchet_saturated"
            and r["card_id"] == "forecast-skill-floor" and r["window"] == 163)]
    result = g.sf1b_ratchet_cadence(rows, {})
    assert result.status == g.FAIL
    assert {"card": "forecast-skill-floor", "window": 163,
            "duration_reset": [35, None]} in result.evidence["problems"]


def test_old_rows_without_explicit_hold_do_not_get_a_blanket_exemption(rows):
    for row in rows:
        row.pop("unmeasured_held", None)
    result = g.sf1b_ratchet_cadence(rows, {})
    assert result.status == g.FAIL
    for window, duration in [(163, 36), (223, 51)]:
        assert {"card": "forecast-skill-floor", "window": window,
                "missed_reset": [0, duration]} in result.evidence["problems"]
