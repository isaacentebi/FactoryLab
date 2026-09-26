"""The gauntlet's pure criteria, each on synthetic rows: a passing set and a violating one.

AGENTS.md: every invariant gets a test that attempts to violate it. A criterion that
passes on its violating rows does not discriminate and must be rewritten (design G2).
"""

import json
import math
from decimal import Decimal
from random import Random

import pytest

from scripts import gauntlet as g

M = {"prices": {"eta": 0.5, "decay": 0.1, "lambda_max": 1.0, "penalty_cap": 0.5, "kp": 0.0},
     "immune": {"k": 3, "gain_step": 0.05, "gamma_max": 0.5, "price_step": 0.05,
                "tv_threshold": 0.2},
     "timing": {"min_ratio": 3}, "novelty": {"share": 0.1},
     "evaluation": {"no_swap_regret_kinds": ["Tick"]}}


def _w(index, *, acts=False, sf=False, thrash=False, ld=False, lam=0.0, pen=0.0,
       profile=None, frontier=(), violated=None, unsettled=None, lifespans=(), tv=None):
    """An ``immune.window`` row. A stable-failure window names the card it holds (card
    ``c`` unless ``violated`` says otherwise), as ``versions.diagnose`` does."""
    if violated is None:
        violated = ["card:c"] if sf else []
    return {"kind": "immune.window", "window": index, "acts": acts,
            "flags": {"stable_failure": sf, "thrash": thrash, "learning_death": ld},
            # The thrash price's violation: positive while flagged unless given.
            "thrash": {"lambda": lam, "penalty": pen,
                       "violation": (1.0 if thrash else 0.0) if tv is None else tv},
            "unsettled": unsettled,
            "violated_cards": list(violated), "lifespans": list(lifespans),
            # The organ's learning-death evidence names the routers it read quarantined.
            "frontier": {"quarantined_routers": sorted(
                r["router"] for r in frontier if r.get("quarantined") and not r.get("core"))
                if ld else [], "uninvoked_routers": []},
            "profile": profile or {"access:registration_route": 1.0},
            "frontier_invocation": list(frontier)}


def _seq(rows):
    return [dict(row, seq=i) for i, row in enumerate(rows)]


# --- derived physics -------------------------------------------------------------------


def test_physics_reads_the_world_and_defaults_to_the_manifest_defaults():
    ph = g.physics(M)
    assert (ph.r, ph.k, ph.H, ph.cap, ph.no_swap_regret_kinds) == (3, 3, 9, 0.5, ("Tick",))
    assert g.physics({}).H == 9 and g.physics(None).cap == 0.5


def test_w_sat_is_the_least_window_count_that_reaches_the_cap():
    ph = g.physics(M)
    assert g.w_sat(ph, 1.0) == 1  # the scripted gains: one window
    assert g.w_sat(ph, 0.5) == 4  # (0.5 w)·0.25 >= 0.5 at w = 4
    assert g.w_sat(ph, 0.0) is None
    slow = g.physics({**M, "prices": {**M["prices"], "eta": 0.01}})
    assert g.w_sat(slow, 1.0) == 50
    capped = g.physics({**M, "prices": {**M["prices"], "lambda_max": 0.4}})
    assert g.w_sat(capped, 1.0) is None  # lambda_max · v never reaches the cap
    pid = g.physics({**M, "prices": {**M["prices"], "kp": 0.5}})
    assert g.w_sat(pid, 1.0) == 1  # edition 6: P alone already reaches it


def test_sf0_refuses_a_world_whose_integrator_saturates_before_detection():
    """SF-0's negative control: eta = 0.5 and cap = 0.5 saturate in 1 < H = 9 windows."""
    bad = g.sf0_relation(M, cards=[{"id": "c", "window": {"kind": "windows"},
                                    "region": {"kind": "min", "lo": 0.2}}])
    assert bad.status == g.FAIL and bad.evidence["gain_headroom_windows"] == 1
    slow = {**M, "prices": {**M["prices"], "eta": 0.05}}
    good = g.sf0_relation(slow, cards=[{"id": "c", "window": {"kind": "returns"},
                                        "region": {"kind": "band", "lo": 0, "hi": 1}}])
    assert good.ok and good.evidence["cards"][0]["w_sat"] == 10


def test_release_gain_and_learning_scales():
    ph = g.physics(M)
    # Kernel-exact float loops: 0.5 leaks to exactly 0 in six windows at decay 0.1 (the
    # fifth leaves 2.8e-17), and 0.4 unwinds in nine gain steps of 0.05.
    assert g.t_release(ph, 0.5) == 6 and g.t_release(ph, 0.0) == 0
    assert g.t_gamma(ph, 0.4, 3) == 27
    assert g.gain_steps(ph, 0.1) == 9 and g.gain_steps(ph, 0.5) == 0
    assert g.t_learn(0.3, 3) == 37


def test_violation_is_the_controllers_formula():
    assert g.violation({"kind": "min", "lo": 0.2, "hi": None, "scale": 0.2}, 0.0) == 1.0
    assert g.violation({"kind": "max", "lo": None, "hi": 5.0, "scale": 5.0}, 10.0) == 1.0
    assert g.violation({"kind": "band", "lo": 0.0, "hi": 1.0, "scale": 1.0}, 0.5) == 0.0


# --- stable failure --------------------------------------------------------------------


def _price_window(index, value, lo=0.2):
    return {"kind": "price.window", "window": index, "values": {"c": value},
            "regions": {"c": {"kind": "min", "lo": lo, "hi": None, "scale": lo}}}


def test_sf1a_detection_within_the_horizon_and_its_violation():
    rows = [_price_window(i, 0.0) for i in range(1, 15)]
    ok = rows + [_w(i, sf=i >= 4) for i in range(1, 15)]
    assert g.sf1a_detection(ok, M, card="c").ok
    late = rows + [_w(i, sf=i >= 12) for i in range(1, 15)]
    assert g.sf1a_detection(late, M, card="c").status == g.FAIL
    never = rows + [_w(i) for i in range(1, 15)]
    assert g.sf1a_detection(never, M, card="c").status == g.FAIL
    assert g.sf1a_detection([_price_window(1, 0.5)], M, card="c").status == g.UNSUPPORTED


def test_sf1a_episodes_reset_on_compliance_and_are_never_summed():
    """Codex P2: violations separated by measured compliance are separate episodes, each
    no longer than H, so an unflagged diary of them is no evidence (the kernel's
    persistence never spans a compliant window); a window that did not measure the card
    neither ends nor extends an episode."""
    values = {w: (0.5 if w % 6 == 0 else 0.0) for w in range(1, 31)}  # 5-window episodes
    rows = [_price_window(w, v) for w, v in values.items()]
    result = g.sf1a_detection(rows + [_w(i) for i in range(1, 31)], M, card="c")
    assert result.status == g.UNSUPPORTED and result.evidence["episodes"][0]["onset"] == 1
    # An unmeasured window inside an episode does not reset it: 1-12 is one episode.
    gappy = [_price_window(w, 0.0) for w in range(1, 13) if w != 6]
    assert g.sf1a_detection(gappy + [_w(i) for i in range(1, 13)], M,
                            card="c").status == g.FAIL
    # A flag on another card is not a detection of this one.
    other = [_price_window(w, 0.0) for w in range(1, 15)]
    flagged_elsewhere = [_w(i, sf=i >= 4, violated=["card:d"]) for i in range(1, 15)]
    assert g.sf1a_detection(other + flagged_elsewhere, M, card="c").status == g.FAIL
    # A second episode, detected within H of its own onset, passes.
    second = {w: (0.0 if w <= 4 or w >= 8 else 0.5) for w in range(1, 25)}
    rows2 = [_price_window(w, v) for w, v in second.items()]
    closes = [_w(i, sf=i >= 12) for i in range(1, 25)]
    result = g.sf1a_detection(rows2 + closes, M, card="c")
    assert result.ok and result.evidence["detected"][0]["onset"] == 8


def test_sf1a_counts_measured_observations_and_expires_with_the_tail():
    """Codex P2: unmeasured gap windows never count toward H. Violations measured every
    other window (k = 3) are one episode, but six observations over eleven windows are
    not H = 9 of them; an episode whose measurements fall more than k windows apart has
    left the kernel's tail and is several."""
    sparse = [_price_window(w, 0.0) for w in range(1, 12, 2)]
    closes = [_w(i) for i in range(1, 25)]
    result = g.sf1a_detection(sparse + closes, M, card="c")
    assert result.status == g.UNSUPPORTED
    assert result.evidence["episodes"][0]["observations"] == 6
    # Measured every other window long enough: the deadline is the tenth observation's
    # window (19), not onset + 9 windows (10).
    long = [_price_window(w, 0.0) for w in range(1, 41, 2)]
    closes = [_w(i, sf=i >= 15) for i in range(1, 45)]
    result = g.sf1a_detection(long + closes, M, card="c")
    assert result.ok and result.evidence["detected"][0]["deadline"] == 19
    late = [_w(i, sf=i >= 25) for i in range(1, 45)]
    assert g.sf1a_detection(long + late, M, card="c").status == g.FAIL
    # Measurements four windows apart: the tail (k = 3) loses each before the next.
    apart = [_price_window(w, 0.0) for w in range(1, 60, 4)]
    result = g.sf1a_detection(apart + [_w(i) for i in range(1, 62)], M, card="c")
    assert result.status == g.UNSUPPORTED
    assert {e["observations"] for e in result.evidence["episodes"]} == {1}


def _ratchets(*pairs):
    return [{"kind": "immune.price_ratchet", "card_id": "c", "window": w, "duration": d,
             "lambda_after": 1.0} for w, d in pairs]


def test_sf1b_duration_rises_on_the_acting_grid_and_both_resets_are_caught():
    closes = [_w(i, acts=i % 3 == 0, sf=True) for i in range(1, 13)]
    assert g.sf1b_ratchet_cadence(closes + _ratchets((3, 1), (6, 2), (9, 3), (12, 4)), M).ok
    # A duration-reset no-op: the flag persists and the duration falls back (Astra M-6).
    reset = g.sf1b_ratchet_cadence(closes + _ratchets((3, 1), (6, 2), (9, 1), (12, 2)), M)
    assert reset.status == g.FAIL
    assert any("duration_reset" in p for p in reset.evidence["problems"])
    # The transient resolution: an unflagged acting window must reset the next ratchet.
    gap = [_w(i, acts=i % 3 == 0, sf=i != 6) for i in range(1, 13)]
    assert g.sf1b_ratchet_cadence(gap + _ratchets((3, 1), (9, 1), (12, 2)), M).ok
    missed = g.sf1b_ratchet_cadence(gap + _ratchets((3, 1), (9, 2), (12, 3)), M)
    assert any("missed_reset" in p for p in missed.evidence["problems"])
    unflagged = g.sf1b_ratchet_cadence(gap + _ratchets((6, 1)), M)
    assert unflagged.status == g.FAIL


def test_sf1b_holds_per_card_and_needs_a_rise():
    """The sweep (A, B): a window flagged on another card does not hold this one, so a
    ratchet there is unflagged and an unratcheted window there is no reset; and first
    ratchets alone never exercised the duration."""
    # Card c is measured compliant at window 6, where the flag holds on card d alone.
    compliant = [_price_window(6, 0.5)]
    other = compliant + [_w(i, acts=i % 3 == 0, sf=True,
                            violated=["card:d"] if i == 6 else None) for i in range(1, 13)]
    assert g.sf1b_ratchet_cadence(other + _ratchets((3, 1), (9, 1), (12, 2)), M).ok
    misplaced = g.sf1b_ratchet_cadence(other + _ratchets((3, 1), (6, 2), (9, 3)), M)
    assert misplaced.status == g.FAIL
    assert {"unflagged_ratchet": 6, "card": "c"} in misplaced.evidence["problems"]
    closes = [_w(i, acts=i % 3 == 0, sf=True) for i in range(1, 5)]
    assert g.sf1b_ratchet_cadence(closes + _ratchets((3, 1)), M).status == g.UNSUPPORTED


def test_sf1b_an_unmeasured_card_stays_in_its_attractor():
    """Astra M-6 (longrun1 window 24): a card that leaves ``violated_cards`` only because
    no window of the tail measured it is still failing; a reset there is a failure."""
    unmeasured = [_w(i, acts=i % 3 == 0, sf=True, violated=["card:d"] if i == 6 else None)
                  for i in range(1, 13)]
    result = g.sf1b_ratchet_cadence(unmeasured + _ratchets((3, 1), (9, 1), (12, 2)), M)
    assert result.status == g.FAIL
    assert {"card": "c", "window": 6, "duration_reset": [1, None]} in result.evidence["problems"]


def _updates(card, triples):
    return [{"kind": "price.update", "card_id": card, "lambda_after": lam, "violation": v,
             "i": i, "window_end_event": 10 * n} for n, (lam, v, i) in enumerate(triples, 1)]


def test_sf1c_the_integral_is_frozen_exactly_at_the_cap():
    frozen = _updates("c", [(0.5, 1.0, 0.5), (0.5, 1.0, 0.5), (0.5, 1.0, 0.5)])
    assert g.sf1c_anti_windup(frozen, M, card="c").ok
    winding = _updates("c", [(0.5, 1.0, 0.5), (1.0, 1.0, 1.0), (1.0, 1.0, 1.0)])
    assert g.sf1c_anti_windup(winding, M, card="c").status == g.FAIL
    assert g.sf1c_anti_windup(_updates("c", [(0.1, 1.0, 0.1)]), M,
                              card="c").status == g.UNSUPPORTED


def test_sf1c_reads_only_runs_at_the_cap_and_restarts_across_an_uncapped_interval():
    """Codex P2: after saturation the violation eases below the cap and the integral
    legitimately moves; that is no windup. A new run at the cap is read afresh, and a
    windup inside it still fails."""
    eased = _updates("c", [(0.5, 1.0, 0.5), (0.5, 1.0, 0.5),   # at the cap, frozen
                           (0.6, 0.5, 0.6), (0.7, 0.5, 0.7),   # eased: 0.3, 0.35 < 0.5
                           (0.7, 1.0, 0.7), (0.7, 1.0, 0.7)])  # at the cap again, frozen
    result = g.sf1c_anti_windup(eased, M, card="c")
    assert result.ok and result.evidence["runs"] == 2
    winding = eased + _updates("c", [(0.9, 1.0, 0.9)])  # still at the cap, integral moved
    assert g.sf1c_anti_windup(winding, M, card="c").status == g.FAIL


def _windowed(card, triples, first=1):
    """Price updates at consecutive windows from ``first``, each with its window's
    ``price.window`` row (the two share ``window_end_event``)."""
    rows = []
    for n, (lam, v, i) in enumerate(triples):
        w = first + n
        rows += [{"kind": "price.update", "card_id": card, "lambda_after": lam,
                  "violation": v, "i": i, "window_end_event": 10 * w},
                 {"kind": "price.window", "window": w, "window_end_event": 10 * w}]
    return rows


def test_sf1d_saturation_is_ledgered_with_a_rising_duration():
    at_cap = _windowed("c", [(0.5, 1.0, 0.5)] * 4)
    rows = at_cap + _saturated((1, 1), (2, 2), (3, 3))
    assert g.sf1d_escalation(rows, M, card="c").ok
    assert g.sf1d_escalation(at_cap, M, card="c").status == g.FAIL
    stuck = at_cap + _saturated((1, 1), (2, 1))
    assert g.sf1d_escalation(stuck, M, card="c").status == g.FAIL
    unwindowed = at_cap + [{"kind": "immune.price_ratchet_saturated", "card_id": "c",
                            "duration": d} for d in (1, 2, 3)]
    assert g.sf1d_escalation(unwindowed, M, card="c").status == g.FAIL


def test_sf1d_one_capped_update_then_uncapped_ones_is_not_sustained_saturation():
    """Codex review: SF-1d uses SF-1c's partition. One capped update followed by
    min_ratio − 1 uncapped ones demands no escalation; min_ratio consecutive capped ones
    do, and a gap of one uncapped update restarts the count."""
    one = _updates("c", [(0.5, 1.0, 0.5), (0.2, 1.0, 0.2), (0.2, 1.0, 0.2)])
    assert g.sf1d_escalation(one, M, card="c").status == g.UNSUPPORTED
    broken = _updates("c", [(0.5, 1.0, 0.5), (0.5, 1.0, 0.5), (0.2, 1.0, 0.2),
                            (0.5, 1.0, 0.5), (0.5, 1.0, 0.5)])
    assert g.sf1d_escalation(broken, M, card="c").status == g.UNSUPPORTED
    sustained = _updates("c", [(0.2, 1.0, 0.2), *[(0.5, 1.0, 0.5)] * 3])
    assert g.sf1d_escalation(sustained, M, card="c").status == g.FAIL  # nothing published


def _saturated(*pairs):
    """Saturation rows, each ``(window, duration)``."""
    return [{"kind": "immune.price_ratchet_saturated", "card_id": "c", "window": w,
             "duration": d} for w, d in pairs]


def test_sf1d_durations_are_read_per_saturation_episode():
    """The sweep (A): two sustained runs at the cap, each counted from 1, pass; the count
    may restart only at 1, and every sustained run needs its own episode reaching r."""
    two = _windowed("c", [*[(0.5, 1.0, 0.5)] * 3, (0.2, 1.0, 0.2), *[(0.5, 1.0, 0.5)] * 3])
    both = _saturated((1, 1), (2, 2), (3, 3), (5, 1), (6, 2), (7, 3))
    assert g.sf1d_escalation(two + both, M, card="c").ok
    one_episode = g.sf1d_escalation(two + _saturated((1, 1), (2, 2), (3, 3), (5, 4)),
                                    M, card="c")
    assert one_episode.status == g.FAIL and one_episode.evidence["unmatched"] == [
        {"run": [5, 7]}]
    skipped = g.sf1d_escalation(
        two + _saturated((1, 1), (2, 2), (3, 3), (5, 2), (6, 3), (7, 4)), M, card="c")
    assert skipped.status == g.FAIL
    assert [m["duration"] for m in skipped.evidence["malformed"]] == [2, 3, 4]


def test_sf1d_episodes_are_aligned_to_their_runs_not_counted():
    """Codex P2: capped runs at windows 1-3 and 20-22 need episodes overlapping each;
    two episodes at 1-3 and 40-42 are two, but the run at 20-22 has none."""
    rows = (_windowed("c", [(0.5, 1.0, 0.5)] * 3, first=1)
            + _windowed("c", [(0.2, 1.0, 0.2)], first=10)
            + _windowed("c", [(0.5, 1.0, 0.5)] * 3, first=20))
    misaligned = _saturated((1, 1), (2, 2), (3, 3), (40, 1), (41, 2), (42, 3))
    result = g.sf1d_escalation(rows + misaligned, M, card="c")
    assert result.status == g.FAIL and result.evidence["unmatched"] == [{"run": [20, 22]}]
    aligned = _saturated((1, 1), (2, 2), (3, 3), (20, 1), (21, 2), (22, 3))
    assert g.sf1d_escalation(rows + aligned, M, card="c").ok


def _gain(window, before, after, pathology="stable_failure", router="router:Tick"):
    return {"kind": "immune.gain", "router": router, "window": window,
            "pathology": pathology, "gamma_before": [before], "gamma_after": [after]}


def test_sf1e_gain_rises_to_its_bound_and_never_unwinds_while_flagged():
    closes = [_w(i, acts=i % 3 == 0, sf=i >= 3) for i in range(1, 40)]
    steps = [_gain(w, round(0.1 + 0.05 * n, 2), round(0.15 + 0.05 * n, 2))
             for n, w in enumerate(range(3, 27, 3))]
    assert g.sf1e_gain(closes + steps, M).ok
    unwound = closes + steps + [_gain(30, 0.5, 0.45, "cleared")]
    assert g.sf1e_gain(unwound, M).status == g.FAIL
    assert g.sf1e_gain([_w(1)], M).status == g.UNSUPPORTED
    # Stepping, but the diary ends before the router's bound: not a pass.
    short = [_w(i, acts=i % 3 == 0, sf=i >= 3) for i in range(1, 12)]
    early = [_gain(w, round(0.1 + 0.05 * n, 2), round(0.15 + 0.05 * n, 2))
             for n, w in enumerate(range(3, 12, 3))]
    assert g.sf1e_gain(short + early, M).status == g.UNSUPPORTED
    # The bound is fully flagged and the top was never reached: a failure.
    stalled = closes + steps[:3]
    assert g.sf1e_gain(stalled, M).status == g.FAIL


def test_sf1e_the_peak_and_the_start_are_the_episodes_own():
    """Codex P2: a router that reached the top in an earlier episode, unwound after it, and
    then sat through a whole later episode without climbing back fails; the old reading
    took the top over every row and passed it."""
    first = [_w(i, acts=i % 3 == 0, sf=3 <= i <= 30) for i in range(1, 34)]
    climb = [_gain(w, round(0.1 + 0.05 * n, 2), round(0.15 + 0.05 * n, 2))
             for n, w in enumerate(range(3, 27, 3))]
    unwind = [_gain(33, 0.5, 0.45, "cleared")]
    second = [_w(i, acts=i % 3 == 0, sf=True) for i in range(34, 80)]
    one_more = [_gain(36, 0.45, 0.5 - 1e-9)]  # never quite back to the top
    result = g.sf1e_gain(first + second + climb + unwind + one_more, M)
    assert result.status == g.FAIL
    assert result.evidence["problems"][0]["episode"] == [34, 79]
    # The later episode's γ₀ is the latest pre-episode state (0.45): one step to the top.
    back = [_gain(36, 0.45, 0.5)]
    assert g.sf1e_gain(first + second + climb + unwind + back, M).ok


def _novelty(budget=1_000_000, carried=0, accrued="1/3", cap=None, amount=None, share="0.1"):
    """A ``novelty.window`` row as ``NoveltyReserve.open_window`` computes it, in its own
    Decimal and integer arithmetic."""
    num, den = Decimal(share).as_integer_ratio()
    true_cap = budget * num // den
    a, b = (int(x) for x in accrued.split("/"))
    true_amount = min(true_cap, carried + true_cap * a // b)
    return {"kind": "novelty.window", "budget": budget, "carried": carried,
            "accrued": accrued, "cap": true_cap if cap is None else cap,
            "amount": true_amount if amount is None else amount}


def test_ld1a_and_sf1f_accrual_is_exact_and_the_route_stays_open():
    rows = [_novelty(), _novelty(carried=33_333)]
    assert g.ld1a_accrual(rows, M).ok
    assert g.ld1a_accrual([_novelty(amount=1)], M).status == g.FAIL
    assert g.sf1f_route_open(rows + [_w(1), _w(2)], M).ok
    shut = [_w(1), _w(2, profile={"access:registration_route": 0.0})]
    assert g.sf1f_route_open(rows + shut, M).status == g.FAIL


def test_ld1a_uses_the_reserves_decimal_arithmetic_at_share_0_3():
    """Codex review: at share 0.3 on 10 µUSD the kernel's cap is 3 (Decimal "0.3"); a
    float ratio would say 2. The predicate agrees with the real NoveltyReserve."""
    from fractions import Fraction

    from factorylab.kernel.ledger import Ledger
    from factorylab.kernel.reserve import NoveltyReserve

    assert (0.3).as_integer_ratio()[0] * 10 // (0.3).as_integer_ratio()[1] == 2
    ledger = Ledger(None)
    reserve = NoveltyReserve(0.3, has_history=lambda _c: False, ledger=ledger,
                             clock_ns=lambda: 0)
    reserve.open_window(1, 10, accrued=Fraction(1))
    reserve.open_window(2, 1_000_001, accrued=Fraction(1, 3))
    rows = [r for r in ledger._recovery_items() if r["kind"] == "novelty.window"]
    assert rows[0]["cap"] == 3
    world = {**M, "novelty": {"share": 0.3}}
    assert g.ld1a_accrual(rows, world).ok
    assert g.ld1a_accrual([_novelty(budget=10, accrued="1/1", share="0.3")], world).ok
    wrong = _novelty(budget=10, accrued="1/1", share="0.3", cap=2, amount=2)
    assert g.ld1a_accrual([wrong], world).status == g.FAIL


def _open(handle, chosen, actor="router:Tick", probs=None, ids=None, seed=None):
    ids = ids or [chosen, "NOOP"]
    probs = probs or [0.5, 0.5]
    if seed is None:
        seed = next(s for s in range(10_000)
                    if Random(s).choices(ids, weights=probs, k=1)[0] == chosen)
    return {"kind": "decision.open", "handle": handle, "actor": actor,
            "propensity": {"action_ids": ids, "probs": probs, "chosen": chosen,
                           "rng_seed": seed, "source": "sampled"}}


def _penalty(handle, share, window=1, violation=1.0, obs="revision_rate", owner=None):
    term = {"card_id": "c", "observation": obs, "window": window, "violation": violation,
            "lambda": 0.5, "weight": 0.5, "share": share}
    if owner:
        term["owner"] = owner
    return {"kind": "price.penalty", "handle": handle, "penalty": 0.5 * share,
            "raw": min(1.0, 0.5 + 0.5 * share), "effective": 0.5, "terms": [term]}


def _member(handle, seat, actor="router:Tick", role="producer"):
    """A split member: a decision opened and invoked (``split_members``)."""
    return [_open(handle, seat, actor=actor),
            {"kind": "invocation", "handle": handle, "role": role, "status": "ok"}]


def _settled(handle, channel="verdict"):
    """A scored settlement (``decision.settle``, ``original_status`` settled)."""
    return {"kind": "decision.settle", "original_status": "settled",
            "return": {"handle": handle, "channel": channel, "status": "settled",
                       "score": 0.5}}


def _sf2_window(*members):
    """Window 1: the members opened and invoked, then the window closed with card c
    violated (``_price_window(1, 0.0)``: 0 under a floor of 0.2)."""
    return [row for m in members for row in m] + [_price_window(1, 0.0)]


KW = {"card": "c", "relievers": {"rel"}, "holders": {"hold"}}


def test_sf2a_relievers_bear_nothing_and_holders_share_equally():
    window = _sf2_window(_member("d1", "rel"), _member("d2", "hold"), _member("d3", "hold"))
    good = window + [_penalty("d1", 0.0), _penalty("d2", 0.5), _penalty("d3", 0.5)]
    assert g.sf2_gradient(good, M, **KW).ok
    generic = window + [_penalty("d1", 1 / 3), _penalty("d2", 1 / 3), _penalty("d3", 1 / 3)]
    assert g.sf2_gradient(generic, M, **KW).status == g.FAIL


def test_sf2a_needs_both_arms_and_the_complete_non_relieving_count():
    """Codex review: uniformly zero holder shares fail, and 1/n counts every
    non-reliever of the split, a NOOP of the same router included (R9)."""
    window = _sf2_window(_member("d1", "rel"), _member("d2", "hold"), _member("d3", "hold"))
    zero = window + [_penalty("d1", 0.0), _penalty("d2", 0.0), _penalty("d3", 0.0)]
    assert g.sf2_gradient(zero, M, **KW).status == g.FAIL
    noop = [_open("d4", "NOOP")]
    with_noop = _sf2_window(_member("d1", "rel"), _member("d2", "hold"),
                            _member("d3", "hold"), noop)
    thirds = with_noop + [_penalty("d1", 0.0), _penalty("d2", 1 / 3), _penalty("d3", 1 / 3)]
    assert g.sf2_gradient(thirds, M, **KW).ok
    halves = with_noop + [_penalty("d1", 0.0), _penalty("d2", 0.5), _penalty("d3", 0.5)]
    assert g.sf2_gradient(halves, M, **KW).status == g.FAIL


def test_sf2a_a_holder_the_kernel_never_priced_fails():
    """Codex P2 (gauntlet.py:1250): the expected holders are the window's split members
    (opened, invoked, not niche), not the priced rows. A holder that settled with a
    score and has no pricing row fails, even when another holder was priced; one still
    open is missing evidence."""
    window = _sf2_window(_member("d1", "rel"), _member("d2", "hold"), _member("d3", "hold"))
    priced = [_penalty("d1", 0.0), _penalty("d2", 0.5)]
    hidden = g.sf2_gradient(window + priced + [_settled("d3")], M, **KW)
    assert hidden.status == g.FAIL
    assert {"window": 1, "unpriced": "d3", "side": "holder"} in hidden.evidence["problems"]
    assert g.sf2_gradient(window + priced, M, **KW).ok  # d3 still open: no share to read
    dropped = g.sf2_gradient(window + [_penalty("d1", 0.0), _settled("d2"), _settled("d3")],
                             M, **KW)
    assert dropped.status == g.FAIL
    # A holder priced in a policy settlement is no split pricing (governance settles it).
    assert "d3" not in g.settled_unpriced([_settled("d3", channel="policy")])


def test_sf2b_shares_are_order_blind_and_the_one_over_rank_shape_fails():
    equal = [_penalty(f"d{i}", 0.25) for i in range(4)]
    assert g.sf2b_order_blind(equal, M, card="c").ok
    ranked = [_penalty(f"d{i}", 1 / (i + 1)) for i in range(4)]
    result = g.sf2b_order_blind(ranked, M, card="c")
    assert result.status == g.FAIL and result.evidence["rank_shaped"] == 1
    exact = [_penalty("d0", 0.0, obs="well_formed_rate"),
             _penalty("d1", 1.0, obs="well_formed_rate")]
    assert g.sf2b_order_blind(exact, M, card="c").status == g.UNSUPPORTED


def test_sf2b_is_unsupported_without_a_comparable_pair():
    """Codex P2: one decision per (window, role) group compares nothing."""
    alone = [_penalty("d0", 0.25) | {"terms": [{**_penalty("d0", 0.25)["terms"][0],
                                                 "window": w}]}
             for w in (1, 2, 3)]
    result = g.sf2b_order_blind(alone, M, card="c")
    assert result.status == g.UNSUPPORTED and result.evidence["groups"] == 3


# --- thrash --------------------------------------------------------------------------------


def test_th1a_and_th1b_detection_and_a_price_that_holds_duration():
    closes = [_w(i, thrash=i >= 5, lam=0.1 * max(0, i - 4), pen=min(0.5, 0.08 * max(0, i - 4)))
              for i in range(1, 12)]
    assert g.th1a_detection(closes, M, cycle_start=3).ok
    assert g.th1a_detection(closes, M, cycle_start=-10).status == g.FAIL
    assert g.th1b_duration(closes, M).ok
    flat = [_w(i, thrash=True, lam=0.0, unsettled=1.0) for i in range(1, 8)]
    assert g.th1b_duration(flat, M).status == g.FAIL  # the price never holds duration
    falling = [_w(i, thrash=True, lam=0.3 - 0.02 * i, pen=0.1) for i in range(1, 8)]
    assert g.th1b_duration(falling, M).status == g.FAIL


def test_th1c_every_charge_is_price_times_movement_and_some_round_is_charged():
    rows = _seq([
        _w(1, lam=0.4),
        _open("d1", "a", ids=["a", "NOOP"], probs=[0.8, 0.2]),
        _open("d2", "a", ids=["a", "NOOP"], probs=[0.1, 0.9]),  # moved 0.7
        _open("d3", "a", ids=["a", "NOOP"], probs=[0.1, 0.9]),  # did not move
    ])
    charged = rows + [{"kind": "thrash.charged", "handle": "d2", "router": "router:Tick",
                       "charge": 0.4 * 0.7, "reward": 0.4}]
    assert g.th1c_movement(charged, M).ok
    wrong = rows + [{"kind": "thrash.charged", "handle": "d3", "router": "router:Tick",
                     "charge": 0.1, "reward": 0.4}]
    assert g.th1c_movement(wrong, M).status == g.FAIL
    # The negative control's shape: a charge that never lands on a learned round fails;
    # on a round not learned yet it is pending (``_thrash_charged`` runs at learning).
    assert g.th1c_movement(rows + [_settled("d2")], M).status == g.FAIL
    assert g.th1c_movement(rows, M).status == g.UNSUPPORTED


def test_th1c_one_of_two_expected_charges_missing_fails():
    """Codex P2: a correct charge for one moved draw must not excuse a dropped one."""
    rows = _seq([
        _w(1, lam=0.4),
        _open("d1", "a", ids=["a", "NOOP"], probs=[0.8, 0.2]),
        _open("d2", "a", ids=["a", "NOOP"], probs=[0.1, 0.9]),  # moved 0.7
        _open("d3", "a", ids=["a", "NOOP"], probs=[0.6, 0.4]),  # moved 0.5
    ])
    both = rows + [{"kind": "thrash.charged", "handle": h, "router": "router:Tick",
                    "charge": c, "reward": 0.4} for h, c in (("d2", 0.4 * 0.7),
                                                             ("d3", 0.4 * 0.5))]
    assert g.th1c_movement(both, M).ok
    one = [*both[:-1], _settled("d3")]
    result = g.th1c_movement(one, M)
    assert result.status == g.FAIL and result.evidence["missing"] == ["d3"]
    # Codex P2 (gauntlet.py:1598): not learned yet, the same round is pending.
    pending = g.th1c_movement(both[:-1], M)
    assert pending.ok and pending.evidence["pending"] == 1
    wrong_amount = [*both[:-1], {**both[-1], "charge": 0.1}]
    assert g.th1c_movement(wrong_amount, M).status == g.FAIL


def test_th1c_compares_each_charge_exactly():
    """The sweep (C): the expectation is ``_record_movement``'s own float arithmetic, so a
    charge one ulp off is a different charge."""
    rows = _seq([
        _w(1, lam=0.4),
        _open("d1", "a", ids=["a", "NOOP"], probs=[0.8, 0.2]),
        _open("d2", "a", ids=["a", "NOOP"], probs=[0.1, 0.9]),
    ])
    charge = {"kind": "thrash.charged", "handle": "d2", "router": "router:Tick",
              "charge": 0.4 * 0.7, "reward": 0.4}
    assert g.th1c_movement(rows + [charge], M).ok
    off = charge | {"charge": math.nextafter(0.4 * 0.7, 1.0)}
    assert g.th1c_movement(rows + [off], M).status == g.FAIL


def test_th1c_a_duplicate_charge_fails_even_at_the_right_amount():
    """Codex P2: exactly one ``thrash.charged`` row per positive expected handle."""
    rows = _seq([
        _w(1, lam=0.4),
        _open("d1", "a", ids=["a", "NOOP"], probs=[0.8, 0.2]),
        _open("d2", "a", ids=["a", "NOOP"], probs=[0.1, 0.9]),  # moved 0.7
    ])
    charge = {"kind": "thrash.charged", "handle": "d2", "router": "router:Tick",
              "charge": 0.4 * 0.7, "reward": 0.4}
    assert g.th1c_movement(rows + [charge], M).ok
    result = g.th1c_movement(rows + [charge, dict(charge)], M)
    assert result.status == g.FAIL and result.evidence["duplicated"] == ["d2"]


def test_th1d_no_charge_reaches_the_frontier_or_the_niche():
    ok = [{"kind": "thrash.charged", "handle": "d1", "router": "router:Tick", "charge": 0.1}]
    assert g.th1d_frontier(ok, M).ok
    frontier = [{"kind": "thrash.charged", "handle": "d1", "router": "router:WorldUpdate",
                 "charge": 0.1}]
    assert g.th1d_frontier(frontier, M).status == g.FAIL
    niche = ok + [{"kind": "niche.action", "handle": "d1"}]
    assert g.th1d_frontier(niche, M).status == g.FAIL
    assert g.th1d_frontier([], M).status == g.UNSUPPORTED  # no charge is no evidence


def test_th1e_release_after_the_cycle_stops():
    closes = [_w(i, thrash=i < 10, lam=0.5 if i < 10 else max(0.0, 0.5 - 0.1 * (i - 9)))
              for i in range(1, 25)]
    assert g.th1e_release(closes, M, steady_from=8).ok
    stuck = [_w(i, thrash=True, lam=0.5) for i in range(1, 25)]
    assert g.th1e_release(stuck, M, steady_from=8).status == g.FAIL


def test_th1e_passes_only_on_an_observed_zero_and_is_unsupported_when_the_diary_ends():
    """Codex review: three readings. The flag clears at window 10 and the bound is
    T_rel(0.5) + 1 = 6 windows, so windows 10-16 are the release horizon."""
    def diary(last, zero_at=None):
        return [_w(i, thrash=i < 10,
                   lam=0.5 if i < 10 or zero_at is None or i < zero_at else 0.0)
                for i in range(1, last + 1)]
    observed = g.th1e_release(diary(20, zero_at=14), M, steady_from=8)
    assert observed.ok and observed.evidence["zero"] == 14
    never = g.th1e_release(diary(20), M, steady_from=8)
    assert never.status == g.FAIL
    truncated = g.th1e_release(diary(13), M, steady_from=8)
    assert truncated.status == g.UNSUPPORTED
    unclear = [_w(i, thrash=True, lam=0.5) for i in range(1, 12)]
    assert g.th1e_release(unclear, M, steady_from=8).status == g.UNSUPPORTED


def test_th1a_an_episode_already_flagged_when_the_cycle_starts_is_not_detection():
    """The sweep (A): the flag counted is an episode's onset at or after the cycle start."""
    straddle = [_w(i, thrash=i >= 2) for i in range(1, 15)]
    assert g.th1a_detection(straddle, M, cycle_start=3).status == g.UNSUPPORTED
    fresh = [_w(i, thrash=i >= 5) for i in range(1, 15)]
    assert g.th1a_detection(fresh, M, cycle_start=3).ok


def test_th1e_the_release_bound_is_the_released_episodes_own_peak():
    """The sweep (A): an earlier episode's high price does not lengthen a later release.
    The released episode (6-9) peaks at 0.2: T_rel = 2, bound 3 windows after the clear at
    10, so a zero first seen at 16 is late; the old global peak (1.0) excused it."""
    lam = {**{i: 1.0 for i in range(1, 4)}, **{i: 0.2 for i in range(4, 16)}}
    rows = [_w(i, thrash=i in (1, 2, 3, 6, 7, 8, 9), lam=lam.get(i, 0.0))
            for i in range(1, 25)]
    result = g.th1e_release(rows, M, steady_from=8)
    assert result.status == g.FAIL
    assert result.evidence["peak"] == 0.2 and result.evidence["episode"] == [6, 9]
    never = [_w(i, lam=0.0) for i in range(1, 25)]
    assert g.th1e_release(never, M, steady_from=8).status == g.UNSUPPORTED


def test_th1a_is_unsupported_when_the_diary_ends_inside_the_horizon():
    closes = [_w(i) for i in range(1, 6)]
    assert g.th1a_detection(closes, M, cycle_start=3).status == g.UNSUPPORTED
    longer = [_w(i) for i in range(1, 20)]
    assert g.th1a_detection(longer, M, cycle_start=3).status == g.FAIL


def test_th1f_thrash_has_priority_over_stable_failure():
    closes = [_w(1, sf=True, thrash=True)]
    assert g.th1f_priority(closes + [_gain(1, 0.2, 0.15, "thrash")], M).ok
    assert g.th1f_priority(closes + [_gain(1, 0.2, 0.25)], M).status == g.FAIL
    # (B): no gain act in such a window never exercised the priority.
    assert g.th1f_priority(closes, M).status == g.UNSUPPORTED


def test_clopper_pearson_upper_bound_is_exact():
    assert g.clopper_pearson_upper(0, 10) == pytest.approx(1 - 0.05 ** (1 / 10), abs=1e-9)
    upper = g.clopper_pearson_upper(3, 100)
    tail = math.exp(g._log_binom_cdf(3, 100, upper))
    assert tail == pytest.approx(0.05, abs=1e-6) and 0.03 < upper < 0.09
    assert g.clopper_pearson_upper(5, 5) == 1.0


def test_th4_the_world_null_is_bounded_by_the_synthetic_null():
    quiet = [_w(i, thrash=i % 40 == 0) for i in range(1, 70)]
    assert g.th4_null(quiet, M, synthetic=(18, 600)).ok
    noisy = [_w(i, thrash=i % 3 == 0) for i in range(1, 70)]
    assert g.th4_null(noisy, M, synthetic=(18, 600)).status == g.FAIL


_LIFESPAN = {"loop": "seat:m", "lifespan_ticks": 2, "latency_ticks": 10, "ratio": 0.2,
             "tick": 7}


def test_th2_a_short_lived_configuration_reads_as_thrash_and_is_never_refused():
    rows = _seq([{"kind": "config.lifespan", **_LIFESPAN},
                 _w(1, thrash=True, unsettled=0.8, lifespans=[_LIFESPAN])])
    assert g.th2_short_lived(rows, M, loop="seat:m").ok
    refused = rows + [{"kind": "registration.rejected", "reason": "too soon after last"}]
    assert g.th2_short_lived(refused, M, loop="seat:m").status == g.FAIL
    unread = _seq([rows[0], _w(1, unsettled=0.0)])
    assert g.th2_short_lived(unread, M, loop="seat:m").status == g.FAIL


def test_th2_passes_only_on_a_short_lifespan_it_read():
    """Codex P2: a short lifespan as the diary's last row, with no window after it, is no
    evidence; nor is a diary of long lifespans alone. A speed refusal still fails."""
    last = _seq([_w(1, unsettled=0.0), {"kind": "config.lifespan", **_LIFESPAN}])
    result = g.th2_short_lived(last, M, loop="seat:m")
    assert result.status == g.UNSUPPORTED and result.evidence["short_checked"] == 0
    long_only = _seq([{"kind": "config.lifespan", **_LIFESPAN, "ratio": 2.0},
                      _w(1, unsettled=0.0)])
    assert g.th2_short_lived(long_only, M, loop="seat:m").status == g.UNSUPPORTED
    refused = long_only + [{"kind": "registration.rejected", "reason": "too fast"}]
    assert g.th2_short_lived(refused, M, loop="seat:m").status == g.FAIL


def test_th2_reads_the_lifespan_in_the_windows_whose_tail_holds_it():
    """The sweep (A): the windows that carry the short lifespan must read it; a high
    reading in a later, unrelated window does not excuse a low one where it was held."""
    held = [_w(1, unsettled=0.1, lifespans=[_LIFESPAN]), _w(2, unsettled=0.1),
            _w(3, unsettled=0.1)]
    later = [_w(i, thrash=True, unsettled=0.9) for i in range(4, 10)]
    rows = _seq([{"kind": "config.lifespan", **_LIFESPAN}, *held, *later])
    result = g.th2_short_lived(rows, M, loop="seat:m")
    assert result.status == g.FAIL and result.evidence["misread"][0]["window"] == 1


def _boundary(at, previous, slow=10):
    """A ``charter.boundary`` row as ``GovernanceCadence.boundary`` writes it."""
    return {"kind": "charter.boundary", "boundary_ns": at, "previous_ns": previous,
            "slowest_period_ns": slow}


def _cadence(at, slow=10):
    """A ``charter.cadence`` row as ``GovernanceCadence.activated`` writes it at a boundary:
    the boundary has already anchored the cadence at ``at``, so the previous activation is
    ``at`` and ``earliest_ns`` is the next threshold."""
    return {"kind": "charter.cadence", "activation_ns": at, "previous_activation_ns": at,
            "slowest_period_ns": slow, "earliest_ns": at + 3 * slow}


def test_th3_boundaries_and_activations_respect_the_cascade_ratio():
    ok = [_boundary(30, 0), _cadence(30), _cadence(30), _boundary(60, 30), _cadence(60)]
    assert g.th3_governance_gap(ok, M).ok
    early = [_boundary(30, 0), _cadence(30), _boundary(50, 30), _cadence(50)]
    result = g.th3_governance_gap(early, M)
    assert result.status == g.FAIL and result.evidence["bad"][0]["gap"] == 20
    off_boundary = [_boundary(30, 0), _cadence(30), _cadence(45)]
    assert g.th3_governance_gap(off_boundary, M).status == g.FAIL
    assert g.th3_governance_gap([_cadence(30)], M).status == g.UNSUPPORTED
    # Codex P2: cadence rows alone, however many, carry no boundary evidence for a pass.
    spaced = [_cadence(30), _cadence(60), _cadence(90)]
    assert g.th3_governance_gap(spaced, M).status == g.UNSUPPORTED
    # Codex pass on b1e3bae: but two activations 1 ns apart violate the ratio whatever
    # the boundaries show, and an observed violation fails.
    assert g.th3_governance_gap([_cadence(30), _cadence(31)], M).status == g.FAIL


def test_th3_reads_the_kernels_own_cadence_rows():
    """Codex review: the rows ``GovernanceCadence`` itself writes, read by the predicate."""
    from factorylab.kernel.ledger import Ledger
    from factorylab.runtime.cadence import GovernanceCadence

    ledger = Ledger(None)
    cadence = GovernanceCadence(ledger, min_ratio=3, backstop=10, sample=20)
    tick = 1_000_000_000
    cadence.launch(0)
    for boundary, now_event in ((1, 30), (2, 60)):
        cadence.advance(now_event)
        now = now_event * tick
        assert cadence.ready(now_ns=now, tick_interval_ns=tick, window=boundary)
        cadence.boundary(boundary, window=boundary, now_ns=now, tick_interval_ns=tick)
        cadence.activated(f"m{boundary}", now, tick)
    rows = [row for row in ledger._recovery_items()
            if row["kind"] in ("charter.boundary", "charter.cadence")]
    assert {r["kind"] for r in rows} == {"charter.boundary", "charter.cadence"}
    assert g.th3_governance_gap(rows, M).ok


# --- learning death, overfitting ---------------------------------------------------------------


def test_ld1d_niche_decisions_bear_no_penalty():
    niche = [{"kind": "niche.action", "handle": f"d{i}"} for i in range(10)]
    free = niche + [_penalty(f"d{i}", 0.0) for i in range(10)]
    assert g.ld1d_exemption(free, M).ok
    charged = niche + [_penalty(f"d{i}", 0.5) for i in range(10)]
    assert g.ld1d_exemption(charged, M).status == g.FAIL
    assert g.ld1d_exemption(niche[:3], M).status == g.UNSUPPORTED


def test_ld1e_and_ld1f_quarantine_is_flagged_and_gain_holds():
    row = {"router": "router:WorldUpdate", "quarantined": True, "core": False}
    closes = [_w(i, ld=i >= 5, frontier=[row]) for i in range(1, 10)]
    assert g.ld1e_detection(closes, M).ok
    unflagged = [_w(i, frontier=[row]) for i in range(1, 15)]
    assert g.ld1e_detection(unflagged, M).status == g.FAIL
    # The diary ends before H windows after the quarantine began: no evidence either way.
    truncated = [_w(i, frontier=[row]) for i in range(1, 8)]
    assert g.ld1e_detection(truncated, M).status == g.UNSUPPORTED
    raised = [_gain(2, 0.25, 0.3)]
    acting = [_w(i, acts=i == 6, ld=i >= 5, frontier=[row]) for i in range(1, 10)]
    assert g.ld1f_hold(acting + raised, M).ok
    assert g.ld1f_hold(acting + raised + [_gain(6, 0.3, 0.25, "cleared")],
                       M).status == g.FAIL
    # (B): no acting dead window with γ above its floor never exercised the hold.
    assert g.ld1f_hold(closes + [_gain(6, 0.3, 0.3)], M).status == g.UNSUPPORTED


def test_ld1e_two_routers_alternating_quarantine_is_no_tail():
    """Codex P2: r1 and r2 quarantined in alternate windows for 10 windows are not one
    router quarantined for 10 windows: the runs are keyed by router, as the organ's
    evidence is."""
    r1 = {"router": "router:r1", "quarantined": True, "core": False}
    r2 = {"router": "router:r2", "quarantined": True, "core": False}
    alternating = [_w(i, frontier=[r1 if i % 2 else r2]) for i in range(1, 11)]
    assert g.ld1e_detection(alternating, M).status == g.UNSUPPORTED
    # One router quarantined throughout, the other in alternate windows, unflagged past
    # H: the one router's tail fails, and it alone.
    one = [_w(i, frontier=[r1, r2] if i % 2 else [r1]) for i in range(1, 15)]
    result = g.ld1e_detection(one, M)
    assert result.status == g.FAIL
    assert {run[2] for run in result.evidence["late"]} == {"router:r1"}


def test_ld1e_a_flag_naming_another_router_is_not_detection():
    """The sweep (A): the learning-death flag detects the router its evidence names."""
    r1 = {"router": "router:r1", "quarantined": True, "core": False}
    r2 = {"router": "router:r2", "quarantined": True, "core": False}
    closes = [_w(i, ld=i >= 5, frontier=[r1, r2] if i >= 5 else [r1]) for i in range(1, 15)]
    for w in closes:
        w["frontier"] = {"quarantined_routers": ["router:r2"] if w["flags"]["learning_death"]
                         else [], "uninvoked_routers": []}
    result = g.ld1e_detection(closes, M)
    assert result.status == g.FAIL and result.evidence["late"][0][2] == "router:r1"


def test_s8_instrumented_with_no_base_is_unsupported():
    assert g.gain_neutral({"bases": []}, {"bases": []}).status == g.UNSUPPORTED


def test_of2d_every_challenge_traces_to_a_seats_return():
    opened = [_open("d1", "adv", actor="router:Verdict")]
    ret = {"kind": "invocation", "handle": "d1", "assembly_id": "adv", "status": "ok"}
    challenge = {"kind": "challenge.proposed", "handle": "d1"}
    assert g.of2d_authorship(opened + [ret, challenge], M, seats={"adv"}).ok
    kernel = {"kind": "challenge.proposed", "handle": "decision-99"}
    assert g.of2d_authorship(opened + [ret, kernel], M).status == g.FAIL
    other = g.of2d_authorship(opened + [ret, challenge], M, seats={"someone-else"})
    assert other.status == g.FAIL
    anonymous = {"kind": "holdout.proposed", "card_id": "c"}  # no handle: authored by none
    assert g.of2d_authorship(opened + [ret, anonymous], M).status == g.FAIL
    trial_only = [{"kind": "challenge.window", "challenge_id": "x"}]
    assert g.of2d_authorship(trial_only, M).status == g.UNSUPPORTED


def test_sf1f_without_a_reserve_window_is_unsupported():
    assert g.sf1f_route_open([_w(1)], M).status == g.UNSUPPORTED


def _consequence(about, q, y, phase="final"):
    return {"kind": "verdict.consequence", "about_handle": about, "q": q, "y": y,
            "phase": phase}


def test_of1a_y_is_one_fact_per_return_whatever_the_verdict():
    same = [_consequence("r1", 0.9, 0.2), _consequence("r1", 0.1, 0.2)]
    assert g.of1a_outside_the_loop(same, M).ok
    moved = [_consequence("r1", 0.9, 0.55), _consequence("r1", 0.1, 0.15)]
    assert g.of1a_outside_the_loop(moved, M).status == g.FAIL
    assert g.of1a_outside_the_loop([same[0]], M).status == g.UNSUPPORTED


def test_of1a_repetition_is_counted_in_rows_not_in_distinct_verdicts():
    """Codex P2: two judges with the same q still read one return; a y that differs
    between them fails."""
    same_q = [_consequence("r1", 0.5, 0.2), _consequence("r1", 0.5, 0.7)]
    result = g.of1a_outside_the_loop(same_q, M)
    assert result.status == g.FAIL and result.evidence["returns"] == 1
    assert g.of1a_outside_the_loop([same_q[0], dict(same_q[0])], M).ok
    # Different phases of one return are different facts.
    phased = [_consequence("r1", 0.5, 0.2), _consequence("r1", 0.5, 0.7, phase="early")]
    assert g.of1a_outside_the_loop(phased, M).status == g.UNSUPPORTED


def _returned_event(handle, about, seq):
    return {"kind": "event", "seq": seq, "event": {"id": f"producerreturn-{seq}",
                                                   "kind": "ProducerReturn",
                                                   "payload": {"about_handle": about}}}


def test_of3a_a_judge_is_drawn_only_after_the_return_it_reads():
    ok = [{"kind": "invocation", "handle": "p1", "seq": 1}, _returned_event("e", "p1", 2),
          {"kind": "decision.open", "handle": "j1", "event_id": "producerreturn-2", "seq": 3}]
    assert g.of3a_sampling_behind_return(ok, M).ok
    early = [{"kind": "decision.open", "handle": "j1", "event_id": "producerreturn-2",
              "seq": 0}, *ok[:2]]
    assert g.of3a_sampling_behind_return(early, M).status == g.FAIL
    # Codex review: after the invocation but before the ProducerReturn it reads.
    before_event = [{"kind": "invocation", "handle": "p1", "seq": 1},
                    {"kind": "decision.open", "handle": "j1",
                     "event_id": "producerreturn-3", "seq": 2},
                    _returned_event("e", "p1", 3)]
    result = g.of3a_sampling_behind_return(before_event, M)
    assert result.status == g.FAIL and result.evidence["early"] == ["j1"]


def test_of2c_the_holdout_part_of_a_violation_is_what_bites():
    rows = _seq([_price_window(5, 0.3), _open("d1", "registrar"),
            _penalty("d1", 0.5, window=5, violation=0.05) | {"terms": [{
                "card_id": "c", "observation": "revision_rate", "window": 5,
                "violation": 0.05, "lambda": 0.5, "weight": 0.025, "share": 0.5}]}])
    assert g.of2c_holdout_bites(rows, M, card="c", seats={"registrar"}, after_window=4).ok
    region_only = _seq([rows[0], rows[1], _penalty("d1", 0.5, window=5, violation=0.0)])
    result = g.of2c_holdout_bites(region_only, M, card="c", seats={"registrar"},
                                  after_window=4)
    assert result.status == g.FAIL


def test_i3c_and_i4a_niche_against_noop_and_the_blind_actuator():
    rows = [{"kind": "price.window", "window": 1}, _open("n1", "seat"), _open("z1", "NOOP"),
            {"kind": "niche.action", "handle": "n1"}, _penalty("n1", 0.4),
            {"kind": "router.abstention_priced", "handle": "z1", "penalty": 0.1,
             "reward": 0.4, "neutral": 0.5}]
    assert g.i3c_niche_no_worse_than_noop(rows, M).status == g.FAIL
    fair = rows[:4] + [_penalty("n1", 0.2)] + rows[5:]
    assert g.i3c_niche_no_worse_than_noop(fair, M).ok
    blind = [{"kind": "sampling.lower", "outcome_slope": None, "verdict_slope": None}]
    assert g.i4a_no_blind_step_back(blind, M).status == g.FAIL
    seen = [{"kind": "sampling.lower", "outcome_slope": 0.1, "verdict_slope": -0.1}]
    assert g.i4a_no_blind_step_back(seen, M).ok
    # (B): raises alone never step back.
    raises = [{"kind": "sampling.raise", "outcome_slope": None, "verdict_slope": None}]
    assert g.i4a_no_blind_step_back(raises, M).status == g.UNSUPPORTED


# --- pricing, not steering (S1-S8) --------------------------------------------------------------


def test_s1_every_draw_replays_from_its_seed_and_every_act_traces_to_a_return():
    good = [_open("decision-1", "a"),
            {"kind": "invocation", "handle": "decision-1", "status": "ok"},
            {"kind": "order.intent", "handle": "decision-1"}]
    assert g.s1_draw_sovereignty(good).ok
    forced = [dict(_open("decision-1", "a"))]
    forced[0]["propensity"] = {**forced[0]["propensity"], "chosen": "NOOP"}
    forced[0]["propensity"]["rng_seed"] = next(
        s for s in range(1000) if Random(s).choices(["a", "NOOP"], weights=[.5, .5])[0] == "a")
    assert g.s1_draw_sovereignty(forced).status == g.FAIL
    kernel_order = [_open("decision-1", "a"), {"kind": "order.intent", "handle": "decision-1"},
                    {"kind": "invocation", "handle": "decision-1", "status": "ok"}]
    assert g.s1_draw_sovereignty(kernel_order).status == g.FAIL
    # A diary that ends inside the call that made the act: pending, never a pass or a
    # failure (the call's tool.call and the wake's invocation come after it).
    ended = kernel_order[:2]
    assert g.s1_draw_sovereignty(ended).status == g.UNSUPPORTED
    # A charter proposal names its decision as ``proposer_handle`` (CharterBook.propose).
    proposal = {"kind": "charter.propose", "id": "m", "proposer_handle": "decision-1"}
    traced = g.s1_draw_sovereignty(good + [proposal])
    assert traced.ok and traced.evidence["acts"] == 2
    orphan = g.s1_draw_sovereignty([_open("decision-1", "a"), proposal])
    assert orphan.status == g.FAIL


def test_s1_an_act_whose_handle_is_not_a_returned_decision_fails():
    """Codex P2: no act row is dropped before the check; a system handle or none fails."""
    good = [_open("decision-1", "a"),
            {"kind": "invocation", "handle": "decision-1", "status": "ok"}]
    system = g.s1_draw_sovereignty(good + [{"kind": "order.intent",
                                            "handle": "system-generated"}])
    assert system.status == g.FAIL
    (unreturned,) = system.evidence["unreturned"]
    assert (unreturned["handle"], unreturned["why"]) == ("system-generated",
                                                         "no decision opened before it")
    nameless = g.s1_draw_sovereignty(good + [{"kind": "treasury.intent"}])
    assert nameless.status == g.FAIL


def test_s4_bounds_on_penalties_rewards_and_ratchets():
    ok = [_penalty("d", 1.0), {"kind": "router.abstention_priced", "handle": "d",
                               "reward": 0.4, "neutral": 0.5, "penalty": 0.1}]
    assert g.s4_boundedness(ok, M).ok
    assert g.s4_boundedness([_penalty("d", 1.0) | {"penalty": 0.6}], M).status == g.FAIL
    over = [{"kind": "immune.price_ratchet", "card_id": "c", "lambda_after": 1.5}]
    assert g.s4_boundedness(over, M).status == g.FAIL
    assert g.s4_boundedness([], M).status == g.UNSUPPORTED
    learned = [{"kind": "propensity.learned", "handle": "d", "reward": 1.5}]
    assert g.s4_boundedness(learned, M).status == g.FAIL
    settled = [{"kind": "decision.settle", "return": {"handle": "d", "score": -0.1}}]
    assert g.s4_boundedness(settled, M).status == g.FAIL
    abstained = [ok[1] | {"penalty": 0.7}]
    assert g.s4_boundedness(abstained, M).status == g.FAIL


def test_s4_compares_a_clamped_penalty_exactly_and_a_weighted_one_with_an_ulp():
    """The sweep (C): ``min(total, cap) * share`` never passes the cap, so an ulp over it
    fails; an abstention's role-weighted sum may carry one ulp, and does not."""
    ulp = 0.5000000000000001
    assert g.s4_boundedness([_penalty("d", 1.0) | {"penalty": ulp}], M).status == g.FAIL
    weighted = {"kind": "router.abstention_priced", "handle": "d", "reward": 0.0,
                "neutral": 0.5, "penalty": ulp}
    assert g.s4_boundedness([weighted], M).ok


def test_s5_and_s5b_abstention_credit():
    ok = [{"kind": "router.abstention_priced", "handle": "d", "router": "router:Tick",
           "neutral": 0.5, "penalty": 0.2, "reward": 0.3}]
    assert g.s5_neutral_imputation(ok, M).ok
    bad = [ok[0] | {"reward": 0.5}]
    assert g.s5_neutral_imputation(bad, M).status == g.FAIL
    # (C) exact: the kernel's own clip of the two ledgered values, to the last bit.
    assert g.s5_neutral_imputation([ok[0] | {"reward": 0.3 + 1e-15}], M).status == g.FAIL
    settled = [_open("d0", "seat"), _penalty("d0", 0.0) | {"raw": 0.3}]
    assert g.s5b_observed_neutral(settled + [ok[0] | {"neutral": 0.3}], M).ok
    assert g.s5b_observed_neutral(settled + ok, M).status == g.FAIL
    # Before the first settled round the prior stands and is not read.
    assert g.s5b_observed_neutral(ok + settled, M).status == g.UNSUPPORTED


def test_s5b_compares_neutral_with_the_routers_computed_mean():
    """Codex review: a router whose settled raw scores truly average 0.5 credits 0.5 and
    passes; one averaging 0.7 that credits 0.5 fails."""
    def rounds(*raws):
        rows = []
        for i, raw in enumerate(raws):
            rows += [_open(f"s{i}", "seat"), _penalty(f"s{i}", 0.0) | {"raw": raw}]
        return rows
    credit = {"kind": "router.abstention_priced", "handle": "z", "router": "router:Tick",
              "neutral": 0.5, "penalty": 0.0, "reward": 0.5}
    assert g.s5b_observed_neutral(rounds(0.3, 0.7) + [credit], M).ok
    wrong = g.s5b_observed_neutral(rounds(0.6, 0.8) + [credit], M)
    assert wrong.status == g.FAIL and wrong.evidence["mismatched"] == 1
    assert g.s5b_observed_neutral(rounds(0.6, 0.8) + [credit | {"neutral": 0.7}], M).ok


def test_s7_s8_gain_names_routers_and_moves_gamma_by_one_common_step():
    assert g.s7_gain_targets([_gain(1, 0.1, 0.15)]).ok
    assert g.s7_gain_targets([_gain(1, 0.1, 0.15, router="learner:seat")]).status == g.FAIL
    # (C): the kernel's own step, min(gamma_max, 0.1 + 0.05), to the last bit.
    assert g.s8_gain_rows_uniform([_gain(1, 0.1, 0.1 + 0.05)], M).ok
    assert g.s8_gain_rows_uniform([_gain(1, 0.1, 0.15 + 1e-12)], M).status == g.FAIL
    down = _gain(2, 0.3, 0.3 - 0.05, "cleared")
    assert g.s8_gain_rows_uniform([_gain(1, 0.1, 0.1 + 0.05), down], M).ok
    # With the seed out of the diary a lowering's lower bound is not verified.
    assert g.s8_gain_rows_uniform([down], M).status == g.UNSUPPORTED
    split = {"kind": "immune.gain", "router": "router:Tick", "window": 1,
             "gamma_before": [0.1, 0.1], "gamma_after": [0.15, 0.2]}
    assert g.s8_gain_rows_uniform([split], M).status == g.FAIL


def test_s8_instrumented_a_gamma_step_is_arm_symmetric_and_a_weight_change_is_not():
    """Astra C-2: the gain act redistributes uniformly; a policy-weight change on one arm
    (which the organ never makes, S7) is exactly what S8 must refuse."""
    base = {"actions": ["a", "b", "NOOP"], "gamma": 0.1,
            "log_weights": {"a": 2.0, "b": 0.5, "NOOP": -1.0}}
    raised = dict(base, gamma=0.15)
    assert g.gain_neutral({"bases": [base]}, {"bases": [raised]}).ok
    probs_before, probs_after = g._probs(base), g._probs(raised)
    k = 3
    for p, q, w in zip(probs_before, probs_after,
                       [math.exp(2.0), math.exp(0.5), math.exp(-1.0)], strict=True):
        share = w / (math.exp(2.0) + math.exp(0.5) + math.exp(-1.0))
        assert q - p == pytest.approx(0.05 * (1 / k - share), abs=1e-12)
    steered = dict(raised, log_weights={"a": 2.0, "b": 1.5, "NOOP": -1.0})
    assert g.gain_neutral({"bases": [base]}, {"bases": [steered]}).status == g.FAIL
    uneven = g.gain_neutral({"bases": [base, base]}, {"bases": [raised, dict(base, gamma=.2)]})
    assert uneven.status == g.FAIL


# --- diaries ---------------------------------------------------------------------------------


def test_load_events_reads_a_directory_a_jsonl_file_and_a_json_list(tmp_path):
    rows = [{"kind": "immune.window", "seq": 2, "window": 1},
            {"kind": "price.window", "seq": 1, "window": 1},
            {"kind": "snapshot", "seq": 3}]
    directory = tmp_path / "open"
    directory.mkdir()
    for row in rows:
        with (directory / f"{row['kind']}.jsonl").open("a") as handle:
            handle.write(json.dumps(row) + "\n")
    got = g.load_events(directory)
    assert [r["seq"] for r in got] == [1, 2]  # ordered, the snapshot skipped
    (tmp_path / "e.json").write_text(json.dumps(rows))
    assert [r["seq"] for r in g.load_events(tmp_path / "e.json", kinds={"price.window"})] == [1]
    (tmp_path / "e.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    assert len(g.load_events(tmp_path / "e.jsonl")) == 3


def _launch(manifest=None, *, name="unit", seed=1):
    """A Launch event row as ``Runtime._launch`` ledgers it: the manifest and its hash."""
    launched = {**(manifest or M), "name": name, "seed": seed}
    return {"kind": "event", "event": {"id": "launch", "kind": "Launch", "payload": {
        "manifest": launched, "manifest_hash": g.manifest_hash(launched),
        "launch_nonce": "0" * 32}}}


def test_replay_reads_every_criterion_over_a_diary_without_a_runtime():
    rows = _seq([_launch(), _price_window(1, 0.0), _w(1, acts=True, sf=True),
                 {"kind": "price.update", "card_id": "c", "lambda_after": 0.5, "violation": 1.0,
                  "i": 0.5}])
    results = {r.name: r.status for r in g.replay(rows, M)}
    assert results["SF-0"] == g.FAIL and "SF-1c[c]" in results
    assert set(results.values()) <= {g.PASS, g.FAIL, g.UNSUPPORTED}



# --- diaries: bound to their world and seed, and well formed ----------------------------------


def test_a_diary_is_bound_to_its_launch():
    """The sweep (diaries): a criterion reads a diary only under the world, seed and
    physics its own Launch names."""
    rows = _seq([_launch(name="w", seed=7), _w(1)])
    manifest, evidence = g.bind_diary(rows, world="w", seed=7, manifest=M)
    assert evidence["world"] == "w" and evidence["seed"] == 7 and manifest["name"] == "w"
    with pytest.raises(g.DiaryInvalid, match="not 'x'"):
        g.bind_diary(rows, world="x")
    with pytest.raises(g.DiaryInvalid, match="not 8"):
        g.bind_diary(rows, seed=8)
    other = {**M, "prices": {**M["prices"], "eta": 0.25}}
    with pytest.raises(g.DiaryInvalid, match="physics"):
        g.bind_diary(rows, manifest=other)
    with pytest.raises(g.DiaryInvalid, match="0 Launch rows"):
        g.replay(_seq([_w(1)]), M)
    tampered = _seq([_launch(name="w")])
    tampered[0]["event"]["payload"]["manifest"]["seed"] = 2
    with pytest.raises(g.DiaryInvalid, match="does not hash"):
        g.bind_diary(tampered)


def test_a_malformed_diary_is_refused(tmp_path):
    """(iii) validated: every row has a kind and an integer seq, none repeats, and an
    opened diary's row sits in its kind's file."""
    (tmp_path / "rows.json").write_text(json.dumps([{"kind": "a", "seq": 1},
                                                     {"kind": "b", "seq": 1}]))
    with pytest.raises(g.DiaryInvalid, match="seq repeats"):
        g.load_events(tmp_path / "rows.json")
    (tmp_path / "rows.json").write_text(json.dumps([{"kind": "a"}]))
    with pytest.raises(g.DiaryInvalid, match="integer seq"):
        g.load_events(tmp_path / "rows.json")
    opened = tmp_path / "open"
    opened.mkdir()
    (opened / "price.window.jsonl").write_text(json.dumps({"kind": "immune.window",
                                                          "seq": 1}) + "\n")
    with pytest.raises(g.DiaryInvalid, match="another kind"):
        g.load_events(opened)
    (opened / "price.window.jsonl").unlink()
    (opened / "event_Launch.jsonl").write_text(json.dumps(
        {"kind": "event", "seq": 1, "event": {"kind": "Tick"}}) + "\n")
    with pytest.raises(g.DiaryInvalid, match="another kind"):
        g.load_events(opened)


def test_the_replay_command_refuses_an_unbound_diary(tmp_path, capsys):
    (tmp_path / "rows.json").write_text(json.dumps(_seq([_launch(name="w"), _w(1)])))
    assert g.main(["replay", str(tmp_path / "rows.json"), "--world", "v"]) == 2
    assert "refused" in capsys.readouterr().err
    assert g.main(["replay", str(tmp_path / "rows.json"), "--world", "w", "--seed", "1"]) == 0



# --- the consolidated round on 9fea1f2: Codex pass 11 and Sol's cross-family pass -----------


def _gain2(window, before, after, pathology="stable_failure", router="router:Tick"):
    """A gain row over several bases."""
    return {"kind": "immune.gain", "router": router, "window": window,
            "pathology": pathology, "gamma_before": list(before), "gamma_after": list(after)}


def _sf1e_base():
    """The passing SF-1e diary: flagged from window 3, acts every third window, the Tick
    router climbing from 0.1 to the top by window 24."""
    closes = [_w(i, acts=i % 3 == 0, sf=i >= 3) for i in range(1, 40)]
    steps = [_gain(w, round(0.1 + 0.05 * n, 2), round(0.15 + 0.05 * n, 2))
             for n, w in enumerate(range(3, 27, 3))]
    return closes, steps


def test_ca_sf1e_a_router_registered_mid_episode_is_bound_from_its_registration():
    """Codex C-a: a router first seen at window 30 of an episode that began at 3 is held
    to a bound from 30, not from 3."""
    closes, steps = _sf1e_base()
    late = [_gain(30, 0.45, 0.5, router="router:Late")]
    result = g.sf1e_gain(closes + steps + late, M)
    assert result.ok, result.evidence
    assert result.evidence["reached"]["router:Late"]["window"] == 30
    # A router registered at 21 (its first draw) that has not climbed by its own bound
    # (two steps from 0.4: 21 + 3 × 3 = 30) while the episode ran on to 39 fails.
    drawn = [{"kind": "price.window", "window": w} for w in range(1, 21)] + [
        _open("late-1", "a", actor="router:Late")]
    stuck = [_gain(33, 0.4, 0.45, router="router:Late")]
    result = g.sf1e_gain(drawn + closes + steps + stuck, M)
    assert result.status == g.FAIL
    assert any(p.get("router") == "router:Late" and p.get("begin") == 21
               for p in result.evidence["problems"])


def test_cb_sf1d_the_duration_must_reach_min_ratio_inside_the_run():
    """Codex C-b: a saturation episode that overlaps the capped run at 1-3 but reaches
    min_ratio only after it (windows 3-5) is not the run's escalation."""
    run = _windowed("c", [(0.5, 1.0, 0.5)] * 3, first=1) + _windowed(
        "c", [(0.2, 1.0, 0.2)] * 3, first=4)
    overlapping = _saturated((3, 1), (4, 2), (5, 3))
    result = g.sf1d_escalation(run + overlapping, M, card="c")
    assert result.status == g.FAIL and result.evidence["unmatched"] == [{"run": [1, 3]}]
    inside = _saturated((1, 1), (2, 2), (3, 3))
    assert g.sf1d_escalation(run + inside, M, card="c").ok


@pytest.mark.parametrize("value", [-0.1, "0.3", float("nan"), float("inf"), True, None])
def test_cc_s4_every_capped_value_is_a_finite_number_within_the_cap(value):
    """Codex C-c: a negative, non-numeric, non-finite or missing capped value fails."""
    row = _penalty("d", 1.0) | {"penalty": value}
    if value is None:
        row.pop("penalty")
    result = g.s4_boundedness([row], M)
    assert result.status == g.FAIL, result.evidence


def test_k1_s4_a_penalty_only_diary_is_checked_not_unsupported():
    """Sol K1: capped fields are evidence: a penalty over the cap in a diary with no unit
    field fails, and one within the cap passes."""
    over = {"kind": "price.penalty", "handle": "h1", "penalty": 0.9, "raw": None,
            "effective": None} | {"unresolved": ["c"]}
    assert g.s4_boundedness([over], M).status == g.FAIL
    assert g.s4_boundedness([over | {"penalty": 0.3}], M).ok


def test_a1_sf1a_measurements_k_apart_are_one_episode_as_the_kernel_reads_them():
    """Sol A1 (disagreed, with the kernel's own predicate): the tail is the last k
    windows (versions.py:82) and a card fails while every window of it that measured the
    card violated, and one did (live.py:334-347). Measurements k apart keep a violating
    measurement in every tail between them, so the kernel's failing set never drops the
    card: one episode. k + 1 apart, one tail holds none: two episodes."""
    from factorylab.versioning.live import persistent_violations

    k = g.physics(M).k

    def window(index, measured):
        return {"window": index, "profile": {"foo": 0.5} if measured else {},
                "regions": {"foo": {"kind": "min", "lo": 1.0, "hi": None, "scale": 1.0}}}

    for gap, episodes in ((k, 1), (k + 1, 2)):
        last = 2 + gap
        diary = {i: window(i, i in (2, last)) for i in range(1, last + 1)}
        # The kernel's reading at every window from the first full tail to the second
        # measurement: diary[W - k + 1 .. W].
        failing = {w: persistent_violations([diary[i] for i in range(w - k + 1, w + 1)])
                   == ["foo"] for w in range(max(k, 2), last + 1)}
        assert all(failing.values()) is (episodes == 1), failing
        assert len(g.violation_episodes({2: 1.0, last: 1.0}, k)) == episodes


def test_b1_s8_a_down_step_is_the_kernels_exactly():
    """Sol B1: immune.py:145-148 lowers γ to min(old, max(seed, old − gain_step)) (the
    floor is the router's seed γ, not 0.0): a partial step that is not onto the seed
    fails; one onto the seed passes; one with the seed out of the diary is unverified."""
    seeded = [_gain(1, 0.1, 0.1 + 0.05)]
    short = seeded + [_gain(5, 0.4, 0.38, "thrash")]
    assert g.s8_gain_rows_uniform(short, M).status == g.FAIL
    exact = seeded + [_gain(5, 0.4, 0.4 - 0.05, "thrash")]
    assert g.s8_gain_rows_uniform(exact, M).ok
    onto_seed = seeded + [_gain(6, 0.12, 0.1, "cleared")]
    assert g.s8_gain_rows_uniform(onto_seed, M).ok
    unseeded = [_gain(5, 0.4, 0.38, "thrash")]
    assert g.s8_gain_rows_uniform(unseeded, M).status == g.UNSUPPORTED


def test_c1_of2c_an_unmeasured_reference_window_is_missing_evidence():
    """Sol C1: a decision priced while window 4 was open is compared with window 3; with
    window 3 unmeasured there is no region violation to subtract, not a zero one."""
    rows = _seq([_price_window(2, 0.1), _open("d1", "registrar"),
                 _penalty("d1", 0.5, window=4, violation=0.3)])
    result = g.of2c_holdout_bites(rows, M, card="c", seats={"registrar"}, after_window=1)
    assert result.status == g.UNSUPPORTED, result.evidence


def test_d1_sf2a_a_noop_counts_only_for_a_router_that_drew_an_arm_that_window():
    """Sol D1: r1 drew both arms in window 1 and only a NOOP in window 2, where r2 drew
    the holder: window 2's non-relieving set is the holder alone (n = 1)."""
    rows = _seq([
        *_member("d1", "rel", actor="router:r1"), *_member("d2", "hold", actor="router:r1"),
        _price_window(1, 0.0),
        *_member("d3", "hold", actor="router:r2"), _open("d4", "NOOP", actor="router:r1"),
        _price_window(2, 0.0),
        _penalty("d1", 0.0, window=1), _penalty("d2", 1.0, window=1),
        _penalty("d3", 1.0, window=2)])
    result = g.sf2_gradient(rows, M, **KW)
    assert result.ok, result.evidence
    assert result.evidence["windows"] == 1  # window 2 has no reliever to compare


def test_e1_of3a_an_invocation_without_a_handle_returns_nothing():
    """Sol E1: a handle-less invocation is no return. A ProducerReturn always names its
    handle (loop.py emits it with ``about_handle``), so one naming none is a malformed
    row and fails, never a draw on nothing (Codex pass on 7c714a2)."""
    ok = [{"kind": "invocation", "handle": "p1", "seq": 1}, _returned_event("e", "p1", 2),
          {"kind": "decision.open", "handle": "j1", "event_id": "producerreturn-2", "seq": 3}]
    stray = [{"kind": "event", "seq": 12, "event": {"id": "ev1", "kind": "ProducerReturn",
                                                    "payload": {}}},
             {"kind": "decision.open", "handle": "j2", "event_id": "ev1", "seq": 13},
             {"kind": "invocation", "seq": 15}]
    result = g.of3a_sampling_behind_return(ok + stray, M)
    assert result.status == g.FAIL, result.evidence
    assert result.evidence["malformed"]["field"] == "event.payload.about_handle"
    named = [{**stray[0], "event": {**stray[0]["event"], "payload": {"about_handle": "zz"}}},
             *stray[1:]]
    result = g.of3a_sampling_behind_return(ok + named, M)
    assert result.ok and result.evidence["draws"] == 1, result.evidence


def test_g1_sf1e_reads_gamma_as_the_kernel_does_and_any_base_unwinding_fails():
    """Sol G1: γ is the first base's (immune.py:117-119), for γ₀ as for the top; a
    lowering of any base while flagged is an unwind."""
    closes = [_w(i, acts=i % 3 == 0, sf=i >= 10) for i in range(1, 31)]
    prior = [_gain2(9, [0.4, 0.35], [0.45, 0.40])]
    late_top = [_gain2(18, [0.45, 0.45], [0.5, 0.5])]
    result = g.sf1e_gain(closes + prior + late_top, M)
    assert result.status == g.FAIL  # one step from 0.45: bound 10 + 2*3 = 16 < 18
    base, steps = _sf1e_base()
    lowered = [_gain2(27, [0.5, 0.5], [0.5, 0.45], "cleared")]
    unwound = g.sf1e_gain(base + steps + lowered, M)
    assert unwound.status == g.FAIL
    assert {"router": "router:Tick", "unwound_while_flagged": 27} in \
        unwound.evidence["problems"]


def test_h1_sf1f_an_unmeasured_route_is_missing_evidence():
    """Sol H1: immune.close_window writes access:registration_route as None when unknown;
    a window without it is not a shut route."""
    unmeasured = [_w(1, profile={"registrations": 0.0}), _novelty()]
    assert g.sf1f_route_open(unmeasured, M).status == g.UNSUPPORTED
    shut = [_w(1, profile={"access:registration_route": 0.0}), _novelty()]
    assert g.sf1f_route_open(shut, M).status == g.FAIL
    open_ = [_w(1, profile={"access:registration_route": 1.0}), _w(2, profile={}),
             _novelty()]
    assert g.sf1f_route_open(open_, M).ok


def test_i1_th3_one_activation_instant_is_bound_by_its_slowest_period():
    """Sol I1: two cadence rows at one instant with different slowest periods bind it by
    the larger: 400 ns after the last instant is under 3 × 200."""
    rows = [_boundary(1000, 700, slow=100), _cadence(1000, slow=100),
            _boundary(1400, 1000, slow=100), _cadence(1400, slow=200),
            _cadence(1400, slow=100)]
    result = g.th3_governance_gap(rows, M)
    assert result.status == g.FAIL and result.evidence["bad"][0]["required"] == 600


def test_j1_of1a_a_null_y_is_a_malformed_row_not_a_pending_one():
    """Sol J1, corrected by Codex's pass on 7c714a2: the kernel writes a
    ``verdict.consequence`` row only on a measured outcome, with its float ``y``
    (feedback.py ``_settle_evaluations``: ``if state == "measured": _score_verdict``), so a
    null ``y`` is a malformed row and fails, never a reading to skip."""
    pending = [_consequence("h5", 0.6, None), _consequence("h5", 0.8, 0.7)]
    result = g.of1a_outside_the_loop(pending, M)
    assert result.status == g.FAIL and result.evidence["malformed"]["field"] == "y"
    settled = [_consequence("h5", 0.8, 0.7), _consequence("h5", 0.2, 0.7)]
    assert g.of1a_outside_the_loop(settled, M).ok


def test_l1_s5b_an_untraceable_round_is_no_routers_mean():
    """Sol L1, corrected by Codex's pass on 56cf3ff: a settled round whose decision
    names no actor joins no router's mean, and is never a pass: the kernel ledgers
    every decision's open with its actor before it can settle (queue.py:255, :279), so
    an untraced settled score fails S5b."""
    rows = [{"kind": "decision.open", "handle": "h_old2",
             "propensity": {"chosen": "seat_x"}},
            _penalty("h_old2", 0.0) | {"raw": 0.2},
            {"kind": "router.abstention_priced", "handle": "h_abs", "router": None,
             "neutral": 0.5, "penalty": 0.0, "reward": 0.5}]
    result = g.s5b_observed_neutral(rows, M)
    assert result.status == g.FAIL and result.evidence["untraced"] == 1



# --- Codex pass on b75003b ----------------------------------------------------------------


def test_sf1e_the_deadline_is_the_organs_cadence_not_the_slow_rows_own_gaps():
    """Codex P2: a router that climbs one step every 9 windows set its own deadline from
    those gaps (1 + 10 × 9); the deadline is the organ's cadence (acts every 3rd window:
    3 + 10 × 3 = 33), so reaching the top at 75 while the episode ran on fails."""
    closes = [_w(i, acts=i % 3 == 0, sf=i >= 3) for i in range(1, 90)]
    slow = [_gain(w, round(0.1 + 0.05 * n, 2), round(0.15 + 0.05 * n, 2))
            for n, w in enumerate(range(3, 84, 9))]
    result = g.sf1e_gain(closes + slow, M)
    assert result.status == g.FAIL, result.evidence
    assert result.evidence["problems"][0]["bound"] == 3 + 10 * 3


@pytest.mark.parametrize("row, ok", [
    ({"kind": "propensity.learned", "handle": "d", "reward": True}, False),
    ({"kind": "evaluator.settled", "handle": "d", "grade": False}, False),
    ({"kind": "counter.opened", "handle": "d", "q": True}, False),
    ({"kind": "verdict.mean", "handle": "d", "score": True}, False),
    ({"kind": "policy.outcome", "handle": "d", "y": False, "q": 0.5, "score": 0.25}, True),
])
def test_s4_a_boolean_passes_only_where_the_kernel_writes_a_boolean_outcome(row, ok):
    """Codex P2: a reward, grade, probability or score that is a boolean fails; a
    motion's kept promise (policy.outcome's y) is a boolean outcome by the kernel."""
    assert g.s4_boundedness([row], M).status == (g.PASS if ok else g.FAIL)


def test_s8_a_gamma_below_zero_fails_whatever_the_seed():
    """Codex P2: the lower bound is checked too: γ below 0 fails outright; a lowering with
    the seed unknown is unsupported, never a pass."""
    negative = [_gain(1, 0.1, 0.1 + 0.05), _gain(3, 0.02, -0.03, "thrash")]
    assert g.s8_gain_rows_uniform(negative, M).status == g.FAIL
    assert g.s8_gain_rows_uniform([_gain(3, 0.02, -0.03, "thrash")], M).status == g.FAIL
    assert g.s8_gain_rows_uniform([_gain(3, 0.3, 0.25, "cleared")], M).status == \
        g.UNSUPPORTED



def test_sf1e_a_routers_own_loop_is_read_from_its_rounds_not_its_gain_rows():
    """The kernel steps a router's gain at most once per min_ratio of its measured round
    periods (immune._gain, time audit T2): a router whose rounds settle 3 windows after
    they open is allowed 3 x 3 = 9 windows a step, read from those rounds."""
    closes = [_w(i, acts=i % 3 == 0, sf=i >= 3) for i in range(1, 120)]
    rounds = []
    for w in range(1, 118):
        rounds.append({"kind": "price.window", "window": w})
        if w % 5 == 0:
            rounds.append(_open(f"r{w}", "a", actor="router:Slow"))
        if w % 5 == 3 and w > 5:
            rounds.append({"kind": "decision.settle", "return": {"handle": f"r{w - 3}"}})
    assert g.router_round_periods(rounds)["router:Slow"] == 3
    climb = [_gain(w, round(0.1 + 0.05 * n, 2), round(0.15 + 0.05 * n, 2),
                   router="router:Slow") for n, w in enumerate(range(3, 84, 9))]
    assert g.sf1e_gain(rounds + closes + climb, M).ok
    assert g.sf1e_gain(closes + climb, M).status == g.FAIL  # no rounds: the organ's cadence


# --- Codex pass on 11ea116 ----------------------------------------------------------------


@pytest.mark.parametrize("row, ok", [
    # Required: always written by every emitter of the kind.
    ({"kind": "propensity.learned", "handle": "d"}, False),
    ({"kind": "verdict.mean", "handle": "d", "score": None}, False),
    ({"kind": "counter.opened", "handle": "d", "q": 0.5}, False),
    ({"kind": "price.penalty", "handle": "d", "penalty": 0.1, "effective": 0.4}, False),
    # Nullable, only under the condition the kernel writes None.
    ({"kind": "price.penalty", "handle": "d", "penalty": 0.1, "raw": None,
      "effective": None}, False),
    ({"kind": "exposure.settled", "handle": "d", "score": None}, True),
    ({"kind": "counter.settled", "handle": "d", "score": None}, True),
    ({"kind": "counter.settled", "handle": "d", "q": 0.5, "judge_q": 0.5, "y": 0.5},
     False),
    ({"kind": "counter.settled", "handle": "d", "q": 0.5, "judge_q": 0.5, "score": 0.5},
     False),
    ({"kind": "policy.outcome", "handle": "d", "q": 0.5, "y": None, "score": 0.0,
      "status": "censored"}, True),
    ({"kind": "policy.outcome", "handle": "d", "q": 0.5, "y": None, "score": 0.0,
      "status": "settled"}, False),
    ({"kind": "evaluator.settled", "handle": "d", "grade": None, "consequence": None,
      "reward": None}, True),
    ({"kind": "evaluator.settled", "handle": "d", "grade": 0.5, "consequence": None,
      "reward": None}, False),
])
def test_s4_a_missing_field_fails_unless_the_kernel_writes_it_as_none(row, ok):
    """Codex P2 (gauntlet.py:1772): a required field that is absent or None fails S4;
    only a field the kernel writes as None, under the condition it does, may be."""
    assert g.s4_boundedness([row], M).status == (g.PASS if ok else g.FAIL)


def test_sf1e_every_present_router_is_judged_not_only_those_with_gain_rows():
    """Codex P2 (gauntlet.py:830): the routers are read from ``router_presence``. A
    router that drew decisions through a flagged episode long enough for a step, and has
    no gain row, is unsupported: the kernel writes no row for a router already at
    gamma_max (immune.py ``_gain``: ``if before == after: continue``), so its γ is
    unobserved, never a pass. An ``assembly:`` actor is no router."""
    closes, steps = _sf1e_base()
    assert g.sf1e_gain(closes + steps, M).ok
    other = g.sf1e_gain([_open("d-o", "a", actor="router:Other"), *closes, *steps], M)
    assert other.status == g.UNSUPPORTED, other.evidence
    assert other.evidence["stateless"][0]["router"] == "router:Other"
    seat = [_open("d-s", "a", actor="assembly:committee"), *closes, *steps]
    assert g.sf1e_gain(seat, M).ok
    # No gain row at all, the organ acting while flagged: unobserved, not proven failed.
    alone = g.sf1e_gain([_open("d-t", "a"), *closes], M)
    assert alone.status == g.UNSUPPORTED
    assert g.sf1e_gain(closes, M).status == g.UNSUPPORTED


def test_sf1e_a_router_replaced_before_its_bound_is_no_evidence():
    """A replaced router leaves ``_all_router_states`` (routing.py ``_build_router``), so
    the kernel stops stepping it: it is not failed for the bound it could not reach."""
    closes = [_w(i, acts=i % 3 == 0, sf=i >= 3) for i in range(1, 40)]
    stuck = [_gain(3, 0.1, 0.15)]
    assert g.sf1e_gain(closes + stuck, M).status == g.FAIL
    replaced = [{"kind": "price.window", "window": 5},
                {"kind": "router.created", "learner_id": "router:Tick#1",
                 "event_kind": "Tick", "replaces": ["router:Tick"]}]
    result = g.sf1e_gain(replaced + closes + stuck, M)
    assert result.status == g.UNSUPPORTED, result.evidence
    assert result.evidence["resolved"] >= 1


def test_ld1e_a_flag_after_the_quarantine_cleared_is_not_detection():
    """Codex P2 (gauntlet.py:1451): router r1 quarantined in windows 1-4 (a k-tail), then
    invoked again; a flag naming it at window 6, inside H of the run's start but after
    the run cleared, does not detect the run: the run failed. A flag inside the run
    does."""
    r1 = {"router": "router:r1", "quarantined": True, "core": False}
    closes = [_w(i, ld=i == 6, frontier=[r1] if i <= 4 else []) for i in range(1, 15)]
    closes[5]["frontier"] = {"quarantined_routers": ["router:r1"], "uninvoked_routers": []}
    assert g.physics(M).H > 6
    result = g.ld1e_detection(closes, M)
    assert result.status == g.FAIL, result.evidence
    assert result.evidence["late"][0][:2] == (1, 4)
    inside = [_w(i, ld=i == 4, frontier=[r1] if i <= 4 else []) for i in range(1, 15)]
    assert g.ld1e_detection(inside, M).ok


@pytest.mark.parametrize("drop", ["propensity", "rng_seed", "probs", "action_ids", "chosen"])
def test_s1_a_decision_missing_a_required_propensity_field_fails(drop):
    """The same pattern as S4 (Codex pass on 11ea116): ``Decision.propensity`` and every
    field of it are required by the kernel, so a decision row without one is malformed
    and fails S1, never skipped as unsampled."""
    good = _open("decision-1", "a")
    bad = json.loads(json.dumps(good))
    if drop == "propensity":
        del bad["propensity"]
    else:
        del bad["propensity"][drop]
    assert g.s1_draw_sovereignty([good]).ok
    result = g.s1_draw_sovereignty([good, bad])
    assert result.status == g.FAIL
    assert result.evidence["bad_draws"] == ["decision-1"]


# --- Codex pass on 144323c ----------------------------------------------------------------


def test_replay_runs_the_thrash_antiwindup_and_every_card_and_loop_the_diary_names():
    """Codex P2 (gauntlet.py:2090, :2128): TH-1b's anti-windup and OF-2d are replayed;
    the per-card criteria run over every card any row names (the thrash price's own, a
    card only ratcheted, a card only measured), and TH-2 over every refactored loop."""
    region = {"kind": "min", "lo": 0.2, "hi": None, "scale": 0.2}
    rows = _seq([_launch(), _w(1, acts=True, sf=True),
                 *_updates("pathology:thrash", [(0.5, 1.0, 0.5), (0.5, 1.0, 0.5)]),
                 {"kind": "immune.price_ratchet", "card_id": "r", "window": 1,
                  "duration": 1, "lambda_after": 1.0},
                 {"kind": "price.window", "window": 1, "values": {"v": 0.0},
                  "regions": {"v": region}},
                 {"kind": "config.lifespan", "loop": "price", "ratio": 0.5, "tick": 3}])
    names = {r.name for r in g.replay(rows, M)}
    assert {"TH-1b-antiwindup", "OF-2d", "TH-2[price]"} <= names
    assert {"SF-1c[pathology:thrash]", "SF-1a[r]", "SF-1a[v]", "SF-1a[c]"} <= names


def test_sf1b_reads_every_card_and_a_known_violated_card_never_ratcheted_fails():
    """The sweep: SF-1b's cards are every card the diary names. The organ ratchets every
    violated card it knows at a flagged acting window (immune.close_window), so a
    registered card named violated there with no ratchet is a missed ratchet, even when
    no ratchet was issued at all; a card the controller never knew is not."""
    closes = [_w(i, acts=i % 3 == 0, sf=True) for i in range(1, 13)]
    known = [{"kind": "price.register", "card_id": "c"}]
    assert g.sf1b_ratchet_cadence(closes, M).status == g.UNSUPPORTED
    missed = g.sf1b_ratchet_cadence(known + closes, M)
    assert missed.status == g.FAIL
    assert missed.evidence["problems"][0] == {"card": "c", "window": 3,
                                              "missed_ratchet": True}
    both = [_w(i, acts=i % 3 == 0, sf=True, violated=["card:c", "card:d"])
            for i in range(1, 13)]
    ratchets = _ratchets((3, 1), (6, 2), (9, 3), (12, 4))
    assert g.sf1b_ratchet_cadence(known + both + ratchets, M).ok
    d_known = [*known, {"kind": "price.register", "card_id": "d"}]
    result = g.sf1b_ratchet_cadence(d_known + both + ratchets, M)
    assert result.status == g.FAIL
    assert {p["card"] for p in result.evidence["problems"]} == {"d"}


def test_sf1e_a_router_the_diary_names_only_at_its_creation_is_judged():
    closes, steps = _sf1e_base()
    created = {"kind": "router.created", "learner_id": "router:Quiet", "event_kind": "Quiet",
               "replaces": []}
    result = g.sf1e_gain([created, *closes, *steps], M)
    assert result.status == g.UNSUPPORTED, result.evidence
    assert result.evidence["stateless"][0]["router"] == "router:Quiet"


# --- Codex pass on 7c714a2: missing evidence is never a value ------------------------------


@pytest.mark.parametrize("row, ok", [
    # An explicit null where the kernel writes one: allowed.
    ({"kind": "evaluator.settled", "handle": "d", "grade": None, "consequence": None,
      "reward": None}, True),
    ({"kind": "exposure.settled", "handle": "d", "score": None}, True),
    ({"kind": "composed.settled", "handle": "d", "verdict": None, "reward": 0.4}, True),
    # The same fields absent: the kernel always writes them, so absence fails.
    ({"kind": "evaluator.settled", "handle": "d", "consequence": None, "reward": None},
     False),
    ({"kind": "exposure.settled", "handle": "d"}, False),
    ({"kind": "composed.settled", "handle": "d", "reward": 0.4}, False),
    # The censored counter leaves out q, judge_q and y beside an explicit null score;
    # without the score key it is no emitter's row.
    ({"kind": "counter.settled", "handle": "d", "score": None}, True),
    ({"kind": "counter.settled", "handle": "d"}, False),
])
def test_s4_an_absent_field_is_not_an_explicit_null(row, ok):
    """Codex P2 (gauntlet.py:2012): absence is allowed only where an emitter leaves the
    field out (``OMITTED``), an explicit null only where one writes it (``NULLABLE``)."""
    assert g.s4_boundedness([row], M).status == (g.PASS if ok else g.FAIL)


@pytest.mark.parametrize("drop", ["thrash", "thrash.lambda", "thrash.penalty", "flags"])
def test_th1e_a_window_missing_its_thrash_price_fails_never_reads_zero(drop):
    """Codex P2 (gauntlet.py:1224): immune.py ``close_window`` ledgers ``"thrash":
    rt.stats.thrash`` on every window, and ``thrash_penalty`` always returns its
    ``lambda`` and ``penalty``: a window without them is malformed, never a zero price
    that "released" the cycle."""
    closes = [_w(i, thrash=i < 10, lam=0.5) for i in range(1, 25)]
    assert g.th1e_release(closes, M, steady_from=8).status == g.FAIL  # never released
    head, _, leaf = drop.partition(".")
    for w in closes[10:]:
        if leaf:
            del w[head][leaf]
        else:
            del w[head]
    result = g.th1e_release(closes, M, steady_from=8)
    assert result.status == g.FAIL and result.evidence["malformed"]["field"].startswith(
        head), result.evidence
    assert g.th1b_duration(closes, M).status == g.FAIL


def test_a_window_missing_an_always_written_field_fails_every_criterion_that_reads_it():
    """The sweep: the organ's window always carries its flags, acts, violated cards and
    frontier evidence (versions.py ``diagnose``, immune.py ``close_window``); a
    criterion that reads one from a window lacking it fails, naming it."""
    row = {"router": "router:r1", "quarantined": True, "core": False}
    base = [_w(i, acts=i % 3 == 0, sf=i >= 3, ld=i >= 5, frontier=[row])
            for i in range(1, 15)]
    for field, fn in (("flags", g.ld1e_detection), ("acts", g.sf1b_ratchet_cadence),
                      ("violated_cards", g.sf1b_ratchet_cadence),
                      ("frontier_invocation", g.ld1e_detection),
                      ("frontier", g.ld1e_detection)):
        rows = json.loads(json.dumps(base))
        del rows[6][field]
        result = fn(rows, M)
        assert result.status == g.FAIL, (field, result.evidence)
        assert result.evidence["malformed"]["field"].split(".")[0] == field


# --- Codex pass on b1e3bae: an observed violation fails, whatever else is missing ------


def test_s1_an_orphaned_act_fails_with_no_sampled_decision_at_all():
    """Codex P1 (gauntlet.py:1981): an act no seat's return traces to is the kernel acting
    for a seat; with no sampled decision to replay it still fails. Only a diary with
    neither draws nor acts is unsupported."""
    orphan = [{"kind": "order.intent", "handle": "system-generated"}]
    result = g.s1_draw_sovereignty(orphan)
    assert result.status == g.FAIL and result.evidence["unreturned"]
    assert g.s1_draw_sovereignty([]).status == g.UNSUPPORTED


def test_sf1d_a_broken_saturation_count_fails_before_any_sustained_run():
    broken = _saturated((5, 1), (6, 3))
    result = g.sf1d_escalation(broken, M, card="c")
    assert result.status == g.FAIL and result.evidence["malformed"]
    assert g.sf1d_escalation(_saturated((5, 1), (6, 2)), M,
                             card="c").status == g.UNSUPPORTED


def test_sf1f_a_wrong_accrual_or_a_shut_route_fails_with_thin_evidence():
    wrong = [_novelty(amount=1)]
    result = g.sf1f_route_open(wrong, M)
    assert result.status == g.FAIL
    assert [f["name"] for f in result.evidence["failed"]] == ["LD-1a"]
    shut = [_w(1, profile={"access:registration_route": 0.0})]
    assert g.sf1f_route_open(shut, M).status == g.FAIL


def test_th2_a_refusal_for_speed_fails_with_no_lifespan_row():
    refused = [{"kind": "registration.rejected", "reason": "too fast"}]
    result = g.th2_short_lived(refused, M, loop="price")
    assert result.status == g.FAIL and result.evidence["speed_refusals"] == 1
    assert g.th2_short_lived([], M, loop="price").status == g.UNSUPPORTED


def test_ld1d_one_penalized_niche_decision_fails_below_the_minimum():
    thin = [{"kind": "niche.action", "handle": "n1"}, _penalty("n1", 0.4)]
    result = g.ld1d_exemption(thin, M)
    assert result.status == g.FAIL and result.evidence["penalized"] == ["n1"]
    free = [{"kind": "niche.action", "handle": "n1"}, _penalty("n1", 0.0)]
    assert g.ld1d_exemption(free, M).status == g.UNSUPPORTED


def _thin_violations():
    """For every replayed criterion, the thinnest diary that holds one violation of it
    and nothing else a pass would need. SF-1a and LD-1e need their horizon to show a
    missed detection at all (a violation there is a deadline passed), so theirs run it."""
    niche = [{"kind": "niche.action", "handle": "n1"}, _penalty("n1", 0.4)]
    quarantined = {"router": "router:r1", "quarantined": True, "core": False}
    cleared = [_w(i, frontier=[quarantined] if i <= 3 else []) for i in range(1, 5)]
    return {
        "SF-1a": (g.sf1a_detection, [*(_price_window(i, 0.0) for i in range(1, 15)),
                                     *(_w(i) for i in range(1, 15))], {"card": "c"}),
        "SF-1b": (g.sf1b_ratchet_cadence, [_w(3, acts=True), *_ratchets((3, 1))], {}),
        "SF-1c": (g.sf1c_anti_windup, _updates("c", [(0.5, 1.0, 0.5), (0.5, 1.0, 0.6)]),
                  {"card": "c"}),
        "SF-1d": (g.sf1d_escalation, _saturated((5, 1), (6, 3)), {"card": "c"}),
        "SF-1e": (g.sf1e_gain, [_w(1, sf=True), _gain(1, 0.2, 0.15, "cleared")], {}),
        "SF-1f": (g.sf1f_route_open, [_novelty(amount=1)], {}),
        "SF-2b": (g.sf2b_order_blind, [_penalty("a", 0.5), _penalty("b", 0.25)],
                  {"card": "c"}),
        "LD-1a": (g.ld1a_accrual, [_novelty(amount=1)], {}),
        "LD-1d": (g.ld1d_exemption, niche, {}),
        "LD-1e": (g.ld1e_detection, cleared, {}),
        "LD-1f": (g.ld1f_hold, [_w(1, ld=True), _gain(1, 0.2, 0.15, "cleared")], {}),
        "TH-1b": (g.th1b_duration, [_w(1, lam=0.3), _w(2, thrash=True, lam=0.2)], {}),
        "TH-1b-antiwindup": (g.th1b2_frozen, _updates("pathology:thrash",
                                                      [(0.5, 1.0, 0.5), (0.5, 1.0, 0.6)]),
                             {}),
        "TH-1c": (g.th1c_movement, [{"kind": "thrash.charged", "handle": "x",
                                     "router": "router:Tick", "charge": 0.1}], {}),
        "TH-1d": (g.th1d_frontier, [{"kind": "thrash.charged", "handle": "x",
                                     "router": "router:WorldUpdate", "charge": 0.1}], {}),
        "TH-1f": (g.th1f_priority, [_w(1, thrash=True, sf=True), _gain(1, 0.1, 0.15)], {}),
        "TH-2": (g.th2_short_lived, [{"kind": "registration.rejected", "reason": "too fast"}],
                 {"loop": "price"}),
        "TH-3": (g.th3_governance_gap, [_cadence(30), _cadence(31)], {}),
        "OF-1a": (g.of1a_outside_the_loop, [_consequence("r", 0.5, 0.2),
                                            _consequence("r", 0.5, 0.3)], {}),
        "OF-2d": (g.of2d_authorship, [{"kind": "holdout.proposed", "card_id": "c"}], {}),
        "OF-3a": (g.of3a_sampling_behind_return,
                  [_returned_event("e", "p1", 2), {"kind": "decision.open", "handle": "j1",
                                                   "event_id": "producerreturn-2", "seq": 3},
                   {"kind": "invocation", "handle": "p1", "seq": 4}], {}),
        "I-3c": (g.i3c_niche_no_worse_than_noop,
                 [_open("n1", "seat"), _open("z1", "NOOP"), *niche,
                  {"kind": "router.abstention_priced", "handle": "z1", "penalty": 0.1,
                   "reward": 0.4, "neutral": 0.5}], {}),
        "I-4a": (g.i4a_no_blind_step_back,
                 [{"kind": "sampling.lower", "outcome_slope": None, "verdict_slope": None}],
                 {}),
        "S1": (g.s1_draw_sovereignty, [{"kind": "order.intent", "handle": "x"}], {}),
        "S4": (g.s4_boundedness, [{"kind": "propensity.learned", "handle": "d",
                                   "reward": 1.5}], {}),
        "S5": (g.s5_neutral_imputation, [{"kind": "router.abstention_priced", "handle": "d",
                                          "router": "router:Tick", "neutral": 0.5,
                                          "penalty": 0.2, "reward": 0.5}], {}),
        "S5b": (g.s5b_observed_neutral,
                [_open("d0", "seat"), _penalty("d0", 0.0) | {"raw": 0.3},
                 {"kind": "router.abstention_priced", "handle": "d", "router": "router:Tick",
                  "neutral": 0.5, "penalty": 0.2, "reward": 0.3}], {}),
        "S7": (g.s7_gain_targets, [_gain(1, 0.1, 0.15, router="learner:seat")], {}),
        "S8": (g.s8_gain_rows_uniform, [_gain(3, 0.02, -0.03, "thrash")], {}),
    }


def test_every_replayed_criterion_fails_on_a_thin_diary_with_one_violation():
    """Codex P1 (b1e3bae), the general rule: an observed violation is FAIL whatever other
    evidence is missing; ``unsupported`` is only for no relevant evidence and no
    violation. Every replayed criterion is exercised by a diary holding one violation and
    as little else as the violation allows."""
    cases = _thin_violations()
    replayed = {*g.GENERIC, *g.PER_CARD, *g.PER_LOOP}
    assert replayed <= set(cases), sorted(replayed - set(cases))
    results = {name: fn(rows, M, **kw) for name, (fn, rows, kw) in cases.items()}
    assert {n: r.status for n, r in results.items() if r.status != g.FAIL} == {}
    # Each fails on its violation, never on a malformed fixture row.
    assert [n for n, r in results.items()
            if isinstance(r.evidence.get("malformed"), dict)] == []


# --- Codex pass on 5ba444a ----------------------------------------------------------------


def test_a_card_whose_id_starts_with_card_keeps_its_id_through_replay():
    """Codex P2 (gauntlet.py:574): only the organ's fields prefix a card id with
    ``card:`` (immune.py:303), so only there is one prefix removed. A card registered as
    ``card:latency`` keeps that id, and its per-card criteria read its own rows."""
    cid = "card:latency"
    region = {"kind": "min", "lo": 0.2, "hi": None, "scale": 0.2}
    rows = [_launch(), {"kind": "price.register", "card_id": cid}]
    for i in range(1, 15):
        rows.append({"kind": "price.window", "window": i, "values": {cid: 0.0},
                     "regions": {cid: region}})
        close = _w(i, sf=i >= 4, violated=[f"card:{cid}"])
        close["regions"] = {f"card:{cid}": region}
        rows.append(close)
    rows += _updates(cid, [(0.5, 1.0, 0.5), (0.5, 1.0, 0.5)])
    rows = _seq(rows)
    assert g.diary_cards(rows) == [cid]
    results = {r.name: r for r in g.replay(rows, M)}
    assert "SF-1a[latency]" not in results
    assert results[f"SF-1a[{cid}]"].ok, results[f"SF-1a[{cid}]"].evidence
    assert results[f"SF-1c[{cid}]"].ok, results[f"SF-1c[{cid}]"].evidence


# --- Codex pass on 1de5c37 ----------------------------------------------------------------


@pytest.mark.parametrize("change, why", [
    ({"probs": [-0.1, 1.1]}, "finite number in [0, 1]"),
    ({"probs": [float("nan"), 0.5]}, "finite number in [0, 1]"),
    ({"probs": [True, False]}, "finite number in [0, 1]"),
    ({"action_ids": ["a", "a"]}, "repeats"),
    ({"probs": [1.0]}, "differ in length"),
    ({"chosen": "z"}, "outside the support"),
    ({"probs": [0.5, 0.5 + 1e-9]}, "sum to 1"),
    ({"rng_seed": 1.5}, "not an integer"),
    ({"rng_seed": True}, "not an integer"),
    ({"action_ids": []}, "no action support"),
])
def test_s1_a_propensity_breaking_its_contract_fails_as_malformed(change, why):
    """Codex P2 (gauntlet.py:2011): the whole ``PropensityRecord`` contract is checked
    before a draw is replayed (queue.py ``validate``, sum within ``abs_tol=1e-12``); a
    row that breaks it fails as malformed, never raises and never passes."""
    good = _open("d1", "a")
    assert g.s1_draw_sovereignty([good]).ok
    row = json.loads(json.dumps(good))
    row["propensity"].update(change)
    result = g.s1_draw_sovereignty([row])
    assert result.status == g.FAIL, result.evidence
    assert why in result.evidence["malformed_propensities"][0]["why"]


# --- Codex pass on 4024237: an act follows its decision and the seat's own return ------


@pytest.mark.parametrize("rows, ok", [
    # An answer's act: after the decision's invocation returned ok.
    ([("open",), ("invocation", "ok"), ("act",)], True),
    # A later invocation alone does not make an earlier act the seat's.
    ([("open",), ("act",), ("invocation", "ok")], False),
    # A failed or malformed invocation before it does not either.
    ([("open",), ("invocation", "failed"), ("act",)], False),
    ([("open",), ("invocation", "malformed"), ("act",)], False),
    # An act before any decision was opened for it.
    ([("act",), ("open",), ("invocation", "ok")], False),
    # A tool call's act: inside the invocation, recorded by its tool.call row, whatever
    # the final answer's status (compute.py ``_run_tool`` -> ``_venue_write``).
    ([("open",), ("act",), ("tool.call", "venue.place_market"),
      ("invocation", "malformed")], True),
    # The call after it must be the write tool that ledgers the act (``ACT_TOOLS``): a
    # read-only or unrelated call never traces it, nor does a write after a read.
    ([("open",), ("act",), ("tool.call", "catalogue.search"), ("invocation", "ok")], False),
    ([("open",), ("act",), ("tool.call", "treasury.transfer"), ("invocation", "ok")], False),
    ([("open",), ("act",), ("tool.call", "catalogue.search"),
      ("tool.call", "venue.place_market"), ("invocation", "ok")], False),
    # A tool.call after the invocation closed is not the call that made it.
    ([("open",), ("act",), ("invocation", "ok"), ("tool.call", "venue.place_market")],
     False),
])
def test_s1_an_act_follows_its_decision_and_the_seats_own_return(rows, ok):
    """Codex P2 (gauntlet.py:2057): an act traces only in ledger order: after a
    ``decision.open`` for its handle and either after that decision's ok invocation (an
    answer's act) or inside that invocation, before its ``tool.call`` row (a tool call's
    act). A later, failed or malformed invocation does not trace it."""
    build = {"open": lambda: _open("d1", "a"),
             "act": lambda: {"kind": "order.intent", "handle": "d1"},
             "tool.call": lambda tool: {"kind": "tool.call", "handle": "d1", "tool": tool},
             "invocation": lambda status: {"kind": "invocation", "handle": "d1",
                                           "status": status}}
    diary = _seq([build[kind](*rest) for kind, *rest in rows])
    assert g.s1_draw_sovereignty(diary).status == (g.PASS if ok else g.FAIL)


# --- Codex pass on 3050436 ----------------------------------------------------------------


def test_s8_instrumented_the_arms_and_their_order_are_part_of_what_gain_must_not_touch():
    """Codex P2 (gauntlet.py:2449): the same weights over other arms, or the same arms in
    another order, are a changed policy, not a γ step."""
    base = {"actions": ["a", "b"], "gamma": 0.1, "log_weights": [1.0, 1.0]}
    assert g.gain_neutral({"bases": [base]}, {"bases": [dict(base, gamma=0.15)]}).ok
    swapped = dict(base, actions=["a", "c"], gamma=0.15)
    result = g.gain_neutral({"bases": [base]}, {"bases": [swapped]})
    assert result.status == g.FAIL and "actions_changed" in result.evidence["problems"][0]
    reordered = dict(base, actions=["b", "a"], gamma=0.15)
    assert g.gain_neutral({"bases": [base]}, {"bases": [reordered]}).status == g.FAIL


@pytest.mark.parametrize("synthetic", [(0, 0), (1, 0), (-1, 10), (11, 10), (0.5, 10),
                                       (True, 10), (10, 10)])
def test_th4_a_degenerate_synthetic_null_is_unsupported_never_a_pass(synthetic):
    """Codex P2 (gauntlet.py:1624): a null of no windows, invalid counts, or one that
    flags every window (a bound of 1.0 no rate can exceed) is no evidence: TH-4 is
    unsupported, never a pass. The sweep: TH-4 is the gauntlet's one binomial bound."""
    quiet = [_w(i, thrash=False) for i in range(1, 40)]
    assert g.th4_null(quiet, M, synthetic=synthetic).status == g.UNSUPPORTED
    with pytest.raises(ValueError):
        g.clopper_pearson_upper(0, 0)


# --- Codex pass on 47c5929 ----------------------------------------------------------------


@pytest.mark.parametrize("statuses, expected", [
    ([g.PASS, g.PASS], g.PASS),
    ([g.PASS, g.UNSUPPORTED], g.UNSUPPORTED),
    ([g.UNSUPPORTED, g.FAIL], g.FAIL),
    ([g.PASS, g.FAIL], g.FAIL),
    ([g.UNSUPPORTED], g.UNSUPPORTED),
    ([], g.UNSUPPORTED),
])
def test_an_aggregate_passes_only_when_every_component_passes(statuses, expected):
    """Codex P2 (populations.py:479): one rule for every aggregation: FAIL if any
    component fails, PASS only when every component passes and one did, otherwise
    UNSUPPORTED (an unsupported component is never read as a pass)."""
    parts = [g.Result(f"c{i}", s, {}) for i, s in enumerate(statuses)]
    assert g.aggregate("agg", parts).status == expected


def test_the_instrumented_s8_aggregate_keeps_an_unsupported_gain_act():
    """An unsupported gain_neutral (a router that saved no base) is not dropped: the
    instrumented S8 reading is unsupported, never a pass."""
    from types import SimpleNamespace

    from tests.gauntlet import populations as P

    base = {"actions": ["a", "b"], "gamma": 0.1, "log_weights": [1.0, 1.0]}
    good = {"router": "router:Tick", "window": 1, "before": {"bases": [base]},
            "after": {"bases": [dict(base, gamma=0.15)]}}
    empty = {"router": "router:Tick", "window": 2, "before": {"bases": []},
             "after": {"bases": []}}
    run = SimpleNamespace(events=[], manifest=M, requests=[], closes=[], gains=[good])
    assert P.assert_prices_not_steers(run)["S8-instrumented"].ok
    run.gains = [good, empty]
    assert P.assert_prices_not_steers(run)["S8-instrumented"].status == g.UNSUPPORTED


def test_s5_a_row_missing_a_field_fails_as_malformed_never_raises():
    """Codex P2 (gauntlet.py:2329): a field read goes through need(), so a priced
    abstention with no reward fails S5 naming the field instead of raising KeyError."""
    row = {"kind": "router.abstention_priced", "handle": "d", "router": "router:Tick",
           "neutral": 0.5, "penalty": 0.2}
    result = g.s5_neutral_imputation([row], M)
    assert result.status == g.FAIL and result.evidence["malformed"]["field"] == "reward"


# --- Codex pass on 56cf3ff ----------------------------------------------------------------


def test_s5b_an_untraced_settled_score_is_never_a_pass():
    """Codex P2 (gauntlet.py:2483): an untraced settled score beside a matching mean
    used to pass with a counter; it fails."""
    rows = [_open("s0", "seat"), _penalty("s0", 0.0) | {"raw": 0.3},
            _penalty("orphan", 0.0) | {"raw": 0.9},
            {"kind": "router.abstention_priced", "handle": "z", "router": "router:Tick",
             "neutral": 0.3, "penalty": 0.0, "reward": 0.3}]
    result = g.s5b_observed_neutral(rows, M)
    assert result.status == g.FAIL and result.evidence["untraced"] == 1
    assert g.s5b_observed_neutral([r for r in rows if r.get("handle") != "orphan"], M).ok


def test_a_criterion_that_raises_fails_and_replay_reports_the_rest(monkeypatch):
    """Codex P2 (gauntlet.py:2514), the class: any exception inside a criterion is a
    FAIL naming its type and message; replay reports every criterion after it."""
    @g.criterion("RAISES")
    def raises(events, manifest):
        raise RuntimeError("no reading")

    result = raises([], M)
    assert result.status == g.FAIL
    assert (result.evidence["error"], result.evidence["message"]) == ("RuntimeError",
                                                                      "no reading")
    generic = dict(g.GENERIC)
    monkeypatch.setattr(g, "GENERIC", {"RAISES": raises, **generic})
    results = {r.name: r for r in g.replay(_seq([_launch(), _w(1)]), M)}
    assert results["RAISES"].status == g.FAIL
    assert set(generic) <= set(results)  # every criterion after it still reported


def test_s8_gamma_vectors_of_different_lengths_fail_with_a_clear_reason():
    """S8 validates the vectors before pairing them: a row whose before and after differ
    in length fails naming it, and the other rows are still read."""
    uneven = {"kind": "immune.gain", "router": "router:Tick", "window": 2,
              "pathology": "stable_failure", "gamma_before": [0.1, 0.1], "gamma_after": [0.15]}
    result = g.s8_gain_rows_uniform([_gain(1, 0.1, 0.15), uneven], M)
    assert result.status == g.FAIL
    assert result.evidence["uneven"] == [{"router": "router:Tick", "window": 2,
                                          "gamma_before": 2, "gamma_after": 1}]
    names = {r.name: r for r in g.replay(_seq([_launch(), _w(1), uneven]), M)}
    assert names["S8"].status == g.FAIL and "error" not in names["S8"].evidence
    assert names["LD-1f"].status in (g.PASS, g.FAIL, g.UNSUPPORTED)


# --- Codex pass on 5c977be: false fails at valid boundaries ---------------------------------


@pytest.mark.parametrize("before, after", [(1.0, 0.95), (0.95, 1.0), (1.0, 1.0)])
def test_s8_instrumented_a_gamma_step_at_one_is_symmetric(before, after):
    """Codex P2 (gauntlet.py:2615): at γ = 1 (valid when gamma_max = 1) the prior is
    uniform whatever the weights; the after-distribution is read from the preserved
    weights with the kernel's mixing (exp3.py:42), so a valid step is not asymmetric."""
    base = {"actions": ["a", "b", "NOOP"], "gamma": before,
            "log_weights": {"a": 2.0, "b": 0.5, "NOOP": -1.0}}
    assert g.gain_neutral({"bases": [base]}, {"bases": [dict(base, gamma=after)]}).ok
    steered = dict(base, gamma=after, log_weights={"a": 2.0, "b": 1.5, "NOOP": -1.0})
    assert g.gain_neutral({"bases": [base]}, {"bases": [steered]}).status == g.FAIL


def test_th1b_a_price_with_no_room_to_rise_is_not_a_failure():
    """The sweep: a flagged run whose price sits at lambda_max, or whose violation is 0
    (a short-lived configuration inside ``tv_threshold``), holds its price validly: TH-1b
    is unsupported there. With room and a positive violation, holding still fails."""
    at_max = [_w(1, lam=1.0),
              *[_w(i, thrash=True, lam=1.0, unsettled=1.0) for i in range(2, 6)]]
    assert g.th1b_duration(at_max, M).status == g.UNSUPPORTED
    inside = [_w(1, lam=0.2),
              *[_w(i, thrash=True, lam=0.2, unsettled=0.1) for i in range(2, 6)]]
    assert g.th1b_duration(inside, M).status == g.UNSUPPORTED
    held = [_w(1, lam=0.2), *[_w(i, thrash=True, lam=0.2, unsettled=1.0) for i in range(2, 6)]]
    assert g.th1b_duration(held, M).status == g.FAIL
    rising = [_w(1, lam=0.2),
              *[_w(i, thrash=True, lam=0.2 + 0.1 * i, unsettled=1.0) for i in range(2, 6)]]
    assert g.th1b_duration(rising, M).ok


def test_sf1e_a_router_seeded_at_gamma_max_writes_no_gain_row_and_is_not_failed():
    """The sweep: seeded at gamma_max the router needs no step and the kernel writes no
    gain row (immune.py ``_gain``: ``before == after``): unsupported, never a failure."""
    closes = [_w(i, acts=i % 3 == 0, sf=i >= 3) for i in range(1, 40)]
    result = g.sf1e_gain([_open("d1", "a"), *closes], M)
    assert result.status == g.UNSUPPORTED, result.evidence


def test_ld1e_and_sf1a_at_k_equal_one():
    """The sweep: k = 1 is a valid tail; a one-window quarantine flagged in its window,
    and a one-window violation flagged in it, are detections."""
    k1 = {**M, "immune": {**M["immune"], "k": 1}}
    row = {"router": "router:r1", "quarantined": True, "core": False}
    closes = [_w(1, ld=True, frontier=[row]), *[_w(i) for i in range(2, 15)]]
    assert g.ld1e_detection(closes, k1).ok
    violated = [_price_window(1, 0.0), *[_price_window(i, 1.0) for i in range(2, 15)]]
    flags = [_w(1, sf=True), *[_w(i) for i in range(2, 15)]]
    assert g.sf1a_detection(violated + flags, k1, card="c").status != g.FAIL


def test_sf2a_a_single_seat_arm_is_not_comparable_not_failed():
    """The sweep: a window with holders and no reliever (or one seat) compares nothing."""
    window = _sf2_window(_member("d2", "hold"))
    assert g.sf2_gradient(window + [_penalty("d2", 1.0)], M, **KW).status == g.UNSUPPORTED


# --- Codex pass on 2c85f43 ----------------------------------------------------------------


def test_th1c_a_seat_learner_failure_does_not_excuse_a_missing_router_charge():
    """Codex P2 (gauntlet.py:1633): only a router's own learning failure (a
    ``propensity.unlearned`` naming a router ``learner_id``) unlearns its round; a
    seat's declared-propensity failure (``assembly_id``, compute.py:3003, :3015) leaves
    the round learned, so its missing charge fails."""
    rows = _seq([
        _w(1, lam=0.4),
        _open("d1", "a", ids=["a", "NOOP"], probs=[0.8, 0.2]),
        _open("d2", "a", ids=["a", "NOOP"], probs=[0.1, 0.9]),  # moved 0.7
        _settled("d2"),
        {"kind": "propensity.unlearned", "handle": "d2", "assembly_id": "a",
         "reason": "declared actions outside the registered action set"}])
    result = g.th1c_movement(rows, M)
    assert result.status == g.FAIL and result.evidence["missing"] == ["d2"]
    router = [*rows[:-1], {"kind": "propensity.unlearned", "handle": "d2",
                           "learner_id": "router:Tick", "reason": "outside the universe"}]
    assert g.th1c_movement(router, M).status == g.UNSUPPORTED  # unlearned: pending


@pytest.mark.parametrize("weights", [{"a": 1.0}, {"a": 1.0, "b": 1.0, "c": 0.0}, [1.0]])
def test_gain_neutral_weights_must_name_exactly_the_declared_actions(weights):
    """Codex P2 (gauntlet.py:2687): a weight vector with an arm missing or extra is no
    EXP3 state (one log weight per action), so the reading is malformed and fails."""
    good = {"actions": ["a", "b"], "gamma": 0.1, "log_weights": {"a": 1.0, "b": 1.0}}
    bad = dict(good, log_weights=weights)
    for before, after in ((bad, dict(good, gamma=0.15)), (good, dict(bad, gamma=0.15))):
        result = g.gain_neutral({"bases": [before]}, {"bases": [after]})
        assert result.status == g.FAIL
        assert result.evidence["malformed"]["field"] == "log_weights"


def test_s4_an_unresolved_penalty_row_may_carry_no_raw_score():
    row = {"kind": "price.penalty", "handle": "d", "penalty": 0.1, "raw": None,
           "effective": None}
    assert g.s4_boundedness([row | {"unresolved": ["c"]}], M).ok
