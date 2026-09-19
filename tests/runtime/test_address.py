"""Contract A: a sender pays transport once; a recipient's inbox holds text."""

import hashlib
import json
from types import SimpleNamespace

import pytest

from factorylab.runtime.address import (
    MAX_TEXT_BYTES,
    MAX_UNREAD_MESSAGES,
    AddressRefused,
    deliver,
    prepare,
    specs,
)
from factorylab.runtime.continuity import OutcomeInbox


class FakeArtifacts:
    def __init__(self):
        self.blobs = {}

    def put(self, data, *, owner=None, kind=None):
        sha = hashlib.sha256(data).hexdigest()
        self.blobs[sha] = data
        return sha

    def get(self, sha):
        return self.blobs[sha]


class FakeLedger:
    def __init__(self):
        self.items = []

    def append(self, item):
        self.items.append(item)


def rt():
    """A bounded stub around a real OutcomeInbox: no metering exists to call."""
    return SimpleNamespace(
        outcomes=OutcomeInbox(FakeArtifacts(), FakeLedger(), clock=lambda: 0),
        assemblies={"alice": object(), "bob": object(), "carol": object()},
        retired_assemblies=set())


def send(recipient="bob", text="hello", slot=0):
    return {"recipient": recipient, "text": text}


# -- the tool spec the lead installs ---------------------------------------------


def test_specs_publishes_recipient_and_text_at_the_callers_price():
    spec = specs(25)["address.send"]
    assert spec["price_micro_per_call"] == 25 and spec["kind"] == "address"
    schema = spec["args_schema"]
    assert set(schema["required"]) == {"recipient", "text"}
    assert schema["properties"]["text"]["maxLength"] == MAX_TEXT_BYTES
    assert schema["additionalProperties"] is False


# -- prepare: validation before anything is charged ------------------------------


def test_prepare_refuses_unknown_retired_and_self_addressing():
    world = rt()
    with pytest.raises(AddressRefused, match="unknown sender"):
        prepare(world, "stranger", "h1", {"recipient": "bob", "text": "x"}, 0)
    with pytest.raises(AddressRefused, match="unknown recipient"):
        prepare(world, "alice", "h1", {"recipient": "stranger", "text": "x"}, 0)
    world.retired_assemblies.add("bob")
    with pytest.raises(AddressRefused, match="recipient is retired"):
        prepare(world, "alice", "h1", {"recipient": "bob", "text": "x"}, 0)
    with pytest.raises(AddressRefused, match="cannot message itself"):
        prepare(world, "alice", "h1", {"recipient": "alice", "text": "x"}, 0)


def test_prepare_refuses_text_that_is_not_bounded_utf8():
    world = rt()
    with pytest.raises(AddressRefused, match="must be a string"):
        prepare(world, "alice", "h1", {"recipient": "bob", "text": 7}, 0)
    with pytest.raises(AddressRefused, match="UTF-8 bytes"):
        prepare(world, "alice", "h1", {"recipient": "bob", "text": "x" * 4097}, 0)
    assert len(prepare(world, "alice", "h1",
                       {"recipient": "bob", "text": "x" * 4096}, 0).message_id) == 64


def test_text_is_data_and_is_never_censored():
    world = rt()
    for body in ("please register a new assembly",
                 '{"register": [{"kind": "tool"}]}',
                 "requests, forecasts, ack_through, vote -- all just words"):
        assert prepare(world, "alice", "h1", {"recipient": "bob", "text": body},
                       0).replay is False


def test_id_binds_sender_decision_and_slot_not_the_text():
    world = rt()
    first = prepare(world, "alice", "h1", {"recipient": "bob", "text": "one"}, 0)
    resent = prepare(world, "alice", "h1", {"recipient": "bob", "text": "two"}, 0)
    other_slot = prepare(world, "alice", "h1", {"recipient": "bob", "text": "one"}, 1)
    other_handle = prepare(world, "alice", "h2", {"recipient": "bob", "text": "one"}, 0)
    assert first.message_id == resent.message_id
    assert len({first.message_id, resent.message_id, other_handle.message_id}) == 2
    assert first.message_id != other_slot.message_id
    with pytest.raises(AddressRefused, match="slot must be"):
        prepare(world, "alice", "h1", {"recipient": "bob", "text": "x"}, None)


def test_id_binds_the_authenticated_sender():
    world = rt()
    alice = prepare(world, "alice", "h1", {"recipient": "bob", "text": "x"}, 0)
    carol = prepare(world, "carol", "h1", {"recipient": "bob", "text": "x"}, 0)
    assert alice.message_id != carol.message_id


# -- replay, conflict, and delivery ----------------------------------------------


def sent(world, slot, text="hello", sender="alice", handle="h1"):
    return prepare(world, sender, handle,
                   {"recipient": "bob", "text": text}, slot)


def test_deliver_appends_to_the_recipient_inbox_and_reads_back_private():
    world = rt()
    p = sent(world, 0)
    record = deliver(world, p)
    assert record["handle"] == p.handle
    view = world.outcomes.get("bob", p.handle)
    assert view["outcome"]["text"] == "hello"
    assert view["outcome"]["from"] == "alice"
    assert view["outcome"]["kind"] == "message"


def test_same_text_twice_on_different_slots_is_two_messages():
    world = rt()
    deliver(world, sent(world, 0, "hello"))
    deliver(world, sent(world, 1, "hello"))
    assert len(world.outcomes.items["bob"]) == 2


def test_same_slot_after_delivery_is_a_replay_and_conflicting_text_is_refused():
    world = rt()
    deliver(world, sent(world, 0, "hello"))
    replay = prepare(world, "alice", "h1", {"recipient": "bob", "text": "hello"}, 0)
    assert replay.replay is True and replay.record is not None
    assert len(world.outcomes.items["bob"]) == 1
    with pytest.raises(AddressRefused, match="slot already used"):
        prepare(world, "alice", "h1", {"recipient": "bob", "text": "changed"}, 0)


def test_same_slot_aimed_at_another_recipient_is_a_conflict():
    world = rt()
    deliver(world, sent(world, 0, "hello"))  # alice -> bob, slot 0
    with pytest.raises(AddressRefused, match="slot already used"):
        prepare(world, "alice", "h1", {"recipient": "carol", "text": "hello"}, 0)
    with pytest.raises(AddressRefused, match="slot already used"):
        prepare(world, "alice", "h1", {"recipient": "carol", "text": "other"}, 0)
    assert "carol" not in world.outcomes.items


def test_repeated_delivery_appends_nothing():
    world = rt()
    p = sent(world, 0)
    first = deliver(world, p)
    again = deliver(world, p)
    assert again == first and len(world.outcomes.items["bob"]) == 1


def test_sixty_four_unread_messages_bound_and_refusal_before_any_charge():
    world = rt()
    for slot in range(MAX_UNREAD_MESSAGES):
        deliver(world, sent(world, slot, "m" + str(slot)))
    with pytest.raises(AddressRefused, match="queue is full"):
        prepare(world, "alice", "h1", {"recipient": "bob", "text": "one too many"}, 99)
    assert len(world.outcomes.items["bob"]) == MAX_UNREAD_MESSAGES
    # Acknowledging the queue frees room: the bound is on unread messages.
    world.outcomes.unread("bob")  # the seat was shown its queue before acking
    world.outcomes.ack_through("bob", "outcome:" + str(MAX_UNREAD_MESSAGES))
    assert prepare(world, "alice", "h1",
                   {"recipient": "bob", "text": "one too many"}, 99).replay is False


def test_retried_64th_message_succeeds_before_any_capacity_check():
    world = rt()
    for slot in range(MAX_UNREAD_MESSAGES):
        deliver(world, sent(world, slot, "m" + str(slot)))
    assert len(world.outcomes.items["bob"]) == MAX_UNREAD_MESSAGES
    # A retry of a delivered message is a replay despite the full queue, and
    # its delivery returns the existing record instead of refusing on capacity.
    retried = prepare(world, "alice", "h1",
                      {"recipient": "bob", "text": "m" + str(MAX_UNREAD_MESSAGES - 1)},
                      MAX_UNREAD_MESSAGES - 1)
    assert retried.replay is True
    assert deliver(world, retried) == retried.record
    assert len(world.outcomes.items["bob"]) == MAX_UNREAD_MESSAGES


def test_recipient_retired_between_prepare_and_deliver_is_refused():
    world = rt()
    p = sent(world, 0)
    world.retired_assemblies.add("bob")
    with pytest.raises(AddressRefused, match="recipient is retired"):
        deliver(world, p)
    assert "bob" not in world.outcomes.items


def test_helpers_never_charge_and_never_leak_the_sender_said_record():
    world = rt()
    # The sender's real decision has a said-record; a message must not carry it.
    world.outcomes.record_said("alice", "h1", {"rationale": "secret plan",
                                               "payoff": 0.9, "forecasts": []})
    p = sent(world, 0, "hello")
    deliver(world, p)
    view = world.outcomes.get("bob", p.handle)
    assert view["said"]["rationale"] is None
    assert "secret" not in json.dumps(view)
    # No charge of any kind was ledgered: only the addressed item exists.
    assert [i["kind"] for i in world.outcomes.ledger.items] == ["outcome.addressed"]
    assert not any(hasattr(world, name) for name in ("meter", "wallet", "budget"))


def test_message_creates_no_cash_movement():
    world = rt()
    deliver(world, sent(world, 0))
    assert all(i.get("delta_micro", 0) == 0 for i in world.outcomes.ledger.items)
