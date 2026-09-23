"""Mechanical gate for the bounded Edition 4 paid-discovery diagnostic.

Every test here runs against a canned provider: no credential is read, no
network call is made and no paid completion is bought.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from factorylab.world.models import ModelRequest, ModelResponse
from scripts import edition4_discovery_probe as probe
from scripts import edition4_investigation_probe as investigation


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """No test here may open a socket: every completion comes from a fixture."""

    def deny(*args, **kwargs):
        pytest.fail("real network is forbidden")

    monkeypatch.setattr("socket.create_connection", deny)
    monkeypatch.setattr("socket.getaddrinfo", deny)


class CannedProvider:
    """Answer each completion from a fixture, recording what was asked."""

    name = "canned"

    def __init__(self, replies, cost_micro=120):
        self.replies = replies
        self.cost_micro = cost_micro
        self.prompts = []
        self.by_model = {}

    def catalogue(self):
        return []

    def balance_micro(self):
        return 10_000_000

    def affordable(self, model_id, ceiling_micro):
        return True, ""

    def complete(self, request):
        prompt = request.system + "".join(
            str(message.get("content", "")) for message in request.messages)
        self.prompts.append(prompt)
        # Each seat runs its own fixture sequence: one model, one conversation.
        seen = self.by_model.get(request.model_id, 0) + 1
        self.by_model[request.model_id] = seen
        reply = self.replies[min(seen, len(self.replies)) - 1]
        body = reply(prompt) if callable(reply) else reply
        return ModelResponse(request.model_id, json.dumps(body), 10, 10, "stop",
                             cost_micro=self.cost_micro)


def discovery_chain():
    """Look the catalogue up, call what it returned, answer with the receipt."""

    def final(prompt):
        value = probe.EXPECTED_VALUE if probe.EXPECTED_VALUE in prompt else "unknown"
        return {"notional_usd": value,
                "evidence": "the published arithmetic capability returned " + value}

    return [
        {"tool_calls": [{"tool": "catalogue.search", "args": {"substring": "notional"}}]},
        {"tool_calls": [{"tool": "calc", "args": {"op": "notional", "size": probe.TASK_SIZE,
                                                  "price": probe.TASK_PRICE}}]},
        final,
    ]


def paths(tmp_path):
    return {"out": tmp_path / "attempts.jsonl", "freeze": tmp_path / "freeze.json"}


def records(out):
    return [json.loads(line) for line in out.read_text().splitlines() if line.strip()]


def journal(record):
    return [json.loads(line) for line in
            open(record["journal"], encoding="utf-8").read().splitlines() if line.strip()]


class BrokenProvider(CannedProvider):
    """A provider whose transport fails after the request has been dispatched."""

    def complete(self, request):
        self.prompts.append("dispatched")
        raise RuntimeError("transport failed")


def test_default_run_freezes_the_preflight_and_buys_nothing(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("a paid provider must not be constructed without --paid")

    monkeypatch.setattr(probe, "build_prepaid_provider", forbidden)
    where = paths(tmp_path)
    record = probe.run_probe(out=where["out"], freeze=where["freeze"])

    assert record["status"] == "frozen" and record["executed"] is False
    assert record["paid"] is False and record["reason"] == "dry_preflight_no_provider"
    assert record["cost"]["attempted"] == 0 and record["cost"]["cap_micro"] == 1_000_000
    frozen = json.loads(where["freeze"].read_text())
    assert frozen["preflight_digest"] == record["preflight_digest"]
    # The frozen inputs are the ones that determine what would be bought.
    assert frozen["preflight"]["task"] == probe.TASK
    assert frozen["preflight"]["seats"] == list(probe.DEFAULT_SEATS)
    assert frozen["preflight"]["world"]["prompt_mode"] == "compact"
    assert frozen["preflight"]["expected_value"] == probe.EXPECTED_VALUE
    assert set(frozen["preflight"]["models"].values()) == {
        "z-ai/glm-5.3-flash", "openai/gpt-5.6-luna"}
    assert [row["executed"] for row in records(where["out"])] == [False]


def test_the_full_chain_is_read_from_receipts_not_from_prose(tmp_path):
    where = paths(tmp_path)
    provider = CannedProvider(discovery_chain())
    record = probe.run_probe(out=where["out"], freeze=where["freeze"], provider=provider)

    assert record["status"] == "completed" and record["executed"] is True
    assert record["paid"] is False
    assert record["chain_complete_seats"] == list(probe.DEFAULT_SEATS)
    for seat in record["seats"]:
        assert seat["status"] == "ok"
        assert [call["tool"] for call in seat["tool_calls"]] == ["catalogue.search", "calc"]
        assert seat["discovered"] and seat["invoked"]
        assert seat["receipt_value"] == probe.EXPECTED_VALUE
        assert seat["answer_matches_receipt"] and seat["answer_matches_expected"]
        assert seat["refused_calls"] == []
    # The task never names the capability it expects to be used.
    assert "calc" not in probe.TASK and "catalogue" not in probe.TASK
    # What the seat was told is the world's own compact prompt, and the receipt
    # value reached the answer through the continuation rather than by restatement.
    assert any(probe.EXPECTED_VALUE in prompt for prompt in provider.prompts)
    assert record["commission"].startswith("Commissioned diagnostic")


def test_every_dispatch_is_journalled_and_the_initial_request_is_the_frozen_one(tmp_path):
    where = paths(tmp_path)
    provider = CannedProvider(discovery_chain())
    record = probe.run_probe(out=where["out"], freeze=where["freeze"], provider=provider)
    rows = journal(record)

    assert rows[0]["event"] == "run_start"
    assert rows[-1]["event"] == "run_end" and rows[-1]["status"] == "completed"
    attempts = [row for row in rows if row["event"] == "attempt"]
    results = [row for row in rows if row["event"] == "result"]
    # One attempt and one result per completion, each carrying what it cost.
    assert len(attempts) == len(results) == len(provider.prompts)
    assert all(row["admission"]["attempted"] >= 1 for row in results)
    # The initial request of each seat is the exact request the preflight froze;
    # the continuations are rendered from what the seat retrieved and are only
    # journalled.
    frozen = json.loads(where["freeze"].read_text())["preflight"]["rendered_requests"]
    assert set(frozen) == set(probe.DEFAULT_SEATS)
    initial = {row["seat"]: row["request_sha256"] for row in attempts if row["initial"]}
    assert initial == frozen
    assert [row["request_sha256"] for row in attempts if not row["initial"]]


def test_a_request_that_drifted_from_the_frozen_one_is_refused_before_dispatch(tmp_path):
    inner = CannedProvider(discovery_chain())
    run = probe.RunJournal(tmp_path / "journal.jsonl", {"preflight_digest": "fixture"})
    guard = probe.JournalingProvider(inner, run, {"mechanism": "a" * 64},
                                     probe.Admission(1_000_000, 12))
    guard.begin_seat("mechanism")
    request = ModelRequest(model_id="venice:z-ai-glm-5-3-flash", system="a different prompt",
                           messages=({"role": "user", "content": "drifted"},))

    with pytest.raises(probe.ProbeRefused):
        guard.complete(request)
    run.close()

    assert inner.prompts == []
    refusal = json.loads((tmp_path / "journal.jsonl").read_text().splitlines()[-1])
    assert refusal["event"] == "dispatch_refused"
    assert refusal["reason"] == "rendered_request_mismatch"
    assert refusal["frozen_sha256"] == "a" * 64


def test_a_crashed_run_leaves_a_marker_that_refuses_the_next_attempt(tmp_path):
    where = paths(tmp_path)
    frozen = probe.run_probe(out=where["out"], freeze=where["freeze"])
    marker = probe._journal_path(where["out"], frozen["preflight_digest"])
    # A run that died mid-dispatch leaves its journal and no summary record.
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"event": "run_start"}) + "\n")

    provider = CannedProvider(discovery_chain())
    record = probe.run_probe(out=where["out"], freeze=where["freeze"], provider=provider)

    assert record["status"] == "failed" and record["reason"] == "run_already_started"
    assert record["executed"] is False and provider.prompts == []
    assert [row["executed"] for row in records(where["out"])] == [False, False]


def test_a_failed_dispatch_is_reported_incomplete_and_kept_in_the_journal(tmp_path):
    where = paths(tmp_path)
    provider = BrokenProvider(discovery_chain())
    record = probe.run_probe(out=where["out"], freeze=where["freeze"], provider=provider)

    assert record["executed"] is True
    assert record["status"] == "incomplete"
    assert record["reason"] in ("invocation_failed", "seat_did_not_complete")
    assert record["chain_complete_seats"] == []
    rows = journal(record)
    assert [row["event"] for row in rows if row["event"] == "dispatch_failed"]
    assert rows[-1]["event"] == "run_end" and rows[-1]["status"] == "incomplete"
    # The attempt survives in the summary log as well as in the journal.
    assert [row["executed"] for row in records(where["out"])] == [True]


def test_a_capability_outside_the_allowance_is_refused_before_dispatch(tmp_path):
    where = paths(tmp_path)
    provider = CannedProvider([
        {"tool_calls": [{"tool": "venue.place_market",
                         "args": {"coin": "PURR/USDC", "side": "buy", "size": "1"}}]},
        {"notional_usd": "0", "evidence": "refused"},
    ])
    record = probe.run_probe(out=where["out"], freeze=where["freeze"], provider=provider)

    assert record["status"] == "completed"
    for seat in record["seats"]:
        assert [row["tool"] for row in seat["refused_calls"]] == ["venue.place_market"]
        # The refusal is ledgered as a failed call, so it earns no continuation
        # and no venue operation is ever dispatched.
        assert seat["tool_calls"] == [
            {"tool": "venue.place_market", "outcome": "failed", "cost": 0}]
        assert seat["discovered"] is False and seat["invoked"] is False
        assert seat["chain_complete"] is False
    assert record["restriction"]["allowed_tools"] == ["calc", "catalogue.search", "world.read"]
    assert record["restriction"]["exchange"] == "FakeExchange"


def test_a_spent_or_changed_preflight_refuses_before_any_call(tmp_path):
    where = paths(tmp_path)
    first = probe.run_probe(out=where["out"], freeze=where["freeze"],
                            provider=CannedProvider(discovery_chain()))
    assert first["executed"] is True

    again = CannedProvider(discovery_chain())
    repeat = probe.run_probe(out=where["out"], freeze=where["freeze"], provider=again)
    assert repeat["status"] == "failed" and repeat["reason"] == "preflight_already_spent"
    assert repeat["executed"] is False and again.prompts == []

    changed = CannedProvider(discovery_chain())
    altered = probe.run_probe(out=where["out"], freeze=where["freeze"],
                              seats=("mechanism",), provider=changed)
    assert altered["status"] == "failed" and altered["reason"] == "preflight_changed"
    assert altered["executed"] is False and changed.prompts == []
    assert [row["executed"] for row in records(where["out"])] == [True, False, False]


def test_an_unknown_bill_stops_the_probe_before_the_second_seat(tmp_path):
    where = paths(tmp_path)
    provider = CannedProvider(discovery_chain(), cost_micro=None)
    record = probe.run_probe(out=where["out"], freeze=where["freeze"], provider=provider)

    assert record["cost"]["stop_reason"] == "unknown_bill"
    # A bill nobody can state leaves the run incomplete, whatever the seat said.
    assert record["status"] == "incomplete"
    assert record["reason"] in ("unknown_bill", "seat_did_not_complete")
    assert record["seats"][0]["seat"] == "mechanism"
    assert record["seats"][1] == {"seat": "opportunity", "status": "skipped",
                                  "reason": "unknown_bill"}
    # One decision's continuations are all that an unknown bill can buy.
    assert record["cost"]["attempted"] == 1
    assert record["cost"]["known_micro"] == 0


def test_reported_bills_are_counted_against_the_one_dollar_bound(tmp_path):
    where = paths(tmp_path)
    provider = CannedProvider(discovery_chain(), cost_micro=1000)
    record = probe.run_probe(out=where["out"], freeze=where["freeze"], provider=provider)

    cost = record["cost"]
    assert cost["attempted"] == len(provider.prompts) <= 12
    assert cost["known_micro"] == 1000 * cost["attempted"] <= 1_000_000
    assert cost["uncertain_micro"] == 0 and cost["overruns"] == 0
    assert sum(seat["cost_micro"] for seat in record["seats"]) > 0


def test_investigation_receipts_allow_reordered_and_duplicate_private_reads():
    expected = [f"fact-{index}" for index in range(6)]
    fetches = [expected[2], expected[0], expected[1], expected[2],
               expected[5], expected[4], expected[3]]
    receipts = [
        {"tool": "outcome.get", "slot": f"{'tool' if index < 4 else 'round1'}:{index}",
         "refused": False, "result": {"outcome": {"payload": {"fact": fact}}}}
        for index, fact in enumerate(fetches)
    ]
    ret = SimpleNamespace(status="ok", cost=700, outputs={"facts": expected})

    result = investigation._result("mechanism", "private_outcomes", ret,
                                   receipts, expected)

    assert result["receipt_success"] and result["retrieved_all_facts"]
    assert result["valid_batch"] and result["exact_final_answer"] and result["success"]


def test_investigation_paid_mode_requires_an_existing_freeze(tmp_path, monkeypatch):
    monkeypatch.setattr(investigation, "build_prepaid_provider",
                        lambda _manifest: pytest.fail("provider must not be constructed"))

    code = investigation.main([
        "--source-root", str(Path.cwd()),
        # Any world the kernel loads: the refusal precedes every model call. An untracked
        # run copy under work/ is history, and history need not load (R8).
        "--world", "worlds/edition6-testnet-rehearsal.toml",
        "--out", str(tmp_path / "arm"),
        "--paid",
    ])

    report = json.loads((tmp_path / "arm" / "report.json").read_text())
    assert code == 1 and report["reason"] == "paid_requires_existing_preflight"
    assert report["executed"] is False


def test_investigation_initial_requests_publish_numeric_tool_call_bound():
    manifest = investigation.effective_manifest(
        investigation.load_manifest(str(probe.DEFAULT_WORLD)))
    seat = "mechanism"

    for case in investigation.CASES:
        runtime = investigation._runtime(manifest, investigation.NullProvider(), [])
        if case == "private_outcomes":
            investigation._seed_outcomes(runtime, seat)
        request = investigation._decision(
            runtime, seat, case, investigation.CAP_MICRO // 4)
        actual, model_request = investigation.actual_model_request(
            runtime, seat, request)
        tool_calls = actual.outcome_schema["properties"]["tool_calls"]
        prompt = "".join(str(message.get("content", ""))
                         for message in model_request.messages)

        assert tool_calls["maxItems"] == manifest.tools.max_tool_calls
        assert actual.outcome_schema["required"] == investigation.SCHEMAS[case]["required"]
        assert set(actual.outcome_schema["properties"]) == {
            *investigation.SCHEMAS[case]["properties"], "tool_calls", "working_state"}
        assert (f"This response may contain at most {manifest.tools.max_tool_calls} "
                "tool_calls" in prompt)


@pytest.mark.parametrize("bound", [{"cap_micro": 2_000_000}, {"max_calls": 13},
                                   {"cap_micro": 0}, {"max_calls": 0}])
def test_bounds_above_the_authorized_ceiling_are_rejected(tmp_path, bound):
    where = paths(tmp_path)
    with pytest.raises(ValueError):
        probe.run_probe(out=where["out"], freeze=where["freeze"], **bound)
