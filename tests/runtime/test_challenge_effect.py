"""A challenge declares its own promise; the runtime never writes it (charter audit P2)."""

from factorylab.cortex.request import Return
from factorylab.kernel.queue import PropensityRecord
from tests.conftest import make_runtime


def _handle(rt, seat="seed-decider"):
    prop = PropensityRecord((seat,), (1.0,), seat, 0, "router:Tick", "test")
    handle = rt.queue.open(actor="router:Tick", event_id="challenge", propensity=prop,
                           channel="verdict", deadline_ns=10**18, parent_handle=None,
                           cost_ceiling=0)
    rt.handle_to_assembly[handle] = seat
    return handle


def _challenge(**changes):
    return {"kind": "challenge", "card_id": "well_formed_rate",
            "evidence": "the rate counts ballots", "trial_windows": 1,
            "replacement": {"observation": "well_formed_rate", "rule": "at least",
                            "value": 0.8, "window": {"kind": "returns", "n": 10,
                                                     "per": "role"}},
            "predicted_effect": {"card_id": "well_formed_rate", "direction": "decrease",
                                 "window": 2},
            **changes}


def _items(rt, kind):
    return [i for i in rt.ledger._recovery_items() if i["kind"] == kind]


def test_the_ballot_carries_the_challenger_s_declared_promise():
    rt = make_runtime()
    rt._manage_reserve_window()
    handle = _handle(rt)
    rt._apply_registrations(handle, Return(handle, {"register": [_challenge()]}, 0, "ok"))
    proposed, = _items(rt, "challenge.proposed")
    assert proposed["replacement"]["region"] == {"rule": "at least", "lo": 0.8, "hi": None}
    challenge_id = proposed["id"]
    rt._close_challenge_window(rt.window.index)
    rt._ballot_due_challenges()
    motion, = _items(rt, "charter.propose")
    # "at least" used to be read as a promise to increase, over one window.
    assert motion["id"] == challenge_id
    assert motion["predicted_effect"] == {"card_id": "well_formed_rate",
                                          "direction": "decrease", "window": 2,
                                          "observation": None}


def test_a_challenge_without_its_own_promise_or_naming_another_card_is_refused():
    rt = make_runtime()
    rt._manage_reserve_window()
    handle = _handle(rt)
    missing = {k: v for k, v in _challenge().items() if k != "predicted_effect"}
    other = _challenge(predicted_effect={"card_id": "forecast_skill",
                                         "direction": "increase", "window": 1})
    rt._apply_registrations(handle, Return(handle, {"register": [missing, other]}, 0, "ok"))
    reasons = [i["reason"] for i in _items(rt, "registration.rejected")]
    assert len(reasons) == 2 and "predicted_effect" in reasons[0]
    assert "names the challenged card" in reasons[1]
    assert _items(rt, "challenge.proposed") == []
