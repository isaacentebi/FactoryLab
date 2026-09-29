"""SF-1c reads the integrator's hold where the published law puts it: on the price a
window ran at, not the price its update set.

The rows are cut from a free scripted replay of the edition-6 rehearsal world on the
longrun1 tape, seed 2 (``tests/fixtures/scripted_s2_sf1c_uptake_rows.json``: the
``independent-uptake`` card's price updates, ratchets, saturation rows and adoption from
window 86 to 111, with each window's ``price.window``, hashes dropped).

Read on ``lambda_after``, the card failed SF-1c: the integral "moved" from 0.532051 to
0.535211 while "held at the cap". The violation wobbles between 0.934 and 0.940, so the
card's bound ``B = penalty_cap / v`` moves with it. When v eases, the new B lies above the
price the window ran at, the window's penalty ``lambda_before · v`` is below the cap
(w96: 0.531646 × 0.939024 = 0.49923), and the published recurrence integrates,
``I' = min(B, I_E + eta·v)``, which here reaches B at once, so the update sets the price
at the cap. That update is integration below the cap, not a windup at it. Every update
whose window ran at its bound (``lambda_before >= B``) held the integral exactly.
"""

import copy
import json
import tomllib
from pathlib import Path

import pytest

from scripts import gauntlet as g

ROOT = Path(__file__).resolve().parents[2]
CARD = "independent-uptake"


@pytest.fixture
def rows():
    return json.loads((ROOT / "tests/fixtures/scripted_s2_sf1c_uptake_rows.json").read_text())


@pytest.fixture(scope="module")
def manifest():
    return tomllib.loads((ROOT / "worlds/edition6-testnet-rehearsal.toml").read_text())


def _update(rows, window):
    """The card's update closed with ``price.window`` ``window``."""
    at = {row["window_end_event"]: row["window"] for row in rows
          if row["kind"] == "price.window"}
    return next(row for row in rows if row["kind"] == "price.update"
                and at.get(row["window_end_event"]) == window)


def test_every_window_that_ran_at_its_bound_held_the_integral(rows, manifest):
    result = g.sf1c_anti_windup(rows, manifest, card=CARD)
    assert result.ok, result.evidence
    assert result.evidence["held"] == 15 and result.evidence["moved"] == []


def test_the_moves_the_old_reading_saw_were_windows_below_the_cap(rows, manifest):
    """On ``lambda_after`` the integral moved inside a capped run six times; each of those
    windows ran below its own bound, so its penalty was below the cap and it integrated."""
    ph = g.physics(manifest)
    moved = [b for run in g.capped_runs(rows, CARD, ph) if len(run) > 1
             for a, b in zip(run, run[1:], strict=False) if a["i"] != b["i"]]
    assert len(moved) == 6
    for row in moved:
        assert row["lambda_before"] * row["violation"] < ph.cap
        assert row["lambda_before"] < ph.cap / row["violation"]
    w96 = _update(rows, 96)
    assert (w96["lambda_before"], w96["lambda_after"], w96["i"]) == \
        (0.5316455696202532, 0.5324675324675325, 0.5324675324675325)


def test_an_adoption_and_a_ratchet_move_the_integral_in_force(rows, manifest):
    """w91 follows an adopted price of 0.2 (``price.proposed``) and w93 a ratchet: the
    integral each held update is compared with is the one in force, moved by them."""
    kinds = [row["kind"] for row in rows]
    assert "price.proposed" in kinds and "immune.price_ratchet" in kinds
    runs = g.held_updates(rows, CARD, g.physics(manifest))
    assert all(held.expected == held.row["i"] for run in runs for held in run)


def test_negative_control_a_held_window_that_integrates_fails(rows, manifest):
    """w95 ran at its bound (lambda_before 0.531646 = B): move its integral by 1e-6."""
    mutant = copy.deepcopy(rows)
    row = _update(mutant, 95)
    assert row["lambda_before"] >= 0.5 / row["violation"]
    row["i"] += 1e-6
    result = g.sf1c_anti_windup(mutant, manifest, card=CARD)
    assert result.status == g.FAIL
    assert result.evidence["moved"] == [(0.532051282051282, 0.532051282051282 + 1e-6)]


def test_negative_control_a_held_window_that_cuts_fails(rows, manifest):
    """w109 ran at its bound after a spike: a cut to its smaller bound is no hold."""
    mutant = copy.deepcopy(rows)
    row = _update(mutant, 109)
    row["i"] = row["bound"]
    assert g.sf1c_anti_windup(mutant, manifest, card=CARD).status == g.FAIL
