"""An answer is not voided by a malformed optional section beside it.

A return carries an answer (its action and order, verdict, conformity, vote and
the fields its outcome schema requires) and optional sections beside it:
working state, an inbox acknowledgement, a rationale, a declared propensity,
registrations, tool calls, child requests and forecasts. The answer is
validated strictly, as before. A section, or one item of a list section, that
does not validate is dropped with its reason; the rest stands, and the seat is
told what was dropped and why.
"""

import json
from dataclasses import replace

import pytest

from factorylab.cortex.assembly import Assembly, AssemblySpec
from factorylab.cortex.request import Request
from factorylab.kernel.ledger import Ledger
from factorylab.kernel.wallet import Wallet
from factorylab.runtime.loop import Runtime
from factorylab.runtime.worlds import load_manifest
from factorylab.world.metering import Meter, MeteredModel
from factorylab.world.models import FakeModel, PriceTable, TokenPrice
from factorylab.world.scripted import ScriptedProvider
from tests.conftest import make_runtime

ORDER = {"action": "order", "coin": "BTC", "side": "buy", "size": "0.001"}


def invoke(body, schema=None, *, validator=None, emits=("ProducerReturn",)):
    wallet = Wallet(100000, Ledger())
    provider = FakeModel(default=json.dumps(body), fixed_input_tokens=1, fixed_output_tokens=1)
    assembly = Assembly(AssemblySpec("a", 1, "v", max_tokens=16, emits=emits),
                        MeteredModel(provider, PriceTable({"v": TokenPrice(1, 1)}), Meter(wallet)),
                        validator=validator)
    req = Request("h", "test", {}, {}, schema or {}, 100, 100000, None, "JSON", "test", "h")
    return assembly.invoke(req)


def producer_schema():
    """The register section as a runtime publishes it: an enum of the kinds it indexes."""
    return {"type": "object", "properties": {"register": make_runtime()._register_schema()}}


def sections(ret):
    return [(d["section"], d.get("index")) for d in ret.dropped]


# --- per-section validation ------------------------------------------------------

@pytest.mark.parametrize("section,bad,index", [
    ("working_state", "remember BTC", None),
    ("ack_through", 3, None),
    ("rationale", {"why": "momentum"}, None),
    ("propensity", [0.5, 0.5], None),
    ("register", {"kind": "model"}, None),
    ("register", [{"kind": "wish"}], 0),
    ("tool_calls", [{"tool": "venue.cancel"}], None),
    ("tool_calls", "venue.cancel", None),
    ("requests", [{"target": "x", "description": " ", "inputs": {}, "outcome_schema": {}}],
     None),
    ("forecasts", [{"predicate": "wallet_up", "q": 2, "params": {}}], 0),
])
def test_one_bad_optional_section_is_dropped_and_the_order_stands(section, bad, index):
    ret = invoke({**ORDER, section: bad}, producer_schema())
    assert ret.status == "ok"
    assert {k: ret.outputs[k] for k in ORDER} == ORDER
    assert sections(ret) == [(section, index)]
    assert all(d["reason"] for d in ret.dropped)
    if index is None:
        assert section not in ret.outputs
    assert not ret.tool_calls and not ret.children


def test_only_the_bad_registrations_go_but_a_tool_batch_goes_whole():
    good = {"tool": "catalogue.search", "args": {"substring": "btc"}}
    ret = invoke({**ORDER, "tool_calls": [good, {"tool": 7, "args": {}}],
                  "register": [{"kind": "router", "event_kind": "Tick", "learner": "exp3"},
                               {"kind": "wish"}]},
                 producer_schema())
    assert ret.status == "ok" and {k: ret.outputs[k] for k in ORDER} == ORDER
    # Registrations are admitted one by one; a tool batch never runs in part.
    assert ret.outputs["register"] == [{"kind": "router", "event_kind": "Tick",
                                        "learner": "exp3"}]
    assert ret.tool_calls == () and "tool_calls" not in ret.outputs
    assert sections(ret) == [("register", 1), ("tool_calls", None)]
    assert ret.dropped[1]["reason"].startswith("item 1: ")


def test_a_reply_with_nothing_valid_in_it_is_still_malformed():
    assert invoke({"tool_calls": [{"tool": 7, "args": {}}]}).status == "malformed"
    assert invoke({"working_state": "x", "rationale": {}}).status == "malformed"


@pytest.mark.parametrize("core,emits", [
    ({"action": "order", "coin": "BTC", "side": "up", "size": "0.001"}, ("ProducerReturn",)),
    ({"action": "order", "coin": "BTC", "side": "buy", "size": "-1"}, ("ProducerReturn",)),
    ({"action": "order", "coin": "BTC", "side": "buy"}, ("ProducerReturn",)),
    ({"action": ["order"]}, ("ProducerReturn",)),
    ({"action": "order", "coin": "BTC", "side": "up", "size": "0.001"}, ("Exposure",)),
    # Primitive audit F7: a seed role's field is strict on the kind that owns it.
    ({"verdict": 2}, ("Verdict",)),
    ({"conformity": -1}, ("MetaVerdict",)),
])
def test_the_answer_itself_is_still_validated_strictly(core, emits):
    ret = invoke({**core, "working_state": "fine", "rationale": "because"}, emits=emits)
    assert ret.status == "malformed" and not ret.dropped


def test_a_required_answer_field_cannot_be_rescued_by_dropping_sections():
    schema = {"type": "object", "properties": {"verdict": {"type": "number"}},
              "required": ["verdict"]}
    assert invoke({"working_state": "x"}, schema).status == "malformed"
    ret = invoke({"verdict": 0.4, "working_state": "x"}, schema)
    assert ret.status == "ok" and sections(ret) == [("working_state", None)]


def test_unfinished_task_fields_do_not_block_valid_retrieval_continuation():
    rt = make_runtime()
    schema = {"type": "object", "properties": {
        "facts": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "evidence": {"type": "array", "items": {"type": "string"}, "minItems": 1},
    }, "required": ["facts", "evidence"]}
    calls = [{"tool": "outcome.get", "args": {"outcome_id": f"outcome:{index}"}}
             for index in range(1, 5)]

    ret = invoke({"facts": [], "evidence": "retrieval not yet performed",
                  "tool_calls": calls}, schema, validator=rt._validate_output_contract)

    assert ret.status == "ok" and list(ret.tool_calls) == calls
    assert ret.outputs == {}
    assert sections(ret) == [("facts", None), ("evidence", None)]
    assert all(item["reason"].startswith("unfinished continuation field: ")
               for item in ret.dropped)


def test_unfinished_task_fields_remain_strict_on_the_final_turn():
    schema = {"type": "object", "properties": {
        "facts": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        "evidence": {"type": "array", "items": {"type": "string"}, "minItems": 1},
    }, "required": ["facts", "evidence"]}

    ret = invoke({"facts": [], "evidence": "retrieval not yet performed"}, schema)

    assert ret.status == "malformed" and not ret.dropped and not ret.tool_calls


@pytest.mark.parametrize("body,emits", [
    ({"verdict": 2}, ("Verdict",)),
    ({"action": "order", "coin": "BTC", "side": "buy", "size": "not-a-number"},
     ("ProducerReturn",)),
])
def test_continuation_does_not_strip_invalid_core_answer_fields(body, emits):
    call = {"tool": "outcome.get", "args": {"outcome_id": "outcome:1"}}

    ret = invoke({**body, "tool_calls": [call]}, emits=emits)

    assert ret.status == "malformed" and not ret.dropped and not ret.tool_calls


def test_continuation_does_not_strip_an_invalid_declared_event_body():
    schema = {"type": "object", "properties": {
        "facts": {"type": "array", "items": {"type": "string"}, "minItems": 1},
    }, "required": ["facts"]}
    call = {"tool": "outcome.get", "args": {"outcome_id": "outcome:1"}}

    ret = invoke({"emits": "Finding", "facts": [], "tool_calls": [call]}, schema)

    assert ret.status == "malformed" and not ret.dropped and not ret.tool_calls


def test_invalid_tool_batch_cannot_rescue_an_unfinished_answer():
    rt = make_runtime()
    schema = {"type": "object", "properties": {
        "facts": {"type": "array", "items": {"type": "string"}, "minItems": 1},
    }, "required": ["facts"]}
    bad = {"tool": "venue.place_market", "args": {
        "coin": "BTC", "side": "sideways", "size": "1"}}

    ret = invoke({"facts": [], "tool_calls": [bad]}, schema,
                 validator=rt._validate_output_contract)

    assert ret.status == "malformed" and not ret.dropped and not ret.tool_calls
    rejected = ret.outputs["rejected_sections"]
    assert [item["section"] for item in rejected] == ["facts", "tool_calls"]


def test_the_runtime_contract_drops_a_bad_tool_argument_but_keeps_the_order():
    rt = make_runtime()
    bad = {"tool": "venue.place_market", "args": {"coin": "BTC", "side": "sideways",
                                                    "size": "1"}}
    ret = invoke({**ORDER, "tool_calls": [bad]},
                 validator=rt._validate_output_contract)
    assert ret.status == "ok" and {k: ret.outputs[k] for k in ORDER} == ORDER
    assert ret.tool_calls == () and sections(ret) == [("tool_calls", None)]
    assert ret.dropped[0]["reason"].startswith("item 0: venue.place_market")


def test_the_runtime_contract_drops_an_unknown_forecast_but_keeps_the_order():
    """The governance override must name the section, not void the whole return.

    ``GovernanceMixin._validate_output_contract`` catches the seed lookup's
    ``SectionError`` as a plain ``ValueError`` so a registered predicate can
    extend the vocabulary. When the predicate is not registered either, the
    fault still belongs to that one forecast: the order beside it stands.
    """
    rt = make_runtime()
    forecast = {"predicate": "the_moon_is_cheese", "q": 0.5, "params": {"horizon_events": 3}}
    ret = invoke({**ORDER, "forecasts": [forecast]}, validator=rt._validate_output_contract)
    assert ret.status == "ok" and {k: ret.outputs[k] for k in ORDER} == ORDER
    assert "forecasts" not in ret.outputs
    assert sections(ret) == [("forecasts", 0)]


def test_a_predicate_the_book_cannot_resolve_drops_only_its_forecast():
    """The lookup answers for the forecast as much as the parameters do.

    ``PredicateBook.get`` raises a plain ``ValueError`` on an id it cannot
    resolve — an empty one the forecast schema allows, or a stored definition
    that no longer builds. Outside the per-forecast guard that fault reached
    ``validate_return_sections`` as a bare ``ValueError`` and voided the order
    beside it.
    """
    rt = make_runtime()
    forecast = {"predicate": "", "q": 0.5, "params": {"horizon_events": 3}}
    ret = invoke({**ORDER, "forecasts": [forecast]}, validator=rt._validate_output_contract)
    assert ret.status == "ok" and {k: ret.outputs[k] for k in ORDER} == ORDER
    assert "forecasts" not in ret.outputs
    assert sections(ret) == [("forecasts", 0)]


def test_a_valid_forecast_survives_the_governance_contract():
    """A seed predicate with good parameters is not collateral damage of the override."""
    rt = make_runtime()
    forecast = {"predicate": "wallet_up", "q": 0.5, "params": {"horizon_events": 3}}
    ret = invoke({**ORDER, "forecasts": [forecast]}, validator=rt._validate_output_contract)
    assert ret.status == "ok" and ret.outputs["forecasts"] == [forecast] and not ret.dropped


def test_the_seat_is_told_what_was_dropped_and_why():
    rt = make_runtime()
    seat = next(a.spec.id for a in rt.assemblies.values() if a.spec.role == "producer")
    dropped = ({"section": "working_state", "reason": "wrong field type"},)
    rt._report_dropped_sections(seat, "decision-x", dropped)
    (record,) = rt.outcomes.items[seat]
    body = rt.outcomes.body(record["sha"])
    assert body["handle"] == "decision-x"
    assert body["outcome"] == {"kind": "return_sections_dropped", "status": "partial",
                               "dropped": list(dropped),
                               "rejected_section": "working_state",
                               "rejection_reason": "wrong field type"}
    entry = rt.outcomes.unread(seat)["items"][0]
    assert entry["rejection_reason"] == dropped[0]["reason"]


class BadSectionProvider(ScriptedProvider):
    """The scripted producer's orders, each with a string working state and a wish."""

    def complete(self, request):
        response = super().complete(request)
        body = json.loads(response.text)
        if body.get("action") == "order":
            body["working_state"] = "long BTC, stop at 58k"
            body["register"] = [{"kind": "wish"}]
        return replace(response, text=json.dumps(body))


@pytest.mark.gate
def test_in_a_world_the_order_stands_and_the_seat_reads_the_receipt(tmp_path):
    rt = Runtime(load_manifest("scripted"), events=30, seed=1, initial_balance_micro=None,
                 ledger_path=str(tmp_path / "w.jsonl"), router_gamma=.1,
                 provider=BadSectionProvider())
    rt.run()
    diary = rt.ledger._recovery_items()
    orders = [i for i in diary if i["kind"] == "invocation" and i["status"] == "ok"
              and json.loads(i["outputs"]).get("action") == "order"]
    assert orders, "the scripted producer's orders stand"
    assert all("working_state" not in json.loads(i["outputs"]) for i in orders)
    receipts = [i for i in diary if i["kind"] == "return.sections_dropped"]
    assert {i["handle"] for i in receipts} == {i["handle"] for i in orders}
    # One receipt per decision; a decision that took a tool round made two calls,
    # each with the same two bad sections, and each is reported.
    assert all({d["section"] for d in i["dropped"]} == {"working_state", "register"}
               for i in receipts)
    addressed = {(i["assembly_id"], i["handle"]) for i in diary
                 if i["kind"] == "outcome.addressed"}
    assert all((i["assembly_id"], i["handle"]) in addressed for i in receipts)


# PR121: four answers were voided for habits that change nothing they said.
JUDGE = {"type": "object", "properties": {
    "verdict": {"type": "number", "minimum": 0, "maximum": 1},
    "payoff": {"type": "number"}, "status": {"enum": ["cannot"]},
    "reason": {"type": "string"}, "rationale": {"type": "string"}},
    "required": ["rationale"]}


def test_null_for_an_optional_field_is_the_field_left_out():
    ret = invoke({"verdict": None, "payoff": None, "rationale": "nothing committed"}, JUDGE)
    assert ret.status == "ok" and "verdict" not in ret.outputs and "payoff" not in ret.outputs


def test_null_in_a_required_field_is_still_malformed():
    schema = {**JUDGE, "required": ["rationale", "verdict"]}
    assert invoke({"verdict": None, "rationale": "x"}, schema).status == "malformed"


def test_a_reason_stands_for_a_missing_required_rationale():
    ret = invoke({"reason": "a hold with no commitment"}, JUDGE)
    assert ret.status == "ok" and ret.outputs["rationale"] == "a hold with no commitment"
    assert invoke({"reason": "  "}, JUDGE).status == "malformed"


def test_status_is_the_refusal_flag_and_nothing_else():
    """The envelope's status says one thing, "cannot" (Chapter II §II.b): any other value
    is refused, whatever a contract of its own declares."""
    ret = invoke({"status": "unmeasured", "reason": "a hold", "rationale": "r"}, JUDGE)
    assert ret.status == "malformed" and "status" in ret.outputs["validation_error"]
    loose = {**JUDGE, "properties": {**JUDGE["properties"], "status": {"type": "string"}}}
    assert invoke({"status": "ok", "rationale": "r"}, loose).status == "malformed"
