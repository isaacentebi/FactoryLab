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
    declines,
    judging_contract,
    reserved_return_fields,
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
    envelope = reserved_return_fields()
    answer, decline = schema["anyOf"]
    assert decline == DECLINE_FORM and "status" not in answer["properties"]
    assert set(answer["required"]) == {"verdict", "rationale"}
    for reply in ({"status": "cannot", "reason": "out of scope"}, {"status": "cannot"},
                  {"verdict": 0.6, "status": "cannot", "reason": "r"}):
        # A reply whose status is "cannot" is a decline (``declines``), a reason or
        # not, as the decline form's own description states, on all three.
        assert declines(reply)
        assert admitted(reply, schema) and admitted(reply, wire)
        assert kernel(reply, schema, "Verdict")[0] == "decline"
        assert admitted(reply, decline)
    # status is the refusal flag and nothing else: the kernel's own "ok" is not an
    # answer, and a reason that is not a string is no decline.
    for reply in ({"verdict": 0.6, "rationale": "r", "status": "ok"},
                  {"status": "cannot", "reason": 3}):
        # The published envelope every return may carry says so (world.read
        # reserved_return_fields), and so do the wire and the kernel.
        assert not admitted(reply, {"type": "object", "properties": envelope})
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
    assert req.outcome_schema["anyOf"][1]["required"] == ["status"]
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
    assert shown == {k: scoring[k] for k in ("evaluator_return", "verdict_is_a_prediction",
                                             "abstention")}
    assert "0.5 + 0.5 * (brier - base)" in section
    # Wave 16, section 9 item 5: D4 is carried verbatim in the SCORING section.
    assert ("a decline, a NOOP and an abstention are priced at the router's observed "
            "average raw score less the same penalty") in section
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
    shown = json.loads(dict(req.sections())["scoring"].split("\n", 2)[2])
    assert "meta_return" in shown and shown["abstention"] == rt._scoring_block()["abstention"]
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


# --- a declared kind can only be one some reply could satisfy ----------------------


@pytest.mark.parametrize("field", ["my-field", "2factor"])
def test_registering_a_kind_whose_fields_no_reply_could_carry_is_refused(field):
    """_validate_return refuses a reply with a non-identifier key, so a kind naming one
    would admit no reply: registration refuses it first, naming the field."""
    from factorylab.cortex.registration import output_contracts, parse_proposals

    for schema in ({"type": "object", "properties": {field: {"type": "number"}}},
                   {"type": "object", "properties": {"ok": {"type": "number"}},
                    "required": [field]},
                   {"type": "object", "anyOf": [{"properties": {field: {}}}]}):
        with pytest.raises(ValueError, match=f"field name '{field}' is not an identifier"):
            output_contracts(["Finding"], {"Finding": schema})
    _accepted, rejected = parse_proposals(
        {"register": [{"kind": "assembly", "id": "finder", "model_id": "m",
                       "system_prompt": "p", "accepts": ["Tick"], "emits": ["Finding"],
                       "schemas": {"Finding": {"type": "object", "properties": {
                           field: {"type": "number"}}}}}]},
        event_kinds=frozenset({"Tick"}), known_models=frozenset({"m"}),
        known_assemblies=frozenset(), tool_jail=True)
    assert len(rejected) == 1 and field in rejected[0].reason
    # A value's own keys are the value's: nested names are not reply fields.
    output_contracts(["Finding"], {"Finding": {"type": "object", "properties": {
        "table": {"type": "object", "properties": {field: {"type": "number"}}}}}})


@pytest.mark.parametrize("field", ["my-field", "2factor"])
def test_a_child_schema_no_reply_could_satisfy_is_refused_on_both_sides(field):
    child = {**CHILD, "outcome_schema": {"type": "object",
                                         "properties": {field: {"type": "number"}}}}
    with pytest.raises(ValueError, match=field):
        _check_child(child)
    wire = wire_schema({"type": "object", "properties": {"action": {"type": "string"}},
                        "required": ["action"]}, ("ProducerReturn",))
    assert not admitted({"requests": [child]}, wire)


# --- world.event_schemas publishes the contract a judge's request carries ------------


def _judging_requests(monkeypatch):
    from dataclasses import replace

    from factorylab.runtime.shared import CH_COUNTER
    from factorylab.runtime.worlds import AssemblySeed, load_manifest

    base = load_manifest("scripted")
    adversary = AssemblySeed(id="adv-a", model_id="fake-sonnet", accepts=("Verdict",),
                             role="adversary", max_tokens=128)
    rt = _consequence_runtime(provider=Population(verdicts=(0.5,)),
                              manifest=replace(base, assemblies=(*base.assemblies, adversary)))
    producer, event = _unsettled_produce(rt)
    captured = {}
    request = rt._request

    def capture(handle, *args, **kwargs):
        req = request(handle, *args, **kwargs)
        captured[rt.handle_to_assembly.get(handle, handle)] = req
        return req

    monkeypatch.setattr(rt, "_request", capture)
    judge = _consequence_decision(rt, "eval-a", CH_CONFORMITY)
    rt.handle_to_assembly[judge] = "eval-a"
    rt._evaluator_step(event, judge, SimpleNamespace(chosen="eval-a"),
                       rt.queue.get(judge).deadline_ns)
    verdict = Event("v", EventKind.VERDICT, rt.clock.now_ns,
                    {"about_handle": producer, "evaluator_handle": judge, "verdict": 0.5,
                     "rationale": "r", "producer_outputs": {}, "propensity": None}, "kernel")
    meta = _consequence_decision(rt, "meta-a", CH_CONFORMITY)
    rt.handle_to_assembly[meta] = "meta-a"
    rt._meta_step(verdict, meta, SimpleNamespace(chosen="meta-a"), rt.queue.get(meta).deadline_ns)
    counter = _consequence_decision(rt, "adv-a", CH_COUNTER)
    rt.handle_to_assembly[counter] = "adv-a"
    rt._counter_step(verdict, counter, SimpleNamespace(chosen="adv-a"),
                     rt.queue.get(counter).deadline_ns)
    return rt, {"Verdict": captured["eval-a"], "MetaVerdict": captured["meta-a"],
                "CounterVerdict": captured["adv-a"]}


def test_event_schemas_publish_the_judging_contract_the_request_carries(monkeypatch):
    rt, requests = _judging_requests(monkeypatch)
    published = rt._world_block()["event_schemas"]
    for kind, req in requests.items():
        assert published[kind] == req.outcome_schema, kind
        assert published[kind]["anyOf"][-1]["required"] == ["status"]  # the decline form
        seat = {"Verdict": "eval-a", "MetaVerdict": "meta-a", "CounterVerdict": "adv-a"}[kind]
        # The schema the prompt prints (``_published_contract``) is that same object.
        assert rt._published_contract(seat, req.handle, req.outcome_schema,
                                      req.scoring_channel) == published[kind]


def test_a_polymorphic_contract_with_a_judging_kind_carries_its_decline_form():
    from dataclasses import replace

    rt = _consequence_runtime(provider=Population())
    spec = rt.assemblies["eval-a"].spec
    rt.assemblies["eval-a"].spec = replace(spec, emits=("Verdict", "ProducerReturn"))
    contract = rt._contract_schema("eval-a")
    assert contract["anyOf"][-1] == DECLINE_FORM


def test_every_wave_16_formula_is_published_in_world_scoring():
    """Wave 16: each changed reward formula is a factual, retrievable world.scoring
    entry (Chapter II §I.b), never advice."""
    from tests.conftest import make_runtime

    rt = make_runtime()
    scoring = rt._scoring_block()
    text = json.dumps(scoring)
    for fact in (
        "declined-trade-net-v1", "attempted-trade-net-v1",  # D1
        "H = timing.world_repricing / timing.min_ratio",  # D2
        "verdict:<definition>:<coin>:<side>:<H in ns>", "uninformative",  # D3
        "the equal mean of those that exist",  # D6
        "No consequence score enters a card, a lambda or a posted lambda",  # section 9
        "priced at the router's observed average raw score less the same penalty",  # D4
        "unhistoried niche", "non-relieving",  # D5
        "another card's pressure never stops it",  # R-E, R10-e
    ):
        assert fact in text, fact
    for advice in ("you should", "try to", "aim to", "it is best"):
        assert advice not in text.lower()


# --- published numbers are derived from the code that enforces them (Codex on #152) ----


def _formula(text: str, start: str, end: str) -> str:
    """The expression published between ``start`` and ``end``, as Python."""
    expression = text.split(start, 1)[1].split(end, 1)[0]
    return expression.replace("^", "**")


@pytest.mark.parametrize("verdict_timeout", [2, 7, 40])
def test_the_published_margin_horizon_is_the_one_the_code_reads(verdict_timeout):
    """R10-k: settlement of a shadow price waits for the consequence patience, H plus
    the verdict window counted ONCE. The published number is the one ``_margin_horizon``
    returns, and it equals that derivation for every verdict window; the published text
    no longer adds a second verdict window."""
    from dataclasses import replace

    from factorylab.runtime.clockwork import tick_ns
    from tests.conftest import make_runtime

    rt = make_runtime()
    rt.m = replace(rt.m, evaluation=replace(rt.m.evaluation,
                                            verdict_timeout_events=verdict_timeout))
    rt.ev = rt.m.evaluation
    published = rt._adaptive_scoring_block()["margin_horizon_windows"]
    assert published == rt._margin_horizon()
    tick = tick_ns(rt.tick_clock)
    patience_ticks = -(-(rt._horizon_ns() + verdict_timeout * tick) // tick)
    window = rt.clockwork.period("price", default=rt.m.timing.min_ratio)
    assert published == max(rt.m.timing.min_ratio, -(-patience_ticks // window))
    text = rt._mechanics_block()["committee"]["shadow_prices"]
    assert "plus verdict_timeout_ticks later" not in text
    assert "world.adaptive_scoring.margin_horizon_windows" in text


def test_every_published_value_is_the_one_the_runtime_enforces():
    """Each number the scoring formulas name is generated from its enforcing function:
    H, the consequence patience (R10-k), the cap, each learner's map bound (R10-l) and
    the uninformative reasons."""
    from factorylab.runtime.clockwork import tick_ns
    from factorylab.settlement.lots import FEE_UNKNOWN, NO_MARK
    from tests.conftest import make_runtime

    rt = make_runtime()
    values = rt._scoring_block()["enforced_values"]
    assert values["consequence_horizon_ns"] == rt._horizon_ns()
    assert values["consequence_horizon_ns"] == (rt.m.timing.world_repricing_ns
                                                // rt.m.timing.min_ratio)
    assert values["consequence_patience_ns"] == rt._patience_ns() == (
        rt._horizon_ns() + rt.ev.verdict_timeout_ticks * tick_ns(rt.tick_clock))
    assert values["penalty_cap"] == rt.controller.snapshot()["parameters"]["penalty_cap"]
    assert values["learned_map_bound"] == {"router": rt._charge_bound(True),
                                           "seat": rt._charge_bound(False)}
    assert values["uninformative_reasons"] == [FEE_UNKNOWN, NO_MARK]
    text = rt._scoring_block()["verdict_is_a_prediction"]
    assert FEE_UNKNOWN in text and NO_MARK in text


@pytest.mark.parametrize("raw,card,thrash", [(0.1, 0.0, 0.0), (0.3, 0.2, 0.0),
                                             (0.3, 0.0, 0.2), (0.0, 0.5, 0.5)])
def test_the_published_learned_map_evaluates_to_what_every_learner_learns(raw, card, thrash):
    """R10-l: the router's published map, evaluated with the published bound, is what
    ``_learning_value`` gives a router; with no thrash term and the seat's bound, what
    it gives a seat's own learner."""
    from tests.conftest import make_runtime

    rt = make_runtime()
    bound = rt._scoring_block()["enforced_values"]["learned_map_bound"]
    formula = _formula(rt._mechanics_block()["thrash_price"], "the router learns ", ", r raw")
    router = rt._all_router_states()[0]
    rt.thrash_charges["h"] = thrash
    learned = rt._learning_value("h", raw, card, router=router)
    assert learned == pytest.approx(eval(formula, {}, {"r": raw, "B": bound["router"],
                                                        "p": card, "c": thrash}))
    assert rt._learning_value("s", raw, card) == pytest.approx(
        eval(formula, {}, {"r": raw, "B": bound["seat"], "p": card, "c": 0.0}))


@pytest.mark.parametrize("entry,exit_,rates", [("0.00045", "0.00035", ()),
                                               ("0.00045", "0.00045", ("0.0001",)),
                                               ("0", "0.0007", ("-0.0002", "0.0001"))])
def test_the_published_net_evaluates_to_what_the_road_not_taken_is_priced_at(entry, exit_,
                                                                            rates):
    """D1, D7 fee legs and R10-m: the net published in world.scoring, evaluated with its
    own named terms, is ``opportunity_cost``'s net for the same mids, legs and funding."""
    from decimal import Decimal

    from factorylab.runtime.grounded import opportunity_cost
    from tests.conftest import make_runtime

    rt = make_runtime()
    text = rt._scoring_block()["verdict_is_a_prediction"]
    formula = _formula(text, "net = ", " bp")
    assert "f0 + f1" in formula
    for side, s in (("buy", 1), ("sell", -1)):
        priced = opportunity_cost([("BTC", "100")], [("BTC", "100.3")], entry, exit_,
                                  {"coin": "BTC", "side": side}, rates)
        expected = eval(formula, {"sum": sum}, {
            "s": s, "m0": 100.0, "m1": 100.3, "f0": float(entry), "f1": float(exit_),
            "rho": [float(r) for r in rates]})
        assert float(Decimal(priced["net_bps"])) == pytest.approx(expected, abs=1e-4)
    assert "at or before H" in text  # R10-m: funding stops at H, on both roads
