"""What the charter says a judge's forecasts are worth, and nothing else.

Edition 2 trained consequence standing from one kernel predicate,
``return_paid_off``: whatever else a judge forecast settled to its handle and to
the prevalence baseline, it never reached the judge's selection weight. Removing
the metric card that named that outcome did not remove the privilege, because
the privilege was in the settler, not in the charter
(``docs/audits/v6/gpt6/factorylab-deep-rearchitecture-evidence.md`` 9).

Edition 3 removes it. Every registered predicate a judge forecast trains
standing, and the charter's cards are the only thing that says how much each
claim counts: a card's ``answers_for`` names the scope the population holds
accountable, so a claim about a return in a named scope carries that card's
share of the charter's weight, and a charter whose cards name no particular
scope weights every claim equally. Remove the card and its weight goes with it.
"""

from __future__ import annotations

from typing import Any


def scope_weight(charter: Any, scopes: Any = ()) -> float:
    """Return the charter's weight on a claim about a return in these scopes, in [0, 1].

    ``scopes`` names the return this claim is about: its emitted kind and the
    role that kind is measured as (``runtime.cards.accountable_scopes`` builds
    them, which is where the kind vocabulary lives).

    Guarantees the three cases the acceptance test pins:

    * a charter whose cards name no particular scope (no cards at all, or only
      ``all``) weights every claim at 1.0 — equally, as before any card existed;
    * otherwise the weight is the share of the charter's cards that answer for
      this return's scope, counting an ``all`` card as answering for every scope;
    * a claim no card answers for carries no weight, and adding or removing a
      card moves the weight by exactly that card's share and nothing else.
    """
    cards = tuple(getattr(charter, "cards", ()) or ())
    if not cards or all(getattr(c, "answers_for", "all") == "all" for c in cards):
        return 1.0
    scopes = frozenset([scopes] if isinstance(scopes, str) else
                       [s for s in (scopes or ()) if s is not None])
    covering = sum(1 for c in cards
                   if c.answers_for == "all" or c.answers_for in scopes)
    return covering / len(cards)
