"""W8 admission, metering, composition and recovery tests use no live transport."""

import json
import tomllib
from dataclasses import replace
from types import SimpleNamespace

import pytest

from factorylab.cortex.registration import ConnectorProposal, parse_proposals
from factorylab.cortex.request import Return
from factorylab.cortex.sandbox import jail_available
from factorylab.kernel.queue import PropensityRecord
from factorylab.kernel.registry import Contract, PriceSpec, ResourceBounds
from factorylab.runtime.resume import RecoveryJournal, restore_runtime, runtime_state
from factorylab.runtime.wake import public_window_item, render_wake
from factorylab.runtime.worlds import WORLDS_DIR, ConnectorsSpec, manifest_from_dict
from factorylab.world.connector import ConnectorProxy
from factorylab.world.models import ModelResponse
from factorylab.world.scripted import ScriptedProvider
from tests.conftest import make_runtime
from tests.world.test_connector import Transport


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def deny(*args, **kwargs):
        pytest.fail("real network is forbidden")
    monkeypatch.setattr("socket.create_connection", deny)
    monkeypatch.setattr("socket.getaddrinfo", deny)


def decision(rt, owner="seed-decider"):
    handle = rt.queue.open(
        actor=owner, event_id="connector-test", propensity=PropensityRecord(
            (owner,), (1.,), owner, 0, owner, "test"), channel="verdict",
        deadline_ns=10**15, parent_handle=None, cost_ceiling=10_000_000,
    )
    rt.handle_to_assembly[handle] = owner
    return handle


def register(rt, monkeypatch, owner="seed-decider", origin="https://example.org"):
    rt._manage_reserve_window()
    handle = decision(rt, owner)
    # Unit fixture supplies eligibility; the scripted acceptance test uses real consequences.
    monkeypatch.setattr(rt, "_committee_eligible", lambda: {
        "seed-decider": "producer", "eval-a": "evaluator", "meta-a": "meta"})
    rt._apply_registrations(handle, Return(handle, {"register": [{
        "kind": "connector", "id": "source", "description": "Public data", "origin": origin,
        "predicted_effect": {"card_id": "cost_per_return", "direction": "decrease", "window": 1},
    }]}, 0, "ok"))
    return handle


def ledger_items(rt, kind=None):
    items = rt.ledger._recovery_items()
    return [i for i in items if kind is None or i["kind"] == kind]


def test_minimal_registration_shape_and_no_extra_knobs():
    item = {"kind": "connector", "id": "source", "description": "Public data",
            "origin": "https://example.org"}
    for extra in ({}, {"headers": {}}, {"path_prefix": "/x"}, {"params_schema": {}}):
        accepted, rejected = parse_proposals(
            {"register": [{**item, **extra}]}, event_kinds=frozenset(),
            known_models=frozenset(), known_assemblies=frozenset(), tool_jail=False,
        )
        if not extra:
            assert accepted == [ConnectorProposal("source", "Public data", "https://example.org")]
        else:
            assert not accepted and "connector fields" in rejected[0].reason


def test_preflight_vote_then_versioned_admission_with_proposer_excluded(monkeypatch):
    rt = make_runtime()
    assert rt.registry.available("connector") == []
    handle = register(rt, monkeypatch)
    contract = rt.registry.get("connector:source")
    assert contract.kind == "connector" and contract.version == 1 and contract.provenance == handle
    assert rt.stats.registrations_accepted == 1
    items = ledger_items(rt)
    kinds = [i["kind"] for i in items]
    assert kinds.index("connector.call") < kinds.index("connector.seated")
    assert kinds.index("connector.tally") < kinds.index("connector.registered")
    seats = ledger_items(rt, "connector.seated")[0]["seats"]
    assert {seat["assembly_id"] for seat in seats} == {"eval-a", "meta-a"}
    ballots = ledger_items(rt, "connector.vote")
    assert len(ballots) == 2 and all(row["vote"] is True for row in ballots)
    assert all(not rt.queue.history(row["handle"]) for row in ballots)
    assert {v["handle"] for v in rt.pending_votes} == {row["handle"] for row in ballots}
    assert all(v["activation_window"] == rt.window.index for v in rt.pending_votes)
    register(rt, monkeypatch, origin="https://example.net")
    assert rt.registry.get("connector:source").version == 2
    assert rt.registry.get("connector:source", 1).input_schema["origin"] == "https://example.org"
    assert len(rt._connector_catalogue()) == 1
    assert rt.wallet.check_conservation()


@pytest.mark.parametrize("origin,reason", [("http://example.org", "https"),
                                           ("https://openrouter.ai", "denylisted")])
def test_registration_refusals_are_public_and_never_vote(monkeypatch, origin, reason):
    rt = make_runtime()
    register(rt, monkeypatch, origin=origin)
    assert not rt.registry.available("connector")
    assert reason in rt.registration_feedback[-1]["reason"]
    assert ledger_items(rt, "connector.refused") and not ledger_items(rt, "connector.seated")


def test_failed_preflight_and_no_majority_cannot_admit(monkeypatch):
    rt = make_runtime()
    rt.connector_proxy = ConnectorProxy(replace(rt.m.connectors, max_bytes=2), Transport())
    register(rt, monkeypatch)
    assert not rt.registry.available("connector") and not ledger_items(rt, "connector.seated")
    assert "max_bytes" in rt.registration_feedback[-1]["reason"]
    rt.connector_proxy = ConnectorProxy(rt.m.connectors, Transport())
    monkeypatch.setattr(rt.provider.target, "complete", lambda req:
                        ModelResponse(req.model_id, '{"vote": false, "reason": "no"}', 1, 1,
                                      "end_turn"))
    register(rt, monkeypatch)
    assert not rt.registry.available("connector")
    assert ledger_items(rt, "connector.tally")[-1]["passed"] is False


def test_empty_experienced_population_refuses_admission(monkeypatch):
    rt = make_runtime()
    rt._manage_reserve_window()
    handle = decision(rt)
    assert rt._committee_eligible() == {}
    rt._apply_registrations(handle, Return(handle, {"register": [{
        "kind": "connector", "id": "source", "description": "Data", "origin": "https://example.org",
        "predicted_effect": {"card_id": "cost_per_return", "direction": "decrease", "window": 1},
    }]}, 0, "ok"))
    assert not rt.registry.available("connector")
    assert "majority" in rt.registration_feedback[-1]["reason"]


def test_flat_cost_precedes_return_and_window_cap_survives_checkpoint(monkeypatch):
    rt = make_runtime()
    register(rt, monkeypatch)
    bounds = replace(rt.m.connectors, max_calls_per_window=2)
    rt.m = replace(rt.m, connectors=bounds)
    transport = Transport()
    rt.connector_proxy = ConnectorProxy(bounds, transport)
    handle = decision(rt)
    before = rt.wallet.balance
    result, cost = rt._run_tool("seed-decider", handle, {
        "tool": "connector.fetch", "args": {"id": "source", "path": "/data"}})
    assert result["body"] == '{"value": 42}' and cost == 1000
    assert rt.wallet.balance == before - cost
    assert (ledger_items(rt, "wallet.commit")[-1]["seq"]
            < ledger_items(rt, "connector.call")[-1]["seq"])
    result, cost = rt._fetch_connector("seed-decider", handle, {"id": "source", "path": "/again"})
    assert "cap" in result["error"] and cost == 0 and len(transport.calls) == 1
    state = runtime_state(rt)
    rt2 = make_runtime()
    rt2.m = rt.m
    restore_runtime(rt2, state)
    assert rt2.connector_calls == rt.connector_calls
    assert rt2.connector_calls_day == rt.connector_calls_day
    assert rt2.registry.get("connector:source").version == 1
    result, cost = rt2._fetch_connector("seed-decider", handle, {"id": "source", "path": "/"})
    assert "cap" in result["error"] and cost == 0
    rt.window.index += 1
    assert rt._fetch_connector("seed-decider", handle, {"id": "source", "path": "/"})[1] == 1000


def test_unaffordable_calls_cannot_dispatch_or_consume_quota(monkeypatch):
    rt = make_runtime()
    register(rt, monkeypatch)
    transport = Transport()
    rt.connector_proxy = ConnectorProxy(rt.m.connectors, transport)
    calls = dict(rt.connector_calls)
    monkeypatch.setattr(rt.meter.wallet, "reserve", lambda *args, **kwargs:
                        (_ for _ in ()).throw(ValueError("infeasible")))
    result, cost = rt._fetch_connector("seed-decider", "new", {"id": "source", "path": "/"})
    assert "unaffordable" in result["error"] and cost == 0 and not transport.calls
    assert rt.connector_calls == calls


def test_failure_after_dispatch_is_metered_and_counted(monkeypatch):
    rt = make_runtime()
    register(rt, monkeypatch)
    rt.connector_proxy = ConnectorProxy(rt.m.connectors, Transport(error=TimeoutError()))
    before = rt.wallet.balance
    result, cost = rt._fetch_connector("eval-a", decision(rt, "eval-a"),
                                      {"id": "source", "path": "/"})
    assert result["error"] == "connector timeout" and cost == 1000
    assert rt.wallet.balance == before - 1000
    assert rt.connector_calls["eval-a"][1] == 1


def parser_tool():
    return {"kind": "tool", "id": "connector-parser", "description": "Parse the value",
            "args_schema": {"type": "object", "properties": {"body": {"type": "string"}},
                            "required": ["body"]},
            "code": "import json,sys\na=json.load(sys.stdin)\n"
                    "print(json.dumps({'value': json.loads(a['body'])['value']}))", "timeout_s": 2}


def composition(rt, monkeypatch, *, stub_parser):
    register(rt, monkeypatch)
    if stub_parser:
        rt.tool_jail_available = True
        # This unit stub parses only fixture JSON; it does not execute population source.
        monkeypatch.setattr(rt.tool_runner.target, "run", lambda tool, args:
                            {"value": json.loads(args["body"])["value"]})
    handle = decision(rt)
    rt._apply_registrations(handle, Return(handle, {"register": [parser_tool()]}, 0, "ok"))
    requests = []
    body = '{"value": 42, "sentinel": "BODY-NEVER-IN-LEDGER"}'
    rt.connector_proxy = ConnectorProxy(rt.m.connectors, Transport(body.encode()))

    def complete(req):
        requests.append(req)
        if len(requests) == 1:
            reply = {"tool_calls": [{"tool": "connector.fetch",
                                    "args": {"id": "source", "path": "/data"}}]}
        elif len(requests) == 2:
            assert "BODY-NEVER-IN-LEDGER" in str(req.messages)
            assert "seen_tool_results" in str(req.messages)
            reply = {"tool_calls": [{"tool": "connector-parser", "args": {"body": body}}]}
        else:
            assert "42" in str(req.messages)
            reply = {"action": "hold", "parsed": 42}
        return ModelResponse(req.model_id, json.dumps(reply), 1, 1, "end_turn")

    monkeypatch.setattr(rt.provider.target, "complete", complete)
    rt.ledger.active = True
    req = rt._request(handle, "Produce a return", {}, {
        "type": "object", "properties": {"action": {"type": "string"}}, "required": ["action"]},
        10**15, "verdict")
    ret = rt._invoke("seed-decider", req, "producer")
    assert ret.status == "ok" and ret.outputs["parsed"] == 42
    assert len(requests) == 3
    assert ret.cost == 3 * 30 + 1000 + rt.m.tools.population_tool_micro_per_call
    rows = ledger_items(rt)
    # The body is recovery evidence on the io plane only; no public surface carries it.
    public = [row for row in rows if row["kind"] not in ("io.call", "io.result")]
    assert "BODY-NEVER-IN-LEDGER" not in json.dumps(public, default=str)
    assert [row["tool"] for row in rows if row["kind"] == "tool.call"] == [
        "connector.fetch", "connector-parser"]
    assert not rt.ledger.connector_bodies
    return rt


def test_fetch_continuation_composes_with_parser_stub_and_ledger_omits_body(monkeypatch):
    composition(make_runtime(), monkeypatch, stub_parser=True)


def test_fetch_continuation_composes_with_actual_jailed_parser(monkeypatch):
    if not jail_available():
        pytest.skip("actual jail unavailable; separate stub test covers plumbing only")
    composition(make_runtime(), monkeypatch, stub_parser=False)


def test_observatory_contains_connector_versions_and_daily_counts(monkeypatch):
    rt = make_runtime()
    register(rt, monkeypatch)
    item = public_window_item(rt, window=1, event=10)
    assert item["connectors"]["registered"][0]["id"] == "source"
    assert item["connectors"]["calls_per_day"] == {"1970-01-01": 1}
    assert "Connectors" in render_wake({"connectors": item["connectors"]})
    block = rt._world_block()
    assert block["connectors"]["call_price_micro"] == 1000
    assert block["connectors"]["registered"] == rt._connector_catalogue()
    assert any(tool["id"] == "connector.fetch" for tool in block["tools"])
    # The block states the registration shape, the call shape and the price. How a
    # registration is admitted is physics the runtime enforces, not prompt text.
    connectors = json.dumps(block["connectors"])
    assert not any(word in connectors for word in
                   ("admission", "preflight", "sortition", "vote", "majority", "committee"))
    assert block["proposal_shapes"]["connector"] == {
        "kind": "connector", "id": "public-source", "description": "Public information",
        "origin": "https://example.org",
        "predicted_effect": {"card_id": "a current card id", "direction": "decrease", "window": 1}}


@pytest.mark.parametrize("fields", [
    {"max_bytes": True}, {"max_bytes": 0}, {"timeout_s": 0}, {"timeout_s": 1.5},
    {"call_price_usd": 0.001}, {"call_price_usd": "-1"}, {"call_price_usd": "0.0000001"},
    {"max_calls_per_window": False}, {"origin_denylist": "example.org"},
    {"origin_denylist": ["https://example.org"]}, {"path_prefix": "/x"},
])
def test_manifest_rejects_extra_knobs_and_inexact_money(fields):
    raw = tomllib.loads((WORLDS_DIR / "scripted.toml").read_text())
    raw["connectors"] = fields
    with pytest.raises((ValueError, TypeError)):
        manifest_from_dict(raw)


def test_manifest_defaults_are_minimal_and_prices_are_integer():
    raw = tomllib.loads((WORLDS_DIR / "scripted.toml").read_text())
    raw.pop("connectors")
    m = manifest_from_dict(raw)
    assert m.connectors == ConnectorsSpec()
    assert type(m.connectors.call_price_micro) is int
    assert len(m.connectors.__dataclass_fields__) == 5


def test_connector_contract_cannot_overwrite_version_or_skip_novelty_receipt():
    rt = make_runtime()
    contract = Contract("connector:test", 1, "connector", "test", {"origin": "https://example.org"},
                        {"type": "string"}, PriceSpec({"call": 1000}), frozenset(),
                        ResourceBounds())
    with pytest.raises(PermissionError):
        rt.registry.register(contract, by_handle="population")
    # Kernel fixture only; manifests seed no connectors.
    rt.registry.register(contract)
    with pytest.raises(ValueError, match="version 2"):
        rt.registry.register(contract)


def test_journal_redacts_body_and_escaped_body_copies_outside_the_recovery_plane():
    written = []
    journal = RecoveryJournal(SimpleNamespace(append=lambda item: written.append(item)), lambda: 0)
    body = '{"remote": "SENTINEL"}'
    journal.protect_connector_body(body)
    journal.append({"kind": "invocation", "outputs": {"text": json.dumps({"body": body})}})
    assert "SENTINEL" not in json.dumps(written)
    # Replay needs the recorded read itself, so io.result keeps what it recorded.
    journal.append({"kind": "io.result", "call": 1, "result": {"body": body}})
    assert "SENTINEL" in json.dumps(written[-1])


def test_scripted_population_proposes_and_composes_in_sequence():
    provider = ScriptedProvider(_producer_calls=159)
    registration = provider._produce("Produce", {})
    assert [p["kind"] for p in registration["register"]] == ["connector", "tool"]
    fetch = provider._produce("Produce", {})
    assert fetch["tool_calls"][0]["tool"] == "connector.fetch"
    parse = provider._produce("Produce", {"tool_results": [{"tool": "connector.fetch",
                              "result": {"body": '{"value": 42}'}}]})
    assert parse["tool_calls"][0]["tool"] == "connector-parser"


@pytest.mark.parametrize("stub_parser", [True, False], ids=["stub-parser", "actual-jail"])
def test_scripted_world_admits_by_real_sortition_then_fetches_and_parses(monkeypatch, stub_parser):
    if not stub_parser and not jail_available():
        pytest.skip("actual jail unavailable; stub case tests the remaining acceptance chain")
    rt = make_runtime()
    rt.events_budget = 200
    if stub_parser:
        rt.tool_jail_available = True
        monkeypatch.setattr(rt.tool_runner.target, "run", lambda tool, args:
                            {"value": json.loads(args["body"])["value"]}
                            if tool.id == "connector-parser" else {"half_spread": 0})
    rt.run()
    rows = ledger_items(rt)
    registered = [r for r in rows if r["kind"] == "connector.registered"]
    assert registered, rt.registration_feedback
    called = [r for r in rows if r["kind"] == "connector.call" and r["path"] == "/data"]
    parsed = [r for r in rows if r["kind"] == "tool.call" and r["tool"] == "connector-parser"]
    assert called and parsed and parsed[0]["ok"]
    assert registered[0]["seq"] < called[0]["seq"] < parsed[0]["seq"]
    assert any('"connector_value": 42' in r.get("outputs", "") for r in rows)
    assert rt.wallet.check_conservation()


def recording_journal():
    recorded = []

    def append(item):
        recorded.append(item)
        return len(recorded) - 1

    journal = RecoveryJournal(SimpleNamespace(append=append), lambda: 0)
    journal.active = True
    return journal, recorded


def test_recovery_replays_a_connector_call_from_its_journalled_result():
    """A call after the last checkpoint replays from its recorded io.call/io.result pair,
    the same way an x402 purchase does: no second fetch, no re-read of changed information."""
    from factorylab.runtime.resume import _read_only

    assert _read_only("connector.fetch")  # an interrupted GET is safe to re-issue
    journal, recorded = recording_journal()
    transport = Transport()
    proxy = ConnectorProxy(ConnectorsSpec(), transport)
    result = journal.call("connector.fetch", proxy.fetch, ("https://example.org", "/data"), {})
    assert result["body"] == '{"value": 42}' and len(transport.calls) == 1
    assert [row["kind"] for row in recorded] == ["io.call", "io.result"]

    def refuse(*args, **kwargs):
        raise AssertionError("replay must not fetch again")

    replay, _ = recording_journal()
    replay.tail = [{**row, "seq": index, "ts": 0} for index, row in enumerate(recorded)]
    assert replay.call("connector.fetch", refuse, ("https://example.org", "/data"), {}) == result
    assert replay.peek() is None


def test_request_ceiling_blocks_connector_before_dispatch(monkeypatch):
    rt = make_runtime()
    register(rt, monkeypatch)
    transport = Transport()
    rt.connector_proxy = ConnectorProxy(rt.m.connectors, transport)
    handle = decision(rt)
    calls = iter([
        Return(handle, {}, 1, "ok", tool_calls=({"tool": "connector.fetch",
                "args": {"id": "source", "path": "/"}},)),
        Return(handle, {"action": "hold"}, 0, "ok"),
    ])
    monkeypatch.setattr(rt, "_invoke_compute", lambda *a: next(calls))
    req = rt._request(handle, "Produce", {}, {"type": "object"}, 10**15, "verdict")
    ret = rt._invoke("seed-decider", replace(req, cost_ceiling=1000), "producer")
    assert ret.cost == 1 and not transport.calls
    assert ledger_items(rt, "connector.refused")[-1]["reason"] == "request cost ceiling exhausted"


@pytest.mark.parametrize("owner,role,child", [
    ("eval-a", "evaluator", False), ("meta-a", "meta", False),
    ("antagonist-a", "antagonist", False), ("seed-decider", "child", True),
])
def test_fetch_is_available_on_judging_antagonist_and_child_returns(
    monkeypatch, owner, role, child,
):
    rt = make_runtime()
    register(rt, monkeypatch)
    handle = decision(rt, owner)
    calls = iter([
        Return(handle, {}, 0, "ok", tool_calls=({"tool": "connector.fetch",
                "args": {"id": "source", "path": "/"}},)),
        Return(handle, {"answer": 42}, 0, "ok"),
    ])
    monkeypatch.setattr(rt, "_invoke_compute", lambda *a: next(calls))
    req = rt._request(handle, "Read", {}, {"type": "object"}, 10**15, "verdict")
    ret = rt._invoke(owner, req, role, child=child)
    assert ret.status == "ok" and ret.cost == 1000
    assert ret.outputs == {"answer": 42}


def test_body_cannot_redact_connector_paths_ids_or_metering_metadata():
    written = []
    journal = RecoveryJournal(SimpleNamespace(append=lambda item: written.append(item)), lambda: 0)
    journal.protect_connector_body("/")
    journal.append({"kind": "connector.call", "id": "source", "path": "/", "status": 200,
                    "bytes": 1, "cost": 1000})
    assert written == [{"kind": "connector.call", "id": "source", "path": "/", "status": 200,
                        "bytes": 1, "cost": 1000}]


def test_short_body_cannot_rewrite_an_order_side_into_a_different_action(monkeypatch):
    """A body shorter than ``MIN_PROTECTED_BODY_CHARS`` is a fact, not text: it is neither
    redacted nor refused, so the side the return declared stands exactly as written.
    A body at the threshold is text and stays off every durable surface."""
    from factorylab.runtime.compute import MIN_PROTECTED_BODY_CHARS

    rt = make_runtime()
    register(rt, monkeypatch)
    rt.connector_proxy = ConnectorProxy(rt.m.connectors, Transport(b"buy"))
    handle = decision(rt)
    calls = iter([
        Return(handle, {}, 0, "ok", tool_calls=({"tool": "connector.fetch",
                "args": {"id": "source", "path": "/"}},)),
        Return(handle, {"action": "order", "coin": "BTC", "side": "buy", "size": "1"}, 0, "ok"),
    ])
    monkeypatch.setattr(rt, "_invoke_compute", lambda *a: next(calls))
    req = rt._request(handle, "Read", {}, {"type": "object"}, 10**15, "verdict")
    ret = rt._invoke("seed-decider", req, "producer")
    assert ret.status == "ok" and ret.outputs["side"] == "buy"
    assert ret.tool_calls == () and ret.children == ()
    assert not rt.ledger.connector_bodies
    text = "b" * MIN_PROTECTED_BODY_CHARS
    rt.connector_proxy = ConnectorProxy(rt.m.connectors, Transport(text.encode()))
    handle = decision(rt)
    calls = iter([
        Return(handle, {}, 0, "ok", tool_calls=({"tool": "connector.fetch",
                "args": {"id": "source", "path": "/"}},)),
        Return(handle, {"action": "hold", "rationale": f"the source said {text}"}, 0, "ok"),
    ])
    req = rt._request(handle, "Read", {}, {"type": "object"}, 10**15, "verdict")
    ret = rt._invoke("seed-decider", req, "producer")
    assert ret.status == "malformed" and "action" not in ret.outputs
