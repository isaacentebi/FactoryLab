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

Burn counts from the first successful reading: what the droplet had accrued that
month before it is the month's baseline, not this world's burn.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

COUNTERPARTY = "digitalocean"
#: What a seat may ask DigitalOcean for, a reserve window: a limit, so the population
#: cannot spend the account's API rate limit (5,000 requests an hour).
SEAT_READS_PER_WINDOW = 2
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
    world was bound to at launch; a resume takes it from the checkpoint and the next
    reading checks it live.
    """

    FIELDS = ("bound", "since", "lines", "baseline", "finalized", "unread", "history",
              "history_totals", "reads", "unmatched", "last_period")

    def __init__(self, client: Any, *, droplet_id: int, bound: dict[str, Any] | None,
                 budget_s: float) -> None:
        if type(droplet_id) is not int or droplet_id <= 0:
            raise ValueError("hosting droplet_id must be a positive integer")
        self.client = client
        self.droplet_id = droplet_id
        self.budget_s = budget_s
        self.bound = None if bound is None else dict(bound)
        self.since: str | None = None                  # the month of the first reading
        self.lines: dict[str, dict[str, int]] = {}     # month -> line key -> booked micro
        self.baseline: dict[str, int] = {}             # month -> accrued before launch
        self.finalized: list[str] = []                 # months read from their invoice
        self.unread: str | None = None                 # why the last reading booked nothing
        self.history: list[str] = []                   # billing history entries seen
        self.history_totals: dict[str, int] = {}       # account entries by type
        self.reads: dict[str, Any] = {"window": None, "by_seat": {}}
        self.unmatched: list[str] = []                 # months whose lines name no droplet
        self.last_period: str | None = None

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
        """Each month's booked burn: DigitalOcean's figure for this droplet, less baseline."""
        return {month: sum(lines.values()) - self.baseline.get(month, 0)
                for month, lines in sorted(self.lines.items())}

    def burned_micro(self) -> int:
        return sum(self.burn_by_month().values())

    def view(self) -> dict[str, Any]:
        """The pot's public facts: burn by month, and why its balance is unknown."""
        months = self.burn_by_month()
        return {
            "custodian": COUNTERPARTY, "droplet_id": self.droplet_id,
            "balance_micro": None, "balance": "unknown", "balance_reason": BALANCE_UNKNOWN,
            "burned_micro": sum(months.values()),
            "burn_by_month": [{"month": m, "micro": v,
                               "source": "invoice" if m in self.finalized else "preview"}
                              for m, v in list(months.items())[-12:]],
            "since": self.since, "prelaunch_micro": dict(self.baseline),
            "account_entries_micro": dict(sorted(self.history_totals.items())),
            "unmatched_months": list(self.unmatched), "unread": self.unread,
        }

    def _level(self, month: str, lines: list[dict[str, Any]], source: str) -> list[dict]:
        """Set a month's booked lines to DigitalOcean's; return each change, once."""
        now: dict[str, int] = {}
        about: dict[str, dict] = {}
        for line in lines:
            now[line["key"]] = now.get(line["key"], 0) + line["amount_micro"]
            about[line["key"]] = line
        booked = self.lines.get(month, {})
        changes = []
        for key in sorted(set(now) | set(booked)):
            delta = now.get(key, 0) - booked.get(key, 0)
            if delta:
                line = about.get(key, {})
                changes.append({"month": month, "line": key, "source": source,
                                "product": line.get("product"),
                                "start_time": line.get("start_time"),
                                "delta_micro": delta, "level_micro": now.get(key, 0)})
        self.lines[month] = now
        return changes

    def observe(self, reading: dict[str, Any]) -> dict[str, Any]:
        """Book one accepted reading; guarantees each month's burn is DigitalOcean's figure.

        Returns what changed: the launch baseline (first reading only), every line
        whose booked amount moved (``delta_micro``, either sign), the month an
        invoice closed, the months whose lines named no droplet, and the billing
        history entries not seen before.
        """
        self.unread = None
        month = reading["period"]
        self.last_period = month
        result: dict[str, Any] = {"baseline": None, "changes": [], "closed": None,
                                  "unmatched": [], "entries": []}
        first = self.since is None
        if first:
            self.since = month
            self._level(month, reading["lines"], "preview")
            self.baseline[month] = sum(self.lines[month].values())
            result["baseline"] = {"month": month, "micro": self.baseline[month]}
        # An invoice with lines, none of them this droplet's, is not evidence that the
        # droplet cost nothing: DigitalOcean did not tell its lines apart. The month keeps
        # what is booked, and is flagged, never levelled down to zero.
        closed = reading.get("closed")
        if closed is not None and closed["period"] not in self.finalized:
            if closed["all_lines"] and not closed["lines"]:
                result["unmatched"].append(closed["period"])
            else:
                result["changes"] += self._level(closed["period"], closed["lines"],
                                                 "invoice")
            self.finalized.append(closed["period"])
            result["closed"] = closed["period"]
        if month not in self.finalized:
            if reading["all_lines"] and not reading["lines"]:
                result["unmatched"].append(month)
            else:
                result["changes"] += self._level(month, reading["lines"], "preview")
        result["unmatched"] = [m for m in result["unmatched"] if m not in self.unmatched]
        self.unmatched.extend(result["unmatched"])
        for entry in reading["history"]:
            if entry["key"] in self.history:
                continue
            self.history.append(entry["key"])
            if first:
                continue  # an entry already on the account at launch is not this world's
            self.history_totals[entry["type"]] = (self.history_totals.get(entry["type"], 0)
                                                  + entry["amount_micro"])
            result["entries"].append(entry)
        return result

    # ---- the seats' reads

    def admit_read(self, seat: str, window: int) -> bool:
        """Whether a seat may ask DigitalOcean once more this reserve window; counts it."""
        if self.reads["window"] != window:
            self.reads = {"window": window, "by_seat": {}}
        used = self.reads["by_seat"].get(seat, 0)
        if used >= SEAT_READS_PER_WINDOW:
            return False
        self.reads["by_seat"][seat] = used + 1
        return True
