"""Voluntary addressing (edition 4, contract A): stateless helpers over the
existing outcome inbox.

A participant may hand another participant a message. The sender pays the
transport; the recipient incurs no inference bill and no wake merely because
someone sent it something -- delivery schedules nothing and promises nothing.
A message body is text: it is data, it cannot carry authority, it is not a
return, and it never exposes the sender's private state. The recipient decides
whether and when to answer; an existing inbox acknowledgment can advance a
cursor without promising a reply.

These helpers hold no state of their own. Every fact they read or write lives
in the runtime's existing outcome inbox (rt.outcomes), the assembly roster and
the retired set, so a checkpoint carries everything and a restore replays
exactly what was delivered. There is no second queue and no replay list.

Guarantees:

- The message id is the hash of the authenticated sender, the sender's decision
  handle and the slot the caller assigns. The same slot always names the same
  message; a different slot is a different message even when the text is
  identical, so the same words can be sent twice on purpose.
- A replay is recognised from the inboxes alone, before any capacity bound: a
  slot already delivered with the same text to the same recipient returns the
  existing item however full the queue is, so a retried 64th message still
  succeeds. The same slot aimed at a different recipient, or at the same one
  with different text, is a conflict and is refused. The helpers never charge
  anyone; the caller meters the sender after prepare returns a fresh message.
- Text is bounded at 4096 UTF-8 bytes and is never censored: it may discuss
  anything, including registry or governance words, because it is data and
  cannot change authority -- it arrives as an inbox item, never as a return.
- Delivery is private: the item is addressed to the recipient under a
  synthetic handle, so the sender's own said-record (rationale, payoff,
  forecasts) never rides along, and a recipient holds at most 64 unread
  messages.
- Unknown or retired identities are refused before anything is charged, and
  every refusal is a bounded local reason.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

#: The text a message carries, and the unread bound per recipient.
MAX_TEXT_BYTES = 4_096
MAX_UNREAD_MESSAGES = 64

#: The synthetic handle prefix. A handle beginning with this is never a real
#: decision, so the inbox's what-was-said lookup answers empty for it.
MESSAGE_HANDLE_PREFIX = "msg:"


class AddressRefused(ValueError):
    """A bounded public refusal: the reason is fixed text, never a deeper layer's."""


@dataclass(frozen=True)
class Prepared:
    """One validated message: the id, the text, and whether it is a replay."""

    sender: str
    recipient: str
    text: str
    message_id: str
    handle: str
    replay: bool
    record: dict | None = None


def specs(price_micro: int) -> dict[str, dict]:
    """The tool spec the runtime installs when the world enables addressing.

    Guarantees the schema names exactly recipient and text, both strings, with
    the published text bound; and the price is the caller's exact integer.
    """
    return {"address.send": {
        "id": "address.send",
        "description": "Send one text message to another live participant. You pay "
        "the transport; the recipient is not charged and not woken, and decides "
        "whether and when to answer.",
        "args_schema": {
            "type": "object",
            "properties": {"recipient": {"type": "string"},
                           "text": {"type": "string", "maxLength": MAX_TEXT_BYTES}},
            "required": ["recipient", "text"], "additionalProperties": False,
        },
        "price_micro_per_call": int(price_micro),
        "kind": "address",
    }}


def _slot_text(raw) -> str:
    """The caller's slot as canonical text, or a refusal."""
    if type(raw) is int and raw >= 0:
        return str(raw)
    if isinstance(raw, str) and raw:
        return raw
    raise AddressRefused("slot must be a nonnegative integer or a nonempty string")


def _unread_messages(rt, seat: str) -> int:
    """How many message items the seat has not acknowledged, from the inbox alone."""
    cursor = rt.outcomes.cursors.get(seat, 0)
    return sum(1 for r in rt.outcomes.items.get(seat, ())
               if r["seq"] > cursor and r["handle"].startswith(MESSAGE_HANDLE_PREFIX))


def prepare(rt, sender: str, handle: str, args: dict, slot) -> Prepared:
    """Validate one message completely; identify it; say whether it is a replay.

    Guarantees both parties are live, not retired and not each other; the text
    is a string within 4096 UTF-8 bytes; the recipient holds fewer than 64
    unread messages; and every refusal happens here, before the caller meters
    anything. The id is the hash of the authenticated sender, the sender's
    decision handle and the slot, so the same text under a new slot is a new
    message, the same slot with the same text after delivery is a replay
    carrying the existing item, and the same slot with different text or aimed
    at a different recipient is a conflict. A replay is recognised before the
    unread bound, so a retried message never fails because the queue it
    already joined is full.
    """
    if not isinstance(sender, str) or sender not in rt.assemblies:
        raise AddressRefused("unknown sender")
    if sender in rt.retired_assemblies:
        raise AddressRefused("sender is retired")
    recipient = args.get("recipient") if isinstance(args, dict) else None
    if not isinstance(recipient, str) or recipient not in rt.assemblies:
        raise AddressRefused("unknown recipient")
    if recipient in rt.retired_assemblies:
        raise AddressRefused("recipient is retired")
    if recipient == sender:
        raise AddressRefused("a participant cannot message itself")
    text = args.get("text") if isinstance(args, dict) else None
    if not isinstance(text, str):
        raise AddressRefused("text must be a string")
    if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
        raise AddressRefused("text exceeds " + str(MAX_TEXT_BYTES) + " UTF-8 bytes")
    message_id = hashlib.sha256(json.dumps(
        {"sender": sender, "handle": handle, "slot": _slot_text(slot)},
        sort_keys=True,
        ensure_ascii=False).encode("utf-8")).hexdigest()
    synthetic = MESSAGE_HANDLE_PREFIX + message_id
    # Replay and conflict before any capacity bound, across every inbox: the
    # same slot may not be redelivered to a different recipient, whatever the
    # text says, and a retry of a delivered message never fails on capacity.
    for seat, records in rt.outcomes.items.items():
        for record in records:
            if record["handle"] != synthetic:
                continue
            stored = rt.outcomes.body(record["sha"])["outcome"]
            if seat == recipient and stored["text"] == text:
                return Prepared(sender=sender, recipient=recipient, text=text,
                                message_id=message_id, handle=synthetic,
                                replay=True, record=record)
            raise AddressRefused("message slot already used")
    if _unread_messages(rt, recipient) >= MAX_UNREAD_MESSAGES:
        raise AddressRefused("recipient message queue is full")
    return Prepared(sender=sender, recipient=recipient, text=text,
                    message_id=message_id, handle=synthetic,
                    replay=False, record=None)


def deliver(rt, prepared: Prepared) -> dict:
    """Append the message to the recipient's outcome inbox; return the item record.

    Guarantees the text reaches only the recipient's own inbox, addressed under
    the synthetic handle so the sender's said-record never rides along; a
    repeated delivery of the same id returns the existing record before any
    capacity check, so a retried delivery never fails on a full queue; a full
    queue of new messages, or a recipient retired between prepare and deliver,
    is refused with nothing appended; and no charge, wake or model call is ever
    made against the recipient.
    """
    if prepared.recipient in rt.retired_assemblies:
        raise AddressRefused("recipient is retired")
    for record in rt.outcomes.items.get(prepared.recipient, ()):
        if record["handle"] != prepared.handle:
            continue
        stored = rt.outcomes.body(record["sha"])["outcome"]
        if stored["text"] != prepared.text:
            raise AddressRefused("message slot already carries different text")
        return record
    if _unread_messages(rt, prepared.recipient) >= MAX_UNREAD_MESSAGES:
        raise AddressRefused("recipient message queue is full")
    return rt.outcomes.append(
        prepared.recipient, handle=prepared.handle,
        outcome={"kind": "message", "message_id": prepared.message_id,
                 "text": prepared.text, "from": prepared.sender},
        delta_micro=0, evidence=prepared.message_id)
