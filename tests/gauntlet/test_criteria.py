"""The gauntlet's pure criteria, each on synthetic rows: a passing set and a violating one.

AGENTS.md: every invariant gets a test that attempts to violate it. A criterion that
passes on its violating rows does not discriminate and must be rewritten (design G2).
"""

import json
import math
from random import Random

import pytest

from scripts import gauntlet as g

M = {"prices": {"eta": 0.5, "decay": 0.1, "lambda_max": 1.0, "penalty_cap": 0.5, "kp": 0.0},
     "immune": {"k": 3, "gain_step": 0.05, "gamma_max": 0.5, "price_step": 0.05,
                "tv_threshold": 0.2},
     "timing": {"min_ratio": 3}, "novelty": {"share": 0.1},
     "evaluation": {"no_swap_regret_kinds": ["Tick"]}}


def _w(index, *, acts=False, sf=False, thrash=False, ld=False, lam=0.0, pen=0.0,
       profile=None, frontier=()):
    return {"kind": "immune.window", "window": index, "acts": acts,
            "flags": {"stable_failure": sf, "thrash": thrash, "learning_death": ld},
            "thrash": {"lambda": lam, "penalty": pen},
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
    assert g.t_release(ph, 0.5) == 5 and g.t_release(ph, 0.0) == 0
    assert g.t_gamma(ph, 0.4, 3) == 24
    assert g.t_learn(0.3, 3) == 37


def test_violation_is_the_controllers_formula():
    assert g.violation({"kind": "min", "lo": 0.2, "scale": 0.2}, 0.0) == 1.0
    assert g.violation({"kind": "max", "hi": 5.0, "scale": 5.0}, 10.0) == 1.0
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


def _updates(card, triples):
    return [{"kind": "price.update", "card_id": card, "lambda_after": lam, "violation": v,
             "i": i} for lam, v, i in triples]


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


def test_sf1d_saturation_is_ledgered_with_a_rising_duration():
    at_cap = _updates("c", [(0.5, 1.0, 0.5)] * 4)
    rows = at_cap + [{"kind": "immune.price_ratchet_saturated", "card_id": "c",
                      "duration": d} for d in (1, 2, 3)]
    assert g.sf1d_escalation(rows, M, card="c").ok
    assert g.sf1d_escalation(at_cap, M, card="c").status == g.FAIL
    stuck = at_cap + [{"kind": "immune.price_ratchet_saturated", "card_id": "c",
                       "duration": 1}] * 2
    assert g.sf1d_escalation(stuck, M, card="c").status == g.FAIL


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


def _novelty(budget=1_000_000, carried=0, accrued="1/3", cap=None, amount=None):
    num, den = (0.1).as_integer_ratio()
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
            "effective": 0.5, "terms": [term]}


def test_sf2a_relievers_bear_nothing_and_holders_share_equally():
    opens = [_open("d1", "rel"), _open("d2", "hold"), _open("d3", "hold")]
    good = opens + [_penalty("d1", 0.0), _penalty("d2", 0.5), _penalty("d3", 0.5)]
    assert g.sf2_gradient(good, M, card="c", relievers={"rel"}, holders={"hold"}).ok
    generic = opens + [_penalty("d1", 1 / 3), _penalty("d2", 1 / 3), _penalty("d3", 1 / 3)]
    result = g.sf2_gradient(generic, M, card="c", relievers={"rel"}, holders={"hold"})
    assert result.status == g.FAIL


def test_sf2b_shares_are_order_blind_and_the_one_over_rank_shape_fails():
    equal = [_penalty(f"d{i}", 0.25) for i in range(4)]
    assert g.sf2b_order_blind(equal, M, card="c").ok
    ranked = [_penalty(f"d{i}", 1 / (i + 1)) for i in range(4)]
    result = g.sf2b_order_blind(ranked, M, card="c")
    assert result.status == g.FAIL and result.evidence["rank_shaped"] == 1
    exact = [_penalty("d0", 0.0, obs="well_formed_rate"),
             _penalty("d1", 1.0, obs="well_formed_rate")]
    assert g.sf2b_order_blind(exact, M, card="c").status == g.UNSUPPORTED


# --- thrash --------------------------------------------------------------------------------


def test_th1a_and_th1b_detection_and_a_price_that_holds_duration():
    closes = [_w(i, thrash=i >= 5, lam=0.1 * max(0, i - 4), pen=min(0.5, 0.08 * max(0, i - 4)))
              for i in range(1, 12)]
    assert g.th1a_detection(closes, M, cycle_start=3).ok
    assert g.th1a_detection(closes, M, cycle_start=-10).status == g.FAIL
    assert g.th1b_duration(closes, M).ok
    flat = [_w(i, thrash=True, lam=0.0) for i in range(1, 8)]
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
    # The negative control's shape: a charge that never lands.
    assert g.th1c_movement(rows, M).status == g.FAIL


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
    one = both[:-1]
    result = g.th1c_movement(one, M)
    assert result.status == g.FAIL and result.evidence["missing"] == ["d3"]
    wrong_amount = [*both[:-1], {**both[-1], "charge": 0.1}]
    assert g.th1c_movement(wrong_amount, M).status == g.FAIL


def test_th1d_no_charge_reaches_the_frontier_or_the_niche():
    ok = [{"kind": "thrash.charged", "handle": "d1", "router": "router:Tick", "charge": 0.1}]
    assert g.th1d_frontier(ok, M).ok
    frontier = [{"kind": "thrash.charged", "handle": "d1", "router": "router:WorldUpdate",
                 "charge": 0.1}]
    assert g.th1d_frontier(frontier, M).status == g.FAIL
    niche = ok + [{"kind": "niche.action", "handle": "d1"}]
    assert g.th1d_frontier(niche, M).status == g.FAIL


def test_th1e_release_after_the_cycle_stops():
    closes = [_w(i, thrash=i < 10, lam=0.5 if i < 10 else max(0.0, 0.5 - 0.1 * (i - 9)))
              for i in range(1, 25)]
    assert g.th1e_release(closes, M, steady_from=8).ok
    stuck = [_w(i, thrash=True, lam=0.5) for i in range(1, 25)]
    assert g.th1e_release(stuck, M, steady_from=8).status == g.FAIL


def test_th1f_thrash_has_priority_over_stable_failure():
    closes = [_w(1, sf=True, thrash=True)]
    assert g.th1f_priority(closes + [_gain(1, 0.2, 0.15, "thrash")], M).ok
    assert g.th1f_priority(closes + [_gain(1, 0.2, 0.25)], M).status == g.FAIL


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


def test_th2_a_short_lived_configuration_reads_as_thrash_and_is_never_refused():
    rows = [{"kind": "config.lifespan", "loop": "seat:m", "ratio": 0.2},
            _w(1, thrash=True) | {"thrash": {"unsettled": 0.8, "lambda": 0, "penalty": 0}}]
    assert g.th2_short_lived(rows, M, loop="seat:m").ok
    refused = rows + [{"kind": "registration.rejected", "reason": "too soon after last"}]
    assert g.th2_short_lived(refused, M, loop="seat:m").status == g.FAIL
    unread = [rows[0], _w(1)]
    assert g.th2_short_lived(unread, M, loop="seat:m").status == g.FAIL


def test_th3_activations_respect_the_cascade_ratio():
    def cadence(at, slow=10):
        return {"kind": "charter.cadence", "activation_ns": at, "earliest_ns": at + 3 * slow,
                "slowest_period_ns": slow}
    acts = [{"kind": "charter.activate"}] * 2
    assert g.th3_governance_gap(acts + [cadence(0), cadence(30)], M).ok
    assert g.th3_governance_gap(acts + [cadence(0), cadence(20)], M).status == g.FAIL
    short = acts + [{**cadence(0), "earliest_ns": 10}]
    assert g.th3_governance_gap(short, M).status == g.FAIL


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
    unflagged = [_w(i, frontier=[row]) for i in range(1, 10)]
    assert g.ld1e_detection(unflagged, M).status == g.FAIL
    assert g.ld1f_hold(closes + [_gain(6, 0.3, 0.3)], M).ok
    assert g.ld1f_hold(closes + [_gain(6, 0.3, 0.25, "cleared")], M).status == g.FAIL


def test_of2d_every_challenge_traces_to_a_seats_return():
    opened = [_open("d1", "adv", actor="router:Verdict")]
    ret = {"kind": "invocation", "handle": "d1", "assembly_id": "adv"}
    challenge = {"kind": "challenge.proposed", "handle": "d1"}
    assert g.of2d_authorship(opened + [ret, challenge], M, seats={"adv"}).ok
    kernel = {"kind": "challenge.proposed", "handle": "decision-99"}
    assert g.of2d_authorship(opened + [ret, kernel], M).status == g.FAIL
    other = g.of2d_authorship(opened + [ret, challenge], M, seats={"someone-else"})
    assert other.status == g.FAIL


def _consequence(about, q, y, phase="final"):
    return {"kind": "verdict.consequence", "about_handle": about, "q": q, "y": y,
            "phase": phase}


def test_of1a_y_is_one_fact_per_return_whatever_the_verdict():
    same = [_consequence("r1", 0.9, 0.2), _consequence("r1", 0.1, 0.2)]
    assert g.of1a_outside_the_loop(same, M).ok
    moved = [_consequence("r1", 0.9, 0.55), _consequence("r1", 0.1, 0.15)]
    assert g.of1a_outside_the_loop(moved, M).status == g.FAIL
    assert g.of1a_outside_the_loop([same[0]], M).status == g.UNSUPPORTED


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


# --- pricing, not steering (S1-S8) --------------------------------------------------------------


def test_s1_every_draw_replays_from_its_seed_and_every_act_traces_to_a_return():
    good = [_open("decision-1", "a"), {"kind": "invocation", "handle": "decision-1"},
            {"kind": "order.intent", "handle": "decision-1"}]
    assert g.s1_draw_sovereignty(good).ok
    forced = [dict(_open("decision-1", "a"))]
    forced[0]["propensity"] = {**forced[0]["propensity"], "chosen": "NOOP"}
    forced[0]["propensity"]["rng_seed"] = next(
        s for s in range(1000) if Random(s).choices(["a", "NOOP"], weights=[.5, .5])[0] == "a")
    assert g.s1_draw_sovereignty(forced).status == g.FAIL
    kernel_order = [_open("decision-1", "a"), {"kind": "order.intent", "handle": "decision-1"}]
    assert g.s1_draw_sovereignty(kernel_order).status == g.FAIL


def test_s4_bounds_on_penalties_rewards_and_ratchets():
    ok = [_penalty("d", 1.0), {"kind": "router.abstention_priced", "handle": "d",
                               "reward": 0.4, "neutral": 0.5, "penalty": 0.1}]
    assert g.s4_boundedness(ok, M).ok
    assert g.s4_boundedness([_penalty("d", 1.0) | {"penalty": 0.6}], M).status == g.FAIL
    over = [{"kind": "immune.price_ratchet", "card_id": "c", "lambda_after": 1.5}]
    assert g.s4_boundedness(over, M).status == g.FAIL


def test_s5_and_s5b_abstention_credit():
    ok = [{"kind": "router.abstention_priced", "handle": "d", "router": "router:Tick",
           "neutral": 0.5, "penalty": 0.2, "reward": 0.3}]
    assert g.s5_neutral_imputation(ok, M).ok
    bad = [ok[0] | {"reward": 0.5}]
    assert g.s5_neutral_imputation(bad, M).status == g.FAIL
    settled = [_open("d0", "seat"), {"kind": "decision.settle", "return": {
        "handle": "d0", "status": "settled", "score": 0.3}}]
    assert g.s5b_observed_neutral(settled + [ok[0] | {"neutral": 0.31}], M).ok
    assert g.s5b_observed_neutral(settled + ok, M).status == g.FAIL


def test_s7_s8_gain_names_routers_and_moves_gamma_by_one_common_step():
    assert g.s7_gain_targets([_gain(1, 0.1, 0.15)]).ok
    assert g.s7_gain_targets([_gain(1, 0.1, 0.15, router="learner:seat")]).status == g.FAIL
    assert g.s8_gain_rows_uniform([_gain(1, 0.1, 0.15)], M).ok
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


def test_replay_reads_every_criterion_over_a_diary_without_a_runtime():
    rows = _seq([_price_window(1, 0.0), _w(1, acts=True, sf=True),
                 {"kind": "price.update", "card_id": "c", "lambda_after": 0.5, "violation": 1.0,
                  "i": 0.5}])
    results = {r.name: r.status for r in g.replay(rows, M)}
    assert results["SF-0"] == g.FAIL and "SF-1c[c]" in results
    assert set(results.values()) <= {g.PASS, g.FAIL, g.UNSUPPORTED}
