"""The hosting pot: prepaid host credit, observed; a droplet resize, journaled first.

The factory runs on a host it did not pay for in its own books until now. The
host's prepaid credit is part of the one first move (essay II.II, the
Stackelberg move; II.IV, "a continuous, reciprocal flow of capital is an
objective requirement"), exactly as OpenRouter credit is, so it is a pot: its
balance is what DigitalOcean reports, and what leaves it is what DigitalOcean
reports as used. Nothing here computes a burn from a price, a clock or a
formula. The wallet moves only when money moves, and every hosting debit names
DigitalOcean and the ``generated_at`` of the reading that showed it.

The droplet's size is a surface (AGENTS.md rule 1): a seat may resize it within
limits the manifest fixed, and the price consequence arrives only through the
same observed billing. What is enforced here, and nowhere else:

* the target size is on DigitalOcean's published list, available, and sold in
  the droplet's region;
* its monthly price is at most ``[hosting] max_monthly_usd``;
* ``disk`` is ``false`` (CPU and RAM only, reversible) unless the manifest set
  ``allow_disk_resize``; a disk resize cannot be undone (DigitalOcean cannot
  shrink a disk);
* one resize at a time, each with a durable ``hosting.intent`` before the call,
  and an answer that was lost is recovered by reading the droplet, never by
  sending the resize again.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

COUNTERPARTY = "digitalocean"
#: How many times an unanswered resize is looked for on an idle droplet before it is
#: released as unknown: the bound an unanswered venue order gets
#: (``runtime.venue.UNCERTAIN_ORDER_POLLS``), restated because world code does not
#: import the runtime.
UNCERTAIN_POLLS = 5
READ_FAILED = "hosting read unavailable"
DISK_REFUSED = "disk resize is not allowed in this world ([hosting] allow_disk_resize)"
#: Intent states that hold the one resize slot.
OPEN = ("uncertain", "in-progress")


def _stamp(generated_at: str) -> datetime | None:
    """DigitalOcean's ``generated_at`` as a comparable time, or None if unreadable."""
    try:
        return datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


class HostingAccount:
    """One droplet and the account that pays for it. Guarantees ``state`` restores it.

    ``client`` is the DigitalOcean adapter, journaled by the runtime (reads under
    ``hosting.<read>``, the write as ``hosting.resize``) so a replay answers from the
    record. ``last`` is the balance reading every later one is measured against.
    """

    FIELDS = ("last", "endowment_micro", "burned_micro", "credited_micro", "intents",
              "unread")

    def __init__(self, client: Any, *, droplet_id: int, max_monthly_micro: int,
                 allow_disk_resize: bool = False) -> None:
        if type(droplet_id) is not int or droplet_id <= 0:
            raise ValueError("hosting droplet_id must be a positive integer")
        if type(max_monthly_micro) is not int or max_monthly_micro < 0:
            raise ValueError("hosting max_monthly_micro must be nonnegative integer micro-USD")
        if type(allow_disk_resize) is not bool:
            raise ValueError("hosting allow_disk_resize must be true or false")
        self.client = client
        self.droplet_id = droplet_id
        self.max_monthly_micro = max_monthly_micro
        self.allow_disk_resize = allow_disk_resize
        self.last: dict[str, Any] | None = None
        self.endowment_micro: int | None = None
        self.burned_micro = 0
        self.credited_micro = 0
        self.intents: dict[str, dict[str, Any]] = {}
        # The error class of the last failed balance read, or None after a good one.
        self.unread: str | None = None

    # ---- state

    def state(self) -> dict[str, Any]:
        """Everything a resume needs; never the client or its token."""
        return deepcopy({name: getattr(self, name) for name in self.FIELDS})

    def restore(self, saved: dict[str, Any]) -> None:
        for name in self.FIELDS:
            if name in saved:
                setattr(self, name, deepcopy(saved[name]))

    # ---- the pot

    def credit_micro(self) -> int | None:
        """Credit remaining as last read (negative: owed), or None while unread."""
        if self.last is None or self.unread is not None:
            return None
        return self.last["credit_micro"]

    def view(self) -> dict[str, Any]:
        """The pot's public facts: DigitalOcean's figures and what the books took from them."""
        last = self.last or {}
        return {
            "custodian": COUNTERPARTY, "droplet_id": self.droplet_id,
            "credit_micro": self.credit_micro(),
            "month_to_date_usage_micro": last.get("month_to_date_usage_micro"),
            "generated_at": last.get("generated_at"),
            "endowment_micro": self.endowment_micro,
            "burned_micro": self.burned_micro, "credited_micro": self.credited_micro,
            "unread": self.unread,
            "resize_open": any(i["status"] in OPEN for i in self.intents.values()),
        }

    def observe(self, read: dict[str, Any]) -> dict[str, Any]:
        """Measure one balance reading against the last; guarantees each change counts once.

        The first reading is the endowment. After it, a rise in
        ``month_to_date_balance`` is money DigitalOcean took (burn), a fall is
        credit that arrived from outside (a prepayment, a promotion, a refund), and
        a reading older than the last is stale and changes nothing.
        ``month_to_date_balance`` is DigitalOcean's own total of ``account_balance``
        and ``month_to_date_usage``, so the monthly invoice moving usage into the
        account balance does not show as a change. A charge and a payment landing
        between the same two readings net: the reading cannot tell them apart.
        """
        self.unread = None
        previous = self.last
        if previous is None:
            self.last = dict(read)
            self.endowment_micro = read["credit_micro"]
            return {"effect": "endowment", "micro": read["credit_micro"]}
        before, after = _stamp(previous["generated_at"]), _stamp(read["generated_at"])
        if before is not None and after is not None and after < before:
            return {"effect": "stale", "micro": 0}
        delta = read["month_to_date_balance_micro"] - previous["month_to_date_balance_micro"]
        self.last = dict(read)
        evidence = {
            "generated_at": read["generated_at"],
            "previous_generated_at": previous["generated_at"],
            "month_to_date_balance_micro": [previous["month_to_date_balance_micro"],
                                            read["month_to_date_balance_micro"]],
            "month_to_date_usage_micro": [previous["month_to_date_usage_micro"],
                                          read["month_to_date_usage_micro"]],
            "account_balance_micro": [previous["account_balance_micro"],
                                      read["account_balance_micro"]],
        }
        if delta > 0:
            self.burned_micro += delta
            return {"effect": "burn", "micro": delta, **evidence}
        if delta < 0:
            self.credited_micro += -delta
            return {"effect": "credited", "micro": -delta, **evidence}
        return {"effect": "unchanged", "micro": 0}

    # ---- the resize surface

    def refusal(self, size: Any, disk: Any, droplet: dict, sizes: list[dict]) -> str | None:
        """Why this resize is refused before any intent, or None."""
        if not isinstance(size, str) or not size:
            return "size must be a size slug"
        if type(disk) is not bool:
            return "disk must be true or false"
        if disk and not self.allow_disk_resize:
            return DISK_REFUSED
        if any(intent["status"] in OPEN for intent in self.intents.values()):
            return "a resize of this droplet is still open"
        listed = next((row for row in sizes if row["slug"] == size), None)
        if listed is None:
            return "size is not on DigitalOcean's published size list"
        if not listed["available"] or droplet["region"] not in listed["regions"]:
            return "size is not available in the droplet's region"
        if listed["price_monthly_micro"] > self.max_monthly_micro:
            return "size price_monthly exceeds [hosting] max_monthly_usd"
        if droplet["size_slug"] == size:
            return "droplet is already that size"
        return None

    def resize(self, ledger: Any, *, client_id: str, handle: str, size: Any,
               disk: Any = False) -> dict[str, Any]:
        """Submit one resize under a durable intent; a repeat never submits twice."""
        previous = self.intents.get(client_id)
        if previous is not None:
            if previous["size"] != size or previous["disk"] != disk:
                return self._refuse(ledger, handle, "client id already binds another resize")
            if previous["status"] == "uncertain":
                self._recover(ledger, client_id)
            return self._answer(self.intents[client_id])
        if disk is True and not self.allow_disk_resize:
            # Refused before any read: nothing about the world can make it allowed.
            return self._refuse(ledger, handle, DISK_REFUSED)
        try:
            droplet = self.client.droplet(self.droplet_id)
            sizes = self.client.sizes()
        except Exception:  # noqa: BLE001 - an unread world refuses new writes
            # A fixed reason: a replay raises the recorded failure under another class.
            return self._refuse(ledger, handle, READ_FAILED)
        reason = self.refusal(size, disk, droplet, sizes)
        if reason:
            return self._refuse(ledger, handle, reason)
        listed = next(row for row in sizes if row["slug"] == size)
        intent = {"client_id": client_id, "handle": handle, "droplet_id": self.droplet_id,
                  "size": size, "disk": disk, "from_size": droplet["size_slug"],
                  "price_monthly_micro": listed["price_monthly_micro"],
                  "from_price_monthly_micro": droplet["price_monthly_micro"],
                  "status": "uncertain", "action_id": None, "polls": 0}
        # The intent is durable before DigitalOcean is asked: a crash after this line
        # leaves a record that the resize may have been sent, and nothing resends it.
        ledger.append({"kind": "hosting.intent", **intent})
        self.intents[client_id] = intent
        try:
            action = self.client.resize(self.droplet_id, size, disk)
        except Exception:  # noqa: BLE001 - a lost answer is uncertain, never absent
            ledger.append({"kind": "hosting.uncertain", "client_id": client_id,
                           "handle": handle, "error": "the resize call failed"})
            return self._answer(intent)
        if not isinstance(action, dict) or action.get("status") not in (
                "in-progress", "completed", "errored") or type(action.get("id")) is not int:
            # A replayed call whose answer the crash lost comes back as uncertain.
            ledger.append({"kind": "hosting.uncertain", "client_id": client_id,
                           "handle": handle, "error": "no action in the answer"})
            return self._answer(intent)
        self._record(ledger, client_id, action)
        return self._answer(self.intents[client_id])

    def tick(self, ledger: Any) -> None:
        """Follow every open resize to its end, by reading; never by sending again."""
        for client_id, intent in list(self.intents.items()):
            if intent["status"] == "in-progress":
                try:
                    action = self.client.action(intent["action_id"])
                except Exception:  # noqa: BLE001 - asked again next tick
                    continue
                if action["status"] != "in-progress":
                    self._record(ledger, client_id, action)
            elif intent["status"] == "uncertain":
                self._recover(ledger, client_id)

    def _recover(self, ledger: Any, client_id: str) -> None:
        """Resolve an unanswered resize from the droplet itself.

        The droplet at the target size is the resize done. A droplet that is locked
        or not active is an action still running and costs no poll. An idle droplet
        at another size, asked ``UNCERTAIN_POLLS`` times, releases the intent as
        unknown: the slot opens, and nothing is resent.
        """
        intent = self.intents[client_id]
        try:
            droplet = self.client.droplet(self.droplet_id)
        except Exception:  # noqa: BLE001 - an unread droplet decides nothing
            return
        if droplet["size_slug"] == intent["size"]:
            self.intents[client_id] = {**intent, "status": "completed"}
            ledger.append({"kind": "hosting.resized", "client_id": client_id,
                           "handle": intent["handle"], "size": intent["size"],
                           "evidence": "droplet size", "action_id": intent["action_id"]})
            return
        if droplet["locked"] or droplet["status"] != "active":
            return
        polls = intent["polls"] + 1
        if polls >= UNCERTAIN_POLLS:
            self.intents[client_id] = {**intent, "polls": polls, "status": "unresolved"}
            ledger.append({"kind": "hosting.unresolved", "client_id": client_id,
                           "handle": intent["handle"], "polls": polls,
                           "size_observed": droplet["size_slug"]})
            return
        self.intents[client_id] = {**intent, "polls": polls}

    def _record(self, ledger: Any, client_id: str, action: dict) -> None:
        intent = self.intents[client_id]
        status = action["status"]
        self.intents[client_id] = {**intent, "status": status, "action_id": action["id"]}
        kind = {"in-progress": "hosting.acknowledged", "completed": "hosting.resized",
                "errored": "hosting.errored"}[status]
        ledger.append({"kind": kind, "client_id": client_id, "handle": intent["handle"],
                       "size": intent["size"], "action_id": action["id"],
                       "started_at": action.get("started_at"),
                       "completed_at": action.get("completed_at")})

    @staticmethod
    def _refuse(ledger: Any, handle: str, reason: str) -> dict[str, Any]:
        ledger.append({"kind": "hosting.refused", "handle": handle, "reason": reason})
        return {"status": "refused", "error": reason}

    @staticmethod
    def _answer(intent: dict[str, Any]) -> dict[str, Any]:
        return {"status": intent["status"], "size": intent["size"], "disk": intent["disk"],
                "from_size": intent["from_size"], "action_id": intent["action_id"],
                "price_monthly_micro": intent["price_monthly_micro"]}
