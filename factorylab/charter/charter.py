"""Immutable charter editions expose norms and executable measurement contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from factorylab.charter.windows import MetricWindow
from factorylab.cortex.registration import CONTRACT_ROLES, ROLES, event_name


class Norm(str):
    """A norm is its own name and carries the definition the edition ratified.

    Edition 3 moves the substantive clauses out of TOML comments and into the
    charter object. A norm is still exactly its name for every consumer that
    compares, renders or serialises one (a card's ``norm`` field, the world
    block, the roster survey), so a charter that never carried definitions is
    byte-identical wherever it is written back out; the definition rides along
    for :meth:`Charter.render` and anything that asks for it.
    """

    __slots__ = ("definition",)

    def __new__(cls, name: str, definition: str = "") -> Norm:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("a norm needs a nonempty name")
        if not isinstance(definition, str):
            raise ValueError(f"norm {name} definition: must be a string")
        norm = super().__new__(cls, name)
        object.__setattr__(norm, "definition", definition.strip())
        return norm

    @classmethod
    def parse(cls, raw: object) -> Norm:
        """Accept a bare name (historical charters) or an ``{id, definition}`` table."""
        if isinstance(raw, Norm):
            return raw
        if isinstance(raw, str):
            return cls(raw)
        if isinstance(raw, Mapping):
            unknown = set(raw) - {"id", "definition"}
            if unknown:
                raise ValueError(f"norm fields: unknown {sorted(unknown)}")
            if "id" not in raw:
                raise ValueError("norm table needs an id")
            return cls(raw["id"], raw.get("definition", ""))
        raise ValueError("a norm is a name or a table of id and definition")

    @property
    def id(self) -> str:
        """The norm's name, the identity a card's ``norm`` field names."""
        return str(self)

    def as_dict(self) -> dict:
        """The table form: always the id, the definition only when there is one."""
        return {"id": str(self), **({"definition": self.definition} if self.definition else {})}


@dataclass(frozen=True)
class MetricCard:
    """One norm interpretation binds a typed window and an accountable emitted kind."""

    id: str
    norm: str
    description: str
    units: str
    window: MetricWindow
    acceptable_region: str
    observation: str
    answers_for: str

    def __post_init__(self) -> None:
        for name in ("id", "norm", "description", "units", "acceptable_region", "observation"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f"metric card needs {name}")
        object.__setattr__(self, "window", MetricWindow.parse(self.window))
        try:
            scope = event_name(self.answers_for.strip() if isinstance(self.answers_for, str)
                               else self.answers_for)
        except ValueError as exc:
            raise ValueError(f"card {self.id} answers_for: {exc}") from None
        # A role alias is its own exact lower-case spelling, and a seed kind names
        # the alias of the population that emits it. Every other scope keeps the
        # emitted kind's exact name: ``Producer`` is a kind the registry would
        # refuse, never the producer population under a different capitalisation.
        if scope not in (*ROLES, "all"):
            scope = CONTRACT_ROLES.get(scope, scope)
        object.__setattr__(self, "answers_for", scope)

    def validate_answers_for(self, registered_kinds: frozenset[str]) -> None:
        """Admission refuses an accountability scope absent from the public kind catalogue."""
        if self.answers_for not in (*ROLES, "all") and self.answers_for not in registered_kinds:
            raise ValueError(f"card {self.id} answers_for: unregistered emitted kind")


@dataclass(frozen=True)
class Charter:
    """Each edition retains its exact norms and checked cards."""

    edition: int
    norms: tuple[Norm, ...]
    cards: tuple[MetricCard, ...]

    def __post_init__(self) -> None:
        if self.edition < 1:
            raise ValueError("charter edition starts at 1")
        if not self.norms:
            raise ValueError("a charter needs at least one norm")
        # A bare name is the historical form and keeps an empty definition; an
        # ``{id, definition}`` table is edition 3's. Either way a norm is its name.
        norms = tuple(Norm.parse(n) for n in self.norms)
        names = [str(n) for n in norms]
        if len(set(names)) != len(names):
            duplicate = next(name for name in names if names.count(name) > 1)
            raise ValueError(f"norm {duplicate}: charter norms must be unique")
        object.__setattr__(self, "norms", norms)
        ids = [c.id for c in self.cards]
        if len(set(ids)) != len(ids):
            duplicate = next(card_id for card_id in ids if ids.count(card_id) > 1)
            raise ValueError(f"card {duplicate} id: metric card ids must be unique")
        for c in self.cards:
            if c.norm not in self.norms:
                raise ValueError(f"card {c.id} references an unknown norm")

    def render(self, prices: dict[str, float] | None = None, *,
               price_label: str | None = None) -> str:
        """Expose the full charter: the edition, every norm, and every card in order.

        Guarantees each card renders its id, norm, description, units, window, acceptable
        region, observation and accountability scope identically in every case, and that the
        four cases differ in the ``lambda:`` line alone. A norm that carries a definition
        renders it on the line under its name, so the clauses the edition ratified travel with
        the charter rather than living in the manifest's comments. With ``prices``, it is that
        card's price, and ``0.0`` for a card the mapping does not name. With ``price_label``, it
        is that label verbatim, for every card. With both, ``price_label`` wins and ``prices``
        is not read: a rendering that names where the prices are cannot also inline numbers the
        controller moves at every closed window, which is the whole reason the label exists — a
        disclosure that must hold still between calls names ``world.card_prices`` and lets the
        moving numbers travel there. With neither, it is ``unassigned``.
        """
        lines = [f"CHARTER (edition {self.edition})", "", "NORMS"]
        for n in self.norms:
            lines.append(f"- {n.id}")
            if n.definition:
                lines.append(f"  {n.definition}")
        lines += ["", "METRIC CARDS"]
        for c in self.cards:
            lines += [
                f"- {c.id} (norm: {c.norm})",
                f"  {c.description}",
                f"  units: {c.units}; window: {c.window}; acceptable: {c.acceptable_region}",
                f"  observation: {c.observation}; answers_for: {c.answers_for}",
                "  lambda: " + (price_label if price_label is not None else str(
                    prices.get(c.id, 0.0) if prices is not None else "unassigned")),
            ]
        return "\n".join(lines)
