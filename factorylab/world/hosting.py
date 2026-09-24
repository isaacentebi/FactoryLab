"""The hosting pot: prepaid DigitalOcean credit, observed, booked only by what left it.

The factory runs on a host whose prepaid credit is part of the one first move
(essay II.II, the Stackelberg move; II.IV, "a continuous, reciprocal flow of
capital is an objective requirement"). DigitalOcean credit pays for hosting and
for nothing else, so it is its own pot: a hosting charge lowers this pot and no
other, and never the model wallet. Its balance is what DigitalOcean reports; its
burn is what DigitalOcean's own billing shows it took. Nothing here computes a
charge from a price, a clock or a formula.

What DigitalOcean reports (world/digitalocean.py): ``account_balance`` (A, the
balance as of the last invoice or payment; negative is credit),
``month_to_date_usage`` (U, what the current cycle has used) and their sum. The
books read them so that each dollar DigitalOcean took is booked exactly once:

* **Usage above the cycle's high-water mark is burn.** A fall in U within a cycle
  (a revision) books nothing, and a rise back to the mark books nothing again;
  only increments above the highest U seen this cycle are burn.
* **A genuine new cycle** is a reading whose ``generated_at`` is in a later month
  than the cycle's and whose U fell below the previous reading's U: the counter
  itself reset. A later month whose counter still shows the old figure (an invoice
  that landed first, a revised month) is still the old cycle, so a rollover that
  lands in either order is read the same way. A revision of the closed month's
  figure after the month ended and before either step would read as the reset;
  what it books early is returned as the new month's usage re-accrues past it.
* **A rise in A is an invoice.** It settles the oldest closed cycle's booked usage
  first: what it covers was burn already; what it charges beyond that (usage
  after the last reading, tax) is burn; what it falls short by is credit applied
  at invoice, and that cycle is closed. With no closed cycle waiting, it settles
  the current cycle's booked usage (an invoice that landed before the reset).
* **A fall in A is credit from outside** (a prepayment, a refund): the pot rises
  and the books say so. It is never burn, and it never reduces a later burn.

One invoice per cycle is assumed. A charge and a payment landing between the same
two readings net: the readings cannot tell them apart.

A reading is booked only from the bound account while it is dedicated to this
droplet (``dedicated``, checked with every reading). After any refused reading,
what accrued between the last booked reading and the next accepted one is
**unattributable**: the account paid for something else, or could not be seen to
be this world's, somewhere in that gap. It is ledgered and counted apart from
burn, never as burn.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

COUNTERPARTY = "digitalocean"


class HostingRefused(RuntimeError):
    """The host is not the dedicated, verified account this world is bound to.

    ``reason`` is one of the named codes below; a world that cannot establish or
    keep its binding does not start, and does not book.
    """

    NOT_ON_DROPLET = "not running on a DigitalOcean droplet"
    DROPLET_MISMATCH = "metadata droplet id differs from [hosting] droplet_id"
    DROPLET_NOT_HELD = "the billing account does not hold [hosting] droplet_id"
    NOT_DEDICATED = "the billing account holds resources besides this droplet"
    ACCOUNT_MISMATCH = "the billing account differs from the one this world is bound to"
    UNVERIFIED = "the billing account could not be read"

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        super().__init__(f"[hosting] refused: {reason}" + (f" ({detail})" if detail else ""))


def _stamp(generated_at: str) -> datetime:
    return datetime.fromisoformat(generated_at.replace("Z", "+00:00"))


def _month(generated_at: str) -> str:
    return _stamp(generated_at).strftime("%Y-%m")


def dedicated(resources: dict[str, Any], droplet_id: int) -> bool:
    """Whether the account pays for this droplet and nothing else DigitalOcean lists."""
    return (resources.get("droplets") == [droplet_id] and resources.get("volumes") == 0
            and resources.get("snapshots") == 0)


def verify(client: Any, droplet_id: int) -> dict[str, Any]:
    """Establish that this process runs on a dedicated droplet; return the account identity.

    Guarantees, or raises ``HostingRefused`` with a named reason: the metadata
    service (reachable only from inside a droplet) names ``droplet_id``; the
    token's billing account holds that droplet; and the account holds no other
    droplet, volume or snapshot, so its whole bill is this world's host.
    """
    try:
        seen = client.metadata_droplet_id()
    except Exception as exc:  # noqa: BLE001 - off a droplet the service is unreachable
        raise HostingRefused(HostingRefused.NOT_ON_DROPLET, type(exc).__name__) from None
    if seen != droplet_id:
        raise HostingRefused(HostingRefused.DROPLET_MISMATCH, f"metadata says {seen}")
    try:
        identity = client.identity(droplet_id)
        resources = client.resources()
    except Exception as exc:  # noqa: BLE001
        raise HostingRefused(HostingRefused.UNVERIFIED, type(exc).__name__) from None
    if not identity["droplet_held"]:
        raise HostingRefused(HostingRefused.DROPLET_NOT_HELD)
    if not dedicated(resources, droplet_id):
        raise HostingRefused(HostingRefused.NOT_DEDICATED,
                             f"droplets={len(resources['droplets'])}, "
                             f"volumes={resources['volumes']}, "
                             f"snapshots={resources['snapshots']}")
    return {k: identity[k] for k in ("billing_uuid", "billing_kind", "droplet_id")}


class HostingAccount:
    """One dedicated DigitalOcean account and the books kept on its reports.

    Guarantees ``state`` restores every figure a later reading is measured
    against, and that ``restore`` refuses a checkpoint bound to another account.
    ``identity`` is the account this process verified at start; ``client`` is the
    journaled adapter, so a replay answers from the record.
    """

    FIELDS = ("bound", "last", "cycle", "mark", "invoiced", "pending", "endowment_micro",
              "burned_micro", "credited_micro", "last_burn_micro", "unread",
              "unattributed_micro", "rebase")

    def __init__(self, client: Any, *, droplet_id: int, identity: dict[str, Any]) -> None:
        if type(droplet_id) is not int or droplet_id <= 0:
            raise ValueError("hosting droplet_id must be a positive integer")
        self.client = client
        self.droplet_id = droplet_id
        # Bound at launch; a checkpoint carries it and a resume must match it.
        self.bound: dict[str, Any] = dict(identity)
        self.last: dict[str, Any] | None = None
        self.cycle: str | None = None       # the open cycle's month, "YYYY-MM"
        self.mark = 0                        # the highest usage seen in the open cycle
        self.invoiced = 0                    # the open cycle's usage an invoice settled
        self.pending: list[list] = []        # closed cycles' booked usage: [month, micro]
        self.endowment_micro: int | None = None
        self.burned_micro = 0
        self.credited_micro = 0
        self.last_burn_micro = 0
        self.unread: str | None = None       # why the last reading was not booked
        # A reading was refused since the last booked one: the next accepted reading's
        # burn is unattributable, and is counted here instead of as burn.
        self.rebase = False
        self.unattributed_micro = 0

    # ---- state

    def state(self) -> dict[str, Any]:
        """Everything a resume needs; never the client or its token."""
        return deepcopy({name: getattr(self, name) for name in self.FIELDS})

    def restore(self, saved: dict[str, Any]) -> None:
        """Rebind saved books, refusing a checkpoint bound to another account."""
        if saved.get("bound") != self.bound:
            raise HostingRefused(HostingRefused.ACCOUNT_MISMATCH, "checkpoint")
        for name in self.FIELDS:
            if name in saved:
                setattr(self, name, deepcopy(saved[name]))

    # ---- the pot

    def credit_micro(self) -> int | None:
        """Credit remaining as DigitalOcean last reported it, or None while unread."""
        if self.last is None or self.unread is not None:
            return None
        return self.last["credit_micro"]

    def books_micro(self) -> int | None:
        """What the books say the pot holds: endowment, plus credit, less burn."""
        if self.endowment_micro is None:
            return None
        return (self.endowment_micro + self.credited_micro - self.burned_micro
                - self.unattributed_micro)

    def view(self) -> dict[str, Any]:
        """The pot's public facts, and its reconciliation against its own counterparty."""
        reported, books = self.credit_micro(), self.books_micro()
        last = self.last or {}
        return {
            "custodian": COUNTERPARTY, "droplet_id": self.droplet_id,
            "credit_micro": reported, "books_micro": books,
            "discrepancy_micro": None if reported is None or books is None
            else reported - books,
            "month_to_date_usage_micro": last.get("month_to_date_usage_micro"),
            "generated_at": last.get("generated_at"),
            "endowment_micro": self.endowment_micro, "burned_micro": self.burned_micro,
            "credited_micro": self.credited_micro, "last_burn_micro": self.last_burn_micro,
            "unattributed_micro": self.unattributed_micro,
            "unread": self.unread,
        }

    def observe(self, read: dict[str, Any]) -> dict[str, Any]:
        """Book one reading against the last; guarantees each dollar taken is booked once.

        Returns the effect: ``endowment`` (the first reading), ``stale`` (older than
        the last, nothing changes), or ``observed`` with ``burn_micro`` and the
        credits, by cause, that this reading showed.
        """
        self.unread = None
        previous = self.last
        usage, account = read["month_to_date_usage_micro"], read["account_balance_micro"]
        if previous is None:
            self.last = dict(read)
            self.cycle, self.mark, self.invoiced = _month(read["generated_at"]), usage, 0
            self.endowment_micro = read["credit_micro"]
            return {"effect": "endowment", "micro": read["credit_micro"]}
        if _stamp(read["generated_at"]) < _stamp(previous["generated_at"]):
            return {"effect": "stale"}
        burn, invoice_credit, outside_credit, rolled = 0, 0, 0, False
        reset = usage < previous["month_to_date_usage_micro"]
        if _month(read["generated_at"]) > self.cycle and reset:
            # The counter itself went down in a later month: the reset has been seen,
            # and the cycle closed. Measured against the last reading, never the mark:
            # a month revised below its mark and invoiced before its reset still shows
            # its own, lower, stale figure, and that is not the new month's usage
            # (Codex on #147). What of the closed cycle's booked usage no invoice has
            # settled yet waits for one; an invoice that already landed and fell short
            # applied credit.
            rolled = True
            unsettled = self.mark - self.invoiced
            if self.invoiced and unsettled:
                invoice_credit += unsettled
            elif unsettled:
                self.pending.append([self.cycle, unsettled])
            self.cycle, self.mark, self.invoiced = _month(read["generated_at"]), 0, 0
        if usage > self.mark:
            burn += usage - self.mark
            self.mark = usage
        delta = account - previous["account_balance_micro"]
        if delta > 0:
            if self.pending:
                _, owed = self.pending.pop(0)
                settled = min(delta, owed)
                invoice_credit += owed - settled
            else:
                settled = min(delta, self.mark - self.invoiced)
                self.invoiced += settled
            burn += delta - settled
        elif delta < 0:
            outside_credit += -delta
        self.last = dict(read)
        unattributed = burn if self.rebase else 0
        burn -= unattributed
        self.rebase = False
        self.burned_micro += burn
        self.unattributed_micro += unattributed
        self.credited_micro += invoice_credit + outside_credit
        self.last_burn_micro = burn
        return {"effect": "observed", "burn_micro": burn, "unattributed_micro": unattributed,
                "invoice_credit_micro": invoice_credit,
                "outside_credit_micro": outside_credit, "rolled_over": rolled,
                "generated_at": read["generated_at"],
                "previous_generated_at": previous["generated_at"],
                "month_to_date_usage_micro": [previous["month_to_date_usage_micro"], usage],
                "account_balance_micro": [previous["account_balance_micro"], account]}
