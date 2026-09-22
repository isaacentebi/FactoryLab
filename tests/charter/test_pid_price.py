"""The charter's price law is a bounded PID, and stable failure is priced by its duration.

Essay II.II.b: lambda is set by a PID controller — Kp raises the penalty in
proportion to the violation, Ki stores sustained violation, Kd reacts to how fast
the violation changes to damp escalation before it overshoots — and in stable
failure one should "price the duration of failure, ratcheting up penalties".
"""

from dataclasses import replace

import pytest

from factorylab.charter.controller import CardRegion, PriceController
from factorylab.kernel.ledger import Ledger


def _pid(ledger=None, **changes):
    params = dict(eta=0.5, decay=0.1, lambda_max=1.0, min_window_events=1,
                  kp=0.5, kd=0.0)
    params.update(changes)
    prices = PriceController(ledger or Ledger(), **params)
    prices.register(CardRegion("c", "max", None, 1.0, 1.0))
    return prices


def _updates(ledger):
    return [item for item in ledger._recovery_items() if item["kind"] == "price.update"]


def test_proportional_term_is_proportional_to_the_current_violation():
    """With no integral gain to speak of, the price is Kp times this window's violation."""
    one, two = _pid(eta=1e-9, lambda_max=10.0), _pid(eta=1e-9, lambda_max=10.0)
    one.observe("c", 1.5, 0)  # violation 0.5
    two.observe("c", 2.0, 0)  # violation 1.0
    assert one.price("c") == pytest.approx(0.25)
    assert two.price("c") == pytest.approx(0.5)
    assert two.price("c") == pytest.approx(2 * one.price("c"))


def test_integral_term_accumulates_sustained_violation_and_leaks_once_compliant():
    ledger = Ledger()
    prices = _pid(ledger, kp=0.0, lambda_max=10.0)
    for event in range(3):
        prices.observe("c", 1.4, event)  # violation 0.4 each window
    assert prices.price("c") == pytest.approx(3 * 0.5 * 0.4)
    prices.observe("c", 0.5, 3)  # compliant: only the integral remains, leaking decay
    assert prices.price("c") == pytest.approx(0.6 - 0.1)
    assert [row["i"] for row in _updates(ledger)] == pytest.approx([0.2, 0.4, 0.6, 0.5])


def test_derivative_speeds_a_fast_escalation_and_never_discounts_a_recovery():
    """Kd raises the price while the violation grows; a shrinking one keeps P + I."""
    rising, flat = _pid(kd=0.5, lambda_max=10.0), _pid(kd=0.0, lambda_max=10.0)
    for prices in (rising, flat):
        prices.observe("c", 1.5, 0)
        prices.observe("c", 3.0, 1)  # escalating fast
    assert rising.price("c") > flat.price("c")
    assert rising.price("c") - flat.price("c") == pytest.approx(0.5 * 1.5)

    damped, undamped = _pid(kd=0.5, lambda_max=10.0), _pid(kd=0.0, lambda_max=10.0)
    for prices in (damped, undamped):
        prices.observe("c", 3.0, 0)  # violation 2
        prices.observe("c", 1.5, 1)  # violation 0.5: recovering fast, still violating
    # Only the positive part of the derivative acts (Stooke et al. 2020).
    assert damped.price("c") == pytest.approx(undamped.price("c"))


def test_a_shrinking_violation_is_never_priced_at_zero_while_it_lasts():
    """The review's rehearsal numbers: kp 0.5, eta 0.5, kd 0.25, region "at most 0.30".

    Value 1.0 saturates the price at 1.0; the next window's 0.35 is still out of the
    region. A signed derivative gave P=0.083, I=0.083, D=-0.542 and so lambda=0: the
    card was free while still violating. With the positive part only it pays P + I.
    """
    ledger = Ledger()
    prices = PriceController(ledger, eta=0.5, decay=0.1, lambda_max=1.0, min_window_events=1,
                             kp=0.5, kd=0.25)
    prices.register(CardRegion("c", "max", None, 0.30, 0.30))
    prices.observe("c", 1.0, 0)
    assert prices.price("c") == pytest.approx(1.0)
    prices.observe("c", 0.35, 1)
    row = _updates(ledger)[-1]
    assert row["violation"] == pytest.approx(0.05 / 0.30)
    assert row["p"] == pytest.approx(0.5 * 0.05 / 0.30)
    assert row["i"] == pytest.approx(0.5 * 0.05 / 0.30)
    assert row["d"] == 0.0  # the signed term would have been 0.25 * -0.65 / 0.30 = -0.542
    assert prices.price("c") == pytest.approx(row["p"] + row["i"])
    assert prices.price("c") >= row["i"] > 0


def test_integral_builds_while_p_alone_saturates_unless_the_violation_is_growing():
    """Anti-windup holds I only when P + I already saturates and the violation grows."""
    ledger = Ledger()
    prices = _pid(ledger, kp=0.5, eta=0.5, lambda_max=1.0)
    prices.observe("c", 4.0, 0)  # violation 3, P = 1.5 >= 1: saturated and growing: hold
    prices.observe("c", 4.0, 1)  # the same violation, not growing: I integrates anyway
    prices.observe("c", 5.0, 2)  # growing again, P + I saturates: hold
    assert [row["i"] for row in _updates(ledger)] == pytest.approx([0.0, 1.0, 1.0])
    prices.observe("c", 1.2, 3)  # violation 0.2: P falls to 0.1, the built pressure remains
    assert prices.price("c") == pytest.approx(1.0)


def test_derivative_is_on_the_measurement_so_a_moved_region_cannot_kick_the_price():
    """Re-deriving a card's bound changes its violation, never its rate of change."""
    moved, still = _pid(kd=1.0, lambda_max=10.0), _pid(kd=1.0, lambda_max=10.0)
    for prices in (moved, still):
        prices.observe("c", 2.0, 0)
    moved.update_region(CardRegion("c", "max", None, 0.5, 1.0))  # the bound tightened
    moved.observe("c", 2.0, 1)
    still.observe("c", 2.0, 1)
    rows = {id(p): p.snapshot()["cards"]["c"] for p in (moved, still)}
    assert rows[id(moved)]["lambda"] > rows[id(still)]["lambda"]  # more violation: P and I
    # The measurement did not move, so neither derivative contributed.
    assert moved.price("c") - still.price("c") == pytest.approx(0.5 * 0.5 + 0.5 * 0.5)


def test_price_and_integral_stay_bounded_and_the_integral_never_winds_up():
    ledger = Ledger()
    prices = _pid(ledger, kp=0.5, kd=0.25, lambda_max=1.0)
    for event in range(20):
        prices.observe("c", 100.0, event)  # enormous, sustained violation
        card = prices.snapshot()["cards"]["c"]
        assert 0.0 <= card["lambda"] <= 1.0
        assert 0.0 <= card["integral"] <= 1.0
    assert prices.snapshot()["cards"]["c"]["saturations"] > 0
    # Anti-windup: pressure held at the bound unwinds by decay at once, not after
    # paying back twenty windows of integrated excess.
    prices.observe("c", 0.0, 20)
    assert prices.price("c") == pytest.approx(prices.snapshot()["cards"]["c"]["integral"])
    assert prices.price("c") <= 1.0 - 0.1 + 1e-12
    assert all(row["lambda_after"] <= 1.0 for row in _updates(ledger))


def test_adopted_price_seeds_the_integral_without_a_jump():
    prices = _pid(kp=0.0)
    prices.set_price("c", 0.4, amendment_id="manifest")
    prices.observe("c", 0.5, 0)  # compliant: the adopted price leaks, it is not reset
    assert prices.price("c") == pytest.approx(0.3)


def test_no_gains_leave_the_integral_alone_and_the_ledger_carries_every_term():
    """No gains stated: the PID with Kp = Kd = 0, which is the integral alone."""
    ledger = Ledger()
    prices = PriceController(ledger, eta=0.5, decay=0.25, lambda_max=2.0, min_window_events=1)
    prices.register(CardRegion("c", "max", None, 10.0, 2.0))
    for event, value in enumerate([12, 12, 12, 10, -100]):
        prices.observe("c", value, event)
    assert [row["lambda_after"] for row in _updates(ledger)] == [0.5, 1.0, 1.5, 1.25, 1.0]
    assert all(row["p"] == row["d"] == 0.0 for row in _updates(ledger))
    assert all("damping" not in row for row in _updates(ledger))


@pytest.mark.parametrize("changes", [
    {"kp": -0.1}, {"kd": -1}, {"kp": float("nan")}, {"kd": float("inf")}, {"kp": True},
    {"controller": "pid"}, {"kappa": 0.5},
])
def test_invalid_price_law_is_refused(changes):
    with pytest.raises((ValueError, TypeError)):
        _pid(**changes)


def test_manifest_names_no_law_and_refuses_the_removed_keys():
    """Charter audit U3: one law, so neither ``controller`` nor ``kappa`` is a key."""
    import tomllib

    from factorylab.runtime.worlds import WORLDS_DIR, load_manifest, manifest_from_dict

    seed = load_manifest("scripted")
    pid = replace(seed, prices=replace(seed.prices, kp=0.5))
    pid.validate()
    assert pid.canonical_json() != seed.canonical_json()
    with pytest.raises(ValueError):
        replace(seed, prices=replace(seed.prices, kd=-1.0)).validate()
    raw = tomllib.loads((WORLDS_DIR / "scripted.toml").read_text())
    for key, value in (("controller", "pid"), ("kappa", 0.5)):
        with pytest.raises(ValueError, match=f"prices.{key} was removed"):
            manifest_from_dict({**raw, "prices": {**raw["prices"], key: value}})


def test_stable_failure_ratchets_price_up_with_its_duration_and_stays_bounded():
    ledger = Ledger()
    prices = _pid(ledger, lambda_max=1.0)
    prices.set_price("c", 0.1, amendment_id="start")
    seen = []
    for window in range(1, 4):
        prices.ratchet("c", window=window, step=0.05)
        seen.append(prices.price("c"))
    # Durations 1, 2, 3 add 0.05, 0.10, 0.15: the longer the failure, the steeper.
    assert seen == pytest.approx([0.15, 0.25, 0.40])
    rows = [i for i in ledger._recovery_items() if i["kind"] == "immune.price_ratchet"]
    assert [r["duration"] for r in rows] == [1, 2, 3]
    assert all(r["lambda_after"] > r["lambda_before"] for r in rows)
    for window in range(4, 20):
        prices.ratchet("c", window=window, step=0.05)
    assert prices.price("c") == 1.0 and prices.snapshot()["cards"]["c"]["integral"] == 1.0
    prices.end_failure("c", window=20)
    assert prices.snapshot()["cards"]["c"]["failing_windows"] == 0
    prices.ratchet("c", window=21, step=0.05)  # a new attractor starts from one window
    assert ledger._recovery_items()[-1]["duration"] == 1
    with pytest.raises(ValueError):
        prices.ratchet("c", window=22, step=0.0)
