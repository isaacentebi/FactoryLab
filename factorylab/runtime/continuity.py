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
index. The newest unread items ride on the next request under
``unread_outcomes``; ``outcome.get`` fetches any of them by handle; an answer's
``ack_through`` advances the cursor. An unacknowledged item stays. Nothing is
lost.

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
        # Do not evict addressability by unrelated traffic. A future archive-backed
        # GC must prove that no delayed consequence can reference a removed handle.

    def entry_for(self, handle: str) -> dict[str, Any]:
        """The ``{"handle", "outputs"}`` view of one retained return, for the settler."""
        record = self.said.get(handle, {})
        outputs = {k: record[k] for k in ("rationale", "payoff", "forecasts",
                                          "fidelity_objection") if k in record}
        return {"handle": handle, "outputs": outputs}

    def seat_of(self, handle: str) -> str | None:
        """The seat that made a decision, as the inbox recorded it."""
        record = self.said.get(handle)
        return None if record is None else record["seat"]

    def what_was_said(self, handle: str) -> dict[str, Any]:
        """The ``said`` block of an item: the three fields, empty when nothing was retained."""
        record = self.said.get(handle, {})
        return {"rationale": record.get("rationale"), "payoff": record.get("payoff"),
                "forecasts": record.get("forecasts", [])}

    # -- appending, reading, acknowledging ---------------------------------------------

    def append(self, seat: str, *, handle: str, outcome: dict[str, Any],
               delta_micro: int = 0, evidence: Any = None,
               observed_at_ns: int | None = None) -> dict[str, Any] | None:
        """Address one settled consequence to the seat that decided it; return its record."""
        if not seat or not handle:
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

    def unread(self, seat: str) -> dict[str, Any]:
        """Deliver oldest-first, with unambiguous item addresses, until acknowledged."""
        cursor = self.cursors.get(seat, 0)
        rows = [r for r in self.items.get(seat, ()) if r["seq"] > cursor]
        return {"count": len(rows), "items": [
            {**self.body(r["sha"]), "outcome_id": f"outcome:{r['seq']}"}
            for r in rows[:INLINE_OUTCOMES]]}

    def get(self, seat: str, handle: Any) -> dict[str, Any]:
        """One item by handle, read or unread — the ``outcome.get`` view."""
        if isinstance(handle, str):
            for record in reversed(self.items.get(seat, ())):
                if record["handle"] == handle or handle == f"outcome:{record['seq']}":
                    body = self.body(record["sha"])
                    if body is not None:
                        return {**body, "sha": record["sha"],
                                "outcome_id": f"outcome:{record['seq']}",
                                "related_outcomes": [f"outcome:{r['seq']}"
                                    for r in self.items.get(seat, ())
                                    if r["handle"] == record["handle"]],
                                "read": record["seq"] <= self.cursors.get(seat, 0)}
        return {"error": OUTCOME_UNKNOWN}

    def ack_through(self, seat: str, handle: Any) -> int | None:
        """Advance the seat's cursor to the named item; return the new cursor, or None."""
        if not isinstance(handle, str):
            return None
        for record in reversed(self.items.get(seat, ())):
            if record["handle"] == handle or handle == f"outcome:{record['seq']}":
                cursor = max(self.cursors.get(seat, 0), record["seq"])
                self.ledger.append({"kind": "outcome.ack", "assembly_id": seat,
                                    "handle": handle, "cursor": cursor, "ts": self.clock()})
                self.cursors[seat] = cursor
                return cursor
        return None


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
