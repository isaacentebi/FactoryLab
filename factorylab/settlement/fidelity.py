"""The fidelity objection: a structured, scored, contestable claim, never a kernel verdict.

The fifth norm says a measurement is defeasible evidence of a value and not a
substitute for it. Edition 3 gives that norm a shape a judge can actually use
and the rest of the population can actually answer: a verdict may carry a
``fidelity_objection`` naming the value it says is not served, the measurement it
says is nevertheless favourable, the evidence, and the judge's own uncertainty.

Four properties, in the architect's words (``architect-review.md`` 5.5) and the
edition 3 plan:

* **Validated.** A malformed objection is refused with a reason; it cannot be a
  gesture. The value must be one of the charter's norms and the measurement must
  name something the charter actually measures — a live card, or an observation
  in the factory's vocabulary — so the claim is about a real proxy.
* **Ledgered.** Every accepted objection is written to the diary with the
  judge, the return it judged, and its four fields, before anything scores it.
* **Scored like any verdict.** Its stated confidence (``1 - uncertainty``) is
  scored against the same realised blame the verdict itself is scored against,
  by the same proper score, into the same judge standing. A judge that objects
  loudly to returns the charter does not blame loses standing for it.
It lives beside the settler rather than in ``charter`` because the settler is
what scores it and ``settlement`` imports only the kernel and itself; the
charter it validates against is passed in, never imported.

* **Never a kernel verdict on its own.** It settles no decision, moves no money,
  and blames no card. It is contestable the way any measurement claim is: the
  card it names can be challenged through the existing challenge route, which
  trials a replacement measurement side by side before a committee.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass

MAX_EVIDENCE_CHARS = 2000

OBJECTION_FIELDS = ("value", "measurement", "evidence", "uncertainty")


def objection_schema() -> dict:
    """A fresh JSON schema fragment for the field an evaluator answer may carry."""
    return {
        "type": "object",
        "description": (
            "Optional. A measurement is favourable and the value it stands for is not served. "
            "Name the value (a charter norm), the measurement (a card id or observation), the "
            "evidence, and your uncertainty that the objection is right. It remains an "
            "open, contestable claim pending independent evidence; the challenged proxy "
            "does not score its own fidelity."
        ),
        "properties": {
            "value": {"type": "string"},
            "measurement": {"type": "string"},
            "evidence": {"type": "string"},
            "uncertainty": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": list(OBJECTION_FIELDS),
    }


@dataclass(frozen=True)
class FidelityObjection:
    """One judge's claim that a favourable measurement does not serve its value."""

    value: str
    measurement: str
    evidence: str
    uncertainty: float

    def __post_init__(self) -> None:
        for name in ("value", "measurement", "evidence"):
            field = getattr(self, name)
            if not isinstance(field, str) or not field.strip():
                raise ValueError(f"fidelity objection needs {name}")
            object.__setattr__(self, name, field.strip()[:MAX_EVIDENCE_CHARS])
        u = self.uncertainty
        if type(u) not in (int, float) or isinstance(u, bool) or not 0 <= u <= 1:
            raise ValueError("fidelity objection uncertainty must be in [0, 1]")
        object.__setattr__(self, "uncertainty", float(u))

    @property
    def confidence(self) -> float:
        """The probability the judge attaches to its own objection."""
        return 1.0 - self.uncertainty

    def as_dict(self) -> dict:
        """The four fields, as they are ledgered."""
        return asdict(self)


def parse_objection(raw: object, *, charter=None, measurements=frozenset()) -> FidelityObjection:
    """Validate one answer's ``fidelity_objection``, or raise with the reason it is refused.

    With a charter, the value must be one of its norms; with either a charter or
    a ``measurements`` set, the measurement must be a live card id or a named
    observation. Without them the four fields are still checked, so a caller
    that has no charter to hand cannot admit a shapeless objection.
    """
    if not isinstance(raw, Mapping):
        raise ValueError("fidelity objection must be a table of value, measurement, "
                         "evidence and uncertainty")
    unknown = set(raw) - set(OBJECTION_FIELDS)
    if unknown:
        raise ValueError(f"fidelity objection fields: unknown {sorted(unknown)}")
    missing = set(OBJECTION_FIELDS) - set(raw)
    if missing:
        raise ValueError(f"fidelity objection fields: missing {sorted(missing)}")
    objection = FidelityObjection(raw["value"], raw["measurement"], raw["evidence"],
                                  raw["uncertainty"])
    known = set(measurements)
    if charter is not None:
        norms = [str(n) for n in charter.norms]
        if objection.value not in norms:
            raise ValueError(f"fidelity objection value: {objection.value!r} is not a charter norm")
        known |= {c.id for c in charter.cards} | {c.observation for c in charter.cards}
    if known and objection.measurement not in known:
        raise ValueError("fidelity objection measurement: name a live card or a known "
                         "observation, so the claim can be challenged")
    return objection
