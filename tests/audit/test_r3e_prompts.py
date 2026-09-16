"""R3-E: the prompts GPT-6's third reading asked for, and the arithmetic it asked for.

Five claims are proved here.

The stable prefix is the WORLD CONTRACT wrapper with the charter's own five norms
and a compact base capability index, and nothing else. It is serialised once per
runtime and reused byte-for-byte: two requests in one world open with identical
bytes, and so does a runtime restored from that world's own checkpoint. What used
to ride there — the cards, their prices, the mechanics, every argument schema —
is published once, where it moves, which is the reviewer's own instruction: byte
stability "does not require copying every institutional description into that
prefix".

The lens is out of the system prompt. Every edition 3 seat's system message is the
common system contract, verbatim and identical for all nine; the lens is the first
head of C1's working state, seeded once, and a seat may overwrite it from its very
first return. A permanent role re-asserted on every call is not a hypothesis.

The ``YOU`` block renders §8's template with kernel-serialised slots, and where a
source is missing it says ``unavailable``. It never says zero. A seat told its
venue account is empty when the venue would not answer has been taught something
false about its own world, which is the whole of what this round is for.

The OUTCOME CONTRACT is rendered once per request, after the schema it is about.

And ``calc`` answers every fee, funding and carry case of the calibration set
exactly — the reviewer's §7 repair for a roster that did not meet "every critical
arithmetic case" — while the seventeen cases that are not arithmetic remain a
seat's own work, because a deterministic calculator has no standing to refuse on
a covenant or to write a program.
"""

from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from factorylab.cortex.calc import FUNDING_CONVENTION, calc
from factorylab.cortex.request import OUTCOME_CONTRACT, Request
from factorylab.cortex.schematics import (
    WORLD_CONTRACT_CLOSING,
    WORLD_CONTRACT_OPENING,
)
from factorylab.runtime.resume import restore_runtime, runtime_state
from factorylab.runtime.worlds import load_manifest
from factorylab.settlement.vocabulary import COMMISSIONED_JUDGE_REFUSAL
from tests.conftest import make_runtime

NOW_NS = 1_760_000_000 * 1_000_000_000
SEAT = "seed-decider"


def scripted_world():
    rt = make_runtime()
    rt.clock.now_ns = NOW_NS
    rt._manage_reserve_window()
    return rt


def request_for(rt, seat: str = SEAT, handle: str = "decision-r3e",
                inputs: dict | None = None) -> Request:
    req = rt._request(handle, "Respond to event Tick on scripted.",
                      {"kind": "Tick", "payload": {"index": 1}, "world": rt._world_block()},
                      {"type": "object", "properties": {"action": {"type": "string"}}},
                      NOW_NS + 60 * 1_000_000_000, "verdict")
    return replace(req, inputs={**req.inputs, "you": seat, **(inputs or {})})


# ------------------------------------------------------------------ the stable prefix


def test_the_prefix_is_the_world_contract_and_the_base_capability_index_and_nothing_else():
    """§8's stable prefix, whole and bounded."""
    rt = scripted_world()
    prefix = request_for(rt).stable_prefix()
    assert prefix.startswith(WORLD_CONTRACT_OPENING)
    assert WORLD_CONTRACT_CLOSING in prefix
    contract, index = prefix.split("BASE CAPABILITIES\n", 1)
    # The norms are the charter object's own, so a ratified edition renders what
    # its population voted and never a staler copy of it.
    for norm in rt.charter.norms:
        assert f"{str(norm)[:1].upper()}{str(norm)[1:]}:" in contract
    assert "current facts, not additional standing\ninstructions" in contract
    # The index: every capability, one line and one price, and no schema.
    published = json.loads(index[index.index("{"):].strip())
    assert {row["id"] for row in published["tools"]} == set(rt.tool_specs)
    assert {row["kind"] for row in published["proposals"]} == set(rt.PROPOSAL_SHAPES)
    assert all(set(row) == {"id", "description", "price_micro_per_call"}
               for row in published["tools"])
    assert all("args_schema" not in row for row in published["tools"])
    assert "catalogue.search" in published["schemas"]
    # And nothing that moves. The cards, their prices and the account are elsewhere.
    for absent in ('"card_prices"', '"pots"', '"account"', '"seats"', '"recent_mids"',
                   '"adaptive_scoring"', '"registration_feedback"'):
        assert absent not in prefix


def test_the_addressing_reference_line_is_in_the_prefix_exactly_once():
    """R3-D's reference line is a constant, so it is cached, not re-sent."""
    rt = scripted_world()
    request = request_for(rt)
    prefix = request.stable_prefix()
    assert COMMISSIONED_JUDGE_REFUSAL in prefix
    assert "inputs.you is your own assembly id" in prefix
    # Once in the whole prompt: the world block still publishes ``addressing`` for
    # its other readers, and the prompt suppresses that copy from INPUTS.
    assert request.prompt_text().count(COMMISSIONED_JUDGE_REFUSAL) == 1
    assert "addressing" in rt._world_block()


def test_the_prefix_is_byte_identical_across_two_requests_and_across_a_restore():
    """Serialised once, reused. The property a provider's automatic cache is keyed on."""
    rt = scripted_world()
    first = request_for(rt, handle="a")
    second = request_for(rt, seat="eval-a", handle="b")
    assert first.stable_prefix() == second.stable_prefix()
    # The same string object, not two equal serialisations: there is one.
    assert first.stable_prefix() is second.stable_prefix()
    assert first.prompt_text().startswith(first.stable_prefix())
    assert second.prompt_text().startswith(second.stable_prefix())

    restored = make_runtime()
    restore_runtime(restored, runtime_state(rt))
    restored.clock.now_ns = NOW_NS
    restored._manage_reserve_window()
    assert request_for(restored, handle="c").stable_prefix() == first.stable_prefix()
    assert (request_for(restored, handle="c").stable_prefix().encode("utf-8")
            == first.stable_prefix().encode("utf-8"))


def test_the_prefix_is_never_given_to_the_system_role():
    """The capability index carries prose the population wrote; the system role does not."""
    rt = scripted_world()
    request = request_for(rt)
    for assembly in rt.assemblies.values():
        mreq = assembly.build_model_request(request)
        assert mreq.system == assembly.spec.system_prompt
        assert "WORLD CONTRACT" not in mreq.system
        assert mreq.messages[-1]["content"].startswith(request.stable_prefix())


# ----------------------------------------------------------------- the lens moves out


def test_the_lens_is_absent_from_every_system_message_and_present_in_every_genesis_head():
    """§7: remove the permanent lens from the system prompt, seed it once in working state."""
    import re

    third = Path("docs/audits/v6/gpt6-third/prompts.md").read_text()
    contract = re.findall(r"```text\n(.*?)```", third, re.S)[0].rstrip("\n")
    constructor = json.loads(re.search(r"```json\n(.*?)```", third, re.S).group(1))["lens"]
    source = Path("docs/audits/v6/gpt6/seed-prompts.md").read_text()
    quoted = re.findall(r"^> (.+)$", source, re.MULTILINE)
    names = re.findall(r"^\*\*([a-z-]+)\*\*$", source, re.MULTILINE)
    lenses = dict(zip(names, quoted[1:], strict=True)) | {"constructor": constructor}

    m = load_manifest("edition3-testnet")
    assert len(m.assemblies) == 9
    for seat in m.assemblies:
        assert seat.system_prompt == contract, seat.id
        head = seat.initial_state
        assert head == {"lens": lenses[seat.id], "open_questions": [],
                        "active_commitments": []}, seat.id
        assert head["lens"] not in seat.system_prompt
    # One contract, not nine. A contract that differs by seat is not one.
    assert len({a.system_prompt for a in m.assemblies}) == 1
    # The contract itself names the working state and says the lens is editable.
    assert "Your starting lens is\neditable working state, not a permanent role." in contract


def test_the_seeded_head_is_what_the_runtime_gives_the_seat_back():
    """A head seeded at genesis is the head ``YOU`` renders, verbatim."""
    rt = scripted_world()
    head = {"lens": "a hypothesis about useful work", "open_questions": [],
            "active_commitments": []}
    rt.working_state.put(SEAT, dict(head))
    rendered = request_for(rt, inputs={
        "your_state": rt.working_state.render(SEAT)}).seat_block()["working_state"]
    assert rendered["state"] == head
    # And it is rendered exactly once, in ``YOU``, and never in the moving block.
    text = request_for(rt, inputs={"your_state": rt.working_state.render(SEAT)}).prompt_text()
    assert text.count("a hypothesis about useful work") == 1
    assert "a hypothesis about useful work" not in text.split("\n\nWORLD UPDATE\n", 1)[1]


# ------------------------------------------------------------------------- the YOU block


SLOTS = ("seat", "lineage", "request", "clock", "working_state", "spending_authority",
         "provider_inventory", "venue_accounts", "pending_conversions", "runway",
         "subscription", "open_commitments", "outcomes", "directory")


def test_you_renders_every_slot_of_the_template():
    rt = scripted_world()
    block = request_for(rt, inputs={
        "your_state": {"sha": "a" * 64, "bytes": 3, "state": {}},
        "unread_outcomes": {"count": 0, "items": []}}).seat_block()
    assert tuple(sorted(block)) == tuple(sorted(SLOTS))
    assert block["seat"] == SEAT and block["lineage"] == rt.budget.lineage(SEAT)
    clock = block["clock"]
    assert clock["now_utc"] == "2025-10-09T08:53:20Z"
    assert clock["tick_interval_seconds"] == rt.tick_clock.interval_ns // 1_000_000_000
    assert clock["tick_index"] == rt.tick_index
    authority = block["spending_authority"]
    assert authority["available_micro_usd"] == rt.budget.entitlement(SEAT)
    assert authority["held_micro_usd"] == rt.budget.held_by(SEAT)
    assert set(authority["next_release"]) >= {"at_utc", "your_share_usd", "rule"}
    assert "openrouter_usd" in block["provider_inventory"]
    assert "as_of" in block["provider_inventory"]  # freshness, always
    assert block["subscription"]["next_eligible_tick"] >= rt.tick_index
    # R3-B's typed custody, rendered account by account: each one states whether
    # it was observed and when, and the compute wallet is not among them.
    accounts = block["venue_accounts"]
    assert {"venue_perps", "venue_spot", "base_reserve"} <= set(accounts)
    for name in ("venue_perps", "venue_spot", "base_reserve"):
        assert accounts[name]["status"] in ("observed", "unavailable")
        assert "observed_at_ns" in accounts[name]
    assert "authority" not in accounts and "compute authority is not an asset" in accounts["note"]
    assert block["pending_conversions"]["status"] in ("observed", "unavailable")
    assert set(block["directory"]) == {"notes", "artifacts", "paging"}


def test_a_missing_source_renders_unavailable_and_never_a_fabricated_number():
    """A request with no world and no continuity inputs states its own ignorance."""
    bare = Request("h", "d", {"you": SEAT}, {}, {"type": "object"}, NOW_NS, 0, None,
                   "c", "verdict", "self")
    block = bare.seat_block()
    assert tuple(sorted(block)) == tuple(sorted(SLOTS))
    for slot in ("lineage", "working_state", "spending_authority", "provider_inventory",
                 "venue_accounts", "pending_conversions", "runway", "subscription",
                 "open_commitments", "directory"):
        assert block[slot] == "unavailable", slot
    assert block["clock"] == {"now_utc": "unavailable", "tick_index": "unavailable",
                              "tick_interval_seconds": "unavailable",
                              "last_successful_delivery": "unavailable"}
    assert block["outcomes"] == {"unread_count": "unavailable", "items": [],
                                 "more": "unavailable"}
    # Not one zero, not one empty account, anywhere in the rendering.
    rendered = json.dumps(block)
    assert ": 0" not in rendered and '"0"' not in rendered


def test_a_venue_read_that_fails_is_unavailable_with_its_reason_and_never_an_empty_account():
    rt = scripted_world()

    # The one venue read the prompt path performs (R3-B memoises its failure too).
    rt._tick_account_observation = lambda: (None, "VenueUnavailable", None)
    block = request_for(rt).seat_block()
    accounts = block["venue_accounts"]
    for name in ("venue_perps", "venue_spot"):
        assert accounts[name]["status"] == "unavailable"
        assert "VenueUnavailable" in accounts[name]["reason"]
    # Not one fabricated number: no equity, no cash, no position set.
    rendered = json.dumps({k: v for k, v in accounts.items() if k != "to_date"})
    assert "equity_usd" not in rendered and "positions" not in rendered
    # And the failure is named where a reader looks for what could not be read.
    reasons = request_for(rt).world_update_block()["unavailable_observations"]
    sources = {row["source"] for row in reasons}
    assert {"custody:venue_perps", "custody:venue_spot"} <= sources


def test_private_state_appears_once_and_never_in_the_moving_block():
    rt = scripted_world()
    req = request_for(rt, inputs={
        "your_state": {"sha": "b" * 64, "bytes": 9, "state": {"secret": "PRIVATE-MARKER"}},
        "unread_outcomes": {"count": 1, "items": [
            {"handle": "decision-1", "outcome_id": "outcome:4", "note": "INBOX-MARKER"}]}})
    text = req.prompt_text()
    assert text.count("PRIVATE-MARKER") == 1 and text.count("INBOX-MARKER") == 1
    you, rest = text.split("\n\nWORLD UPDATE\n", 1)
    assert "PRIVATE-MARKER" in you and "PRIVATE-MARKER" not in rest
    assert "INBOX-MARKER" in you and "INBOX-MARKER" not in rest
    assert json.dumps(req.world_update_block()).count("PRIVATE-MARKER") == 0


def test_the_world_update_block_carries_the_seven_slots_in_order():
    rt = scripted_world()
    update = request_for(rt).world_update_block()
    assert list(update) == [
        "observation_window", "changes_since_last_successful_delivery",
        "execution_receipts", "charter", "catalogue", "public_observations",
        "unavailable_observations"]
    assert update["charter"]["edition"] == rt.charter.edition
    assert "version" in update["catalogue"] and "changes" in update["catalogue"]
    # A source R3-F has not landed yet says so rather than reporting none.
    assert update["execution_receipts"]["status"] == "unavailable"
    carried = request_for(rt, inputs={"execution_receipts": [{"outcome_id": "outcome:1"}]})
    assert carried.world_update_block()["execution_receipts"] == [{"outcome_id": "outcome:1"}]
    # And a receipt list carried on the request is rendered there and not in INPUTS.
    text = carried.prompt_text()
    assert text.count('"execution_receipts"') == 1


# ------------------------------------------------------------------ the outcome contract


def test_the_outcome_contract_is_rendered_once_per_request_after_the_schema():
    rt = scripted_world()
    text = request_for(rt).prompt_text()
    assert text.count("OUTCOME CONTRACT") == 1
    assert OUTCOME_CONTRACT in text
    assert text.index("OUTCOME SCHEMA") < text.index("OUTCOME CONTRACT")
    assert text.index("OUTCOME CONTRACT") < text.index("COMPLETION CRITERION")
    for clause in ("intended: no operation has been submitted",
                   "settled: an addressed receipt establishes the consequence",
                   "unknown: the necessary observation is unavailable",
                   "The objection is a contestable claim",
                   "explicit asset, custody account and unit"):
        assert clause in text
    # It rides with the work and not in the system role, like every other contract
    # the population can read.
    mreq = rt.assemblies[SEAT].build_model_request(request_for(rt))
    assert "OUTCOME CONTRACT" not in mreq.system


# ------------------------------------------------------------------------------- calc


def _six(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.000001")))


def calc_answer(case) -> dict | None:
    """Ask ``calc`` this case's question, in the case's own output field names.

    A case ``calc`` cannot be asked returns ``None``: it is a refusal, a safety
    bound, a program to write or a memo to recall, and none of those is
    arithmetic. A calculator that answered them would be prescribing an
    objective, which is exactly what §7 says this capability must not do.
    """
    i = case.inputs
    if case.category == "fee":
        r = calc({"op": "fee", "size": i["size"], "price": i["price_usd"],
                  "fee_bps": i["fee_bps"]})
        return {"notional_usd": r["notional_usd"], "fee_usd": r["fee_usd"]}
    if case.category == "funding":
        r = calc({"op": "funding", "size": i["position_size"], "mark": i["mark_usd"],
                  "rate": i["funding_rate"], "convention": i["convention"]})
        assert r["convention"] == i["convention"]
        return {"funding_usd": r["funding_usd"]}
    if case.category in ("opportunity", "no_op"):
        r = calc({"op": "carry", "size": i["size"], "mark": i["price_usd"],
                  "hourly_rate": i["funding_rate_hourly"], "hours": i["window_hours"],
                  "round_trip_fee": i["round_trip_fee_usd"]})
        return {"carry_usd": r["carry_usd"], "net_usd": r["net_usd"],
                "worth_doing": r["net_usd_positive"]}
    return None


def test_calc_answers_every_arithmetic_case_of_the_calibration_set_exactly():
    """§7's repair: the gate is not met without a deterministic arithmetic capability."""
    from scripts.calibrate_seats import GATE, case_set

    cases = case_set()
    assert len(cases) == 45
    answered = {c.id: calc_answer(c) for c in cases}
    arithmetic = {cid: got for cid, got in answered.items() if got is not None}
    assert len(arithmetic) == 28  # seven fee, seven funding, fourteen carry
    for case in cases:
        got = answered[case.id]
        if got is None:
            continue
        assert got == case.expected["outputs"], case.id
    # Every critical arithmetic case in the gate, and not one of them approximate.
    critical = {c.id for c in cases if c.critical and c.category in ("fee", "funding")}
    assert critical <= set(arithmetic)
    assert {"fee", "funding"} <= set(GATE["critical_categories"])
    # The other seventeen are a seat's own work and calc declines to have an
    # opinion about them: refusals, safety bounds, programs and memory.
    assert {c.category for c in cases if answered[c.id] is None} == {
        "refusal", "safety", "construction", "state", "restart"}


def test_calc_is_exact_unit_explicit_and_states_its_sign_convention():
    fee = calc({"op": "fee", "notional": "918.7575", "fee_bps": "45"})
    assert fee["fee_usd"] == "4.134409" and fee["notional_usd"] == "918.757500"
    # A float argument is read as the number written, not the binary value near it.
    assert calc({"op": "notional", "size": 0.1, "price": 3})["notional_usd"] == "0.300000"
    paid = calc({"op": "funding", "size": "2", "mark": "100", "rate": "0.001"})
    assert paid["funding_usd"] == "0.200000"
    assert paid["convention"] == FUNDING_CONVENTION
    assert "paid by this position" in paid["sign"]
    received = calc({"op": "funding", "size": "-2", "mark": "100", "rate": "0.001"})
    assert received["funding_usd"] == "-0.200000"
    margin = calc({"op": "margin", "size": "-2", "mark": "3000", "leverage": "3"})
    assert margin["margin_usd"] == "2000.000000"  # a short posts margin, never negative
    assert all(k.endswith(("_usd", "_bps", "_hours")) or k in
               ("op", "convention", "period", "sign", "leverage", "net_usd_positive")
               for k in calc({"op": "carry", "size": "1", "mark": "1", "hourly_rate": "0",
                              "hours": "1", "round_trip_fee": "0"}))
    # It prescribes nothing: a subtraction's sign is a fact, "worth doing" is not.
    assert "worth_doing" not in calc({"op": "carry", "size": "1", "mark": "1",
                                      "hourly_rate": "1", "hours": "1",
                                      "round_trip_fee": "0"})


@pytest.mark.parametrize("args,fragment", [
    ({}, "calc op must be one of"),
    ({"op": "carve"}, "calc op must be one of"),
    ({"op": "notional", "size": "x", "price": "1"}, "not a decimal number"),
    ({"op": "notional", "size": "1"}, "calc price is required"),
    ({"op": "fee", "fee_bps": "1"}, "needs notional, or size with price"),
    ({"op": "margin", "size": "1", "mark": "1", "leverage": "0"}, "must be positive"),
    ({"op": "notional", "size": True, "price": "1"}, "decimal number or its string"),
    ({"op": "notional", "size": "nan", "price": "1"}, "must be finite"),
])
def test_calc_says_why_rather_than_raising(args, fragment):
    """A bad argument is a result, never an exception on the invocation path."""
    assert fragment in calc(args)["error"]


def test_calc_is_dispatched_priced_and_ledgered_like_any_tool():
    from factorylab.kernel.queue import PropensityRecord

    rt = scripted_world()
    handle = rt.queue.open(
        actor=SEAT, event_id="r3e-calc",
        propensity=PropensityRecord((SEAT,), (1.,), SEAT, 0, SEAT, "test"),
        channel="verdict", deadline_ns=NOW_NS + 86_400 * 10**9, parent_handle=None,
        cost_ceiling=10_000_000)
    rt.handle_to_assembly[handle] = SEAT
    # Published wherever the fixed primitives are published, and never a manifest
    # parameter: building a world block or dispatching a tool installs it.
    rt._ensure_connector_tool()
    assert "calc" in rt.tool_specs
    published = next(s for s in rt._world_block()["tools"] if s["id"] == "calc")
    assert published["price_micro_per_call"] == 0
    before = rt.wallet.balance
    result, cost = rt._run_tool(SEAT, handle, {
        "tool": "calc", "args": {"op": "notional", "size": "2", "price": "3.5"}})
    assert result == {"op": "notional", "notional_usd": "7.000000"}
    assert cost == 0 and rt.wallet.balance == before
    # Metered like any other tool: the seat's own meter runs it, at its price.
    spends = [row for row in rt.ledger._recovery_items()
              if row.get("reason") == "tool:calc"]
    assert spends and all(row.get("cost", 0) == 0 for row in spends)
    # A restored runtime has it too: the capability is a constant of the runtime.
    restored = make_runtime()
    restore_runtime(restored, runtime_state(rt))
    restored._ensure_connector_tool()
    assert "calc" in restored.tool_specs
