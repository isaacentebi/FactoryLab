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
            "immune", "portfolio")
UNAVAILABLE = "unavailable"
PUBLIC_KIND = "wake.public"
#: Every observatory list is bounded so one page cannot grow with the diary.
MAX_ROWS = 200
ROLES = ("producer", "evaluator", "meta", "antagonist")
RAILS = ("openrouter", "venice", "x402")


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
                 "complete": pots.get("complete")},
        "portfolio": {
            "equity_micro": pots.get("venue"),
            "realized_to_date_micro": rt.realized_to_date,
        },
    }


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
        if event.get("kind") != "Registered" or not isinstance(payload, dict):
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
        if not reason.startswith("model:"):
            return
        rail = rail_for_model(reason.removeprefix("model:"))
        ts = item.get("ts", 0)
        self.spend_day.setdefault(_day(ts), Counter())[rail] += item.get("amount", 0)
        self.spend_week.setdefault(_week(ts), Counter())[rail] += item.get("amount", 0)

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

    def result(self, manifest) -> dict:
        """Return the eight widened sections.

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
            "pots": {"current": latest.get("pots") or {}, "transfers": self.transfers},
            "immune": list(self.windows.values()),
            "portfolio": latest.get("portfolio") or {"equity_micro": UNAVAILABLE,
                                                     "realized_to_date_micro": UNAVAILABLE},
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
            timing = ledger.timing(live=candidate.exchange.kind != "fake",
                                   now_ns=time.time_ns() if now_ns is None else now_ns)
            result.update(aggregates, **observatory.result(candidate), **timing,
                          world=candidate.name, manifest_hash=candidate.manifest_hash())
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
        "portfolio", "pots", "roster", "tools", "connectors", "notes", "observations", "charter",
        "compute", "immune",
        *VIEWS,
    )
    folded = {"wallet_series": "Balance series", "roster": "Roster", "tools": "Tools",
              "connectors": "Connectors", "notes": "Notes", "observations": "Observations",
              "charter": "Charter and amendments",
              "compute": "Compute", "immune": "Windows", "pots": "Pots and transfers"}
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
