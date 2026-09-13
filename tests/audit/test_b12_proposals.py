"""B12: proposal errors are addressable refusals rather than discarded returns."""

import json

import pytest

from factorylab.cortex.assembly import _validate_return, reserved_return_fields
from factorylab.world.models import ModelResponse
from tests.conftest import make_runtime


@pytest.mark.parametrize("bad", [
    {"kind": "router", "event_kind": "Tick", "learner": "exp3", "add": "maybe"},
    {"kind": "assembly", "max_tokens": "512"},
    {"kind": "router", "gamma": 0},
    {"kind": "amendment", "add": ["not a card"]},
    17,
])
def test_bad_proposal_keeps_the_action_and_valid_sibling(bad, monkeypatch):
    rt = make_runtime()
    try:
        rt.reserve.open_window(rt.clock.now_ns, rt.wallet.balance)
        good = {"kind": "assembly", "id": "new-part", "model_id": "fake-haiku",
                "role": "producer", "accepts": ["Tick"], "system_prompt": "Answer the request."}
        body = {"action": "order", "coin": "BTC", "side": "buy", "size": "0.001",
                "register": [bad, good]}
        monkeypatch.setattr(rt.provider.target, "complete", lambda req: ModelResponse(
            req.model_id, json.dumps(body), 1, 1, "stop"))
        request = rt._request("proposer", "propose", {}, {}, 100, "test")
        ret = rt.assemblies["seed-decider"].invoke(request)
        assert ret.status == "ok" and ret.outputs["action"] == "order"
        rt._apply_registrations(request.handle, ret)
        assert "new-part" in rt.assemblies
        assert rt.stats.registrations_accepted == rt.stats.registrations_rejected == 1
        feedback, = rt.registration_feedback
        assert feedback["index"] == 0 and feedback["reason"]
        assert feedback in rt._world_block()["registration_feedback"]
    finally:
        rt._ledger_lock.close()


def test_reserved_names_and_types_are_published_from_the_validator():
    rt = make_runtime()
    try:
        declared = rt._world_block()["reserved_return_fields"]
        # A1: the published fan-out and tool-call caps are the manifest's own bounds.
        assert declared == reserved_return_fields(
            max_children=rt.m.tools.max_children, max_tool_calls=rt.m.tools.max_tool_calls)
        # A10 adds "propensity": a return's own account of the field it drew from.
        assert set(declared) == {"action", "rationale", "reason", "status", "coin", "side",
                                 "verdict", "payoff", "conformity", "vote", "register",
                                 "tool_calls", "requests", "forecasts", "emits", "about_handle",
                                 "propensity"}
        assert declared["status"] == {"type": "string"}
        with pytest.raises(ValueError):
            _validate_return({"status": {"done": True}}, {"type": "object"})
    finally:
        rt._ledger_lock.close()
