"""A registered observation a card on the agenda names is in use (the plateau regression).

An observation registered on trial retires once its patience passes unused. A card a
pending motion would add names it too: the motion's promise is framed on that
observation at every ballot (``_promise_frame``), so retiring it under the motion left
the ballot nothing to frame and the world stopped (found by the 5000-event plateau run
once wave 16's timing moved the trajectory).
"""

from dataclasses import replace
from types import SimpleNamespace

from factorylab.runtime import pricing
from tests.conftest import make_runtime


def test_an_observation_a_pending_motion_names_is_not_retired(monkeypatch):
    monkeypatch.setattr(pricing, "close_window", lambda *_a: None)
    rt = make_runtime()
    card = rt.charter.cards[0]
    rt.registered_observations["probe-count"] = {
        "version": 1, "trial_tick": 0, "trial_window": 0, "unit_range": [0, 1],
        "description": "A probe.", "units": "count",
        "code": "def observe(facts):\n    return None\n"}
    rt.ticks_consumed = rt._patience() + 1
    motion = SimpleNamespace(add=(replace(card, id="probe", observation="probe-count"),),
                             replace=())
    monkeypatch.setattr(rt.charter_book, "pending", lambda: [motion])
    rt._close_price_window()
    assert not rt.registered_observations["probe-count"].get("retired")
    monkeypatch.setattr(rt.charter_book, "pending", lambda: [])
    rt._close_price_window()  # off the agenda and past its patience: retired
    assert rt.registered_observations["probe-count"].get("retired")
