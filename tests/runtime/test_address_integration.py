"""Contract A wired into the runtime: who pays, who does not, and what is written down.

The transport is a tool like any other, published only when the world enables it and
priced like the rest. What is particular to it is the accounting around one append:
a message that is refused was never carried and costs nothing; a message that is
delivered is paid once by its sender; a message replayed after an interruption is
recognised from the recipient inbox and paid for again by nobody. The recipient pays
nothing at any point and is not woken: an inbox item is not a decision.

The body is the recipient own. It reaches the recipient inbox and stops there: not
the diary row the wake page is built from, not the judge projection of a child
return, and not another participant artifact reads.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from factorylab.cortex.request import public_return
from factorylab.runtime.address import MAX_TEXT_BYTES, MESSAGE_HANDLE_PREFIX
from factorylab.runtime.loop import Runtime
from factorylab.runtime.worlds import load_manifest
from factorylab.world.exchange import FakeExchange
from factorylab.world.scripted import ScriptedProvider

SECRET = "The funding carry you asked about runs positive through Friday."


def world(address_enabled=True):
    """A scripted runtime with no events budget: these tests run no world."""
    manifest = load_manifest("worlds/scripted.toml")
    manifest = replace(manifest, tools=replace(manifest.tools,
                                               address_enabled=address_enabled))
    return Runtime(manifest, events=0, seed=1, initial_balance_micro=None, ledger_path=None,
                   router_gamma=0.1, provider=ScriptedProvider(),
                   exchange=FakeExchange(coins=manifest.exchange.coins))


def parties(rt):
    sender, recipient = list(rt.assemblies)[:2]
    return sender, recipient


def send(rt, sender, recipient, text=SECRET, *, handle="decision-1", slot="tool:0"):
    return rt._run_tool(sender, handle,
                        {"tool": "address.send", "args": {"recipient": recipient,
                                                          "text": text}}, slot=slot)


def inbox(rt, seat):
    return [r for r in rt.outcomes.items.get(seat, ())
            if r["handle"].startswith(MESSAGE_HANDLE_PREFIX)]


@pytest.fixture
def open_world():
    return world()


def test_the_capability_exists_only_where_the_world_publishes_it():
    assert "address.send" not in world(address_enabled=False).tool_specs
    rt = world()
    spec = rt.tool_specs["address.send"]
    assert spec["price_micro_per_call"] == rt.m.tools.population_tool_micro_per_call
    assert spec["args_schema"]["required"] == ["recipient", "text"]
    assert spec["args_schema"]["properties"]["text"]["maxLength"] == MAX_TEXT_BYTES
    # Every published tool carries an example its own schema accepts (B1).
    assert spec["args_schema"]["examples"]


def test_the_sender_pays_the_transport_once_and_the_recipient_pays_nothing(open_world):
    rt = open_world
    sender, recipient = parties(rt)
    price = rt.tool_specs["address.send"]["price_micro_per_call"]
    before = rt.budget.entitlement(sender), rt.budget.entitlement(recipient)
    result, cost = send(rt, sender, recipient)
    assert cost == price
    assert result["status"] == "delivered" and result["replay"] is False
    after = rt.budget.entitlement(sender), rt.budget.entitlement(recipient)
    assert before[0] - after[0] == price
    assert after[1] == before[1]
    assert len(inbox(rt, recipient)) == 1


def test_delivery_wakes_nobody_and_buys_no_thinking(open_world):
    rt = open_world
    sender, recipient = parties(rt)
    invocations = dict(rt.stats.invocations_by_assembly)
    outstanding = len(rt.queue.outstanding())
    send(rt, sender, recipient)
    # An inbox item is not a decision: no queue entry was opened for the recipient
    # and no model was called on anyone.
    assert len(rt.queue.outstanding()) == outstanding
    assert dict(rt.stats.invocations_by_assembly) == invocations


def test_a_replayed_return_delivers_once_and_is_charged_once(open_world):
    rt = open_world
    sender, recipient = parties(rt)
    first, paid = send(rt, sender, recipient)
    before = rt.budget.entitlement(sender)
    second, free = send(rt, sender, recipient)
    assert paid > 0 and free == 0
    assert second["replay"] is True
    assert second["message_id"] == first["message_id"]
    assert rt.budget.entitlement(sender) == before
    assert len(inbox(rt, recipient)) == 1


@pytest.mark.parametrize("args, reason", [
    ({"recipient": "nobody-here", "text": SECRET}, "unknown recipient"),
    ({"recipient": None, "text": SECRET}, "invalid address arguments"),
    ({"text": SECRET}, "invalid address arguments"),
])
def test_a_refused_message_is_free_and_carries_nothing(open_world, args, reason):
    rt = open_world
    sender, recipient = parties(rt)
    before = rt.budget.entitlement(sender)
    result, cost = rt._run_tool(sender, "decision-1",
                                {"tool": "address.send", "args": args}, slot="tool:0")
    assert cost == 0
    assert result["error"] == reason
    assert rt.budget.entitlement(sender) == before
    assert inbox(rt, recipient) == []


def test_a_seat_cannot_address_itself_and_pays_nothing_to_learn_it(open_world):
    rt = open_world
    sender, _ = parties(rt)
    before = rt.budget.entitlement(sender)
    result, cost = send(rt, sender, sender)
    assert cost == 0 and "itself" in result["error"]
    assert rt.budget.entitlement(sender) == before


@pytest.mark.parametrize("invalid", ["extra", "list", "wrong_type"])
def test_invalid_address_schema_is_refused_before_transport(open_world, invalid):
    rt = open_world
    sender, recipient = parties(rt)
    args = {"recipient": recipient, "text": "hi"}
    if invalid == "extra":
        args["private_memory"] = SECRET
    elif invalid == "list":
        args = [args]
    else:
        args["text"] = 42
    before = rt.budget.entitlement(sender), rt.budget.entitlement(recipient)
    result, cost = rt._run_tool(
        sender, "decision-schema", {"tool": "address.send", "args": args})
    assert result == {"error": "invalid address arguments"}
    assert cost == 0
    assert (rt.budget.entitlement(sender), rt.budget.entitlement(recipient)) == before
    assert not inbox(rt, recipient)
    assert not any(row["kind"] == "address.delivered" for row in rt.ledger._recovery_items())
    assert SECRET not in json.dumps(result)


def test_one_slot_is_one_message_and_a_changed_body_is_a_conflict(open_world):
    rt = open_world
    sender, recipient = parties(rt)
    send(rt, sender, recipient)
    before = rt.budget.entitlement(sender)
    result, cost = send(rt, sender, recipient, text="a different thing entirely")
    assert cost == 0 and "already used" in result["error"]
    assert rt.budget.entitlement(sender) == before
    # A new slot is a new message, so the same words may be sent again on purpose.
    again, paid = send(rt, sender, recipient, slot="tool:1")
    assert paid > 0 and again["replay"] is False
    assert len(inbox(rt, recipient)) == 2


def test_an_unaffordable_transport_delivers_nothing(open_world):
    rt = open_world
    sender, recipient = parties(rt)
    rt.budget.debit(sender, rt.budget.entitlement(sender), "spent on other work")
    result, cost = send(rt, sender, recipient)
    assert cost == 0 and "error" in result
    assert inbox(rt, recipient) == []


def test_the_body_reaches_the_recipient_inbox_and_stops_there(open_world):
    rt = open_world
    sender, recipient = parties(rt)
    receipt, _ = send(rt, sender, recipient)
    # The sender receipt is proof without the text.
    assert SECRET not in json.dumps(receipt)
    assert receipt["text_bytes"] == len(SECRET.encode("utf-8"))
    # The recipient holds it.
    item = inbox(rt, recipient)[0]
    assert rt.outcomes.body(item["sha"])["outcome"]["text"] == SECRET
    # And nobody else can read those bytes: an inbox item is owned by its recipient.
    assert rt.artifacts.read(item["sha"], reader=sender,
                             lineage_of=rt.budget.lineage).get("error")
    assert rt.artifacts.read(item["sha"], reader=recipient,
                             lineage_of=rt.budget.lineage).get("error") is None


def test_the_diary_row_the_wake_page_reads_has_no_body(open_world):
    rt = open_world
    sender, recipient = parties(rt)
    call = {"tool": "address.send", "args": {"recipient": recipient, "text": SECRET}}
    rt._run_tool(sender, "decision-1", call, slot="tool:0")
    rows = [dict(item) for item in rt.ledger.items()
            if str(item.get("kind", "")).startswith("address.")]
    assert [r["kind"] for r in rows] == ["address.delivered"]
    assert SECRET not in json.dumps(rows, default=str)
    assert rows[0]["recipient"] == recipient
    assert rows[0]["text_bytes"] == len(SECRET.encode("utf-8"))


def test_a_judge_sees_that_a_message_happened_and_not_what_it_said():
    # The projection every child and producer return crosses on its way to a judge.
    outputs = {"action": "message",
               "tool_calls": [{"tool": "address.send",
                               "args": {"recipient": "seed-decider", "text": SECRET}}]}
    projected = public_return(outputs)
    assert SECRET not in json.dumps(projected)
    assert projected["tool_calls"][0]["args"]["recipient"] == "seed-decider"
