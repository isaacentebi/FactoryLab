"""Penalties land on the decisions that did not relieve the violation (wave 16, D5, R-E).

A rate card's violation is attributed by relief: a decision that moved the rate toward
its region bears nothing, and every other decision of the scope bears an equal share,
counted over the window's decisions when it closed, so no share depends on the order
decisions settled in. A decision taken in the unhistoried niche bears no penalty and is
counted in no one's share (essay II.II.b: learning death is prevented "as a fact about
the world"); that is a penalty rule, never a reward floor.
"""

from dataclasses import replace

import pytest

from factorylab.charter.charter import MetricCard
from factorylab.charter.windows import MetricWindow
from factorylab.cortex.request import Return
from factorylab.kernel.queue import PropensityRecord, SettleStatus
from factorylab.runtime import pricing
from factorylab.runtime.loop import Runtime
from factorylab.runtime.worlds import load_manifest, manifest_from_dict

#: A ceiling on the producers' hold share: a trade relieves it, a hold does not.
HOLDS = MetricCard("hold-cap", "care with scarce resources", "Share of holds.", "fraction",
                   MetricWindow("windows", 1, None), {"rule": "at most", "hi": 0.25},
                   "noop_share", "producer")


def _runtime(monkeypatch):
    monkeypatch.setattr(pricing, "close_window", lambda *_a: None)
    seed = load_manifest("scripted")
    rt = Runtime(replace(seed, charter=replace(seed.charter, cards=(HOLDS,))), events=0,
                 seed=1, initial_balance_micro=None, ledger_path=None, router_gamma=0.1)
    rt._derive_regions()
    rt.controller.set_price(HOLDS.id, 0.8, amendment_id="test")
    return rt


def _producer(rt, seat, action, *, ok=True, niche=False):
    """One producer decision of ``seat`` this window, returning ``action``."""
    handle = rt.queue.open(
        actor="test-router", event_id=f"e-{len(rt.handle_to_assembly)}", channel="verdict",
        deadline_ns=10**18, parent_handle=None, cost_ceiling=0,
        propensity=PropensityRecord((seat,), (1.0,), seat, 0, "test-router", "state"))
    rt.handle_to_assembly[handle] = seat
    sample = rt._contribution(handle, "producer")
    sample.update(invocations=1, ok=int(ok), cost=100)
    if niche:
        sample["niche"] = True  # as ``_invoke`` marks a decision of the protected trial
    rt.card_samples.returned(handle=handle, assembly=seat, role="producer",
                             window=rt.window.index,
                             ret=Return(handle, {"action": action}, 100,
                                        "ok" if ok else "failed"))
    # The window's own counters, as the producer step keeps them (noop_share's global
    # closed window reads these).
    rt.window.producer_returns += 1
    rt.window.noop_returns += action == "hold"
    return handle


def _settle(rt, handle, score=0.9):
    rt._settle_priced(handle, channel="verdict", score=score, definition_version="t",
                      sampling_ref=None, cards="producer")


def _rows(rt, kind, **match):
    return [i for i in rt.ledger._recovery_items() if i.get("kind") == kind
            and all(i.get(k) == v for k, v in match.items())]


def _share(rt, handle):
    (term,) = rt._penalty_terms("producer", handle)
    return term["share"]


def test_non_relievers_bear_equal_shares_and_the_reliever_bears_nothing(monkeypatch):
    rt = _runtime(monkeypatch)
    holds = [_producer(rt, "seed-decider", "hold") for _ in range(3)]
    trade = _producer(rt, "seed-decider", "buy")
    rt._close_price_window()
    assert [_share(rt, h) for h in holds] == pytest.approx([1 / 3] * 3)
    assert _share(rt, trade) == 0.0 and rt._penalty_for("producer", trade) == 0.0
    penalties = {rt._penalty_for("producer", h) for h in holds}
    assert len(penalties) == 1 and penalties.pop() > 0


@pytest.mark.parametrize("order", [(0, 1, 2), (2, 1, 0), (1, 2, 0)])
def test_a_share_never_depends_on_the_order_decisions_settled_in(monkeypatch, order):
    """Decisions that settle while their window is open wait for its close (ruling R-I),
    then settle on the count the close froze: here one decision arrives after the
    others settled, and every hold still bears the same third."""
    rt = _runtime(monkeypatch)
    holds = [_producer(rt, "seed-decider", "hold") for _ in range(2)]
    _producer(rt, "seed-decider", "buy")
    settled = [holds[i] for i in order if i < len(holds)]
    for handle in settled:
        _settle(rt, handle)
    assert all(rt.queue.get(h).status is SettleStatus.PENDING for h in settled)
    assert {r["handle"] for r in _rows(rt, "price.deferred")} == set(settled)
    holds.append(_producer(rt, "seed-decider", "hold"))  # after the others settled
    rt._close_price_window()
    for handle in holds[2:]:
        _settle(rt, handle)
    rows = {r["handle"]: r for r in _rows(rt, "price.penalty")}
    assert set(rows) == set(holds)
    assert len({round(rows[h]["penalty"], 12) for h in holds}) == 1
    assert all(r["terms"][0]["share"] == pytest.approx(1 / 3) for r in rows.values())
    assert not rt.deferred_settlements


def test_failed_niche_explorations_bear_no_penalty_and_change_nobody_s_share(monkeypatch):
    """Wave 16, section 9 item 3 and addendum item 5: N failed explorations taken in the
    unhistoried niche bear 0, and are not in the split's denominator."""
    shares = {}
    for n in (0, 5):
        rt = _runtime(monkeypatch)
        holds = [_producer(rt, "seed-decider", "hold") for _ in range(3)]
        _producer(rt, "seed-decider", "buy")
        niche = [_producer(rt, "eval-a", "hold", ok=False, niche=True) for _ in range(n)]
        rt._close_price_window()
        shares[n] = [_share(rt, h) for h in holds]
        for handle in niche:
            assert rt._is_niche(handle)
            assert rt._penalty_for("producer", handle) == 0.0
            _settle(rt, handle, score=0.1)
            (row,) = _rows(rt, "price.penalty", handle=handle)
            assert row["penalty"] == 0.0
    assert shares[0] == shares[5] == pytest.approx([1 / 3] * 3)


def test_the_niche_rule_is_a_penalty_rule_and_never_a_reward_floor(monkeypatch):
    """Addendum item 4: a failed niche exploration keeps what its judges gave it, even
    below what an abstention is credited; nothing raises it to the NOOP's value."""
    rt = _runtime(monkeypatch)
    for _ in range(3):
        _producer(rt, "seed-decider", "hold")
    explored = _producer(rt, "eval-a", "hold", ok=False, niche=True)
    rt._close_price_window()
    _settle(rt, explored, score=0.05)
    assert rt.queue.history(explored)[-1].score == 0.05
    assert rt.queue.history(explored)[-1].score < 0.5  # below the published prior


def test_a_niche_decision_waits_for_no_close(monkeypatch):
    rt = _runtime(monkeypatch)
    _producer(rt, "seed-decider", "hold")
    explored = _producer(rt, "eval-a", "hold", niche=True)
    _settle(rt, explored, score=0.4)
    assert rt.queue.history(explored)[-1].score == 0.4
    assert not _rows(rt, "price.deferred", handle=explored)


def test_the_integrator_freezes_on_the_roles_pressure_at_the_cap(monkeypatch):
    """R-E anti-windup through the runtime: the pressure the close hands the controller
    is the total over the cards of the card's roles, at the prices in force."""
    rt = _runtime(monkeypatch)
    rt.controller.set_price(HOLDS.id, 100.0, amendment_id="at-the-cap")
    for _ in range(3):
        _producer(rt, "seed-decider", "hold")
    rt._close_price_window()
    (update,) = _rows(rt, "price.update", card_id=HOLDS.id)
    assert update["pressure"] >= rt.m.prices.penalty_cap
    assert update["integrator_frozen"] and update["at_cap"]
    assert update["lambda_after"] * update["violation"] == pytest.approx(
        rt.m.prices.penalty_cap)


def test_a_saturated_ratchet_is_ledgered_and_published_to_governance(monkeypatch):
    rt = _runtime(monkeypatch)
    rt.controller.set_price(HOLDS.id, 100.0, amendment_id="at-the-cap")
    for _ in range(3):
        _producer(rt, "seed-decider", "hold")
    rt._close_price_window()
    before = rt.controller.snapshot()["cards"][HOLDS.id]
    rt.controller.ratchet(HOLDS.id, window=1, step=0.05)
    after = rt.controller.snapshot()["cards"][HOLDS.id]
    assert (after["lambda"], after["integral"]) == (before["lambda"], before["integral"])
    (row,) = _rows(rt, "immune.price_ratchet_saturated", card_id=HOLDS.id)
    assert row["lambda"] == pytest.approx(row["bound"])
    published = {r["card_id"]: r for r in rt._card_statistics()}[HOLDS.id]
    assert published["saturated_windows"] >= 1 and published["bound"] == pytest.approx(
        row["bound"])


# --- SF-0: the duration price has room to exist (addendum item 1) ----------------------


def test_gain_headroom_states_the_relation_and_its_numbers():
    """The fewest windows in which the PID alone presses a unit violation onto the cap
    must exceed the fewest windows a stable failure is diagnosed in by min_ratio."""
    edition6 = load_manifest("edition6-capital-loop")
    assert edition6.gain_headroom() == {"saturation_windows": 1, "diagnosis_windows": 3,
                                        "min_ratio": 3, "holds": False}
    p = edition6.prices
    slow = replace(edition6, prices=replace(p, kp=0.0, eta=p.penalty_cap / 9))
    assert slow.gain_headroom()["saturation_windows"] == 9
    assert slow.gain_headroom()["holds"]
    fast = replace(slow, prices=replace(slow.prices, eta=p.penalty_cap / 8))
    assert not fast.gain_headroom()["holds"]  # the violation attempt: one window short


def test_gain_headroom_is_published_with_the_price_law(monkeypatch):
    rt = _runtime(monkeypatch)
    controller = rt._mechanics_block()["controller"]
    assert controller["gain_headroom"] == rt.m.gain_headroom()
    assert controller["penalty_cap"] == rt.m.prices.penalty_cap


def test_lambda_max_is_refused_by_name():
    import tomllib

    from factorylab.runtime.worlds import WORLDS_DIR

    raw = tomllib.loads((WORLDS_DIR / "scripted.toml").read_text())
    with pytest.raises(ValueError, match="prices.lambda_max was removed"):
        manifest_from_dict({**raw, "prices": {**raw["prices"], "lambda_max": 1.0}})
