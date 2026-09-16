"""Continuity: a seat's working state and its outcome inbox (edition 3, contract C1).

Before this, a seat's whole memory was a deque of three returns. A fourth
return pushed the first out, and a consequence that settled afterwards had
nowhere to land: the money and the router learned it, the seat that reasoned
never did. Two objects replace it.

**Working state.** Bytes the seat writes, content-addressed in the artifact
archive (C9), with one head pointer per seat kept here. A seat advances its own
head by returning ``working_state``; the next request of that seat renders the
head verbatim under ``your_state``. Nothing rolls it over: it survives any
number of intervening returns, the retirement of the model behind the seat, and
restore, because the head is a hash and the bytes are an artifact the resume
already verifies. The soft allowance is 8 KiB — above it the state is kept and
the rent is simply what it is — and the hard limit is 64 KiB, above which the
field is refused, ledgered, and the head is left exactly as it was.

**Outcome inbox.** When a consequence settles for a decision a seat made, an
item addressed to that seat is appended: the original handle, what the seat
said then, the outcome, when it was observed, the financial delta, and an
evidence pointer into the diary. The body is an artifact; the inbox holds the
index. The oldest unread items ride on the next request under
``unread_outcomes``, with ``more`` counting the ones the window did not carry;
``outcome.get`` fetches any of them by ``outcome_id`` (a handle is a fallback
that answers with the oldest unread item of that decision, and says so); an
answer's ``ack_through`` takes an id and advances the cursor only as far as this
seat was actually delivered. An unacknowledged item stays. Nothing is lost.

What the seat said is retained until its decision's last consequence settles or
the seat retires; only then, and only over ``MAX_SAID``, is the oldest such
record archived as an artifact and dropped from the table — ``outcome.get`` and
the settler still read it back. A decision with open consequences is never
evictable (R3-F; GPT-6 third reading §3, "MAX_SAID can evict decision-linked
material before a delayed consequence").

Rent is by byte-time at the world's ``notes.micro_per_byte_day`` rate (C3),
accrued on the head's bytes from the moment it is written and collected at each
reserve-window boundary through the same metered path a note's rent takes.
There is no transfer toll: rendering a seat its own state costs the tokens it
costs and nothing else.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

#: The allowance a seat is told about. Above it the state is accepted anyway.
SOFT_STATE_BYTES = 8_192
#: Above this the field is refused and the head is unchanged.
HARD_STATE_BYTES = 65_536
#: Inbox items delivered inline on a request; the rest are counted and fetchable.
INLINE_OUTCOMES = 8
#: What the inbox remembers a seat said, bounded so an unbounded history cannot pin memory.
MAX_SAID = 1_024

STATE_TOO_LARGE = f"working_state exceeds {HARD_STATE_BYTES} bytes"
STATE_NOT_OBJECT = "working_state must be a JSON object"
OUTCOME_UNKNOWN = "no outcome addressed to you carries that handle"


def canonical(obj: Any) -> bytes:
    """Encode a JSON object exactly as it will be stored, or say it is not one."""
    if not isinstance(obj, dict):
        raise ValueError(STATE_NOT_OBJECT)
    try:
        return json.dumps(obj, sort_keys=True, allow_nan=False,
                          ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError):
        raise ValueError(STATE_NOT_OBJECT) from None


class WorkingState:
    """One head pointer per seat over content-addressed bytes in the archive.

    Guarantees: a refused put changes nothing (the head, its rent accrual and
    the archive are all untouched); an accepted put is ledgered before it is
    readable; ``head`` and ``render`` are pure reads; and a head restored from a
    checkpoint names bytes the resume has already verified, so a seat never
    wakes to a state the world cannot show it.
    """

    def __init__(self, artifacts: Any, ledger: Any, clock: Any) -> None:
        self.artifacts = artifacts
        self.ledger = ledger
        self.clock = clock
        # seat -> {"sha", "bytes", "ns", "handle", "rent_ns", "rent_carry", "rent_due"}
        self.heads: dict[str, dict[str, Any]] = {}

    def head(self, seat: str) -> dict[str, Any] | None:
        """The seat's current head record, or None when it has never written one."""
        return self.heads.get(seat)

    def put(self, seat: str, obj: Any, *, handle: str | None = None,
            kind: str = "working.state") -> dict[str, Any]:
        """Advance the seat's head to ``obj``; raise ValueError and change nothing if refused."""
        data = canonical(obj)
        if len(data) > HARD_STATE_BYTES:
            raise ValueError(STATE_TOO_LARGE)
        sha = self.artifacts.put(data, owner=seat, kind=kind)
        now = self.clock()
        previous = self.heads.get(seat)
        successor = {
            "sha": sha, "bytes": len(data), "ns": now, "handle": handle,
            # Accrual continues from the last boundary this seat was accounted to:
            # rewriting a state forgives no rent the old bytes already owed.
            "rent_ns": now,
            "rent_byte_ns": (previous.get("rent_byte_ns", 0) + previous["bytes"] *
                             max(0, now - previous.get("rent_ns", now))) if previous else 0,
            "rent_carry": previous.get("rent_carry", 0) if previous else 0,
            "rent_due": previous.get("rent_due", 0) if previous else 0,
        }
        self.ledger.append({"kind": "state.put", "assembly_id": seat, "sha": sha,
                            "bytes": len(data), "handle": handle,
                            "over_soft": len(data) > SOFT_STATE_BYTES, "ts": now})
        self.heads[seat] = successor
        return successor

    def render(self, seat: str) -> dict[str, Any] | None:
        """The head as the seat is shown it: ``{sha, bytes, state}``, verbatim.

        None means this seat has no head. A head whose bytes cannot be read raises:
        an unreadable state is an unavailable fact, never the absence of one.
        """
        record = self.heads.get(seat)
        if record is None:
            return None
        try:
            state = json.loads(self.artifacts.get(record["sha"]).decode("utf-8"))
        except Exception as exc:
            raise RuntimeError("working state is present but unavailable") from exc
        return {"sha": record["sha"], "bytes": record["bytes"], "state": state}


class OutcomeInbox:
    """Items addressed to the seat whose decision settled, with a per-seat read cursor.

    Guarantees: an item is appended once per settlement fact and never
    overwritten; the cursor only advances, and only to a handle the seat was
    actually addressed on; an item the seat has not acknowledged stays unread
    however many others arrive after it; and every body is an artifact, so a
    checkpoint carries indexes and cursors while the archive carries the text.
    """

    def __init__(self, artifacts: Any, ledger: Any, clock: Any) -> None:
        self.artifacts = artifacts
        self.ledger = ledger
        self.clock = clock
        # seat -> [{"seq", "handle", "sha", "observed_at_ns"}], oldest first.
        self.items: dict[str, list[dict[str, Any]]] = {}
        self.cursors: dict[str, int] = {}          # seat -> highest acknowledged seq
        self.said: dict[str, dict[str, Any]] = {}  # handle -> what its seat said then
        self.seq = 0
        # seat -> the highest seq this seat was actually shown, inline or by a
        # fetch. ``ack_through`` can never advance past it (R3-F, §4: "acknowledging
        # a handle can acknowledge unseen items").
        self.delivered_through: dict[str, int] = {}
        # handle -> the sha of a ``said`` record evicted under MAX_SAID. Nothing is
        # lost: the rationale is an artifact and is read back on demand.
        self.archived_said: dict[str, str] = {}
        # Set by the runtime to ``lambda handle: <the decision still has an open
        # consequence>``. Until it is set nothing is evictable, which is the
        # conservative reading and the behaviour that preceded R3-F.
        self.consequences_open: Any = None

    # -- what the seat said, kept so an outcome can be addressed to a reason ------------

    def record_said(self, seat: str, handle: str, outputs: Any) -> None:
        """Retain the rationale, stated payoff and forecasts of one return, bounded."""
        outputs = outputs if isinstance(outputs, dict) else {}
        forecasts = outputs.get("forecasts")
        self.said[handle] = {
            "seat": seat,
            "rationale": outputs.get("rationale") or outputs.get("action"),
            "payoff": outputs.get("payoff"),
            "forecasts": forecasts if isinstance(forecasts, list) else [],
            # A judge's fidelity objection (C3) rides on its return; the settler
            # reads it back from here when the verdict's consequence settles.
            "fidelity_objection": outputs.get("fidelity_objection"),
        }
        self.archived_said.pop(handle, None)
        self._evict_said()

    def _open(self, handle: str) -> bool:
        """Whether a decision may still receive a consequence, conservatively."""
        if self.consequences_open is None:
            return True
        try:
            return bool(self.consequences_open(handle))
        except Exception:
            return True

    def _evict_said(self) -> None:
        """Keep ``said`` bounded without losing a decision that can still settle (R3-F).

        A record is retained until its decision's last consequence settles or its
        seat retires. Only then, and only once the table is over ``MAX_SAID``, is
        the oldest such record evicted — and it is archived as an artifact first,
        so ``what_was_said`` and ``outcome.get`` still answer for it. The reviewer's
        finding was that ``MAX_SAID`` could drop a decision with open consequences;
        here it can drop nothing at all, only move it.
        """
        if len(self.said) <= MAX_SAID:
            return
        for handle in list(self.said):
            if len(self.said) <= MAX_SAID:
                return
            if self._open(handle):
                continue
            record = self.said[handle]
            sha = self.artifacts.put(canonical(record), owner=record["seat"],
                                     kind="said.archived")
            self.ledger.append({"kind": "said.archived", "assembly_id": record["seat"],
                                "handle": handle, "sha": sha, "ts": self.clock()})
            self.archived_said[handle] = sha
            del self.said[handle]

    def _said(self, handle: str) -> dict[str, Any]:
        """One retained return, from the table or from the artifact it was archived to."""
        record = self.said.get(handle)
        if record is not None:
            return record
        sha = self.archived_said.get(handle)
        if sha is None:
            return {}
        try:
            return json.loads(self.artifacts.get(sha).decode("utf-8"))
        except Exception as exc:
            raise RuntimeError("an archived rationale is unavailable") from exc

    def entry_for(self, handle: str) -> dict[str, Any]:
        """The ``{"handle", "outputs"}`` view of one retained return, for the settler."""
        record = self._said(handle)
        outputs = {k: record[k] for k in ("rationale", "payoff", "forecasts",
                                          "fidelity_objection") if k in record}
        return {"handle": handle, "outputs": outputs}

    def seat_of(self, handle: str) -> str | None:
        """The seat that made a decision, as the inbox recorded it."""
        record = self._said(handle)
        return record.get("seat") or None

    def what_was_said(self, handle: str) -> dict[str, Any]:
        """The ``said`` block of an item: the three fields, empty when nothing was retained."""
        record = self._said(handle)
        return {"rationale": record.get("rationale"), "payoff": record.get("payoff"),
                "forecasts": record.get("forecasts", [])}

    # -- appending, reading, acknowledging ---------------------------------------------

    def append(self, seat: str, *, handle: str, outcome: dict[str, Any],
               delta_micro: int = 0, evidence: Any = None,
               observed_at_ns: int | None = None) -> dict[str, Any] | None:
        """Address one settled consequence to the seat that decided it; return its record.

        A consequence with no owner to address is a **failed delivery** and is
        ledgered as one rather than dropped: the operator can find every fact the
        world settled that reached nobody (R3-F).
        """
        if not seat or not handle:
            self.ledger.append({"kind": "outcome.undeliverable", "assembly_id": seat or None,
                                "handle": handle or None, "outcome": outcome,
                                "delta_micro": int(delta_micro), "ts": self.clock()})
            return None
        fact = (hashlib.sha256(canonical({"handle": handle, "outcome": outcome,
                    "delta_micro": int(delta_micro), "evidence": evidence})).hexdigest()
                if evidence is not None else None)
        if fact is not None:
            for existing in self.items.get(seat, ()):
                if existing.get("fact") == fact:
                    return existing
        observed = self.clock() if observed_at_ns is None else observed_at_ns
        body = {
            "handle": handle,
            "said": self.what_was_said(handle),
            "outcome": outcome,
            "observed_at_ns": observed,
            "delta_micro": int(delta_micro),
            "evidence": evidence,
        }
        sha = self.artifacts.put(canonical(body), owner=seat, kind="outcome.item")
        next_seq = self.seq + 1
        record = {"seq": next_seq, "handle": handle, "sha": sha,
                  "observed_at_ns": observed, "fact": fact}
        self.ledger.append({"kind": "outcome.addressed", "assembly_id": seat, "handle": handle,
                            "sha": sha, "item": next_seq, "delta_micro": int(delta_micro),
                            "evidence": evidence, "ts": observed})
        self.seq = next_seq
        self.items.setdefault(seat, []).append(record)
        return record

    def body(self, sha: str) -> dict[str, Any] | None:
        """The stored body of one item; an item whose bytes are gone is unavailable, not absent."""
        try:
            return json.loads(self.artifacts.get(sha).decode("utf-8"))
        except Exception as exc:
            raise RuntimeError("addressed outcome is unavailable") from exc

    def _mark_delivered(self, seat: str, seq: int) -> None:
        """Record that this seat was actually shown this item, so it can acknowledge it."""
        if seq > self.delivered_through.get(seat, 0):
            self.delivered_through[seat] = seq

    def _find(self, seat: str, ident: Any) -> dict[str, Any] | None:
        """One item by ``outcome:<n>``, or the oldest unread item for a handle (R3-F).

        An id names exactly one item. A handle names a decision, which can carry
        several consequences, so it is a fallback and it resolves to the *oldest
        unread* item of that handle — the next thing the seat has not read —
        rather than the latest, which is what hid the earlier facts before.
        """
        if not isinstance(ident, str):
            return None
        rows = self.items.get(seat, ())
        for record in rows:
            if ident == f"outcome:{record['seq']}":
                return record
        cursor = self.cursors.get(seat, 0)
        matching = [r for r in rows if r["handle"] == ident]
        unread = [r for r in matching if r["seq"] > cursor]
        return (unread or matching or [None])[0]

    def unread(self, seat: str) -> dict[str, Any]:
        """Deliver oldest-first, with unambiguous item addresses, until acknowledged.

        The inline window is the oldest ``INLINE_OUTCOMES`` unread items and
        ``more`` is how many unread items it did not carry, so a seat can tell the
        window from the queue (§4: "the inline window is a subset").
        """
        cursor = self.cursors.get(seat, 0)
        rows = [r for r in self.items.get(seat, ()) if r["seq"] > cursor]
        window = rows[:INLINE_OUTCOMES]
        for record in window:
            self._mark_delivered(seat, record["seq"])
        return {"count": len(rows), "more": len(rows) - len(window), "items": [
            {**self.body(r["sha"]), "outcome_id": f"outcome:{r['seq']}"} for r in window]}

    def get(self, seat: str, ident: Any) -> dict[str, Any]:
        """One item by ``outcome_id``, or by handle as a fallback — the ``outcome.get`` view."""
        record = self._find(seat, ident)
        if record is None:
            return {"error": OUTCOME_UNKNOWN}
        body = self.body(record["sha"])
        if body is None:
            return {"error": OUTCOME_UNKNOWN}
        self._mark_delivered(seat, record["seq"])
        view = {**body, "sha": record["sha"], "outcome_id": f"outcome:{record['seq']}",
                "related_outcomes": [f"outcome:{r['seq']}" for r in self.items.get(seat, ())
                                     if r["handle"] == record["handle"]],
                "read": record["seq"] <= self.cursors.get(seat, 0)}
        if ident != view["outcome_id"]:
            view["note"] = ("a handle can carry several outcomes; this is the oldest you "
                            "have not read. Address one exactly by its outcome_id.")
        return view

    def ack_through(self, seat: str, ident: Any) -> int | None:
        """Acknowledge every item delivered at or before ``ident``; return the new cursor.

        Two bounds (R3-F, §4). The cursor never moves backwards, and it never moves
        past what this seat was actually shown: acknowledging an id it learned from
        ``related_outcomes`` but was never delivered acknowledges only up to its
        last delivery, and everything after that stays unread.
        """
        record = self._find(seat, ident)
        if record is None:
            return None
        cursor = max(self.cursors.get(seat, 0),
                     min(record["seq"], self.delivered_through.get(seat, 0)))
        self.ledger.append({"kind": "outcome.ack", "assembly_id": seat,
                            "through": f"outcome:{record['seq']}", "handle": record["handle"],
                            "cursor": cursor, "ts": self.clock()})
        self.cursors[seat] = cursor
        return cursor

    # -- the delivery and retention bookkeeping a checkpoint carries -------------------

    def delivery_state(self) -> dict[str, Any]:
        """Plain data: how far each seat was delivered, and what ``said`` was archived."""
        return {"delivered_through": dict(sorted(self.delivered_through.items())),
                "archived_said": dict(sorted(self.archived_said.items()))}

    def restore_delivery(self, state: dict[str, Any]) -> None:
        """Adopt a checkpoint's delivery bookkeeping; a world without one starts empty."""
        self.delivered_through = {k: int(v)
                                  for k, v in (state.get("delivered_through") or {}).items()}
        self.archived_said = dict(state.get("archived_said") or {})


def charge_window(rt) -> None:
    """Every head pays the rent its bytes accrued since the last boundary (C3).

    The same arithmetic and the same metered path the notebook's rent takes:
    exact byte-nanoseconds at ``notes.micro_per_byte_day``, the remainder carried
    on the head so collecting often can never round up and collecting rarely can
    never round down. An unaffordable boundary forgives nothing — the accrued
    interval closes and its amount stays due on the head — and a paid charge is a
    scored liability of the decision that wrote the state, not merely a debit.
    """
    from factorylab.runtime.notes import accrue
    from factorylab.world.metering import Infeasible

    now_ns = rt.clock.now_ns
    # The window boundary is also where the archive collects (R3-F): blobs no
    # reference names and nothing published — what a crash between the durable
    # write and its ledger item leaves — are removed and ledgered. An owned or
    # published blob is never a candidate, so this can take nothing a seat holds.
    collect = getattr(rt.artifacts, "collect", None)
    if collect is not None:
        collect()
    for seat, head in rt.working_state.heads.items():
        micro, carry = accrue(head, now_ns, rt.m.notes)
        price = head.get("rent_due", 0) + micro
        head["rent_ns"], head["rent_carry"] = now_ns, carry
        head["rent_byte_ns"] = 0
        if not price:
            continue
        handle = head.get("handle")
        try:
            if handle is None:
                raise Infeasible("a seeded head has no decision to charge")
            paid = rt._seat_meter(seat).run(handle=handle, reason="tool:state.storage",
                                            ceiling=price, execute=lambda: None,
                                            cost_of=lambda _, price=price: price)
        except Infeasible:
            head["rent_due"] = price
            rt.ledger.append({"kind": "state.rent_due", "assembly_id": seat, "cost": price,
                              "window": rt.window.index, "handle": handle, "ts": now_ns})
            continue
        rt.ledger.append({"kind": "state.rent", "assembly_id": seat, "cost": paid.cost,
                          "window": rt.window.index, "handle": handle, "ts": now_ns})
        rt._charge_storage(handle, paid.cost)
        head["rent_due"] = 0
