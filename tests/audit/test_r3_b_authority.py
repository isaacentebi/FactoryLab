"""Round three, group B: who may judge whom and who may write is a property of the
decision, never of the return kind (triage rows T2, T24, T25, T45).

Nothing here touches a network; every venue and provider is the fake one.
"""

import json
from decimal import Decimal

from factorylab.cortex.registration import AssemblyProposal
from factorylab.cortex.request import Return
from factorylab.kernel.queue import PropensityRecord
from factorylab.runtime.shared import CH_VERDICT
from factorylab.world.exchange import OrderResult
from factorylab.world.models import ModelResponse
from tests.conftest import make_runtime
from tests.runtime.test_loop import _consequence_produce


def _items(rt, kind):
    return [i for i in rt.ledger._recovery_items() if i["kind"] == kind]


def _reply(monkeypatch, rt, body):
    monkeypatch.setattr(
        rt.provider.target,
        "complete",
        lambda request: ModelResponse(request.model_id, json.dumps(body), 1, 1, "stop"),
    )


def _producing_decision(rt, assembly="seed-decider"):
    """A routed producing decision with an open consequence account."""
    actor = f"r3b:{assembly}:{rt.stats.decisions}"
    rt.stats.decisions += 1
    handle = rt.queue.open(
        actor=actor,
        event_id="r3b-input",
        propensity=PropensityRecord((assembly,), (1.0,), assembly, 0, actor, "test"),
        channel=CH_VERDICT,
        deadline_ns=10**15,
        parent_handle=None,
        cost_ceiling=10_000_000,
    )
    rt.handle_to_assembly[handle] = assembly
    rt._start_return(handle)
    return handle


def _register(rt, aid, *, accepts, emits):
    rt._register(
        "author",
        AssemblyProposal(
            aid, "producer", "fake-haiku", "Reply with JSON.", accepts, 128, "low", emits, {}
        ),
    )


# --- T24: an author whose contract can judge never draws its own subject ------------


def test_a_mixed_contract_author_is_not_drawn_to_judge_its_own_return(monkeypatch):
    """A ``(ProducerReturn, Verdict)`` contract used to stay in the router for its own
    return, be woken and paid, then refused at ``_judged_event``. It is excluded before
    the draw; a pure producer accepting its own kind still continues its own work."""
    rt = make_runtime()
    rt._manage_reserve_window()
    _register(rt, "dual", accepts=("Tick", "ProducerReturn"), emits=("ProducerReturn", "Verdict"))
    _register(rt, "pure", accepts=("Tick", "ProducerReturn"), emits=("ProducerReturn",))
    _reply(monkeypatch, rt, {"emits": "ProducerReturn", "action": "hold"})
    _handle, own = _consequence_produce(rt, "dual")
    assert "dual" not in rt._universe_for("ProducerReturn", own)
    assert "dual" in rt._universe_for("ProducerReturn", None)
    assert "pure" in rt._universe_for("ProducerReturn", own)
    _reply(monkeypatch, rt, {"action": "hold"})
    _handle, own = _consequence_produce(rt, "pure")
    assert "pure" in rt._universe_for("ProducerReturn", own)  # a producer may continue its work
    assert "dual" in rt._universe_for("ProducerReturn", own)


# --- T25: an unacknowledged venue write is its own outcome, finalised later ---------


def test_an_unacknowledged_venue_write_is_uncertain_not_failed_and_reconciles(monkeypatch):
    rt = make_runtime()
    handle = _producing_decision(rt)
    venue = rt.exchange.target
    original_place, original_lookup = venue.place, venue.lookup

    def place(order):
        original_place(order)  # the venue fills it; the acknowledgement is lost in transit
        return OrderResult(None, "uncertain", Decimal(0), None, "acknowledgement lost")

    outage = [True]

    def lookup(client_id, *, order_id=None):
        if outage:
            outage.pop()
            raise RuntimeError("venue unavailable")
        return original_lookup(client_id, order_id=order_id)

    monkeypatch.setattr(venue, "place", place)
    monkeypatch.setattr(venue, "lookup", lookup)
    calls = iter(
        [
            Return(
                handle,
                {},
                0,
                "ok",
                tool_calls=(
                    {
                        "tool": "venue.place_market",
                        "args": {"coin": "BTC", "side": "buy", "size": "0.001"},
                    },
                ),
            ),
            Return(handle, {"action": "hold"}, 0, "ok"),
        ]
    )
    monkeypatch.setattr(rt, "_invoke_compute", lambda *a: next(calls))
    req = rt._request(handle, "Trade", {}, {"type": "object"}, 10**15, CH_VERDICT)
    ret = rt._invoke("seed-decider", req, "producer")
    assert ret.status == "ok"
    call = _items(rt, "tool.call")[-1]
    assert call["outcome"] == "uncertain" and call["ok"] is True
    assert call["client_id"] == f"{handle}:tool:0"
    assert rt.stats.tool_calls == 1 and rt.stats.tool_call_failures == 0
    assert _items(rt, "order.uncertain")[-1]["client_id"] == call["client_id"]
    rt._reconcile_orders()
    acknowledged = _items(rt, "order.acknowledged")[-1]
    assert acknowledged["client_id"] == call["client_id"]
    assert acknowledged["result"]["status"] == "filled"
    assert rt.consequences.table.lots  # the fill now belongs to the decision that placed it
    # A call the kernel refuses is still a failure.
    calls = iter(
        [
            Return(handle, {}, 0, "ok", tool_calls=({"tool": "venue.nothing", "args": {}},)),
            Return(handle, {"action": "hold"}, 0, "ok"),
        ]
    )
    rt._invoke("seed-decider", req, "producer")
    assert _items(rt, "tool.call")[-1]["outcome"] == "failed"
    assert rt.stats.tool_call_failures == 1
