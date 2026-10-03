"""Every router round learned is on the record exactly once (R16b-5).

A plain EXP3 update writes no row of its own, so "every round learned exactly once"
(essay I.a, Blum-Mansour) could not be read from the diary: a round learned with no
charge, carry or rescale left no trace, and a gauntlet read three niche rounds as
never learned. Each round a router trains on now writes one ``router.learned``, on
every path (direct, carried, credited at its window's close), a NOOP credit included.
"""

from __future__ import annotations

import pytest

from factorylab.kernel.queue import SettleStatus
from factorylab.runtime.shared import NOOP
from tests.runtime.test_cutoff_meter import _drawn_at
from tests.runtime.test_penalty_attribution import _runtime

KINDS = ["exp3", "blum_mansour"]  # a plain router; a keyed swap router


def _learned(rt, handle=None):
    return [i for i in rt.ledger._recovery_items() if i["kind"] == "router.learned"
            and (handle is None or i["handle"] == handle)]


@pytest.mark.parametrize("kind", KINDS)
def test_a_settled_round_writes_one_router_learned_row_and_never_a_second(monkeypatch,
                                                                          kind):
    rt = _runtime(monkeypatch)
    rt._build_router("Tick", kind)
    state, handle = _drawn_at(rt, "seed-decider", at=10)
    rt.queue.settle(handle, channel="verdict", score=0.8, status=SettleStatus.SETTLED,
                    definition_version="t", sampling_ref=None)
    rt._deliver_returns()
    (row,) = _learned(rt, handle)
    assert (row["router"], row["learner"], row["action"], row["path"]) == (
        state.learner.id, state.learner.id, "seed-decider", "direct")
    assert row["raw"] == pytest.approx(0.8) and "reward" in row
    rt._deliver_returns()  # delivered once, learned once
    assert len(_learned(rt, handle)) == 1


@pytest.mark.parametrize("kind", KINDS)
def test_a_noop_credited_at_its_windows_close_writes_one_row(monkeypatch, kind):
    rt = _runtime(monkeypatch)
    rt._build_router("Tick", kind)
    _state, noop = _drawn_at(rt, NOOP, at=10)
    rt._contribution(noop, "producer")
    rt.queue.settle(noop, channel="verdict", score=0.0, status=SettleStatus.INAPPLICABLE,
                    definition_version="noop", sampling_ref=None)
    rt._deliver_returns()
    assert noop in rt.noop_credits  # owed: an abstention is credited when due
    rt._close_price_window()
    rt.ticks_consumed += 1_000  # past its due tick
    rt._deliver_returns()
    assert noop not in rt.noop_credits
    (row,) = _learned(rt, noop)
    assert row["action"] == NOOP and row["path"] == "credit"
    rt._deliver_returns()
    assert len(_learned(rt, noop)) == 1


def test_a_niche_round_with_a_stored_charge_is_exempt_and_charged_nothing(monkeypatch):
    """R-E as amended: a round drawn in the niche bears no thrash charge; the row says a
    stored charge was dropped, and no ``thrash.charged`` is written."""
    rt = _runtime(monkeypatch)
    rt._build_router("Tick", "exp3")
    _state, handle = _drawn_at(rt, "seed-decider", at=10)
    rt._contribution(handle, "producer")["niche"] = True  # a protected trial's round
    rt.thrash_charges[handle] = 0.05  # its movement was recorded, positive
    rt.queue.settle(handle, channel="verdict", score=0.8, status=SettleStatus.SETTLED,
                    definition_version="t", sampling_ref=None)
    rt._deliver_returns()
    (row,) = _learned(rt, handle)
    assert row["exempt"] == "niche" and row["charge"] == 0.0
    assert not [i for i in rt.ledger._recovery_items()
                if i["kind"] == "thrash.charged" and i["handle"] == handle]
