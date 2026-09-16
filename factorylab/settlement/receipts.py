"""Four settlement objects, each independently addressable and each ledgered.

GPT-6 Pro's third reading, §6.A: "Separate execution receipts, learning receipts,
commitments and adjudications as independently addressable objects." Edition 3
had one undifferentiated notion of "an outcome", so a fill, a score, a promise
and a contested interpretation all reached a seat as the same kind of news and
one of them could stand in for another. They are four different things:

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
* An **adjudication** is a contestable interpretation: a value, the measurement
  said to be favourable, the evidence, the finding, and the adjudicator who made
  it. It settles nothing by itself; what it produces is a learning receipt for
  the objector and, when upheld, a proposal the population votes on.

Ids are content addresses over the object's own fields, so the same fact
recorded twice is the same receipt and never two. ``ReceiptBook`` is the ledger
of record: nothing is addressable that was not written down first.

Imports the kernel and the standard library only, like the rest of
``settlement``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator, Mapping
from dataclasses import asdict, dataclass, field, replace

from factorylab.kernel.ledger import Ledger, canonical

#: What an execution receipt may be a receipt for. A seventh kind is a new fact
#: about the world, not a new interpretation of an old one.
EXECUTION_KINDS = (
    "fill",
    "refusal",
    "charge",
    "transfer",
    "program_result",
    "failed_delivery",
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


@dataclass(frozen=True)
class Adjudication(_Receipt):
    """A contestable interpretation: value, measurement, evidence, finding, adjudicator.

    ``adjudicator`` is ``None`` while the adjudication is open — it has been
    queued for someone who did not write the verdict and does not own the
    measurement, and nobody has answered yet. ``upheld`` is ``None`` for the
    same reason: an unanswered objection is an open claim, never a finding.
    """

    PREFIX = "adjud"

    value: str
    measurement: str
    evidence: str
    objector: str  # the seat whose return carried the objection
    objection_handle: str  # its judging decision
    about_handle: str  # the return that decision judged
    uncertainty: float
    adjudicator: str | None = None
    upheld: bool | None = None
    finding: str | None = None
    excluded: tuple[str, ...] = ()  # who may not adjudicate it, and why it was queued

    def __post_init__(self) -> None:
        for name in ("value", "measurement", "evidence", "objector", "objection_handle",
                     "about_handle"):
            object.__setattr__(self, name, _require_text(getattr(self, name), name))
        u = self.uncertainty
        if type(u) not in (int, float) or isinstance(u, bool) or not 0 <= u <= 1:
            raise ValueError("adjudication uncertainty must be in [0, 1]")
        object.__setattr__(self, "uncertainty", float(u))
        object.__setattr__(self, "excluded", tuple(str(e) for e in self.excluded or ()))

    @property
    def id(self) -> str:
        """The claim's identity is the claim, not its resolution.

        An adjudication answered later is the same adjudication: its id is taken
        over the objection's own fields, so the open claim and the resolved one
        are one object with one address.
        """
        claim = {name: getattr(self, name) for name in
                 ("value", "measurement", "evidence", "objector", "objection_handle",
                  "about_handle", "uncertainty")}
        return receipt_id(self.PREFIX, claim)

    @property
    def confidence(self) -> float:
        """The probability the objector attached to its own claim."""
        return 1.0 - self.uncertainty

    def resolved(self, *, adjudicator: str, upheld: bool, finding: str) -> Adjudication:
        """The same claim with an independent finding on it."""
        return replace(self, adjudicator=_require_text(adjudicator, "adjudicator"),
                       upheld=bool(upheld), finding=_require_text(finding, "finding"))


#: The four kinds, named once, in the order §6.A names them. A tuple rather than
#: a table: ``settlement`` holds no mutable module state
#: (``tests/settlement/test_settlement_boundaries.py``).
RECEIPT_TYPES = (
    ("execution", ExecutionReceipt),
    ("learning", LearningReceipt),
    ("commitment", Commitment),
    ("adjudication", Adjudication),
)


def _kind_of(receipt: object) -> str | None:
    return next((name for name, cls in RECEIPT_TYPES if type(receipt) is cls), None)


def _class_of(kind: str):
    return next((cls for name, cls in RECEIPT_TYPES if name == kind), None)


class ReceiptBook:
    """Every settlement object is written down before it is addressable, and once.

    Recording the same object twice is the same row and the same id: the id is a
    content address, so idempotence is a property of the object rather than of
    the caller's care. An adjudication is the one object that changes — from an
    open claim to a resolved one — and it keeps its id when it does, so the
    finding lands on the claim rather than beside it.
    """

    def __init__(self, ledger: Ledger) -> None:
        self.__ledger = ledger
        self.__by_id: dict[str, _Receipt] = {}

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
        return identity

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
    "Adjudication",
    "Commitment",
    "ExecutionReceipt",
    "LearningReceipt",
    "ReceiptBook",
    "execution_receipt",
    "learning_receipt",
    "receipt_id",
)
