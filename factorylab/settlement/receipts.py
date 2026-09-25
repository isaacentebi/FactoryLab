"""Four settlement objects, each independently addressable and each ledgered.

GPT-6 Pro's third reading, §6.A: "Separate execution receipts, learning receipts,
commitments ... as independently addressable objects." Edition 3
had one undifferentiated notion of "an outcome", so a fill, a score, a promise
and a contested interpretation all reached a seat as the same kind of news and
one of them could stand in for another. They are different things:

* An **execution receipt** is a fact about the world: a fill, a refusal, a
  charge, a transfer, a program result, a failed delivery. It is not an
  assessment and carries no score.
* A **learning receipt** is one assessment of one decision: the decision it
  addresses, the scoring rule and its version, the observation horizon, the
  outcome observed, the score, and the sampling record if the assessment was
  sampled. Its score may be ``None`` with a reason — an assessment that could
  not be made is not a zero.
* A **commitment** is a promise: what was promised, which principal is
  responsible, by when, the rule by which it will be observed, and the
  conditions under which it is unobservable through nobody's fault.

The fourth object the reading named, an adjudication of a fidelity objection,
was a designed procedure no passage of Chapter II calls for (evaluations U1): the
answer to overfitting is realized consequence and adversarial populations
(II.III.b), not an adjudication protocol. It is deleted.

Ids are content addresses over the object's own fields, so the same fact
recorded twice is the same receipt and never two. ``ReceiptBook`` is the ledger
of record: nothing is addressable that was not written down first.

Imports the kernel and the standard library only, like the rest of
``settlement``.
"""

from __future__ import annotations

import hashlib
from bisect import bisect_left
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import asdict, dataclass, field

from factorylab.kernel.ledger import Ledger, canonical

#: What an execution receipt may be a receipt for. A further kind is a new fact
#: about the world, not a new interpretation of an old one.
EXECUTION_KINDS = (
    "fill",
    "refusal",
    "charge",
    "transfer",
    "program_result",
    "failed_delivery",
    # An event market's resolution: what an outcome token redeemed at, and what
    # that realised for the decision holding it.
    "resolution",
)


def _require_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")
    return value.strip()


def _plain(value: object) -> object:
    """A detached, JSON-ready copy: a receipt never holds a caller's mutable table."""
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    return value


@dataclass(frozen=True)
class _Receipt:
    """The shared half of all four: a content-addressed id over the object's fields."""

    @property
    def id(self) -> str:
        return receipt_id(self.PREFIX, self.as_dict())

    def as_dict(self) -> dict:
        """The object's fields, exactly as they are ledgered."""
        return asdict(self)


def receipt_id(prefix: str, payload: Mapping) -> str:
    """Name one object by its own content, so recording it twice names it once."""
    digest = hashlib.sha256(canonical(dict(payload))).hexdigest()
    return f"{prefix}-{digest[:32]}"


@dataclass(frozen=True)
class ExecutionReceipt(_Receipt):
    """One fact the world produced, addressed to the decision that caused it."""

    PREFIX = "exec"

    kind: str
    handle: str  # the decision this happened under
    owner: str | None  # the seat it belongs to, when the runtime knows one
    at_event: int  # settlement speaks in event indices; the ledger row carries the clock
    facts: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in EXECUTION_KINDS:
            raise ValueError(f"execution receipt kind must be one of {list(EXECUTION_KINDS)}")
        _require_text(self.handle, "handle")
        if type(self.at_event) is not int or self.at_event < 0:
            raise ValueError("at_event must be a nonnegative event index")
        object.__setattr__(self, "facts", _plain(self.facts or {}))


@dataclass(frozen=True)
class LearningReceipt(_Receipt):
    """One assessment of one decision, under a named scoring rule and horizon.

    ``score`` is ``None`` when the assessment could not be made; ``reason`` then
    says why, and the receipt is the record that nothing was learned here. An
    absent score is never a zero.
    """

    PREFIX = "learn"

    handle: str  # the decision handle this assessment addresses
    assessed: str  # the seat or evaluator whose decision it is
    scoring_rule: str
    rule_version: str
    horizon: int | None
    outcome: float | int | None
    score: float | None
    baseline: float | None = None
    sampling_ref: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.handle, "handle")
        _require_text(self.assessed, "assessed")
        _require_text(self.scoring_rule, "scoring_rule")
        _require_text(self.rule_version, "rule_version")
        if self.horizon is not None and (type(self.horizon) is not int or self.horizon < 0):
            raise ValueError("horizon must be a nonnegative event count or None")
        if self.score is None and not self.reason:
            raise ValueError("an unscored assessment must say why it is unscored")


@dataclass(frozen=True)
class Commitment(_Receipt):
    """A promise with a responsible principal, a deadline and an observation rule.

    ``unobservable_when`` names the conditions under which the world may simply
    fail to answer: a commitment nobody can observe through nobody's fault is
    not a broken one (edition 3, C3).
    """

    PREFIX = "commit"

    promise: str
    principal: str
    deadline_event: int | None
    observation_rule: str
    unobservable_when: tuple[str, ...] = ()
    handle: str | None = None  # the decision that made the promise, when there is one

    def __post_init__(self) -> None:
        _require_text(self.promise, "promise")
        _require_text(self.principal, "principal")
        _require_text(self.observation_rule, "observation_rule")
        if self.deadline_event is not None and (
            type(self.deadline_event) is not int or self.deadline_event < 0
        ):
            raise ValueError("deadline_event must be a nonnegative event index or None")
        conditions = self.unobservable_when
        if isinstance(conditions, str):
            conditions = (conditions,)
        object.__setattr__(self, "unobservable_when", tuple(str(c) for c in conditions or ()))


#: The kinds, named once, in the order §6.A names them. A tuple rather than
#: a table: ``settlement`` holds no mutable module state
#: (``tests/settlement/test_settlement_boundaries.py``).
RECEIPT_TYPES = (
    ("execution", ExecutionReceipt),
    ("learning", LearningReceipt),
    ("commitment", Commitment),
)


def _kind_of(receipt: object) -> str | None:
    return next((name for name, cls in RECEIPT_TYPES if type(receipt) is cls), None)


def _class_of(kind: str):
    return next((cls for name, cls in RECEIPT_TYPES if name == kind), None)


class ReceiptBook:
    """Every settlement object is written down before it is addressable, and once.

    Recording the same object twice is the same row and the same id: the id is a
    content address, so idempotence is a property of the object rather than of
    the caller's care.
    """

    def __init__(self, ledger: Ledger) -> None:
        self.__ledger = ledger
        self.__by_id: dict[str, _Receipt] = {}
        # How many execution receipts were ever recorded (the global cursor), and how
        # many of them were released with their decisions (wave 17b): the cursor is
        # the released ones plus the ones still held.
        self.__executions = 0
        self.__released_executions = 0
        self.__execution_by_handle: dict[str, list[tuple[int, str]]] = {}

    def _index_execution(self, identity: str, receipt: _Receipt) -> None:
        """Append one execution receipt to the derived global and per-handle indexes."""
        if not isinstance(receipt, ExecutionReceipt):
            return
        ordinal = self.__executions
        self.__executions += 1
        self.__execution_by_handle.setdefault(receipt.handle, []).append((ordinal, identity))

    def restore(self, receipts: Iterable[_Receipt], *, released_executions: int = 0) -> None:
        """Restore record order and rebuild derived execution indexes in one pass.

        ``released_executions`` is how many execution receipts were released before
        the checkpoint (``released_executions()``); an older checkpoint released none.
        Guarantees ``execution_count`` equals the recording book's, so every receipt
        recorded after the checkpoint takes the position it took there, and a cursor
        taken at the checkpoint or later reads exactly what it read there. Held
        receipts are numbered after the released ones, in record order.
        """
        self.__by_id = {receipt.id: receipt for receipt in receipts}
        self.__released_executions = released_executions
        self.__executions = released_executions
        self.__execution_by_handle = {}
        for identity, receipt in self.__by_id.items():
            self._index_execution(identity, receipt)

    def release(self, handles) -> int:
        """Forget every receipt about the released ``handles``; return how many.

        Wave 17b (essay II.IV.c: a verdict is "consumed ... and then discarded"):
        a receipt is addressable evidence about a decision, and a released decision
        is owed nothing more. Guarantees ``execution_count`` is unchanged and that
        no receipt about any other handle moves. Its ledger rows stay the record.
        """
        gone = set(handles)
        drop = [i for i, r in self.__by_id.items() if getattr(r, "handle", None) in gone]
        for identity in drop:
            receipt = self.__by_id.pop(identity)
            if isinstance(receipt, ExecutionReceipt):
                self.__released_executions += 1
        for handle in gone:
            self.__execution_by_handle.pop(handle, None)
        return len(drop)

    def released_executions(self) -> int:
        """How many execution receipts this book released (``release``)."""
        return self.__released_executions

    def record(self, receipt: _Receipt) -> str:
        """Ledger one object and return its id; an identical re-record writes nothing."""
        kind = _kind_of(receipt)
        if kind is None:
            raise TypeError("only settlement receipt objects may be recorded")
        identity = receipt.id
        existing = self.__by_id.get(identity)
        if existing == receipt:
            return identity
        # The object's own fields sit under ``receipt`` rather than beside the
        # row's: an execution receipt has a ``kind`` of its own, and the row must
        # not decide which of the two a reader is looking at.
        self.__ledger.append({"kind": f"receipt.{kind}", "id": identity,
                              "receipt": receipt.as_dict()})
        self.__by_id[identity] = receipt
        self._index_execution(identity, receipt)
        return identity

    def execution_count(self) -> int:
        """Return the global execution-receipt cursor in constant time."""
        return self.__executions

    def executions_since(self, handle: str, cursor: int) -> list[ExecutionReceipt]:
        """Return this handle's execution receipts at or after one global cursor."""
        if not isinstance(handle, str) or not handle:
            raise ValueError("handle is required")
        if type(cursor) is not int or not 0 <= cursor <= self.__executions:
            raise ValueError("execution cursor is outside this receipt book")
        rows = self.__execution_by_handle.get(handle, ())
        start = bisect_left(rows, (cursor, ""))
        return [self.__by_id[identity] for _, identity in rows[start:]]

    def get(self, identity: str) -> _Receipt | None:
        """The object with this id, or None: an id nobody issued describes nothing."""
        return self.__by_id.get(identity)

    def ids(self, kind: str | None = None) -> list[str]:
        """Issued ids in the order they were recorded, optionally of one kind only."""
        cls = _class_of(kind) if kind is not None else None
        if kind is not None and cls is None:
            raise ValueError(f"receipt kind must be one of {[n for n, _ in RECEIPT_TYPES]}")
        return [i for i, r in self.__by_id.items() if cls is None or isinstance(r, cls)]

    def all(self, kind: str | None = None) -> list[_Receipt]:
        """The recorded objects themselves, in record order."""
        return [self.__by_id[i] for i in self.ids(kind)]

    def __iter__(self) -> Iterator[_Receipt]:
        return iter(self.__by_id.values())

    def __len__(self) -> int:
        return len(self.__by_id)


def execution_receipt(book: ReceiptBook | None, **kwargs) -> str | None:
    """Record one execution receipt where a book exists; a bookless caller is not an error."""
    if book is None:
        return None
    return book.record(ExecutionReceipt(**kwargs))


def learning_receipt(book: ReceiptBook | None, **kwargs) -> str | None:
    """Record one learning receipt where a book exists."""
    if book is None:
        return None
    return book.record(LearningReceipt(**kwargs))


__all__ = (
    "EXECUTION_KINDS",
    "RECEIPT_TYPES",
    "Commitment",
    "ExecutionReceipt",
    "LearningReceipt",
    "ReceiptBook",
    "execution_receipt",
    "learning_receipt",
    "receipt_id",
)
