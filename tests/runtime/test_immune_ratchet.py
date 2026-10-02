"""Stable failure is priced by its duration; no learner is changed by a diagnosis.

Essay II.II.b: "In the case of stable failure, one should price the duration of
failure, ratcheting up penalties the longer the factory spends in a wide-spectral-gap
attractor that is failing its input-output target." The organ used to halve the
violated cards' price for a window instead, and it used to raise every router's
exploration while failure held: an exploration floor inside the learners, which made
them not no-regret (audit s06 #2). The gain is lambda (essay II.IV.b); the learners'
exploration is their own schedule (docs/architecture/learners-noregret.md §2.5).
"""

from dataclasses import replace

import pytest

from factorylab.charter.windows import MetricWindow
from factorylab.learners.base import state_bytes
from factorylab.runtime.loop import Runtime
from factorylab.runtime.pricing import MeasureWindow
from factorylab.runtime.worlds import load_manifest


def _runtime(**prices):
    """The scripted world, its price gains overridden by ``prices``: a test of the
    duration price needs gains that leave it room below the cap (``gain_headroom``)."""
    seed = load_manifest("scripted")
    seed = replace(seed, prices=replace(seed.prices, **prices))
    charter = replace(seed.charter, cards=tuple(replace(c, window=MetricWindow("windows", 1, None))
                                               for c in seed.charter.cards))
    rt = Runtime(replace(seed, charter=charter), events=1, seed=1, initial_balance_micro=None,
                 ledger_path=None)
    rt._derive_regions()
    return rt


def _close(rt, well_formed: float, registrations: int = 1, *, organ_due: bool = True):
    """Close one window, a price-loop period of ticks after the last.

    ``organ_due`` makes the immune organ's own loop due at this close (versioning P5:
    left to itself it acts once every min_ratio price periods or more).
    """
    rt.n += 10
    rt.ticks_consumed += rt.m.timing.min_ratio
    if organ_due:
        rt.clockwork.force("immune", rt.ticks_consumed)
    rt.window = MeasureWindow(rt.n, rt.wallet.balance, invocations=10,
                              ok=int(well_formed * 10), registrations=registrations)
    rt._close_price_window()


def _items(rt, kind):
    return [i for i in rt.ledger._recovery_items() if i["kind"] == kind]


def test_stable_failure_raises_violated_price_with_duration_and_never_halves_it():
    rt = _runtime(eta=0.01)  # the PID alone takes many windows to reach the cap
    prices = [0.0]
    for _ in range(6):
        _close(rt, 0.2)  # the same failing cell, window after window
        prices.append(rt.controller.price("well_formed_rate"))
    ratchets = _items(rt, "immune.price_ratchet")
    assert len(ratchets) >= 3 and _items(rt, "pathology.stable_failure")
    # Each window's rise is the controller's own step plus the ratchet, and the
    # ratchet's part grows with how long the attractor has held.
    steps = [b - a for a, b in zip(prices, prices[1:], strict=False)]
    assert steps[-1] - steps[0] > 0
    assert steps[-1] - steps[-2] > 0
    assert [r["duration"] for r in ratchets] == list(range(1, len(ratchets) + 1))
    assert [r["step"] for r in ratchets] == [
        rt.m.immune.price_step * r["duration"] for r in ratchets]
    assert all(r["lambda_after"] >= r["lambda_before"] for r in ratchets)
    # Bounded by the one bound (wave 16, R-E): the card's penalty never passes the cap.
    bound = rt.controller.saturation("well_formed_rate")["bound"]
    assert prices == sorted(prices) and prices[-1] <= bound


def test_stable_failure_ratchets_price_not_learner():
    """Every learner is byte-identical through a stable-failure episode and its end: the
    organ ratchets the price, writes no gain row and touches no learner."""
    rt = _runtime()

    def learners():
        return [state_bytes(r.learner.state()) for r in rt._all_router_states()]

    before = learners()
    for _ in range(4):
        _close(rt, 0.2)
    assert _items(rt, "pathology.stable_failure") and _items(rt, "immune.price_ratchet")
    assert rt.controller.snapshot()["cards"]["well_formed_rate"]["failing_windows"] > 0
    for i in range(12):
        _close(rt, 1.0, registrations=3 * (i % 2))  # compliant and active: no pathology
    assert rt.controller.snapshot()["cards"]["well_formed_rate"]["failing_windows"] == 0
    assert _items(rt, "immune.price_ratchet_ended")
    assert learners() == before
    assert not _items(rt, "immune.gain")


def test_the_organ_diagnoses_every_window_and_acts_on_its_own_slower_loop():
    """Versioning P5, time audit T2: the organ acts once every min_ratio price periods or
    more, jittered, and diagnoses every window in between."""
    rt = _runtime()
    rt.clockwork.fire("price", rt.ticks_consumed, 1)
    rt.clockwork.fire("immune", rt.ticks_consumed, rt.clockwork.period("price"))
    for _ in range(12):
        _close(rt, 0.2, organ_due=False)
    windows = _items(rt, "immune.window")
    acted = [w["window"] for w in windows if w["acts"]]
    assert len(windows) == 12 and 1 <= len(acted) <= 12 // rt.m.timing.min_ratio
    loops = [i for i in _items(rt, "clock.loop") if i["loop"] == "immune"]
    assert loops and all(i["period_ticks"] >= rt.m.timing.min_ratio * i["inner_ticks"]
                         for i in loops)
    # Only a window the organ acted in carries a gain step or a ratchet.
    assert {i["window"] for i in _items(rt, "immune.price_ratchet")} <= set(acted)


def test_price_step_alone_sets_the_ratchet_and_is_part_of_the_identity():
    seed = load_manifest("scripted")
    assert '"price_step":0.05' in seed.canonical_json()
    stepped = replace(seed, immune=replace(seed.immune, price_step=0.2))
    stepped.validate()
    assert stepped.canonical_json() != seed.canonical_json()
    for bad in (None, 0.0, -0.1, float("nan"), float("inf"), True):
        with pytest.raises(ValueError):
            replace(seed, immune=replace(seed.immune, price_step=bad)).validate()

    rt = _runtime(eta=0.01)
    rt.m = replace(rt.m, immune=replace(rt.m.immune, price_step=0.02))
    for _ in range(6):
        _close(rt, 0.2)
    ratchets = _items(rt, "immune.price_ratchet")
    assert ratchets and [r["step"] for r in ratchets] == pytest.approx(
        [0.02 * r["duration"] for r in ratchets])


def test_an_unmeasured_failing_card_holds_its_duration():
    """Wave 16, second addendum (M-6): windows that measure nothing of a failing card
    are missing evidence, not relief: its duration is not reset."""
    rt = _runtime(eta=0.01)
    for _ in range(4):
        _close(rt, 0.2)
    before = rt.controller.snapshot()["cards"]["well_formed_rate"]["failing_windows"]
    assert before > 0
    for _ in range(4):  # no invocation: well_formed_rate is unmeasured
        rt.n += 10
        rt.ticks_consumed += rt.m.timing.min_ratio
        rt.clockwork.force("immune", rt.ticks_consumed)
        rt.window = MeasureWindow(rt.n, rt.wallet.balance, invocations=0, ok=0,
                                  registrations=1)
        rt._close_price_window()
    after = rt.controller.snapshot()["cards"]["well_formed_rate"]["failing_windows"]
    assert after >= before
    assert not _items(rt, "immune.price_ratchet_ended")


def _answering_for(rt, **change):
    """The charter with card ``well_formed_rate`` changed by ``change``, derived."""
    card = replace(next(c for c in rt.charter.cards if c.id == "well_formed_rate"), **change)
    rt.charter = replace(rt.charter, cards=tuple(
        card if c.id == "well_formed_rate" else c for c in rt.charter.cards))
    rt._derive_regions()


@pytest.mark.parametrize("change", [{"observation": "noop_share"},
                                    {"answers_for": "producer"}],
                         ids=["new-observation", "same-observation-new-role"])
def test_a_replay_of_the_diary_diagnoses_every_window_as_the_live_organ_did(change):
    """Codex on #152 (735d50a): live and replay run one step (``versions.organ_step``).
    A world fails a card answering for the evaluators for five windows, the charter
    then redefines that card under the same id (a new observation, or the same one
    answering for the producers: a new ``metric_identity``), and seven more windows
    close. Versioning does not splice the two populations: the newest window reads the
    card only from windows that measured what it measures now. Replaying the organ's
    own ledgered windows gives, window by window, exactly the flags, violated cards,
    held cards and gaps the live organ ledgered."""
    from factorylab.versioning.live import current_metrics, organ_record, redefined
    from factorylab.versioning.versions import replay

    rt = _runtime()
    _answering_for(rt, answers_for="evaluator")
    for _ in range(5):
        _close(rt, 0.2)
    _answering_for(rt, **change)
    for i in range(7):
        _close(rt, 0.2 if i % 3 else 1.0)
    rows = _items(rt, "immune.window")
    assert len(rows) == 12
    name = "card:well_formed_rate"
    was, now = rows[4]["semantics"][name], rows[5]["semantics"][name]
    assert was["identity"][:2] == ["well_formed_rate", "evaluator"]
    assert now["identity"] != was["identity"]
    records = [organ_record(row) for row in rows]
    assert redefined(records[:6], name) and not redefined(records[:5], name)
    assert [name in w["profile"] for w in current_metrics(records[:6])] == [False] * 5 + [True]
    spec = rt.m.immune
    readings = replay(records, k=spec.k,
                      horizon=rt.m.timing.min_ratio * spec.k,
                      tv_threshold=spec.tv_threshold, gap_threshold=spec.gap_threshold,
                      registration_bins=tuple(spec.registration_bins),
                      revision_bins=tuple(spec.revision_bins))
    for row, reading in zip(rows, readings, strict=True):
        diagnosis = reading["diagnosis"]
        for name in ("violated_cards", "unmeasured_held", "gap", "card_gap", "rolling_gap",
                     "volatility", "version"):
            assert diagnosis[name] == row[name], (row["window"], name)
        assert diagnosis["flags"] == row["flags"], row["window"]
    assert any(row["violated_cards"] for row in rows[:5])  # the old metric failed
