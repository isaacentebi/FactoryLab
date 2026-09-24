"""The hosting pot: what DigitalOcean bills for this droplet, booked line by line.

The factory runs on a host whose prepaid credit is part of the one first move
(essay II.II, the Stackelberg move; II.IV, "a continuous, reciprocal flow of
capital is an objective requirement"). DigitalOcean credit pays for hosting and
nothing else, so hosting is its own pot, and a hosting charge never moves the
model wallet.

Nothing here infers a charge from a balance, a price or a clock. Burn is exactly
the lines DigitalOcean's own billing puts against this droplet (``resource_id``
equal to its id): the finalized invoice's lines for a closed month, the invoice
preview's lines for the month in progress. The books hold, for every month and
every line, the amount last booked; a reading books the difference between what
DigitalOcean now shows for a line and what is booked for it, up or down. Booking
is a level, not a flow: reading the same figures again, or replaying them after a
resume, books nothing, and a month's booked burn is always DigitalOcean's latest
figure for this droplet in that month. When a month's invoice posts it replaces
the preview's figures for that month, and is read once.

Everything else on the account (other resources, and every payment, credit,
refund or adjustment, which DigitalOcean's billing history records without naming
a resource) is not this world's: it is ledgered as a fact about the account, and
never booked. So the part of the account's credit that is available to this
droplet is not something DigitalOcean reports, and the pot's balance is published
as unknown rather than invented.

Burn counts from the world's launch. In the launch month, what the droplet accrued
before the launch is not this world's: it is each of this droplet's lines' amount
shared by the line's own span, and only a reading whose line reaches past the launch
can say it. Until one does, the launch month books nothing and says so.

Tax is on DigitalOcean's invoices, not on their lines, so this droplet's burn
excludes it: on a taxed account the credit drains faster than the burn shows.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

COUNTERPARTY = "digitalocean"
BALANCE_UNKNOWN = ("DigitalOcean credit is account-wide and its billing history names no "
                   "resource, so the part available to this droplet is not reported")


class HostingRefused(RuntimeError):
    """The host is not the verified account and droplet this world is bound to.

    ``reason`` is one of the named codes below: a launch that cannot establish the
    binding does not start, and a reading that does not match it books nothing.
    """

    NOT_ON_DROPLET = "not running on a DigitalOcean droplet"
    DROPLET_MISMATCH = "metadata droplet id differs from [hosting] droplet_id"
    DROPLET_NOT_HELD = "the billing account does not hold [hosting] droplet_id"
    ACCOUNT_MISMATCH = "the billing account differs from the one this world is bound to"
    UNVERIFIED = "the billing account could not be read"
    NO_LOOKUP = "no bounded name lookup on this host (getent)"

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        super().__init__(f"[hosting] refused: {reason}" + (f" ({detail})" if detail else ""))


def verify(client: Any, droplet_id: int, budget_s: float) -> dict[str, Any]:
    """Establish that this process runs on ``droplet_id`` and return the account to bind.

    Guarantees, or raises ``HostingRefused`` with a named reason: the metadata
    service (reachable only from inside a droplet) names ``droplet_id``, and the
    token's billing account holds that droplet. The account may hold anything
    else: only this droplet's invoice lines are ever booked.
    """
    try:
        seen = client.metadata_droplet_id(budget_s)
    except Exception as exc:  # noqa: BLE001 - off a droplet the service is unreachable
        raise HostingRefused(HostingRefused.NOT_ON_DROPLET, type(exc).__name__) from None
    if seen != droplet_id:
        raise HostingRefused(HostingRefused.DROPLET_MISMATCH, f"metadata says {seen}")
    try:
        identity = client.identity(droplet_id, budget_s)
    except Exception as exc:  # noqa: BLE001
        raise HostingRefused(HostingRefused.UNVERIFIED, type(exc).__name__) from None
    if not identity["droplet_held"]:
        raise HostingRefused(HostingRefused.DROPLET_NOT_HELD)
    return bound_of(identity)


def bound_of(identity: dict[str, Any]) -> dict[str, Any]:
    return {k: identity[k] for k in ("billing_uuid", "billing_kind", "droplet_id")}


def refusal(bound: dict[str, Any], identity: dict[str, Any]) -> str | None:
    """Why a reading with this identity may not be booked for ``bound``, or None."""
    if bound_of(identity) != bound:
        return HostingRefused.ACCOUNT_MISMATCH
    if identity["metadata_id"] != bound["droplet_id"]:
        return HostingRefused.DROPLET_MISMATCH
    if not identity["droplet_held"]:
        return HostingRefused.DROPLET_NOT_HELD
    return None


class HostingAccount:
    """One droplet's billing, as DigitalOcean reports it, and the books kept on it.

    Guarantees ``state`` restores every figure a later reading is measured against,
    so a resume books from where the checkpoint was. ``bound`` is the account this
    world was bound to at launch and ``launch_ns`` the world's launch time; a resume
    takes both from the checkpoint.

    The books hold, per month, a level per line: preview lines under ``p:`` keys,
    each closed invoice's lines under its uuid. A month's level is the sum of its
    invoices' lines once one of them has this droplet's lines (a month can have
    several invoices), else its preview's. Its booked burn is the level less the
    launch month's pre-launch share, never below zero; each reading books, per
    month, the change in that figure, so re-reading or replaying books nothing.
    """

    FIELDS = ("bound", "launch_ns", "lines", "baseline", "reconciled", "invoiced",
              "unmatched", "booked", "negative", "unread", "history", "snapshot",
              "pending_baseline", "pending_noted", "droplet_uuid", "spans",
              "estimate_final", "launch_price", "cursor", "held")

    def __init__(self, client: Any, *, droplet_id: int, bound: dict[str, Any] | None,
                 launch_ns: int | None, budget_s: float) -> None:
        if type(droplet_id) is not int or droplet_id <= 0:
            raise ValueError("hosting droplet_id must be a positive integer")
        self.client = client
        self.droplet_id = droplet_id
        self.budget_s = budget_s
        self.bound = None if bound is None else dict(bound)
        self.launch_ns = launch_ns
        self.lines: dict[str, dict[str, int]] = {}     # month -> line key -> level micro
        self.baseline: dict[str, dict] = {}            # launch month -> pre-launch share
        self.reconciled: list[str] = []                # closed invoices read, by uuid
        self.invoiced: list[str] = []                  # months with a matching invoice
        self.unmatched: list[str] = []                 # months whose lines named no droplet
        self.booked: dict[str, int] = {}               # month -> booked burn, never < 0
        self.negative: list[str] = []                  # months DigitalOcean showed < 0
        self.unread: str | None = None                 # why the last reading booked nothing
        self.history: list[str] = []                   # history entries seen (keys)
        self.snapshot: dict[str, Any] = {}             # the droplet and sizes last read
        self.pending_baseline = True                   # launch month's share not yet known
        self.pending_noted = False                     # and the diary has said so
        self.droplet_uuid: str | None = None           # learned from its own line
        # The launch month's lines' spans (key -> [start, end, the droplet's own line]),
        # so its pre-launch share is recomputed from every current line on each read.
        self.spans: dict[str, list] = {}
        # Whether the launch month has closed and every invoice known for it is read.
        self.estimate_final = False
        # The droplet's hourly rate and monthly cap as first read in the launch month:
        # the prices its launch-month lines were billed at, whatever it is resized to.
        self.launch_price: dict[str, int] | None = None
        # Where the next read of the waiting invoices starts: the last one read.
        self.cursor: list | None = None
        # Invoices read and held, by uuid, with the reason they could not be classified.
        self.held: dict[str, str] = {}

    @property
    def since(self) -> str | None:
        """The launch month, from the world's launch clock (None until a resume restores it)."""
        return None if self.launch_ns is None else _month_of_ns(self.launch_ns)

    # ---- state

    def state(self) -> dict[str, Any]:
        """Everything a resume needs; never the client or its token."""
        return deepcopy({name: getattr(self, name) for name in self.FIELDS})

    def restore(self, saved: dict[str, Any]) -> None:
        for name in self.FIELDS:
            if name in saved:
                setattr(self, name, deepcopy(saved[name]))

    # ---- the books

    def burn_by_month(self) -> dict[str, int]:
        """Each month's booked burn."""
        return dict(sorted(self.booked.items()))

    def burned_micro(self) -> int:
        return sum(self.booked.values())

    def view(self) -> dict[str, Any]:
        """What is published: this droplet's burn and its status, and nothing of the account."""
        months = self.burn_by_month()
        return {
            "custodian": COUNTERPARTY, "droplet_id": self.droplet_id,
            "balance_micro": None, "balance": "unknown", "balance_reason": BALANCE_UNKNOWN,
            "burned_micro": sum(months.values()),
            "burn_by_month": [{"month": m, "micro": v,
                               "source": "invoice" if m in self.invoiced else "preview",
                               **self._estimate(m)}
                              for m, v in list(months.items())[-12:]],
            "launch_month": self.since,
            "launch_share": ("pending" if self.pending_baseline
                             else self.baseline.get(self.since, {}).get("method")),
            "unmatched_months": list(self.unmatched), "unread": self.unread,
        }

    def _estimate(self, month: str) -> dict[str, Any]:
        """How a month's booked burn was reached: measured, or the launch month's estimate.

        The launch month's pre-launch share is an allocation, not a measurement, so its
        booked burn says ``estimated`` and carries the most it can exceed the true
        post-launch charge by (``overshoot_bound_micro``).
        """
        share = self.baseline.get(month) if month == self.since else None
        if share is None:
            return {"estimated": False}
        return {"estimated": True, "overshoot_bound_micro": share["overshoot_bound_micro"],
                "bound": share["bound"], "estimate_final": self.estimate_final}

    def _prelaunch(self) -> dict | None:
        """The launch month's pre-launch share, from every one of its current lines.

        Each line's amount is shared by DigitalOcean's own span for it, start to end;
        what falls before the launch is not this world's. It is recomputed from the
        current version of every launch-month line on each read (a backup line that
        appears late, a supplemental invoice, a revision all move it). Only lines of
        which one reaches past the launch can say it; until then it is pending, and
        the month books nothing.

        The share is an allocation, and it carries a bound, never below zero, on how
        far the booked post-launch burn can exceed the true one: a sum of each line's
        uncertainty, never reduced by any line. A line wholly before or wholly after
        the launch (a line with no span is dated at its instant) is certain. For a
        positive line of the droplet's own, when its launch-month price was read: the
        pre-launch part of what DigitalOcean discounted the line by, against that
        hourly price for the hours in its span (the monthly cap discounts the end of a
        month, which sharing by span spreads over the whole of it, so the share falls
        short of the pre-launch charge by at most that part). For any other line, a
        credit included, or when the launch-month price is unknown: the absolute size
        of its whole post-launch part.
        """
        amounts = self.lines.get(self.since, {})
        spans = [(*self.spans[key][:2], amount, self.spans[key][2])
                 for key, amount in amounts.items() if key in self.spans]
        if not any(end > self.launch_ns for _, end, _, _ in spans):
            return None
        hourly = (self.launch_price or {}).get("price_hourly_micro")
        share = bound = 0
        for start, end, amount, own in spans:
            if end <= self.launch_ns:            # before the launch (or dated before it)
                share += amount
                continue
            if start >= self.launch_ns:          # after it: all this world's, certain
                continue
            pre = amount * (self.launch_ns - start) // (end - start)
            share += pre
            post = amount - pre
            if own and amount > 0 and type(hourly) is int:
                listed = hourly * (end - start) // 3_600_000_000_000
                discount = max(0, listed - amount)
                # The true pre-launch charge is at most the listed price for its hours,
                # so the share falls short of it, and the burn over-reaches, by at most
                # the discount's pre-launch part.
                bound += -(-discount * (self.launch_ns - start) // (end - start))
            else:
                bound += abs(post)
        return {"micro": share, "method": "within each line's own span", "estimated": True,
                "overshoot_bound_micro": bound,
                "bound": ("the booked launch-month burn exceeds the true post-launch charge "
                          "by at most overshoot_bound_micro")}

    def _book(self, month: str, source: str, result: dict) -> None:
        """Book the change in one month's figure; never below zero."""
        level = sum(self.lines.get(month, {}).values())
        if month == self.since:
            share = self._prelaunch()
            if share is not None:
                share = {**share, "source": source}
                if share != self.baseline.get(month):
                    self.baseline[month] = share
                    result["baseline"] = {"month": month, **share}
                self.pending_baseline = False
            if self.pending_baseline:
                return
            level -= self.baseline[month]["micro"]
        if level < 0:
            if month not in self.negative:
                self.negative.append(month)
                result["negative"].append({"month": month, "level_micro": level,
                                           "lines": dict(self.lines.get(month, {}))})
            level = 0
        delta = level - self.booked.get(month, 0)
        self.booked[month] = level
        if delta:
            result["changes"].append({"month": month, "delta_micro": delta,
                                      "booked_micro": level, "source": source,
                                      "lines": len(self.lines.get(month, {})),
                                      **self._estimate(month)})

    def _match(self, month: str, result: dict) -> None:
        if month in self.unmatched:
            self.unmatched.remove(month)
            result["cleared"].append(month)

    def _unmatch(self, month: str, result: dict) -> None:
        if month not in self.unmatched:
            self.unmatched.append(month)
            result["unmatched"].append(month)

    def observe(self, reading: dict[str, Any]) -> dict[str, Any]:
        """Book one accepted reading; guarantees each month's burn is DigitalOcean's figure.

        Returns what changed, per month; the launch month's share once it is known;
        the months whose lines named no droplet, and those that later did; months
        DigitalOcean showed below zero; the invoices reconciled; and the billing
        history entries not seen before.
        """
        self.unread = None
        self.snapshot = {"droplet": reading["droplet"], "sizes": reading["sizes"]}
        droplet = reading["droplet"] or {}
        if (self.launch_price is None and reading["period"] == self.since
                and type(droplet.get("price_hourly_micro")) is int):
            # The launch month's prices, first read in it, and journaled with the read.
            self.launch_price = {"price_hourly_micro": droplet["price_hourly_micro"],
                                 "price_monthly_micro": droplet.get("price_monthly_micro")}
            result_price = dict(self.launch_price)
        else:
            result_price = None
        if not self.droplet_uuid and reading.get("droplet_uuid"):
            # Only a valid uuid, actually seen, is ever kept: unknown is the only other
            # state, and every read looks again.
            self.droplet_uuid = reading["droplet_uuid"]
        self.cursor = reading.get("cursor") or self.cursor
        result: dict[str, Any] = {"changes": [], "baseline": None, "unmatched": [],
                                  "cleared": [], "negative": [], "reconciled": [],
                                  "entries": [], "launch_price": result_price,
                                  "held": []}
        for invoice in reading.get("held", []):
            if self.held.get(invoice["uuid"]) != invoice["reason"]:
                self.held[invoice["uuid"]] = invoice["reason"]
                result["held"].append({k: invoice[k] for k in ("uuid", "period", "reason")})
        for invoice in reading["closed"]:
            if invoice["uuid"] in self.reconciled:
                continue
            self.reconciled.append(invoice["uuid"])
            self.held.pop(invoice["uuid"], None)
            month = invoice["period"]
            result["reconciled"].append({"uuid": invoice["uuid"], "month": month,
                                         "lines": len(invoice["mine"]),
                                         "other_lines": invoice["others"]})
            if month < self.since:
                continue
            if not invoice["mine"]:
                if invoice["others"] and month not in self.invoiced:
                    self._unmatch(month, result)
                continue
            self._match(month, result)
            booked = self.lines.get(month, {})
            if month not in self.invoiced:
                # The first invoice with this droplet's lines replaces the preview.
                booked = {k: v for k, v in booked.items() if not k.startswith("p:")}
                self.invoiced.append(month)
            booked.update({f"{invoice['uuid']}:{x['key']}": x["amount_micro"]
                           for x in invoice["mine"]})
            self.lines[month] = booked
            if month == self.since:
                self._keep_spans(invoice["mine"], invoice["uuid"])
            self._book(month, "invoice", result)
        month = reading["period"]
        if month >= self.since and month not in self.invoiced:
            if reading["lines"]:
                self._match(month, result)
                self.lines[month] = {f"p:{x['key']}": x["amount_micro"]
                                     for x in reading["lines"]}
                if month == self.since:
                    self._keep_spans(reading["lines"], "p")
                self._book(month, "preview", result)
            elif reading["others"]:
                self._unmatch(month, result)
        # The estimate is final once the launch month has closed, an invoice for it
        # with this droplet's lines is read, and no invoice known for it is waiting.
        self.estimate_final = (self.since in self.invoiced and month > self.since
                               and self.since not in reading.get("waiting_periods", []))
        result["pending"] = self.pending_baseline and not self.pending_noted
        self.pending_noted = self.pending_noted or self.pending_baseline
        for entry in reading["history"]:
            if entry["key"] in self.history:
                continue
            self.history = (self.history + [entry["key"]])[-500:]
            result["entries"].append(entry)
        return result

    def _keep_spans(self, lines: list[dict], prefix: str) -> None:
        # A preview line's identity moves as it accrues: keep the spans of the launch
        # month's current lines only.
        current = set(self.lines.get(self.since, {}))
        from datetime import datetime

        def ns(stamp: str) -> int:
            return int(datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
                       * 1_000_000_000)

        self.spans.update({f"{prefix}:{x['key']}": [ns(x["start_time"]), ns(x["end_time"]),
                                                    x.get("droplet", True)]
                           for x in lines})
        self.spans = {k: v for k, v in self.spans.items() if k in current}


def _month_of_ns(ns: int | None) -> str:
    from datetime import UTC, datetime

    if ns is None:
        raise ValueError("a hosting account needs its world's launch time")
    return datetime.fromtimestamp(ns / 1_000_000_000, UTC).strftime("%Y-%m")
