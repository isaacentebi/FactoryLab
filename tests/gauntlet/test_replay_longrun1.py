"""The criteria discriminate on a dead diary: they reproduce longrun1's known failures.

Phase-2 design §2.2: "Running them over longrun1 must reproduce the known failures:
SF-1c/d (no saturation publication), SF-2b (shares of 1/rank), I-2 (NOOP credited 0.5,
above r̄). That replay is the first validation that the criteria discriminate." The
fixture is an ordered slice of longrun1's opened diary (windows 18-30, a subset of row
kinds, hashes dropped): ``tests/fixtures/longrun1_gauntlet_slice.json``.
"""

import tomllib
from pathlib import Path

import pytest

from scripts import gauntlet as g

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def diary():
    events = g.load_events(ROOT / "tests/fixtures/longrun1_gauntlet_slice.json")
    manifest = tomllib.loads((ROOT / "worlds/edition6-capital-loop.toml").read_text())
    return events, manifest, {r.name: r for r in g.replay(events, manifest)}


def test_the_replay_needs_no_runtime_and_reads_every_criterion(diary):
    _events, _manifest, results = diary
    assert {"SF-0", "SF-1b", "S1", "S5b", "SF-1d[independent-uptake]"} <= set(results)


def test_longrun1_published_no_saturation_sf1d(diary):
    assert diary[2]["SF-1d[independent-uptake]"].status == g.FAIL


def test_longrun1_integrated_past_the_cap_sf1c(diary):
    assert diary[2]["SF-1c[independent-uptake]"].status == g.FAIL


def test_longrun1_split_a_window_by_settlement_rank_sf2b(diary):
    result = diary[2]["SF-2b[independent-consequence]"]
    assert result.status == g.FAIL and result.evidence["rank_shaped"] > 0


def test_longrun1_credited_nothing_delivered_at_the_prior_i2(diary):
    result = diary[2]["S5b"]
    assert result.status == g.FAIL and result.evidence["at_prior"] == result.evidence["checked"]


def test_longrun1_reset_a_ratchet_while_its_attractor_held_sf1b(diary):
    """Astra M-6, found in longrun1: at window 24 the paid-off card went unmeasured for a
    whole tail, left the failing set while stable failure persisted, and its duration
    price restarted from one (an unmeasured card is missing evidence, not compliance)."""
    problems = diary[2]["SF-1b"].evidence["problems"]
    assert any(p.get("window") == 24 and "duration_reset" in p for p in problems)


def test_longrun1_saturated_in_one_window_sf0(diary):
    result = diary[2]["SF-0"]
    assert result.status == g.FAIL and result.evidence["gain_headroom_windows"] == 1


def test_longrun1_drew_every_arm_by_its_own_seed_s1(diary):
    assert diary[2]["S1"].ok
