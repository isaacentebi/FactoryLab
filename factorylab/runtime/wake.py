"""The public wake contains only authenticated aggregates and account statements.

The control tower sees whatever the population already sees. The
world block an assembly reads on every request is public by construction, so its
standing facts (roster, tools, observations, charter, pots, portfolio) are
projected into one unsealed ledger item at each window close and republished
here; the histories (registrations, amendments, compute spend, transfers,
pathologies and the immune organ's answers) are read from public ledger items
that already existed. Nothing the essay keeps private crosses this boundary:
learner state, router weights and propensities, private memories, raw request
and return text and per-decision scores stay sealed until death.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import stat
import tempfile
import time
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from cryptography.fernet import InvalidToken

from factorylab.kernel.ledger import Ledger, LedgerIntegrityError, canonical
from factorylab.runtime.worlds import WORLDS_DIR, load_manifest

VIEWS = (
    "wallet_series", "spend_by_capability", "invocations_by_assembly",
    "action_frequencies", "settlement_latency",
)
SECTIONS = ("roster", "tools", "connectors", "notes", "observations", "charter", "compute", "pots",
            "immune", "portfolio",
            # Edition 2: the architect watches money, deliveries, open promises, the
            # behavioural cells and the alive/dormant/terminated state, without a lever.
            "money", "deliveries", "commitments", "cells", "liveness", "entitlements")
UNAVAILABLE = "unavailable"
PUBLIC_KIND = "wake.public"
#: Every observatory list is bounded so one page cannot grow with the diary.
MAX_ROWS = 200
ROLES = ("producer", "evaluator", "meta", "antagonist")
RAILS = ("openrouter", "venice", "x402")
INCOME_CLASSES = ("earned_micro", "subsidy_micro", "converted_from_principal_micro")
#: Money that entered the wallet, by class, and money that left it, by the reason it left.
IN_CLASSES = ("initial", "drip", "release", "subsidy", "earned", "converted_from_principal",
              "exchange_pnl", "funding")
OUT_CLASSES = ("model", "tool", "connector", "treasury", "registration", "exchange_pnl",
               "funding", "transfer_fees", "other")


def rail_for_model(model_id: str) -> str:
    """Name the compute rail a model id is bought on, exactly as registration routes it."""
    if model_id.startswith("x402:"):
        return "x402"
    if model_id.startswith("venice:"):
        return "venice"
    return "openrouter"


def _tool_version(rt, tool_id: str) -> int:
    """Return the registered contract version of a tool; seed tools are version 1."""
    try:
        return rt.registry.get(f"tool:{tool_id}").version
    except (KeyError, AttributeError, TypeError):
        return 1


def public_window_item(rt, *, window: int, event: int) -> dict:
    """Project the population's own world block into one unsealed window-close item.

    Every field here is already public to every assembly through the world block
    (roster counts, tool and observation catalogues, the charter with its prices
    and regions, the pots, the account). No position is copied: A17 publishes no
    positions, no entry prices and no assembly ids, and a coin with a side is a
    position. The portfolio escapes as equity and realised P&L only. Nothing is
    read that the runtime does not already hold, so this item costs no external
    call and adds no resumable state.
    """
    from factorylab.charter.measurement import measurement_catalogue
    from factorylab.runtime.notes import counts

    roster: Counter = Counter()
    for assembly in rt.assemblies.values():
        roster[(assembly.spec.role, assembly.spec.model_id)] += 1
    pots = rt.wallet.pots()
    return {
        "kind": PUBLIC_KIND,
        "window": window,
        "window_end_event": event,
        "roster": [{"kind": role, "model_id": model, "count": count}
                   for (role, model), count in sorted(roster.items())],
        "tools": [{"id": spec["id"], "description": spec["description"],
                   "version": _tool_version(rt, spec["id"])}
                  for spec in sorted(rt.tool_specs.values(), key=lambda spec: spec["id"])],
        "notes": counts(rt.notes),
        "connectors": {"registered": rt._connector_catalogue(),
                       "calls_per_day": {
                           datetime.fromtimestamp(day * 86400, UTC).date().isoformat(): count
                           for day, count in sorted(rt.connector_calls_day.items())[-MAX_ROWS:]}},
        "observations": [{"id": row["id"], "description": row["description"],
                          "units": row["units"]} for row in measurement_catalogue()],
        "charter": {
            "edition": rt.charter.edition,
            "norms": list(rt.charter.norms),
            "cards": [{"id": card.id, "norm": card.norm, "observation": card.observation,
                       "answers_for": card.answers_for,
                       "lambda": rt.controller.price(card.id),
                       "region": (asdict(region)
                                  if (region := rt.regions.get(card.id)) is not None else None)}
                      for card in rt.charter.cards],
        },
        "pots": {"venue": pots.get("venue"), "reserve": pots.get("reserve"),
                 "venice": pots.get("sellers", {}).get("venice"), "seed": pots.get("seed"),
                 "complete": pots.get("complete"),
                 # The endowment (C1) and the pause between its releases (C2).
                 "locked_micro": rt.wallet.locked, "unlocked_micro": rt.wallet.unlocked,
                 "next_release_ns": rt.wallet.next_release_ns,
                 "dormant": getattr(rt, "dormancy", None) is not None},
        "income": {name: pots.get(name) for name in INCOME_CLASSES},
        "portfolio": {
            "equity_micro": pots.get("venue"),
            "realized_to_date_micro": rt.realized_to_date,
        },
        # Each seat's entitlement (C10): an id is a public schematic every assembly
        # already reads in the roster, and the number is what the seat may spend.
        "entitlements": {
            "seats": [{"id": seat, "kind": rt.assemblies[seat].spec.role
                       if seat in rt.assemblies else "other", "micro": micro}
                      for seat, micro in rt.budget.entitlements().items()],
            "unallocated_micro": rt.budget.unallocated(),
        },
    }


def _fold_entitlements(item: dict | None) -> dict:
    """Only role totals escape the wake, as for every identity-bearing view."""
    if not isinstance(item, dict):
        return {"by_kind": {}, "seats": 0, "unallocated_micro": UNAVAILABLE}
    totals: Counter = Counter()
    seats = item.get("seats") or []
    for row in seats:
        kind = row.get("kind")
        totals[kind if kind in ROLES else "other"] += row.get("micro", 0)
    return {"by_kind": dict(sorted(totals.items())), "seats": len(seats),
            "unallocated_micro": item.get("unallocated_micro", UNAVAILABLE)}


def _day(ts_ns: int) -> str:
    return datetime.fromtimestamp(ts_ns // 1_000_000_000, UTC).strftime("%Y-%m-%d")


def _week(ts_ns: int) -> str:
    return datetime.fromtimestamp(ts_ns // 1_000_000_000, UTC).strftime("%G-W%V")


class _Observatory:
    """Accumulate only public ledger items into the widened wake, in one streaming pass.

    Every input is an item the population either produced or could read: the
    window-close projection above, registration and retirement announcements,
    the charter's own amendment record, wallet commitments naming a model id,
    treasury transfers, and the immune organ's public window verdicts. No item
    body reaches the page verbatim; each is reduced to counts, ids and reasons.
    """

    def __init__(self) -> None:
        self.latest: dict | None = None
        self.roster_series: list[dict] = []
        self.registered: list[dict] = []
        self.retired: list[dict] = []
        self.amendments: dict[str, dict] = {}
        self.spend_day: dict[str, Counter] = {}
        self.spend_week: dict[str, Counter] = {}
        self.invocations: dict[str, Counter] = {}
        self.transfers: list[dict] = []
        self.windows: dict[int, dict] = {}
        self.dormancy: list[dict] = []
        # Edition 2 views. Money by class; deliveries per closed window; open
        # decisions and sealed forecasts still waiting (ages only: a handle is a
        # sealed key); the immune organ's own window profiles (for cells);
        # dormancy and termination.
        self.money_in: Counter = Counter()
        self.money_out: Counter = Counter()
        self.current_window = 0
        self.deliveries: dict[int, dict[str, Counter]] = {}
        self.open_handles: dict[str, dict] = {}
        self.open_forecasts: dict[str, dict] = {}
        self.profiles: list[dict] = []
        self.launched_ns: int | None = None
        self.terminated_ns: int | None = None
        self.dormant_periods: list[dict] = []
        self.dormant_since: int | None = None

    def feed(self, item: dict) -> None:
        """Read one authenticated item; unknown and sealed kinds are simply not read."""
        kind = item.get("kind")
        handler = (getattr(self, "_on_" + kind.replace(".", "_"), None)
                   if isinstance(kind, str) and kind else None)
        if handler is not None:
            handler(item)

    def _on_wake_public(self, item: dict) -> None:
        self.latest = item
        counts: Counter = Counter()
        models: Counter = Counter()
        for row in item.get("roster", []):
            counts[row["kind"]] += row["count"]
            models[row["model_id"]] += row["count"]
        self.roster_series = [*self.roster_series, {
            "window": item.get("window"), "ts_ns": item.get("ts"),
            "by_kind": dict(sorted(counts.items())), "by_model": dict(sorted(models.items())),
        }][-MAX_ROWS:]

    def _on_event(self, item: dict) -> None:
        # What joined the population and when; the identity itself stays with the
        # five aggregates' rule that no assembly id is published.
        event = item.get("event", {})
        payload = event.get("payload", {})
        kind = event.get("kind")
        if kind == "Launch":
            self.launched_ns = event.get("ts_ns")
        elif kind == "Terminated":
            self.terminated_ns = event.get("ts_ns")
        elif kind == "ForecastSettled" and isinstance(payload, dict):
            self.open_forecasts.pop(str(payload.get("handle")), None)
            self._delivered("forecast", "settled")
        if kind != "Registered" or not isinstance(payload, dict):
            return
        self.registered = [*self.registered, {
            "ts_ns": event.get("ts_ns"), "registered": payload.get("kind"),
            "role": payload.get("role"),
        }][-MAX_ROWS:]

    def _on_actor_retire(self, item: dict) -> None:
        actor = str(item.get("actor", ""))
        self.retired = [*self.retired, {
            "ts_ns": item.get("ts"),
            "retired": "router" if actor.startswith("router:") else "assembly",
        }][-MAX_ROWS:]

    def _amendment(self, amendment_id) -> dict:
        record = self.amendments.get(str(amendment_id))
        if record is None:
            record = {"id": str(amendment_id), "proposed": None, "passed": None,
                      "refused": None, "activated": None, "predicted_effect": None,
                      "reason": None, "edition": None, "add": [], "replace": [], "remove": []}
            if len(self.amendments) >= MAX_ROWS:
                self.amendments.pop(next(iter(self.amendments)))
            self.amendments[str(amendment_id)] = record
        return record

    def _on_charter_propose(self, item: dict) -> None:
        record = self._amendment(item.get("id"))
        record.update(proposed=item.get("ts"), predicted_effect=item.get("predicted_effect"),
                      add=[str(card.get("id")) for card in item.get("add") or []],
                      replace=[str(card.get("id")) for card in item.get("replace") or []],
                      remove=[str(card) for card in item.get("remove") or []])

    def _on_charter_approved(self, item: dict) -> None:
        self._amendment(item.get("amendment_id"))["passed"] = item.get("ts")

    def _on_charter_activate(self, item: dict) -> None:
        record = self._amendment(item.get("amendment_id"))
        record.update(activated=item.get("ts"), edition=item.get("edition"))

    def _refuse(self, item: dict, amendment_id) -> None:
        record = self._amendment(amendment_id)
        record.update(refused=item.get("ts"), reason=str(item.get("reason", ""))[:300])

    def _on_charter_refused(self, item: dict) -> None:
        self._refuse(item, item.get("amendment_id"))

    def _on_policy_refused(self, item: dict) -> None:
        self._refuse(item, item.get("amendment_id"))

    def _on_amendment_rejected(self, item: dict) -> None:
        self._refuse(item, item.get("id"))

    def _on_wallet_commit(self, item: dict) -> None:
        reason = str(item.get("reason", ""))
        amount = item.get("amount", 0)
        if type(amount) is int:
            prefix = reason.split(":", 1)[0]
            self.money_out[prefix if prefix in OUT_CLASSES else "other"] += amount
        if not reason.startswith("model:"):
            return
        rail = rail_for_model(reason.removeprefix("model:"))
        ts = item.get("ts", 0)
        self.spend_day.setdefault(_day(ts), Counter())[rail] += item.get("amount", 0)
        self.spend_week.setdefault(_week(ts), Counter())[rail] += item.get("amount", 0)

    # --- money by class ------------------------------------------------------

    def _money_in(self, name: str, amount) -> None:
        if type(amount) is int and amount > 0:
            self.money_in[name] += amount

    def _on_wallet_initial(self, item: dict) -> None:
        self._money_in("initial", item.get("amount"))

    def _on_wallet_drip(self, item: dict) -> None:
        self._money_in("drip", item.get("amount"))

    def _on_wallet_settle(self, item: dict) -> None:
        # Signed venue effects: a gain is money in, a loss is money out, by source.
        reason, amount = str(item.get("reason", "")), item.get("amount")
        if reason not in ("exchange_pnl", "funding") or type(amount) is not int:
            return
        if amount >= 0:
            self.money_in[reason] += amount
        else:
            self.money_out[reason] += -amount

    def _on_wallet_release(self, item: dict) -> None:
        # A hold cancelled moves nothing; an endowment tranche released (edition 2)
        # does. The tranche says so with its reason; a reservation names its handle.
        if str(item.get("reason", "")) == "release":
            self._money_in("release", item.get("amount"))

    def _on_income_earned(self, item: dict) -> None:
        self._money_in("earned", item.get("micro"))

    def _on_treasury_subsidy(self, item: dict) -> None:
        self._money_in("subsidy", item.get("micro"))

    # --- deliveries and open commitments ---------------------------------------

    def _delivered(self, kind: str, status: str) -> None:
        window = self.deliveries.get(self.current_window)
        if window is None:
            window = {}
            if len(self.deliveries) >= MAX_ROWS:
                self.deliveries.pop(next(iter(self.deliveries)))
            self.deliveries[self.current_window] = window
        window.setdefault(str(kind), Counter())[str(status)] += 1

    def _on_price_window(self, item: dict) -> None:
        # A closed window ends the bucket its settlements were counted in.
        index = item.get("window")
        self.current_window = index + 1 if type(index) is int else self.current_window + 1

    def _on_decision_open(self, item: dict) -> None:
        handle = str(item.get("handle"))
        self.open_handles[handle] = {"opened_ns": item.get("opened_ns", item.get("ts")),
                                     "channel": item.get("channel"),
                                     "deadline_ns": item.get("deadline_ns")}

    def _settled_return(self, item: dict, status_override: str | None = None) -> None:
        ret = item.get("return") or {}
        if not isinstance(ret, dict):
            return
        self.open_handles.pop(str(ret.get("handle")), None)
        self._delivered(ret.get("channel", "unknown"), status_override or ret.get("status"))

    def _on_decision_settle(self, item: dict) -> None:
        self._settled_return(item)

    def _on_decision_timeout(self, item: dict) -> None:
        self._settled_return(item, "timed_out")

    def _on_forecast_seal(self, item: dict) -> None:
        self.open_forecasts[str(item.get("handle"))] = {
            "sealed_ns": item.get("ts"), "due_at_event": item.get("due_at_event"),
            "predicate": item.get("predicate_id")}

    # --- cells and state -------------------------------------------------------

    def _on_dormant(self, item: dict) -> None:
        # Tolerates the shapes a dormancy item may take: a phase field, or the
        # phase as the key that carries the timestamp.
        phase = item.get("state") or item.get("phase") or item.get("transition")
        ts = item.get("ts")
        for name in ("entered", "exited"):
            if phase is None and name in item:
                phase = name
                if type(item[name]) is int:
                    ts = item[name]
        if phase == "entered":
            self.dormant_since = ts
        elif phase == "exited":
            self.dormant_periods = [*self.dormant_periods, {
                "entered_ns": self.dormant_since, "exited_ns": ts}][-MAX_ROWS:]
            self.dormant_since = None
        # The episode list W1 publishes beside the liveness view: a fact of the
        # account, without any assembly id or position.
        self.dormancy = [*self.dormancy, {
            "state": phase, "ts_ns": ts,
            "locked_micro": item.get("locked"), "next_release_ns": item.get("next_release_ns"),
        }][-MAX_ROWS:]

    def _on_invocation(self, item: dict) -> None:
        role = item.get("role")
        self.invocations.setdefault(_day(item.get("ts", 0)), Counter())[
            role if role in ROLES else "other"
        ] += 1

    def _transfer(self, item: dict, status: str, direction, amount) -> None:
        self.transfers = [*self.transfers, {
            "ts_ns": item.get("ts"), "status": status, "direction": direction,
            "amount_micro": amount, "reason": str(item["reason"])[:200]
            if item.get("reason") else None,
        }][-MAX_ROWS:]

    def _on_treasury_confirmed(self, item: dict) -> None:
        state = item.get("state", {})
        self._transfer(item, "confirmed", state.get("direction"), state.get("amount_micro"))
        if state.get("direction") == "to_venice":
            self._money_in("converted_from_principal", state.get("received_micro"))
        fees = state.get("fees_micro")
        if type(fees) is int and fees > 0:
            self.money_out["transfer_fees"] += fees

    def _on_treasury_submitted(self, item: dict) -> None:
        state = item.get("state", {})
        self._transfer(item, "submitted", state.get("direction"), state.get("amount_micro"))

    def _on_treasury_refused(self, item: dict) -> None:
        self._transfer(item, "refused", item.get("direction"), None)

    def _window(self, index) -> dict:
        record = self.windows.get(index)
        if record is None:
            record = {"window": index, "flags": {}, "responses": []}
            if len(self.windows) >= MAX_ROWS:
                self.windows.pop(next(iter(self.windows)))
            self.windows[index] = record
        return record

    def _on_immune_window(self, item: dict) -> None:
        self._window(item.get("window"))["flags"] = item.get("flags", {})
        if isinstance(item.get("profile"), dict) and isinstance(item.get("regions"), dict):
            self.profiles = [*self.profiles, {
                "window": item.get("window"), "profile": item["profile"],
                "regions": item["regions"], "charter_edition": item.get("charter_edition"),
            }][-(MAX_ROWS + 1):]

    def _respond(self, index, response: dict) -> None:
        self._window(index)["responses"].append(response)

    def _on_immune_gain(self, item: dict) -> None:
        # The exploration rate itself is learner state: only the direction escapes.
        self._respond(item.get("window"), {"response": "router_gain",
                                           "pathology": item.get("pathology")})

    def _on_immune_decay(self, item: dict) -> None:
        self._respond(item.get("window"), {"response": "price_decay",
                                           "decay_after": item.get("decay_after")})

    def _on_immune_price_relief(self, item: dict) -> None:
        self._respond(item.get("window"), {"response": "price_relief",
                                           "card_id": item.get("card_id")})

    def _on_novelty_grant(self, item: dict) -> None:
        self._respond(item.get("window"), {"response": "novelty_grant"})

    def _cells(self, manifest) -> dict:
        """Cells from the immune organ's own per-window profiles, by the versioning module."""
        from factorylab.versioning.operator import cell_series

        cards = sorted({name for row in self.profiles for name in row["regions"]})
        try:
            series = cell_series(self.profiles, cards,
                                 registration_bins=tuple(manifest.immune.registration_bins),
                                 revision_bins=tuple(manifest.immune.revision_bins))
        except (ValueError, TypeError, KeyError):
            return {"dimensions": [], "cuts": {}, "windows": [], "transitions": 0}
        rows, previous, transitions = [], None, 0
        for row, cell in zip(self.profiles, series["cells"], strict=True):
            changed = previous is not None and cell != previous
            transitions += changed
            rows.append({"window": row["window"], "cell": list(cell), "changed": changed,
                         "charter_edition": row["charter_edition"]})
            previous = cell
        return {"dimensions": series["dimensions"], "cuts": series["cuts"],
                "windows": rows[-MAX_ROWS:], "transitions": transitions}

    def _commitments(self, now_ns: int | None) -> dict:
        def age(ts):
            return now_ns - ts if type(ts) is int and type(now_ns) is int else None

        handles = sorted(self.open_handles.items(),
                         key=lambda kv: (kv[1]["opened_ns"] is None, kv[1]["opened_ns"] or 0))
        forecasts = sorted(self.open_forecasts.items(),
                           key=lambda kv: (kv[1]["sealed_ns"] is None, kv[1]["sealed_ns"] or 0))
        return {
            "as_of_ns": now_ns,
            "handles": {
                "unsettled": len(handles),
                "oldest_age_ns": age(handles[0][1]["opened_ns"]) if handles else None,
                "rows": [{"channel": row["channel"], "age_ns": age(row["opened_ns"]),
                          "deadline_ns": row["deadline_ns"]}
                         for _, row in handles[:MAX_ROWS]],
            },
            "forecasts": {
                "pending": len(forecasts),
                "oldest_age_ns": age(forecasts[0][1]["sealed_ns"]) if forecasts else None,
                "rows": [{"predicate": row["predicate"], "age_ns": age(row["sealed_ns"]),
                          "due_at_event": row["due_at_event"]}
                         for _, row in forecasts[:MAX_ROWS]],
            },
        }

    def _liveness(self) -> dict:
        status = ("terminated" if self.terminated_ns is not None
                  else "dormant" if self.dormant_since is not None else "alive")
        return {"status": status, "launched_ns": self.launched_ns,
                "terminated_ns": self.terminated_ns, "dormant_since_ns": self.dormant_since,
                "dormant_periods": self.dormant_periods}

    def result(self, manifest, *, now_ns: int | None = None) -> dict:
        """Return the widened sections.

        Before the first window closes there is no published world block yet, so
        the roster and the charter fall back to the genesis manifest the wake has
        already authenticated. Nothing else is guessed: an unevidenced section is
        empty.
        """
        latest = self.latest or {}
        roster = latest.get("roster") or [
            {"kind": role, "model_id": model, "count": count}
            for (role, model), count in sorted(
                Counter((a.role, a.model_id) for a in manifest.assemblies).items())
        ]
        series = self.roster_series or [{
            "window": None, "ts_ns": None,
            "by_kind": dict(sorted(Counter(a.role for a in manifest.assemblies).items())),
            "by_model": dict(sorted(Counter(a.model_id for a in manifest.assemblies).items())),
        }]
        return {
            "roster": {"current": roster, "over_time": series,
                       "registered": self.registered, "retired": self.retired},
            "tools": latest.get("tools", []),
            "connectors": latest.get("connectors", {"registered": [], "calls_per_day": {}}),
            "notes": latest.get("notes", {"keys": 0, "bytes": 0}),
            "observations": latest.get("observations", []),
            "charter": {**(latest.get("charter") or _genesis_charter(manifest)),
                        "amendments": list(self.amendments.values())},
            "compute": {
                "spend_by_rail_per_day": _rails(self.spend_day),
                "spend_by_rail_per_week": _rails(self.spend_week),
                "invocations_by_kind_per_day": {day: dict(sorted(counts.items()))
                                                for day, counts in sorted(
                                                    self.invocations.items())[-MAX_ROWS:]},
            },
            "pots": {"current": latest.get("pots") or {}, "transfers": self.transfers,
                     "dormancy": self.dormancy,
                     "income": latest.get("income") or dict.fromkeys(INCOME_CLASSES)},
            "immune": list(self.windows.values()),
            "portfolio": latest.get("portfolio") or {"equity_micro": UNAVAILABLE,
                                                     "realized_to_date_micro": UNAVAILABLE},
            "money": {
                "in_by_class": {name: self.money_in.get(name, 0) for name in IN_CLASSES},
                "out_by_class": {name: self.money_out.get(name, 0) for name in OUT_CLASSES},
            },
            "deliveries": {
                # Rows, not keys: a channel name such as "verdict" is a sealed key
                # when it names a field, and here it only names a count.
                "per_window": [{"window": window,
                                "by_kind": [{"kind": kind, "counts": dict(sorted(counts.items()))}
                                            for kind, counts in sorted(kinds.items())]}
                               for window, kinds in sorted(self.deliveries.items())],
            },
            "commitments": self._commitments(now_ns),
            "cells": self._cells(manifest),
            "liveness": self._liveness(),
            "entitlements": _fold_entitlements(latest.get("entitlements")),
        }


def _genesis_charter(manifest) -> dict:
    """The launch charter, exactly as the first ledger item committed it."""
    prices = dict(getattr(manifest, "charter_prices", ()))
    charter = manifest.charter
    return {
        "edition": charter.edition,
        "norms": list(charter.norms),
        "cards": [{"id": card.id, "norm": card.norm, "observation": card.observation,
                   "answers_for": card.answers_for, "lambda": prices.get(card.id, 0.0),
                   "region": None} for card in charter.cards],
    }


def _rails(buckets: dict[str, Counter]) -> dict[str, dict[str, int]]:
    return {period: {rail: counts.get(rail, 0) for rail in RAILS}
            for period, counts in sorted(buckets.items())[-MAX_ROWS:]}


class _Snapshot(Ledger):
    """A frozen, sealed ledger supports kernel aggregates even after termination.

    Recovery and wake share the kernel's streaming verification and aggregate
    indexes. The read-only boundary remains stable when the writer appends.
    """

    def __init__(self, path: Path, manifest) -> None:
        key_path = Path(str(path) + ".key")
        mode = key_path.lstat().st_mode
        if not stat.S_ISREG(mode) or stat.S_IMODE(mode) != 0o600:
            raise LedgerIntegrityError("ledger key unavailable")
        frozen = Ledger.open_read_only(path, manifest=json.loads(manifest.canonical_json()))
        self.__dict__.update(frozen.__dict__)

    def append(self, entry: dict) -> int:
        """A wake reader cannot append evidence."""
        raise PermissionError("read-only wake")

    def timing(self, *, live: bool, now_ns: int) -> dict:
        """Only event timestamps escape; payloads, identities and kinds remain private."""
        times = self.event_times()
        start = times["first_tick"] if live else 0
        last = times["last_event"]
        end = now_ns if live and not times["terminated"] else last
        uptime = max(0, end - start) if start is not None and end is not None else 0
        return {"uptime_ns": uptime, "last_event_time_ns": last}

    def public_aggregates(self, manifest, observatory=None) -> dict:
        """Only role totals escape identity-bearing views, including new assemblies.

        An optional observatory reads the same authenticated stream once, so the
        widened sections cost no second decryption pass over the diary.
        """
        aggregates = {view: self.aggregate(view) for view in VIEWS}
        roles = {a.id: a.role for a in manifest.assemblies}
        allowed = {"producer", "evaluator", "meta", "antagonist"}
        # Streaming projection avoids materialising the item diary. Identities
        # are only join keys here; unknown provenance never becomes public text.
        for item in self.items():
            if observatory is not None:
                observatory.feed(item)
            if item.get("kind") == "event":
                event = item.get("event", {})
                payload = event.get("payload", {})
                if event.get("kind") == "Registered" and payload.get("kind") == "assembly":
                    roles[payload["id"]] = payload.get("role", "other")
            elif item.get("kind") == "invocation" and item.get("role") in allowed:
                roles.setdefault(item["assembly_id"], item["role"])
        for view, field in (("invocations_by_assembly", "counts"), ("action_frequencies", "counts"),
                            ("spend_by_capability", "spend")):
            totals = Counter()
            for name, value in aggregates[view][field].items():
                role = roles.get(name, "noop" if name == "NOOP" else "other")
                totals[role if role in allowed | {"noop"} else "other"] += value
            aggregates[view] = {field: dict(sorted(totals.items()))}
        return aggregates


def _open_snapshot(path: Path):
    # Match the public genesis to an installed manifest, never a summary or diary.
    with path.open("rb") as stream:
        header = json.loads(stream.readline())
    for manifest_path in sorted(WORLDS_DIR.glob("*.toml")):
        manifest = load_manifest(str(manifest_path))
        genesis = hashlib.sha256(canonical(
            {"manifest": json.loads(manifest.canonical_json())},
        )).hexdigest()
        if header == {"format": 1, "genesis_hash": genesis}:
            return _Snapshot(path, manifest), manifest
    raise LedgerIntegrityError("manifest unavailable")


def _micro(value: Decimal) -> int:
    return int(value * 1_000_000)


def _realized(exchange) -> int | str:
    # The venue retains at most 10,000 fills. Never label a truncated sum all-time.
    start, count, total = 0, 0, Decimal(0)
    while True:
        rows = exchange._guarded(
            "wake fills", lambda start=start: exchange._info.user_fills_by_time(
                exchange._address, start,
            ),
        )
        count += len(rows)
        if count >= 10_000:
            return UNAVAILABLE
        if len(rows) < 2000:
            total += sum((Decimal(str(row["closedPnl"])) for row in rows), Decimal(0))
            return _micro(total)
        # Repeat the boundary millisecond so simultaneous fills cannot be lost.
        boundary = max(int(row["time"]) for row in rows)
        if boundary <= start:
            return UNAVAILABLE
        total += sum((Decimal(str(row["closedPnl"])) for row in rows
                      if int(row["time"]) < boundary), Decimal(0))
        start = boundary


def _venue(manifest) -> dict:
    from factorylab.world.exchange import live_exchange

    result = {"equity_micro": UNAVAILABLE,
              "realized_to_date_micro": UNAVAILABLE}
    try:
        exchange = live_exchange(manifest.exchange)
        account = exchange.account()
        result.update(equity_micro=_micro(account.equity_usd))
        result["realized_to_date_micro"] = _realized(exchange)
    except Exception:
        pass  # Provider exceptions can contain credentials or response bodies.
    return result


def _reserve() -> dict:
    from factorylab.world.x402 import X402Client

    result = {"usdc_micro": UNAVAILABLE, "venice_micro": UNAVAILABLE}
    try:
        client = X402Client()
    except Exception:
        return result
    readers = (("usdc_micro", client.usdc_balance), ("venice_micro", client.venice_balance))
    for field, read in readers:
        try:
            result[field] = read()
        except Exception:
            pass
    return result


def collect_wake(path: str | Path, *, now_ns: int | None = None, sleep=time.sleep) -> dict:
    """Exactly the allowlist escapes; a failed chain is retried once, never partially shown."""
    result = dict.fromkeys((*VIEWS, *SECTIONS, "world", "manifest_hash", "uptime_ns",
                            "last_event_time_ns"), UNAVAILABLE)
    manifest = None
    for attempt in range(2):
        try:
            ledger, candidate = _open_snapshot(Path(path))
            observatory = _Observatory()
            aggregates = ledger.public_aggregates(candidate, observatory)
            live = candidate.exchange.kind != "fake"
            timing = ledger.timing(live=live,
                                   now_ns=time.time_ns() if now_ns is None else now_ns)
            # Ages of open commitments are measured against the clock the world
            # keeps: wall time while it lives, event time once it does not.
            as_of = (time.time_ns() if now_ns is None else now_ns) if live and not (
                observatory.terminated_ns is not None) else timing["last_event_time_ns"]
            result.update(aggregates, **observatory.result(candidate, now_ns=as_of),
                          **timing, world=candidate.name,
                          manifest_hash=candidate.manifest_hash())
            manifest = candidate
            break
        except (LedgerIntegrityError, InvalidToken, OSError, ValueError, KeyError, TypeError):
            if attempt == 0:
                sleep(0.1)
    # An account is published only when this world owns it. A key in the
    # environment says what the host can reach, not what the world is: reading
    # the live venue for a fake world put the architect's real equity on the
    # page beside the world's own, two contradictory figures on one page.
    live_venue = manifest is not None and manifest.exchange.kind == "hyperliquid"
    configured_reserve = (manifest is not None
                          and getattr(manifest.treasury, "reserve_address", None) is not None)
    if os.environ.get("HL_PRIVATE_KEY"):
        result["venue"] = _venue(manifest) if live_venue else {
            "equity_micro": UNAVAILABLE,
            "realized_to_date_micro": UNAVAILABLE,
        }
    if os.environ.get("RESERVE_PRIVATE_KEY"):
        result["reserve"] = _reserve() if configured_reserve else {
            "usdc_micro": UNAVAILABLE,
            "venice_micro": UNAVAILABLE,
        }
    return result


def _chart(wallet) -> str:
    if not isinstance(wallet, dict) or not wallet["series"]:
        return "<p>unavailable</p>"
    series = wallet["series"]
    low, high = min(p["balance"] for p in series), max(p["balance"] for p in series)
    first, last = series[0]["ts"], series[-1]["ts"]
    points = " ".join(
        f'{20 + (p["ts"] - first) * 760 // max(1, last - first)},'
        f'{180 - (p["balance"] - low) * 150 // max(1, high - low)}' for p in series
    )
    return (
        '<svg viewBox="0 0 800 200" role="img" aria-label="Wallet balance in micro-USD">'
        f'<title>Wallet balance: {low} to {high} micro-USD</title>'
        f'<polyline points="{points}" fill="none" stroke="currentColor" stroke-width="3"/>'
        f'</svg><p>{low} – {high} micro-USD</p>'
    )


def render_wake(data: dict) -> str:
    """The page is self-contained, script-free and escapes every dynamic text value."""
    sections = []
    order = (
        "world", "manifest_hash", "uptime_ns", "last_event_time_ns", "venue", "reserve",
        "portfolio", "pots", "entitlements", "liveness", "money", "roster", "tools", "connectors",
        "notes",
        "observations", "charter", "compute", "deliveries", "commitments", "cells", "immune",
        *VIEWS,
    )
    folded = {"wallet_series": "Balance series", "roster": "Roster", "tools": "Tools",
              "connectors": "Connectors", "notes": "Notes", "observations": "Observations",
              "charter": "Charter and amendments",
              "compute": "Compute", "immune": "Windows", "pots": "Pots and transfers",
              "money": "Money in and out by class", "deliveries": "Deliveries per window",
              "commitments": "Open commitments", "cells": "Behavioural cells",
              "liveness": "Alive, dormant or terminated",
              "entitlements": "Entitlements"}
    for field in order:
        if field not in data:
            continue
        value = data[field]
        chart = _chart(value) if field == "wallet_series" else ""
        body = '<pre>' + html.escape(json.dumps(value, indent=2, ensure_ascii=False)) + '</pre>'
        if field in folded:
            body = (f'<details><summary>{html.escape(folded[field])}</summary>'
                    f'{body}</details>')
        sections.append(f'<section><h2>{html.escape(field)}</h2>{chart}{body}</section>')
    return '''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>Factory wake</title><style>
:root{color-scheme:dark;font:16px/1.5 system-ui,sans-serif;background:#10151b;color:#e7edf4}
body{max-width:960px;margin:auto;padding:24px}h1{font-size:2rem}h2{font-size:1rem;color:#aedacb}
main{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,350px),1fr));gap:16px}
section{min-width:0;padding:16px;border:1px solid #34404d;border-radius:12px}
pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:.85rem}svg{width:100%;color:#91e0bf}
text{fill:currentColor;font-size:16px}@media(max-width:480px){body{padding:12px}}
</style></head><body><h1>Factory wake</h1><main>''' + "".join(sections) + '</main></body></html>'


def write_wake(path: str | Path, out: str | Path) -> dict:
    """Each public artifact replaces its predecessor atomically; no private bytes are written."""
    data = collect_wake(path)
    directory = Path(out)
    directory.mkdir(parents=True, exist_ok=True)
    for name, body in (("wake.json", json.dumps(data, indent=2) + "\n"),
                       ("wake.html", render_wake(data))):
        fd, temporary = tempfile.mkstemp(prefix=".wake-", dir=directory)
        try:
            with os.fdopen(fd, "w") as stream:
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
                os.fchmod(stream.fileno(), 0o644)
            os.replace(temporary, directory / name)
        finally:
            Path(temporary).unlink(missing_ok=True)
    return data
