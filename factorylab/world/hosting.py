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
              "pending_baseline", "pending_noted", "droplet_uuid")

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
                "bound": share["bound"]}

    def _prelaunch(self, month: str, lines: list[dict]) -> dict | None:
        """The launch month's pre-launch share, from lines whose span covers the launch.

        Each line's amount is shared by DigitalOcean's own span for it, start to end;
        what falls before the launch is not this world's. Only a reading in which one of
        this droplet's lines reaches past the launch can say it; until then it is
        pending, and the month books nothing.

        The share is an allocation, and it carries a bound on how far the booked
        post-launch burn can exceed the true one. For the droplet's own line: the
        pre-launch part of what DigitalOcean discounted the line by, against the
        droplet's published hourly price for the hours in its span (the monthly cap
        discounts the end of a month, which sharing by span spreads over the whole of
        it, so the share falls short of the pre-launch charge by at most that part).
        For any other line billed against the droplet (a backup, say), whose accrual
        over its span nothing here knows: its whole post-launch part.
        """
        from datetime import datetime

        def ns(stamp: str) -> int:
            return int(datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
                       * 1_000_000_000)

        spans = [(ns(x["start_time"]), ns(x["end_time"]), x["amount_micro"],
                  x.get("droplet", True)) for x in lines]
        if month != self.since or not any(end > self.launch_ns for _, end, _, _ in spans):
            return None
        hourly = (self.snapshot.get("droplet") or {}).get("price_hourly_micro")
        share = bound = 0
        for start, end, amount, own in spans:
            if end <= self.launch_ns:
                share += amount
                continue
            pre = amount * (max(start, self.launch_ns) - start) // (end - start)
            share += pre
            post = amount - pre
            if own and type(hourly) is int:
                listed = hourly * (end - start) // 3_600_000_000_000
                discount = max(0, listed - amount)
                # The true pre-launch charge is at most the listed price for its hours,
                # so the share falls short of it, and the burn over-reaches, by at most
                # the discount's pre-launch part.
                bound += -(-discount * (max(start, self.launch_ns) - start) // (end - start))
            else:
                bound += post
        return {"micro": share, "method": "within each line's own span", "estimated": True,
                "overshoot_bound_micro": bound,
                "bound": ("the booked launch-month burn exceeds the true post-launch charge "
                          "by at most overshoot_bound_micro")}

    def _book(self, month: str, source: str, result: dict) -> None:
        """Book the change in one month's figure; never below zero."""
        level = sum(self.lines.get(month, {}).values())
        if month == self.since:
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
        self.droplet_uuid = self.droplet_uuid or reading.get("droplet_uuid")
        result: dict[str, Any] = {"changes": [], "baseline": None, "unmatched": [],
                                  "cleared": [], "negative": [], "reconciled": [],
                                  "entries": []}
        for invoice in reading["closed"]:
            if invoice["uuid"] in self.reconciled:
                continue
            self.reconciled.append(invoice["uuid"])
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
            if month == self.since and self.pending_baseline:
                self._settle_baseline(month, invoice["mine"], "invoice", result)
            self._book(month, "invoice", result)
        month = reading["period"]
        if month >= self.since and month not in self.invoiced:
            if reading["lines"]:
                self._match(month, result)
                self.lines[month] = {f"p:{x['key']}": x["amount_micro"]
                                     for x in reading["lines"]}
                if month == self.since and self.pending_baseline:
                    self._settle_baseline(month, reading["lines"], "preview", result)
                self._book(month, "preview", result)
            elif reading["others"]:
                self._unmatch(month, result)
        result["pending"] = self.pending_baseline and not self.pending_noted
        self.pending_noted = self.pending_noted or self.pending_baseline
        for entry in reading["history"]:
            if entry["key"] in self.history:
                continue
            self.history = (self.history + [entry["key"]])[-500:]
            result["entries"].append(entry)
        return result

    def _settle_baseline(self, month: str, lines: list[dict], source: str,
                         result: dict) -> None:
        share = self._prelaunch(month, lines)
        if share is None:
            return
        self.baseline[month] = {**share, "source": source}
        self.pending_baseline = False
        result["baseline"] = {"month": month, "source": source, **share}


def _month_of_ns(ns: int | None) -> str:
    from datetime import UTC, datetime

    if ns is None:
        raise ValueError("a hosting account needs its world's launch time")
    return datetime.fromtimestamp(ns / 1_000_000_000, UTC).strftime("%Y-%m")
