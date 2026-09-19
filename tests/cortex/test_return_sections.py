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


def invoke(body, schema=None, *, validator=None):
    wallet = Wallet(100000, Ledger())
    provider = FakeModel(default=json.dumps(body), fixed_input_tokens=1, fixed_output_tokens=1)
    assembly = Assembly(AssemblySpec("a", 1, "v", max_tokens=16),
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
                  "register": [{"kind": "router", "event_kind": "Tick"}, {"kind": "wish"}]},
                 producer_schema())
    assert ret.status == "ok" and {k: ret.outputs[k] for k in ORDER} == ORDER
    # Registrations are admitted one by one; a tool batch never runs in part.
    assert ret.outputs["register"] == [{"kind": "router", "event_kind": "Tick"}]
    assert ret.tool_calls == () and "tool_calls" not in ret.outputs
    assert sections(ret) == [("register", 1), ("tool_calls", None)]
    assert ret.dropped[1]["reason"].startswith("item 1: ")


def test_a_reply_with_nothing_valid_in_it_is_still_malformed():
    assert invoke({"tool_calls": [{"tool": 7, "args": {}}]}).status == "malformed"
    assert invoke({"working_state": "x", "rationale": {}}).status == "malformed"


@pytest.mark.parametrize("core", [
    {"action": "order", "coin": "BTC", "side": "up", "size": "0.001"},
    {"action": "order", "coin": "BTC", "side": "buy", "size": "-1"},
    {"action": "order", "coin": "BTC", "side": "buy"},
    {"action": ["order"]},
    {"verdict": 2},
    {"vote": "yes"},
])
def test_the_answer_itself_is_still_validated_strictly(core):
    ret = invoke({**core, "working_state": "fine", "rationale": "because"})
    assert ret.status == "malformed" and not ret.dropped


def test_a_required_answer_field_cannot_be_rescued_by_dropping_sections():
    schema = {"type": "object", "properties": {"verdict": {"type": "number"}},
              "required": ["verdict"]}
    assert invoke({"working_state": "x"}, schema).status == "malformed"
    ret = invoke({"verdict": 0.4, "working_state": "x"}, schema)
    assert ret.status == "ok" and sections(ret) == [("working_state", None)]


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
                               "dropped": list(dropped)}


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
                 ledger_path=str(tmp_path / "w.jsonl"), drip=True, router_gamma=.1,
                 provider=BadSectionProvider())
    rt.run()
    diary = rt.ledger._recovery_items()
    orders = [i for i in diary if i["kind"] == "invocation" and i["status"] == "ok"
              and json.loads(i["outputs"]).get("action") == "order"]
    assert orders, "the scripted producer's orders stand"
    assert all("working_state" not in json.loads(i["outputs"]) for i in orders)
    receipts = [i for i in diary if i["kind"] == "return.sections_dropped"]
    assert {i["handle"] for i in receipts} == {i["handle"] for i in orders}
    assert all([d["section"] for d in i["dropped"]] == ["working_state", "register"]
               for i in receipts)
    addressed = {(i["assembly_id"], i["handle"]) for i in diary
                 if i["kind"] == "outcome.addressed"}
    assert all((i["assembly_id"], i["handle"]) in addressed for i in receipts)
