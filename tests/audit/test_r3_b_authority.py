"""Round three, group B: who may judge whom and who may write is a property of the
decision, never of the return kind (triage rows T2, T24, T25, T45).

Nothing here touches a network; every venue and provider is the fake one.
"""

import json
from decimal import Decimal
from types import SimpleNamespace

from factorylab.charter.committee import Committee, Seat
from factorylab.cortex.registration import AssemblyProposal, ConnectorProposal
from factorylab.cortex.request import Return
from factorylab.kernel.queue import PropensityRecord, SettleStatus
from factorylab.runtime.shared import CH_VERDICT
from factorylab.world.exchange import OrderResult
from factorylab.world.models import ModelResponse
from tests.conftest import make_runtime
from tests.runtime.test_loop import _consequence_judge, _consequence_produce


def _items(rt, kind):
    return [i for i in rt.ledger._recovery_items() if i["kind"] == kind]


def _reply(monkeypatch, rt, body):
    monkeypatch.setattr(rt.provider.target, "complete", lambda request: ModelResponse(
        request.model_id, json.dumps(body), 1, 1, "stop"))


def _producing_decision(rt, assembly="seed-decider"):
    """A routed producing decision with an open consequence account."""
    actor = f"r3b:{assembly}:{rt.stats.decisions}"
    rt.stats.decisions += 1
    handle = rt.queue.open(
        actor=actor, event_id="r3b-input",
        propensity=PropensityRecord((assembly,), (1.0,), assembly, 0, actor, "test"),
        channel=CH_VERDICT, deadline_ns=10**15, parent_handle=None, cost_ceiling=10_000_000,
    )
    rt.handle_to_assembly[handle] = assembly
    rt._start_return(handle)
    return handle


def _register(rt, aid, *, accepts, emits):
    rt._register("author", AssemblyProposal(aid, "producer", "fake-haiku", "Reply with JSON.",
                                            accepts, 128, "low", emits, {}))


# --- T2: a ballot is a policy decision whatever the voter's contract says ---------


def test_a_ballot_binds_no_return_kind_and_a_two_kind_voter_can_still_vote(monkeypatch):
    """A ballot is not a contract return: it records no emitted kind, so a voter that
    declares two kinds is not refused for omitting ``emits``, and the ballot decision
    keeps the queue's policy channel, under which no write is ever permitted."""
    rt = make_runtime()
    rt._manage_reserve_window()
    _register(rt, "dual", accepts=("Tick",), emits=("ProducerReturn", "Verdict"))
    _reply(monkeypatch, rt, {"vote": True, "reason": "useful source"})
    committee = Committee("source-vote", 1, (Seat("seat-1", "dual", "producer"),))
    rt._hold_vote(SimpleNamespace(id="source-vote", proposer_handle=None), committee,
                  connector=ConnectorProposal("weather", "Weather", "https://example.com"))
    vote = _items(rt, "connector.vote")[-1]
    assert vote["vote"] is True and rt.stats.votes_cast == 1
    handle = vote["handle"]
    assert handle not in rt.return_kinds
    assert rt.queue.get(handle).channel == "policy"
    assert not rt._may_write(handle)


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


# --- T45: an unresolvable about_handle is not a discarded judgement -----------------


def test_prose_in_about_handle_falls_back_to_the_delivered_subject_with_feedback(monkeypatch):
    rt = make_runtime()
    rt._manage_reserve_window()
    subject, event = _consequence_produce(rt, "seed-observer")
    _reply(monkeypatch, rt, {"verdict": 0.6, "payoff": 0.4, "rationale": "fine",
                             "forecasts": [], "about_handle": "current-return"})
    judge = _consequence_judge(rt, event, "eval-a")
    assert rt.queue.get(subject).status is SettleStatus.SETTLED
    assert rt.stats.verdicts == 1 and rt.stats.forecasts_sealed == 1
    assert not _items(rt, "return.refused")
    ignored = _items(rt, "about_handle.ignored")[-1]
    assert ignored["handle"] == judge and ignored["about_handle"] == "current-return"
    assert ignored["subject"] == subject
    assert any(f["reason"].startswith("judgement: about_handle")
               for f in rt.registration_feedback)
    assert "about_handle" in rt.A_RETURN_MAY_INCLUDE
    assert "about_handle" in rt._world_block()["a_return_may_include"]


def test_a_refused_judgement_puts_its_reason_in_registration_feedback(monkeypatch):
    rt = make_runtime()
    rt._manage_reserve_window()
    _subject, event = _consequence_produce(rt, "seed-observer")
    stranger = _producing_decision(rt, "seed-decider")  # addressable, but never returned
    _reply(monkeypatch, rt, {"verdict": 0.6, "payoff": 0.4, "rationale": "fine",
                             "forecasts": [], "about_handle": stranger})
    _consequence_judge(rt, event, "eval-a")
    refused = _items(rt, "return.refused")[-1]
    assert refused["about_handle"] == stranger
    assert any(f["reason"] == f"judgement: {refused['reason']}"
               for f in rt.registration_feedback)


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
    calls = iter([
        Return(handle, {}, 0, "ok", tool_calls=({"tool": "venue.place_market", "args": {
            "coin": "BTC", "side": "buy", "size": "0.001"}},)),
        Return(handle, {"action": "hold"}, 0, "ok"),
    ])
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
    calls = iter([
        Return(handle, {}, 0, "ok", tool_calls=({"tool": "venue.nothing", "args": {}},)),
        Return(handle, {"action": "hold"}, 0, "ok"),
    ])
    rt._invoke("seed-decider", req, "producer")
    assert _items(rt, "tool.call")[-1]["outcome"] == "failed"
    assert rt.stats.tool_call_failures == 1
