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
the ledger marks it ``over_soft`` — and the hard limit is 64 KiB, above which the
field is refused, ledgered, and the head is left exactly as it was.
A third bound is about display, not storage: a head over ``INLINE_STATE_BYTES``
is named on the request — sha, exact size, ``loaded: False``, and the tool that
returns it — instead of pasted into it. The bytes are unchanged, nothing is
summarised, and ``artifact.get`` still hands the seat its own state exactly as it
wrote it.

**Outcome inbox.** When a consequence settles for a decision a seat made, an
item addressed to that seat is appended: the original handle, what the seat
said then, the outcome, when it was observed, the financial delta, and an
evidence pointer into the diary. The body is an artifact; the inbox holds the
index. What rides on the next request under ``unread_outcomes`` is that index and
not those bodies: up to four oldest and four newest unread items as compact
entries — the id, the handle, when it was observed, the typed outcome, the money,
the evidence pointer, the sha and size, and the tool that returns the rest — with
``more`` counting the ones the window did not carry. ``outcome.list`` starts after
the oldest prefix and pages every id through the middle without delivering or
acknowledging any of them, so a seat can see a recent failure without losing the
older facts that explain it. The newest four are marked ``preview: true`` and are
discovery only, like ``outcome.list``: fetching one with ``outcome.get`` records
delivery, while merely rendering new previews cannot grow delivery state.
``outcome.get`` fetches any of them whole by ``outcome_id`` (a handle is a fallback
that answers with the oldest unread item of that decision, and says so); an
answer's ``ack_through`` takes an id and advances the cursor only as far as this
seat was actually delivered. An unacknowledged item stays. Nothing is lost.

What the seat said is retained until its decision's last consequence settles or
the seat retires; only then, and only over ``MAX_SAID``, is the oldest such
record archived as an artifact and dropped from the table — ``outcome.get`` and
the settler still read it back. A decision with open consequences is never
evictable (R3-F; GPT-6 third reading §3, "MAX_SAID can evict decision-linked
material before a delayed consequence").

Retained bytes are a constraint, not a cash flow. Holding them pays no one: the
disk is the world's fixed-price machine, so no money leaves the factory at the
margin and the wallet does not move for them (the wallet moves only when money
moves; essay II.II.b casts a scarce resource as a hard limit or prices it through
the charter's λ on reward, II.IV.a). The hard limit above is the cast, and it bounds
the whole of what a seat retains, not each version: a new head releases the
superseded one's reference, whose bytes are collected once no durable checkpoint
names them (``ArtifactStore.release``). Retirement is final for a version, not for
an id: a retired id's head is kept, so the id registered again as its next version
inherits it. A program's next version is new code and starts with no private
state: the old version's is superseded, and released, at that registration. The
disk is finite, so the
whole of retained private state has its own hard limit, fixed for the world's
life (``[storage] retained_private_bytes``): **retained private state is at most
``retained_private_bytes``, always.** A retired id's state is kept until capacity
is needed: a write that would pass the limit first releases retired ids' state,
oldest retirement first, through the journaled release, and a write that still does
not fit is refused with the capacity error, as on a full disk. A live seat's state
is never released to make room. The size of every head is ledgered on its
``state.put`` item, and the archive's size at every boundary on
``artifact.retained``, so the charter can price retained state if the population
proposes to.
Rendering a seat its own state costs the tokens it costs and nothing else.
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
#: Above this a head is *shown* by reference instead of inline. Storage, the soft
#: allowance and the hard limit are untouched: this bounds what a request carries,
#: never what the world keeps.
INLINE_STATE_BYTES = 4_096
#: The largest page one list call will return, so a paged index cannot become a dump.
MAX_LIST_LIMIT = 32
#: Typed fields an index carries verbatim off an outcome, where the outcome has them.
INDEX_FIELDS = ("kind", "status", "phase", "subject", "score",
                "rejection_reason", "rejected_section")
#: An evidence pointer longer than this is named by its size instead of carried.
MAX_INDEX_EVIDENCE = 128
#: The longest a typed index field may be before it is named by its size instead.
#: A field is a label, not a payload: past this it is a body under another name.
MAX_INDEX_FIELD = 256
#: What the inbox remembers a seat said, bounded so an unbounded history cannot pin memory.
MAX_SAID = 1_024

STATE_TOO_LARGE = f"working_state exceeds {HARD_STATE_BYTES} bytes"
STATE_NOT_OBJECT = "working_state must be a JSON object"
OUTCOME_UNKNOWN = "no outcome addressed to you carries that handle"
STATE_NOT_LOADED = (
    f"this head is over the {INLINE_STATE_BYTES}-byte display bound, so it is named here "
    "and not carried; artifact.get on its sha returns the bytes exactly as written"
)
OUTCOME_BODIES = ("an index, never a body: outcome.get {outcome_id} returns one item whole, "
                  "outcome.list {after, limit} pages the ids you have not read")
OUTCOME_PREVIEWS = ("items marked preview are discovery only; use outcome.get on an "
                    "outcome_id to deliver one before acknowledging it")


def canonical(obj: Any) -> bytes:
    """Encode a JSON object exactly as it will be stored, or say it is not one."""
    if not isinstance(obj, dict):
        raise ValueError(STATE_NOT_OBJECT)
    try:
        return json.dumps(obj, sort_keys=True, allow_nan=False,
                          ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError):
        raise ValueError(STATE_NOT_OBJECT) from None


def _shape_of(value: Any) -> dict[str, Any]:
    """Name a value that is too large to carry: what kind it is and exactly how big.

    Guarantees: it reads the value and returns no part of it, so naming something
    can never leak it; the size is the exact byte length the value serialises to,
    or None when it does not serialise at all, which is itself a fact and not a
    silence.
    """
    if isinstance(value, str):
        return {"shape": "str", "bytes": len(value.encode("utf-8"))}
    try:
        size = len(json.dumps(value, sort_keys=True, allow_nan=False,
                              ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        size = None
    shape = {"shape": type(value).__name__, "bytes": size}
    if isinstance(value, (dict, list, tuple)):
        shape["items"] = len(value)
    return shape


def _bounded_field(field: str, value: Any) -> dict[str, Any]:
    """One typed index field verbatim, or its name, shape and size when it is a body.

    Guarantees: what it returns under ``field`` is the value itself, unrounded and
    unshortened, or nothing at all under that name. A number, a boolean and None
    are facts small enough to be labels and ride as they are; a string rides when it
    is within ``MAX_INDEX_FIELD`` bytes. Anything else — a longer string, a nested
    object, a list — is named as ``<field>_not_loaded`` and stays in the body, so
    the size of an index entry is bounded by the schema and not by what a settler,
    a seat or an outside seller decided to put in a field.
    """
    if value is None or isinstance(value, (bool, int, float)):
        return {field: value}
    if isinstance(value, str) and len(value.encode("utf-8")) <= MAX_INDEX_FIELD:
        return {field: value}
    return {f"{field}_not_loaded": _shape_of(value)}


class WorkingState:
    """One head pointer per seat over content-addressed bytes in the archive.

    Guarantees: a refused put changes nothing (the head and the archive are
    both untouched); an accepted put is ledgered before it is
    readable; ``head`` and ``render`` are pure reads; and a head restored from a
    checkpoint names bytes the resume has already verified, so a seat never
    wakes to a state the world cannot show it.
    """

    def __init__(self, artifacts: Any, ledger: Any, clock: Any) -> None:
        self.artifacts = artifacts
        self.ledger = ledger
        self.clock = clock
        # seat -> {"sha", "bytes", "ns", "handle"}
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
        previous = self.heads.get(seat)
        # One head per seat is what is retained (the hard limit bounds the whole of
        # it, not each version): the put releases the superseded head's reference,
        # whose bytes are collected once no durable checkpoint names them, and is
        # measured against the retained private state cap with it gone.
        sha = self.artifacts.put(data, owner=seat, kind=kind,
                                 supersedes=previous["sha"] if previous else None)
        now = self.clock()
        successor = {"sha": sha, "bytes": len(data), "ns": now, "handle": handle}
        self.ledger.append({"kind": "state.put", "assembly_id": seat, "sha": sha,
                            "bytes": len(data), "handle": handle,
                            "over_soft": len(data) > SOFT_STATE_BYTES, "ts": now})
        self.heads[seat] = successor
        return successor

    def render(self, seat: str) -> dict[str, Any] | None:
        """The head as the seat is shown it: ``{sha, bytes, loaded, state}``, verbatim.

        None means this seat has no head. A head whose bytes cannot be read raises:
        an unreadable state is an unavailable fact, never the absence of one — and
        that check runs whatever the size, so a large head is never silently absent.

        Small state rides inline exactly as the seat wrote it. Over
        ``INLINE_STATE_BYTES`` the request carries the reference instead: the sha,
        the true byte size, ``loaded: False``, why, and the tool that returns the
        bytes. Nothing is summarised, shortened or paraphrased — a model's precis of
        a seat's own memory would be a lossy rewrite of a fact the seat owns — and
        nothing about storage, the soft allowance or the hard limit changes.
        """
        record = self.heads.get(seat)
        if record is None:
            return None
        try:
            data = self.artifacts.get(record["sha"])
            state = json.loads(data.decode("utf-8"))
        except Exception as exc:
            raise RuntimeError("working state is present but unavailable") from exc
        view = {"sha": record["sha"], "bytes": len(data), "loaded": True}
        if len(data) > INLINE_STATE_BYTES:
            return {**view, "loaded": False, "state_not_loaded": STATE_NOT_LOADED,
                    "read_with": {"tool": "artifact.get", "args": {"sha": record["sha"]}}}
        return {**view, "state": state}


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
        # seat -> the highest seq through which every earlier item addressed to
        # this seat was actually shown. Global seqs may interleave other seats, so
        # continuity is over this seat's ordered records, not adjacent integers.
        self.delivered_through: dict[str, int] = {}
        # Items fetched past a delivery gap. They become part of
        # ``delivered_through`` only after every earlier item for this seat arrives.
        self.delivered_sparse: dict[str, set[int]] = {}
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
        """Record one shown item and advance only across this seat's delivered prefix."""
        frontier = self.delivered_through.get(seat, 0)
        if seq <= frontier:
            return
        pending = self.delivered_sparse.setdefault(seat, set())
        pending.add(seq)
        for record in self.items.get(seat, ()):
            candidate = record["seq"]
            if candidate <= frontier:
                continue
            if candidate not in pending:
                break
            pending.remove(candidate)
            frontier = candidate
        self.delivered_through[seat] = frontier
        if not pending:
            self.delivered_sparse.pop(seat, None)

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

    def index_of(self, seat: str, record: dict[str, Any]) -> dict[str, Any]:
        """One item as an address rather than a text: exact identity, timing, amounts.

        Guarantees: every field here is copied verbatim off the stored body, none is
        derived, rounded or written by a model, the body stays whole behind ``sha``,
        and the entry is bounded whatever the body contains. It carries what a seat
        needs in order to decide whether to spend a read: the id it must address, the
        decision it answers, when it was observed, the typed outcome (kind, status,
        phase, sender, subject, score, rejection reason and rejected section) where
        the outcome has one, the money, the evidence pointer, and the route to the
        rest.

        A typed field is a label, so only a short scalar is carried: a number, a
        boolean, None, or a string within ``MAX_INDEX_FIELD`` bytes. A long string or
        any nested object or list is named instead, as ``<field>_not_loaded``
        with its shape and exact size, and is read through the same ``read_with``
        route as the rest of the body. Otherwise a nested ``status`` or a
        thousand-character ``subject`` would put the body back on the request under a
        field name, which is the thing this index exists to stop. Nothing is
        truncated or paraphrased on the way: a field is either exact or absent and
        said to be absent, and ``outcome.get`` still returns every one of them whole.
        """
        data = self.artifacts.get(record["sha"])
        body = json.loads(data.decode("utf-8"))
        body = body if isinstance(body, dict) else {}
        outcome = body.get("outcome")
        ident = f"outcome:{record['seq']}"
        entry = {
            "outcome_id": ident,
            "handle": body.get("handle", record["handle"]),
            "observed_at_ns": body.get("observed_at_ns", record.get("observed_at_ns")),
            "delta_micro": body.get("delta_micro", 0),
            "sha": record["sha"],
            "bytes": len(data),
            "read": record["seq"] <= self.cursors.get(seat, 0),
            "read_with": {"tool": "outcome.get", "args": {"outcome_id": ident}},
        }
        if isinstance(outcome, dict):
            for field in INDEX_FIELDS:
                if field in outcome:
                    entry.update(_bounded_field(field, outcome[field]))
        elif outcome is not None:
            # A scalar outcome has no typed fields to name; say what shape it is
            # rather than invent one or hide that it is there.
            entry["outcome_not_loaded"] = _shape_of(outcome)
        evidence = body.get("evidence")
        if isinstance(evidence, str) and len(evidence.encode("utf-8")) <= MAX_INDEX_EVIDENCE:
            entry["evidence"] = evidence
        elif isinstance(evidence, int) and not isinstance(evidence, bool):
            entry["evidence"] = evidence
        elif evidence is not None:
            entry["evidence_not_loaded"] = _shape_of(evidence)
        return entry

    def unread(self, seat: str) -> dict[str, Any]:
        """Index the oldest and newest unread items; bodies stay archived until asked for.

        At or below ``INLINE_OUTCOMES`` every unread item rides unchanged. Above
        it, the window retains the oldest four and newest four in chronological
        order. ``more`` is the total unread count minus the shown count. Paging
        starts after the oldest prefix, so the unshown middle is reachable and a
        recent failure cannot make it skip.

        Each entry is the index above, so no body travels on a request. Delivery is
        bounded: the oldest prefix is delivered, while the newest four are marked
        ``preview: true`` and are discovery only, just like ``outcome.list``. A
        preview becomes a durable sparse delivery only when the seat fetches it with
        ``outcome.get``. Thus repeated requests against an unread growing backlog do
        not grow checkpoint state, and acknowledgement cannot cross the hidden gap.
        """
        cursor = self.cursors.get(seat, 0)
        rows = [r for r in self.items.get(seat, ()) if r["seq"] > cursor]
        preview_seqs: set[int] = set()
        if len(rows) <= INLINE_OUTCOMES:
            window = rows
            oldest_prefix = window
        else:
            oldest_prefix = rows[:INLINE_OUTCOMES // 2]
            newest = rows[-(INLINE_OUTCOMES // 2):]
            preview_seqs = {record["seq"] for record in newest}
            selected = {record["seq"]: record for record in (*oldest_prefix, *newest)}
            window = [selected[seq] for seq in sorted(selected)]
        for record in oldest_prefix:
            self._mark_delivered(seat, record["seq"])
        page_after = oldest_prefix[-1]["seq"] if oldest_prefix else 0
        more = len(rows) - len(window)
        items = []
        for record in window:
            entry = self.index_of(seat, record)
            if record["seq"] in preview_seqs:
                entry["preview"] = True
            items.append(entry)
        return {"count": len(rows), "more": more,
                "items": items,
                "bodies": OUTCOME_BODIES,
                "next_after": page_after if more else None,
                "read_with": {"tool": "outcome.get",
                              "args": {"outcome_id": "<outcome_id from items>"}},
                "paging": {"tool": "outcome.list",
                           "args": {"after": page_after,
                                    "limit": INLINE_OUTCOMES}},
                **({"preview_semantics": OUTCOME_PREVIEWS} if preview_seqs else {})}

    def list(self, seat: str, after: int = 0,
             limit: int = INLINE_OUTCOMES) -> dict[str, Any]:
        """Page this seat's unread ids after ``after``, delivering and acknowledging nothing.

        Guarantees: a seat reads only its own queue; a page is the same index
        ``unread`` shows, so no body travels; and nothing here marks an item
        delivered, which is the point — a seat can discover that item 40 exists
        without that discovery letting it acknowledge the 39 it was never shown. It
        must still fetch what it wants to read, and ``ack_through`` still reaches
        only what it was delivered. ``next_after`` is the cursor for the next page,
        or None at the end of the queue.
        """
        try:
            after = max(0, int(after))
        except (TypeError, ValueError):
            after = 0
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = INLINE_OUTCOMES
        limit = max(1, min(limit, MAX_LIST_LIMIT))
        cursor = self.cursors.get(seat, 0)
        unread = [r for r in self.items.get(seat, ()) if r["seq"] > cursor]
        rows = [r for r in unread if r["seq"] > after]
        page = rows[:limit]
        more = len(rows) - len(page)
        return {"count": len(page), "more": more, "after": after, "limit": limit,
                "unread": len(unread), "bodies": OUTCOME_BODIES,
                "next_after": page[-1]["seq"] if more else None,
                "items": [self.index_of(seat, r) for r in page]}

    def get(self, seat: str, ident: Any, *, delivered: bool = True) -> dict[str, Any]:
        """One item by ``outcome_id``, or by handle as a fallback — the ``outcome.get`` view."""
        record = self._find(seat, ident)
        if record is None:
            return {"error": OUTCOME_UNKNOWN}
        body = self.body(record["sha"])
        if body is None:
            return {"error": OUTCOME_UNKNOWN}
        if delivered:
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
                "delivered_sparse": {seat: sorted(seqs)
                                     for seat, seqs in sorted(self.delivered_sparse.items())},
                "archived_said": dict(sorted(self.archived_said.items()))}

    def restore_delivery(self, state: dict[str, Any]) -> None:
        """Adopt a checkpoint's delivery bookkeeping; a world without one starts empty."""
        self.delivered_through = {k: int(v)
                                  for k, v in (state.get("delivered_through") or {}).items()}
        self.delivered_sparse = {
            seat: {int(seq) for seq in seqs}
            for seat, seqs in (state.get("delivered_sparse") or {}).items()
        }
        self.archived_said = dict(state.get("archived_said") or {})


def collect_window(rt) -> None:
    """The archive collects at each reserve-window boundary (R3-F); no money moves.

    Guarantees: only records whose every reference was released and that no
    checkpoint a resume could start from names are removed, each ledgered by the
    archive, and bytes no record names (a crash's leftover) without an item. An
    owned blob is never a candidate, so this can take nothing a seat holds. Retained
    working state is not charged here or anywhere: it is a constraint with a hard
    limit, not a debit with no counterparty (see the module docstring).
    """
    collect = getattr(rt.artifacts, "collect", None)
    if collect is not None:
        collect()
    retained = getattr(rt.artifacts, "retained", None)
    if retained is not None:
        # The archive's size after collection, so the disk the world keeps is a
        # fact in the diary (what a card may someday price through λ; II.IV.a).
        rt.ledger.append({"kind": "artifact.retained", **retained(),
                          "window": rt.window.index, "ts": rt.clock.now_ns})
