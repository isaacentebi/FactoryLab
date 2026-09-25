"""What a request publishes is what the kernel enforces, for judges, register and the wire.

Chapter II §II.b: physics is enforced, not announced, and the published contract is the
enforced one. Wave 15 (investigators 3, 4 and 5 on longrun1): a judge's verdict was
published optional and censored when absent; the decline was folded into the answer and
announced four times; the forwarded PROPENSITY block shared the answer field's name; the
register schema admitted kind-only items the kernel then refused as "not a slug"; and 60
MiniMax replies satisfied the wire and failed the kernel.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from factorylab.cortex.assembly import (
    DECLINE_FORM,
    FIELD_NAME_PATTERN,
    FORWARDED_RATIONALE_CHARS,
    _check_child,
    _schema_definition,
    _validate_return,
    judging_contract,
    validate_proposal,
    validate_return_sections,
    validate_schema,
    wire_schema,
)
from factorylab.cortex.registration import proposal_schemas, register_item_schema
from factorylab.cortex.request import OUTCOME_CONTRACT_DECLINE, Request
from factorylab.kernel.events import Event, EventKind
from factorylab.runtime.loop import forwarded_rationale
from factorylab.runtime.propensity import (
    DECLINED,
    action_label,
    action_vocabulary,
    declared_record,
    propensity_field,
)
from factorylab.runtime.shared import CH_CONFORMITY, declined_reason
from tests.runtime.test_loop import _consequence_decision, _consequence_runtime
from tests.runtime.test_reward_chain import Population, _unsettled_produce

FIXTURE = (Path(__file__).resolve().parents[1] / "fixtures"
           / "longrun1_minimax_wire_valid_kernel_malformed.json")
REGISTER = {"type": "array", "items": register_item_schema()}
ROLES = {"Verdict": "evaluator", "MetaVerdict": "meta", "CounterVerdict": "adversary"}


def contract(kind):
    return judging_contract(kind, propensity=propensity_field(ROLES[kind]), register=REGISTER)


def kernel(reply, schema, kind):
    """The kernel's reading: ("answer" | "decline", outputs), or ("malformed", reason)."""
    try:
        parsed, _dropped = validate_return_sections(reply, schema, None, None, kind=kind)
    except ValueError as exc:
        return "malformed", str(exc)
    ret = SimpleNamespace(status="ok", outputs=parsed)
    return ("decline" if declined_reason(ret) is not None else "answer"), parsed


def admitted(reply, schema):
    try:
        validate_schema(reply, schema)
    except ValueError:
        return False
    return True


# --- the judging contract: one builder, published = enforced ------------------------


@pytest.mark.parametrize("kind,field", [("Verdict", "verdict"), ("MetaVerdict", "conformity"),
                                        ("CounterVerdict", "verdict")])
def test_a_judgement_without_its_value_fails_the_published_schema_the_wire_and_the_kernel(
        kind, field):
    """The kernel censors a judgement with no value in [0, 1]; the contract requires it."""
    schema, wire = contract(kind), wire_schema(contract(kind), (kind,))
    for reply in ({"rationale": "x"}, {field: 1.4, "rationale": "x"},
                  {field: "0.6", "rationale": "x"}):
        assert not admitted(reply, schema), reply
        assert not admitted(reply, wire), reply
        assert kernel(reply, schema, kind)[0] == "malformed", reply
    good = {field: 0.6, "rationale": "x"}
    assert admitted(good, schema) and admitted(good, wire)
    assert kernel(good, schema, kind)[0] == "answer"


def test_the_decline_is_its_own_form_and_classifies_alike_everywhere():
    schema, wire = contract("Verdict"), wire_schema(contract("Verdict"), ("Verdict",))
    answer, decline = schema["anyOf"]
    assert decline == DECLINE_FORM and "status" not in answer["properties"]
    assert set(answer["required"]) == {"verdict", "rationale"}
    for reply in ({"status": "cannot", "reason": "out of scope"},
                  {"verdict": 0.6, "status": "cannot", "reason": "r"}):
        # A reply carrying status "cannot" and a string reason is a decline, as the
        # decline form's own description states, on all three.
        assert admitted(reply, schema) and admitted(reply, wire)
        assert kernel(reply, schema, "Verdict")[0] == "decline"
        assert admitted(reply, decline)
    # status is the refusal flag and nothing else: the kernel's own "ok" is not an answer.
    for reply in ({"verdict": 0.6, "rationale": "r", "status": "ok"},
                  {"status": "cannot"}):
        assert not admitted(reply, wire)
        assert kernel(reply, schema, "Verdict")[0] == "malformed"


def test_the_decline_is_published_once_in_a_judge_request(monkeypatch):
    rt = _consequence_runtime(provider=Population(verdicts=(0.5,)))
    _producer, event = _unsettled_produce(rt)
    captured = []
    request = rt._request
    monkeypatch.setattr(rt, "_request", lambda *a, **k: captured.append(request(*a, **k))
                        or captured[-1])
    judge = _consequence_decision(rt, "eval-a", CH_CONFORMITY)
    rt._evaluator_step(event, judge, SimpleNamespace(chosen="eval-a"),
                       rt.queue.get(judge).deadline_ns)
    (req,) = captured
    sections = dict(req.sections())
    assert "decline" not in sections["request"] and "cannot" not in sections["request"]
    assert "you_may" not in req.inputs["commission"]
    assert OUTCOME_CONTRACT_DECLINE not in sections["outcome_contract"]
    assert req.outcome_schema["anyOf"][1]["required"] == ["status", "reason"]
    # A producer's schema publishes no decline form, so its OUTCOME CONTRACT states it.
    producer = Request("h", "d", {}, {}, {"type": "object"}, 1, 1, None, "c", "x", "h")
    assert OUTCOME_CONTRACT_DECLINE in dict(producer.sections())["outcome_contract"]


def test_the_judged_return_carries_its_handle_and_no_reserved_status(monkeypatch):
    rt = _consequence_runtime(provider=Population(verdicts=(0.5,)))
    producer, event = _unsettled_produce(rt)
    captured = []
    request = rt._request
    monkeypatch.setattr(rt, "_request", lambda *a, **k: captured.append(request(*a, **k))
                        or captured[-1])
    judge = _consequence_decision(rt, "eval-a", CH_CONFORMITY)
    rt._evaluator_step(event, judge, SimpleNamespace(chosen="eval-a"),
                       rt.queue.get(judge).deadline_ns)
    (req,) = captured
    judged = req.inputs["producer"]
    assert judged["handle"] == producer and req.inputs["subject_handle"] == producer
    assert "status" not in judged and judged["kernel_status"] == "ok"
    assert "subject_handle" in req.inputs["commission"]["scope"]


# --- the action vocabulary beside the propensity field ------------------------------


@pytest.mark.parametrize("role", ["producer", "antagonist", "evaluator", "meta", "adversary"])
def test_every_role_publishes_the_declined_label_beside_its_own(role):
    vocabulary = action_vocabulary()[role]
    assert f'"{DECLINED}"' in vocabulary
    record, reason = declared_record(DECLINED, {DECLINED: 1.0}, learner_id="l", state_hash="s")
    assert reason is None and record.chosen == DECLINED


@pytest.mark.parametrize("kind", ["Verdict", "MetaVerdict", "CounterVerdict"])
def test_the_propensity_field_names_this_roles_action_labels_where_it_is_defined(kind):
    field = contract(kind)["anyOf"][0]["properties"]["propensity"]
    assert action_vocabulary()[ROLES[kind]] in field["description"]
    # The label the field names is the label the kernel gives the answer.
    value = {"Verdict": "verdict", "MetaVerdict": "conformity"}.get(kind, "verdict")
    label = action_label(ROLES[kind], {value: 0.64}, "ok")
    assert label == f"{value}:0.6" and f'"{value}:' in field["description"]
    assert action_label(ROLES[kind], {"status": "cannot", "reason": "r"}, "refused") == DECLINED


def test_the_forwarded_propensity_is_not_named_like_the_answer_field():
    req = Request("h", "d", {"subject_handle": "decision-9"}, {}, {"type": "object"}, 1, 1,
                  None, "c", "x", "h", propensity={"hold": 0.7, "order": 0.3},
                  propensity_chosen="hold")
    block = dict(req.sections())["propensity"]
    header = block.split("\n", 1)[0]
    assert header == "SUBJECT PROPENSITY" and header != "PROPENSITY"
    assert "decision-9" in block


# --- the scoring rule, in the judge's own request -----------------------------------


def test_judges_see_the_rule_they_are_graded_by_in_every_prompt_mode(monkeypatch):
    from dataclasses import replace

    from factorylab.runtime.worlds import PromptSpec, load_manifest

    manifest = replace(load_manifest("scripted"), prompt=PromptSpec(mode="compact"))
    rt = _consequence_runtime(provider=Population(verdicts=(0.5,)), manifest=manifest)
    _producer, event = _unsettled_produce(rt)
    captured = []
    request = rt._request
    monkeypatch.setattr(rt, "_request", lambda *a, **k: captured.append(request(*a, **k))
                        or captured[-1])
    judge = _consequence_decision(rt, "eval-a", CH_CONFORMITY)
    rt._evaluator_step(event, judge, SimpleNamespace(chosen="eval-a"),
                       rt.queue.get(judge).deadline_ns)
    (req,) = captured
    scoring = rt._scoring_block()
    section = dict(req.sections())["scoring"]
    assert section.startswith("SCORING\n")
    shown = json.loads(section.split("\n", 2)[2])
    assert shown == {k: scoring[k] for k in ("evaluator_return", "verdict_is_a_prediction")}
    assert "0.5 + 0.5 * (brier - base)" in section
    for advice in ("should", "try to", "aim", "best", "better"):
        assert advice not in section.lower()


def test_metas_see_the_rule_they_are_graded_by(monkeypatch):
    rt = _consequence_runtime(provider=Population(verdicts=(0.5,)))
    producer, _event = _unsettled_produce(rt)
    captured = []
    request = rt._request
    monkeypatch.setattr(rt, "_request", lambda *a, **k: captured.append(request(*a, **k))
                        or captured[-1])
    meta = _consequence_decision(rt, "meta-a", CH_CONFORMITY)
    verdict = Event("v", EventKind.VERDICT, rt.clock.now_ns,
                    {"about_handle": producer, "evaluator_handle": "decision-x",
                     "verdict": 0.5, "rationale": "r", "producer_outputs": {},
                     "propensity": None}, "kernel")
    rt._meta_step(verdict, meta, SimpleNamespace(chosen="meta-a"), rt.queue.get(meta).deadline_ns)
    (req,) = captured
    assert "meta_return" in json.loads(dict(req.sections())["scoring"].split("\n", 2)[2])
    answer = req.outcome_schema["anyOf"][0]
    assert answer["properties"]["conformity"] == {"type": "number", "minimum": 0,
                                                  "maximum": 1}
    # The meta's subject is the judgement it grades, named where its inputs name it.
    assert req.inputs["subject_handle"] == "decision-x" != producer


def test_a_rationale_past_the_published_limit_is_cut_and_the_cut_is_stated():
    assert str(FORWARDED_RATIONALE_CHARS) in (
        contract("Verdict")["anyOf"][0]["properties"]["rationale"]["description"])
    short = forwarded_rationale({"rationale": "r" * FORWARDED_RATIONALE_CHARS})
    assert short == {"rationale": "r" * FORWARDED_RATIONALE_CHARS}
    long = forwarded_rationale({"rationale": "r" * 2500})
    assert len(long["rationale"]) == FORWARDED_RATIONALE_CHARS
    assert long["rationale_chars"] == 2500
    assert long["rationale_forwarded_chars"] == FORWARDED_RATIONALE_CHARS


# --- the register schema: the one the kernel admits ---------------------------------


def _complete(kind):
    """The published full shape (``catalogue.search``) of ``kind``, cut to the fields its
    register forms name: the rest are optional and their example values are prose."""
    from factorylab.cortex.schematics import SchematicsMixin

    shape = dict(SchematicsMixin.PROPOSAL_SHAPES[kind])
    named = {f for form in proposal_schemas()[kind]
             for f in (*form["required"], *form["properties"])}
    return {**{k: v for k, v in shape.items() if k in named}, "kind": kind}


@pytest.mark.parametrize("kind", sorted(proposal_schemas()))
def test_the_published_register_item_is_the_one_the_kernel_checks(kind):
    """Every kind's published full shape satisfies its published form; dropping any
    required field fails the form and the kernel's check, which names the field."""
    item = _complete(kind)
    validate_schema(item, register_item_schema())
    validate_proposal(item)
    forms = proposal_schemas()[kind]
    required = {f for form in forms for f in form["required"]} - {"kind"}
    for field in sorted(required & set(item)):
        broken = {k: v for k, v in item.items() if k != field}
        if any(set(form["required"]) <= set(broken) for form in forms):
            continue  # another form of the kind needs no such field (market)
        with pytest.raises(ValueError):
            validate_schema(broken, register_item_schema())
        with pytest.raises(ValueError, match=f"required field absent: .*{field}"):
            validate_proposal(broken)


def test_a_missing_id_is_named_not_called_a_bad_slug():
    for kind in ("tool", "predicate", "observation", "assembly"):
        with pytest.raises(ValueError) as refused:
            validate_proposal({"kind": kind, "description": "d", "code": "x"})
        assert "id" in str(refused.value) and "slug" not in str(refused.value)
        with pytest.raises(ValueError, match="required field absent"):
            validate_schema({"kind": kind}, register_item_schema())


def test_a_kind_only_registration_is_refused_by_the_published_schema_with_its_fields():
    """longrun1: 33 kind-only entries validated against the inline schema and were then
    refused by the kernel, 15 as "id must be a slug". The schema now refuses them,
    naming the kind's own fields and no other kind's."""
    with pytest.raises(ValueError) as refused:
        validate_schema({"kind": "observation"}, register_item_schema())
    reason = str(refused.value)
    assert "id, description, unit, range, code" in reason and "openrouter_id" not in reason


# --- the wire: never looser than the kernel -----------------------------------------


CHILD = {"target": "judge", "description": "d", "inputs": {}, "outcome_schema": {
    "type": "object", "properties": {"a": {"type": "array", "items": {
        "type": "object", "properties": {"b": {"type": "number", "minimum": 0}}}}},
    "required": ["a"]}}
REFUSED_CHILD_SCHEMAS = [
    {"answer": "number"},  # an example instance, not a schema
    {"type": "object", "pattern": "x"},  # a keyword outside the whitelist
    {"type": ["string", "number"]},  # a type the kernel does not take
    {"type": "object", "properties": {"x": "string"}},  # a property that is no schema
    {"type": "object", "properties": {"x": {"type": "array", "items": {
        "type": "object", "properties": {"y": {"minLength": 1}}}}}},  # deep keyword
    {"anyOf": []},
    {"type": "array", "minItems": -1},
]


@pytest.mark.parametrize("schema", REFUSED_CHILD_SCHEMAS)
def test_the_wire_refuses_every_child_schema_the_kernel_refuses(schema):
    child = {**CHILD, "outcome_schema": schema}
    with pytest.raises(ValueError):
        _check_child(child)
    with pytest.raises(ValueError):
        _schema_definition(schema)
    wire = wire_schema({"type": "object", "properties": {"action": {"type": "string"}},
                        "required": ["action"]}, ("ProducerReturn",))
    assert not admitted({"requests": [child]}, wire)
    _check_child(CHILD)
    assert admitted({"requests": [CHILD]}, wire)


def test_the_wire_states_the_child_bound_and_propensity_syntax():
    wire = wire_schema({"type": "object", "properties": {"action": {"type": "string"}},
                        "required": ["action"]}, ("ProducerReturn",), max_children=2)
    assert not admitted({"requests": [CHILD] * 3}, wire)
    assert admitted({"requests": [CHILD] * 2}, wire)
    for child in ({**CHILD, "propensity": {}}, {**CHILD, "propensity": {"a": 1.0}},
                  {**CHILD, "chosen": "a"}, {**CHILD, "propensity": {"a": 2}, "chosen": "a"}):
        assert not admitted({"requests": [child]}, wire), child
        with pytest.raises(ValueError):
            _check_child(child)
    assert admitted({"requests": [{**CHILD, "propensity": {"a": 1.0}, "chosen": "a"}]}, wire)


def test_status_is_the_refusal_flag_on_the_wire_in_every_form():
    wire = wire_schema({"type": "object", "properties": {"action": {"type": "string"}},
                        "required": ["action"]}, ("ProducerReturn",))
    for form in wire["anyOf"]:
        assert form["properties"]["status"]["enum"] == ["cannot"]
    call = {"tool": "world.read", "args": {"section": "prices"}}
    assert not admitted({"status": "defer", "tool_calls": [call]}, wire)
    assert not admitted({"status": "ok", "action": "hold"}, wire)


def test_a_field_name_that_is_not_an_identifier_is_refused_on_both_sides():
    """longrun1: 83 ok replies silently lost a key that swallowed its own delimiter."""
    schema = {"type": "object", "properties": {"action": {"type": "string"}},
              "required": ["action"]}
    wire = wire_schema(schema, ("ProducerReturn",))
    for key in ("propensity{", "defer\":1,", "reason:", "tool_calls: "):
        reply = {"action": "hold", key: "hold"}
        assert not admitted(reply, wire), key
        with pytest.raises(ValueError, match="field name"):
            _validate_return(reply, schema, "ProducerReturn")
    assert wire["propertyNames"] == {"pattern": FIELD_NAME_PATTERN}
    _validate_return({"action": "hold", "working_state": {}}, schema, "ProducerReturn")


def _constructor_wire():
    """The constructor seat's published contract, on the wire, with coins listed."""
    from collections import deque
    from dataclasses import replace

    from factorylab.runtime.loop import Runtime
    from factorylab.runtime.worlds import load_manifest
    from factorylab.world.exchange import FakeExchange
    from factorylab.world.scripted import ScriptedProvider

    manifest = load_manifest("edition6-testnet-rehearsal")
    # Resolved allowances stand in for the provider's own: no catalogue is read.
    manifest = replace(manifest, assemblies=tuple(
        replace(a, max_tokens=1024) for a in manifest.assemblies))
    rt = Runtime(manifest, events=0, seed=1, initial_balance_micro=None, ledger_path=None,
                 router_gamma=0.1, provider=ScriptedProvider(),
                 exchange=FakeExchange(coins=manifest.exchange.coins))
    rt.recent_mids = {coin: deque([{"t_s": 0, "mid": "1"}]) for coin in ("BTC", "ETH")}
    rt._may_write = lambda handle: True
    contract = rt._published_contract("constructor", "unopened",
                                      rt._contract_schema("constructor"), "verdict")
    return wire_schema(contract, rt.assemblies["constructor"].spec.emits,
                       max_children=rt.m.tools.max_children)


def test_the_longrun1_minimax_replies_the_kernel_refused_are_refused_on_the_wire():
    """60 constructor replies satisfied the wire they were sent and failed the kernel
    (investigator 3). Each fails the wire the same seat is sent now."""
    fixture = json.loads(FIXTURE.read_text())
    replies = [row["reply"] for row in fixture["replies"]]
    assert len(replies) == 60
    wire = _constructor_wire()
    assert [i for i, reply in enumerate(replies) if admitted(reply, wire)] == []
