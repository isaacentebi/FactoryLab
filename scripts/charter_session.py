"""One charter-time session: the population drafts, a sortition adopts, λ meets the dollar.

Chapter II §IV.a: "if you hand it a norm, it will propose a metric to represent that
norm". The architect supplies norms only (charter audit S1). This script replaces
``draft_edition1.py``, ``ratify_charter.py`` and ``adopt_charter.py`` (U4) with one
sequence:

  (a) draft   every seed assembly, through its own model tier, proposes metric cards
              (and optionally a starting λ) for the manifest's norms, given the card
              contract and the public world block, never the existing cards. Every
              proposal is preflighted through the runtime's measurement contract; a
              stratified sortition votes yes or no on each; a strict majority passes
              a card. Passing cards that bind an observation twice for one role are
              refused by the same preflight the runtime applies to amendments.
  (b) adopt   a fresh sortition votes on the drafted charter whole: adopt or reject.
              There is no "select a subset, unchanged" mode.
  (c) export  an adopted charter is written as TOML with typed regions, headed by the
              roster and content digests the load path verifies.
  (d) report  λ against dollars, from rehearsal diaries (charter audit M5): per card
              and per window, the λ it closed at beside the margins the world
              measured across its scopes, reward and micro-USD per unit of
              violation, recomputed from the diary's price.margin points with the
              runtime's own function.

The ballots carry the λ-to-dollar report when diaries are supplied, so the
correlation between the charter's prices and its material cost is before the
population at charter time (essay II.IV).

It makes live model calls through the rehearsal's prepaid provider, under
``--cap-usd``. ``--dry-run`` swaps in a scripted provider, so the whole sequence runs
offline and deterministically. The card contract the ballots refer to is the world's
own ``world.mechanics.committee.card_contract``. Nothing here touches a wallet, a venue
or a ledger.

    uv run python scripts/charter_session.py session --world W --out-dir D [--diary E]
    uv run python scripts/charter_session.py report --diary work/.../events.json
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import tomllib
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from factorylab.charter.amendment import proposed_price  # noqa: E402
from factorylab.charter.book import validate_observation_bindings  # noqa: E402
from factorylab.charter.charter import Charter, MetricCard  # noqa: E402
from factorylab.charter.committee import Seat, draw  # noqa: E402
from factorylab.charter.market import margin  # noqa: E402
from factorylab.charter.measurement import (  # noqa: E402
    measurement_catalogue,
    preflight_measurement,
)
from factorylab.charter.provenance import charter_digest, norms_raw, roster_hash  # noqa: E402
from factorylab.charter.region import region_schema  # noqa: E402
from factorylab.charter.windows import window_schema  # noqa: E402
from factorylab.cortex.assembly import SEED_SYSTEM_PROMPT, _parse_json_object  # noqa: E402
from factorylab.world.models import ModelRequest, ModelResponse  # noqa: E402

MAX_TOKENS = 1500
VOTE_MAX_TOKENS = 4000
CARD_FIELDS = ("id", "norm", "description", "units", "window", "region", "observation",
               "answers_for")
# --- the provider -------------------------------------------------------------------

class ManifestCatalogue:
    """A provider that answers ``catalogue()`` from the manifest's own model table.

    A world whose seats size their completions natively ("provider" max_tokens,
    edition 5 and 6) needs each model's completion limit before its runtime can
    build a world block. The limits here are the manifest's, never a network read.
    """

    name = "manifest-catalogue"

    def __init__(self, manifest) -> None:
        self.manifest = manifest

    def catalogue(self) -> list:
        from factorylab.world.models import CatalogueEntry

        rows = []
        for model in self.manifest.models:
            rows.append(CatalogueEntry(
                id=model.id, name=model.id,
                prompt_usd_per_token=str(float(model.input_usd_per_mtok) / 1_000_000),
                completion_usd_per_token=str(float(model.output_usd_per_mtok) / 1_000_000),
                context_length=200_000, max_completion_tokens=100_000))
        return rows

    def complete(self, req: ModelRequest) -> ModelResponse:
        raise RuntimeError("the manifest catalogue answers no completion")


class ScriptedCharterProvider(ManifestCatalogue):
    """A deterministic stand-in population for ``--dry-run``: plumbing, not behaviour.

    Each proposer offers one card on the next norm, over the next observation of a
    fixed rotation, so a run exercises preflight, the per-card ballot, a duplicate
    binding refused, the whole-charter ballot and the export without a model call.
    """

    name = "scripted-charter"
    ROTATION = (
        ("well_formed_rate", {"kind": "returns", "n": 20, "per": "role"},
         {"rule": "at least", "lo": 0.9}, "all", "fraction"),
        ("cost_per_attempt", {"kind": "returns", "n": 20, "per": "role"},
         {"rule": "at most", "hi": 5000}, "producer", "micro-USD per attempt"),
        ("censored_share", {"kind": "forecasts", "n": 10, "per": "assembly"},
         {"rule": "at most", "hi": 0.3}, "evaluator", "fraction"),
        ("tool_calls", {"kind": "returns", "n": 10, "per": "role"},
         {"rule": "at most", "hi": 3}, "all", "count per return"),
    )

    def __init__(self, manifest) -> None:
        super().__init__(manifest)
        self.proposals = 0

    def complete(self, req: ModelRequest) -> ModelResponse:
        text = "\n".join(str(m.get("content", "")) for m in req.messages)
        inputs = _section(text, "INPUTS")
        if text.startswith("REQUEST\nPropose"):
            norms = inputs.get("norms") or ["norm"]
            observation, window, region, scope, units = self.ROTATION[
                self.proposals % len(self.ROTATION)]
            norm = norms[self.proposals % len(norms)]
            norm = norm["id"] if isinstance(norm, dict) else norm
            self.proposals += 1
            reply = {"cards": [{"id": f"{observation.replace('_', '-')}-bound",
                                "norm": norm, "description": f"{observation} held to a region",
                                "units": units, "window": window, "region": region,
                                "observation": observation, "answers_for": scope,
                                "lambda": 0.1, "reason": "scripted proposal"}]}
        elif text.startswith("REQUEST\nVote yes or no"):
            reply = {"votes": [{"proposal": p["proposal"], "vote": True,
                                "reason": "scripted yes"} for p in inputs.get("proposals", [])]}
        else:
            reply = {"adopt": True, "reason": "scripted adoption"}
        body = json.dumps(reply)
        return ModelResponse(req.model_id, body, len(text) // 4, len(body) // 4, "stop",
                             cost_micro=0)


def _section(text: str, header: str) -> dict:
    """The JSON a prompt carries under one header, or an empty mapping."""
    start = text.find(f"{header}\n")
    if start < 0:
        return {}
    start += len(header) + 1
    end = text.find("\n\nOUTCOME SCHEMA", start)
    try:
        return json.loads(text[start:end if end >= 0 else None])
    except json.JSONDecodeError:
        return {}


def _prompt(description: str, inputs: dict, schema: dict) -> str:
    """Render like ``Request.prompt_text`` so the population sees its usual shape."""
    return "\n\n".join([f"REQUEST\n{description}",
                        f"INPUTS\n{json.dumps(inputs, sort_keys=True, indent=2)}",
                        f"OUTCOME SCHEMA\n{json.dumps(schema, sort_keys=True, indent=2)}",
                        "COMPLETION CRITERION\na JSON object satisfying the outcome schema"])


@dataclass
class Call:
    """One completion and what it cost; ``error`` is set when it yielded nothing usable."""

    assembly_id: str
    role: str
    model_id: str
    purpose: str
    cost_micro: int = 0
    stop_reason: str = ""
    error: str | None = None
    text: str = ""


def complete(provider: Any, spec: Any, purpose: str, text: str, calls: list[Call],
             max_tokens: int = MAX_TOKENS) -> dict | None:
    """One completion through the seat's own tier; a failure is recorded, never raised."""
    call = Call(spec.id, spec.role, spec.model_id, purpose)
    calls.append(call)
    req = ModelRequest(spec.model_id, SEED_SYSTEM_PROMPT, ({"role": "user", "content": text},),
                       max_tokens=max_tokens, effort=spec.effort, json_object=True)
    try:
        response = provider.complete(req)
    except Exception as exc:  # a provider error is evidence, not a crash
        call.error = f"{type(exc).__name__}: {exc}"[:600]
        return None
    call.cost_micro = response.cost_micro or 0
    call.stop_reason, call.text = response.stop_reason, response.text
    parsed = _parse_json_object(response.text)
    if response.stop_reason != "stop" or not isinstance(parsed, dict):
        call.error = f"malformed or incomplete reply (stop_reason={response.stop_reason})"
        return None
    return parsed


# --- (a) drafting ---------------------------------------------------------------------

@dataclass
class Proposal:
    """A proposed card and its fate; the proposer's identity never reaches a voter."""

    key: str
    proposer: str
    raw: dict
    card: MetricCard | None
    price: float | None
    problem: str | None = None
    ballots: dict[str, bool | None] = field(default_factory=dict)


def survey_world(world: dict) -> dict:
    """The public world block, without the existing cards or the registration menus."""
    return {k: v for k, v in world.items() if k not in (
        "charter", "card_prices", "amendment_feedback", "a_return_may_include",
        "proposal_shapes", "stable_prefix")}


def launch_world(manifest) -> dict:
    """The runtime's own world block over a fake venue: launch-shaped public facts.

    Built on the manifest's catalogue, so it makes no call and reads no network,
    whichever provider the session's ballots then use.
    """
    from factorylab.runtime.loop import Runtime
    from factorylab.world.exchange import FakeExchange

    rt = Runtime(manifest, events=1, seed=None, initial_balance_micro=None, ledger_path=None,
                 router_gamma=0.1, provider=ManifestCatalogue(manifest),
                 exchange=FakeExchange(seed=manifest.exchange.seed, coins=manifest.exchange.coins,
                                       start_cash_usd=manifest.exchange.start_cash_usd))
    return rt._world_block()


def card_from(raw: dict, norms: tuple) -> tuple[MetricCard | None, float | None, str | None]:
    """The executable card a proposal states, its starting price, or the preflight refusal.

    A price has no bound of its own (wave 16, ruling R-E): the one bound is
    ``prices.penalty_cap``, on the penalty, which the controller enforces.
    """
    missing = [f for f in CARD_FIELDS if raw.get(f) in (None, "")]
    if missing:
        return None, None, f"missing {', '.join(missing)}"
    if str(raw["norm"]) not in [str(n) for n in norms]:
        return None, None, f"norm {raw['norm']!r} is not one of the charter's norms"
    try:
        card = MetricCard(**{f: raw[f] for f in CARD_FIELDS})
        if card.rule is None:
            raise ValueError("region has no rule")
        preflight_measurement(card)
        price = (None if raw.get("lambda") is None
                 else proposed_price(raw["lambda"]))
    except (ValueError, TypeError) as exc:
        return None, None, str(exc)[:300]
    return card, price, None


def draft(manifest, provider, world: dict, rng: random.Random,
          calls: list[Call]) -> tuple[list[Proposal], list[Seat]]:
    """(a) Every seat proposes; a sortition votes on each proposal; a strict majority passes."""
    norms = manifest.charter.norms
    card_schema = {"type": "object", "properties": {
        **{f: {"type": "string"} for f in CARD_FIELDS if f not in ("window", "region")},
        "window": window_schema(), "region": region_schema(),
        "norm": {"enum": [str(n) for n in norms]}, "reason": {"type": "string"},
        "lambda": {"type": "number", "minimum": 0}},
        "required": [*CARD_FIELDS, "reason"]}
    text = _prompt(
        "Propose metric cards for the charter. The norms are fixed; the cards are the "
        "population's to write. Give one sentence of reason per card.",
        {"norms": norms_raw(norms), "measurable_today": measurement_catalogue(),
         "world": survey_world(world)},
        {"type": "object", "properties": {"cards": {"type": "array", "items": card_schema}},
         "required": ["cards"]})
    proposals: list[Proposal] = []
    for spec in manifest.assemblies:
        parsed = complete(provider, spec, "propose", text, calls)
        for raw in (parsed or {}).get("cards") or []:
            if not isinstance(raw, dict):
                continue
            card, price, problem = card_from(raw, norms)
            proposals.append(Proposal(f"p{len(proposals) + 1:02d}", spec.id, raw, card, price,
                                      problem))
    voted = [p for p in proposals if p.problem is None]
    seats = list(draw({a.id: a.role for a in manifest.assemblies}, rng,
                      size=manifest.committee.seats))
    if voted:
        ballot = _prompt(
            "Vote yes or no on each proposed metric card, with a reason for each vote. A "
            f"card passes with {len(seats) // 2 + 1} yes votes of {len(seats)}.",
            {"proposals": [{"proposal": p.key, **_card_row(p.card), **(
                {"lambda": p.price} if p.price is not None else {})} for p in voted],
             "norms": norms_raw(norms), "world": survey_world(world)},
            {"type": "object", "properties": {"votes": {"type": "array", "items": {
                "type": "object", "properties": {
                    "proposal": {"enum": [p.key for p in voted]}, "vote": {"type": "boolean"},
                    "reason": {"type": "string"}},
                "required": ["proposal", "vote", "reason"]}}}, "required": ["votes"]})
        specs = {a.id: a for a in manifest.assemblies}
        for seat in seats:
            parsed = complete(provider, specs[seat.assembly_id], "vote", ballot, calls,
                              VOTE_MAX_TOKENS)
            votes = {v.get("proposal"): v.get("vote") for v in (parsed or {}).get("votes") or []
                     if isinstance(v, dict)}
            for p in voted:
                vote = votes.get(p.key)
                p.ballots[seat.alias] = vote if isinstance(vote, bool) else None
    threshold = len(seats) // 2 + 1
    passing: list[MetricCard] = []
    for p in voted:
        if sum(v is True for v in p.ballots.values()) < threshold:
            p.problem = "did not win a majority"
            continue
        try:
            # The runtime's own refusal of a second binding of one observation for one
            # role: physics, the same preflight every amendment meets.
            validate_observation_bindings((*passing, p.card))
        except ValueError as exc:
            p.problem = f"preflight: {exc}"[:300]
            continue
        taken = {c.id for c in passing}
        card_id, n = p.card.id, 2
        while card_id in taken:
            card_id, n = f"{p.card.id}-{n}", n + 1
        p.card = replace(p.card, id=card_id)
        passing.append(p.card)
    return proposals, seats


def _card_row(card: MetricCard) -> dict:
    """A card as its table states it: typed region, window without an absent interval."""
    row = asdict(card)
    row["region"] = card.rule.as_dict()
    row["window"] = {k: v for k, v in row["window"].items() if v is not None or k == "per"}
    if not row["holdout"]:
        del row["holdout"]
    return row


# --- (b) whole-charter adoption, (c) export ------------------------------------------

def adoption_vote(response) -> tuple[bool, str]:
    """Only a complete boolean ballot with a reason counts; truncation is abstention."""
    raw = _parse_json_object(response.text)
    if (response.stop_reason != "stop" or not isinstance(raw, dict)
            or type(raw.get("adopt")) is not bool
            or not isinstance(raw.get("reason"), str) or not raw["reason"].strip()):
        raise ValueError("invalid or incomplete adoption ballot")
    return raw["adopt"], raw["reason"]


def approved(calls: list[dict], seats) -> bool:
    """A strict majority of distinct drawn seats must explicitly approve the whole charter."""
    expected = {seat.assembly_id for seat in seats}
    actual = [row["seat"]["assembly_id"] for row in calls]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValueError("ballots must cover each drawn seat exactly once")
    return sum(row.get("valid") is True and row.get("adopt") is True
               for row in calls) > len(seats) // 2


def render_toml(cards: list[tuple[MetricCard, float | None]], norms) -> str:
    """The charter table, typed regions and all, exactly as the load path reads it."""
    def value(v: Any) -> str:
        if isinstance(v, dict):
            return "{ " + ", ".join(f"{k} = {value(x)}" for k, x in v.items()
                                    if x is not None) + " }"
        return json.dumps(v, ensure_ascii=False)

    rows = norms_raw(norms)
    lines = ["[charter]", "edition = 1", "norms = [",
             *(f"  {value(row)}," for row in rows), "]", ""]
    for card, price in cards:
        lines.append("[[charter.cards]]")
        for key, item in _card_row(card).items():
            lines.append(f"{key} = {value(item)}")
        if price is not None:
            lines.append(f"lambda = {price}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def adopt(manifest, provider, charter_table: dict, world: dict, rng: random.Random,
          report: dict | None, calls: list[Call]) -> tuple[list[Seat], list[dict]]:
    """(b) A fresh sortition votes on the charter whole: adopt or reject."""
    seats = list(draw({a.id: a.role for a in manifest.assemblies}, rng,
                      size=manifest.committee.seats))
    specs = {a.id: a for a in manifest.assemblies}
    text = _prompt(
        "Vote on adopting this charter whole as edition 1. The vote is adopt or reject; "
        "it does not edit or select cards. Give one short reason.",
        {"charter": charter_table, "world": survey_world(world),
         **({"lambda_dollars": report} if report else {})},
        {"type": "object", "properties": {"adopt": {"type": "boolean"},
                                          "reason": {"type": "string"}},
         "required": ["adopt", "reason"]})
    ballots = []
    for seat in seats:
        spec = specs[seat.assembly_id]
        call = Call(spec.id, spec.role, spec.model_id, "adopt")
        calls.append(call)
        row = {"seat": seat._asdict(), "model": spec.model_id}
        try:
            response = provider.complete(ModelRequest(
                spec.model_id, SEED_SYSTEM_PROMPT, ({"role": "user", "content": text},),
                max_tokens=MAX_TOKENS, effort=spec.effort, json_object=True))
            call.cost_micro, call.stop_reason = response.cost_micro or 0, response.stop_reason
            vote, reason = adoption_vote(response)
            row.update(valid=True, adopt=vote, reason=reason)
        except Exception as exc:  # an unusable ballot is an abstention, recorded
            call.error = f"{type(exc).__name__}: {exc}"[:600]
            row.update(valid=False, adopt=None, failure=type(exc).__name__)
        ballots.append(row)
    return seats, ballots


# --- (d) λ against dollars --------------------------------------------------------------

def lambda_report(events: list[dict]) -> dict:
    """Per card and per window: λ beside the margins the world measured, from a diary.

    Charter audit M5, one computation with the runtime: each ``price.margin`` row
    carries a window's anonymous per-scope points, and the margins are recomputed
    from them by ``charter.market.margin``, the same function the runtime ledgered
    them with (and scores λ posts against): ``marginal_consequence`` (reward per unit
    of violation) and ``micro_usd_per_violation`` (compute spent per unit of
    violation). For each card the report states the correlation of λ with its
    marginal dollars over the windows where the margin was identified. A diary
    written before ``price.margin`` existed has no windows.
    """
    cards: dict[str, dict] = {}
    for e in events:
        if e.get("kind") != "price.margin":
            continue
        row = margin(e.get("points") or [])
        cards.setdefault(e["card_id"], {"windows": []})["windows"].append({
            "window": e["window"], "lambda": e.get("lambda"),
            "marginal_consequence": row["slope"],
            "micro_usd_per_violation": row["micro_usd_per_violation"],
            "scopes": row["scopes"]})
    for row in cards.values():
        identified = [w for w in row["windows"] if w["micro_usd_per_violation"] is not None]
        row["identified_windows"] = len(identified)
        row["lambda_usd_correlation"] = _pearson(
            [(w["lambda"], w["micro_usd_per_violation"]) for w in identified
             if w["lambda"] is not None])
    return {"source": "price.margin",
            "windows": len({w["window"] for row in cards.values() for w in row["windows"]}),
            "cards": cards}


def _pearson(pairs: list[tuple[float, float]]) -> float | None:
    """The sample correlation, or None with fewer than three points or no variance."""
    if len(pairs) < 3:
        return None
    xs, ys = zip(*pairs, strict=True)
    try:
        return statistics.correlation(xs, ys)
    except statistics.StatisticsError:
        return None


def report_diaries(paths: list[Path]) -> dict:
    """(d) The λ-to-dollar report of each diary, by path."""
    out = {}
    for path in paths:
        text = path.read_text()
        events = (json.loads(text) if text.lstrip().startswith("[")
                  else [json.loads(line) for line in text.splitlines() if line.strip()])
        out[str(path)] = lambda_report(events)
    return out


# --- the session ----------------------------------------------------------------------

def session(manifest, provider, out_dir: Path, *, diaries: list[Path], seed: int | None,
            world: dict | None = None) -> dict:
    """(a) → (b) → (c), with (d) on the ballot when diaries are given; evidence is written."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in ("charter.toml", "session.json"):
        if (out_dir / name).exists():
            raise FileExistsError(out_dir / name)
    world = world if world is not None else launch_world(manifest)
    rng = random.Random(manifest.seed if seed is None else seed)
    calls: list[Call] = []
    report = report_diaries(diaries) if diaries else None
    proposals, draft_seats = draft(manifest, provider, world, rng, calls)
    passing = [(p.card, p.price) for p in proposals if p.problem is None]
    evidence: dict[str, Any] = {
        "world": manifest.name, "roster_sha256": roster_hash(manifest),
        "norms": norms_raw(manifest.charter.norms),
        "draft": {"seats": [s._asdict() for s in draft_seats], "proposals": [
            {"key": p.key, "proposer": p.proposer, "raw": p.raw, "price": p.price,
             "problem": p.problem, "ballots": p.ballots,
             **({"card": _card_row(p.card)} if p.card is not None else {})}
            for p in proposals]},
        "lambda_dollars": report, "approved": False,
    }
    if passing:
        body = render_toml(passing, manifest.charter.norms)
        table = tomllib.loads(body)["charter"]
        Charter(1, manifest.charter.norms, tuple(card for card, _ in passing))
        seats, ballots = adopt(manifest, provider, table, world, rng, report, calls)
        evidence["adopt"] = {"seats": [s._asdict() for s in seats], "ballots": ballots}
        evidence["charter_sha256"] = charter_digest(table)
        evidence["approved"] = approved(ballots, seats)
        if evidence["approved"]:
            with (out_dir / "charter.toml").open("x") as stream:
                stream.write(f"# roster_sha256 = {evidence['roster_sha256']}\n")
                stream.write(f"# charter_sha256 = {evidence['charter_sha256']}\n")
                stream.write(body)
    evidence["calls"] = [asdict(c) for c in calls]
    evidence["cost_micro"] = sum(c.cost_micro for c in calls)
    (out_dir / "session.json").write_text(json.dumps(evidence, indent=2, default=str) + "\n")
    return evidence


def prepaid_provider(manifest, cap_usd: str):
    """The provider a rehearsal uses: prepaid rails behind an admission cap.

    ``scripts/edition4_rehearsal.py``'s own construction, so the session's calls are
    bounded and billed exactly as a rehearsal's are.
    """
    from decimal import Decimal

    from scripts import edition4_rehearsal as rehearsal

    admission = rehearsal.Admission(cap_micro=int(Decimal(cap_usd) * 1_000_000),
                                    max_calls=10_000, recover_provider_failures=True)
    return rehearsal.PrepaidProvider(rehearsal.build_prepaid_provider(manifest), manifest,
                                     admission)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("session", help="draft, adopt and export a charter")
    run.add_argument("--world", required=True)
    run.add_argument("--out-dir", type=Path, required=True)
    run.add_argument("--diary", type=Path, action="append", default=[])
    run.add_argument("--seed", type=int, default=None)
    run.add_argument("--dry-run", action="store_true",
                     help="a scripted provider instead of model calls")
    run.add_argument("--cap-usd", default="2",
                     help="the prepaid admission cap on the live path's model bill")
    rep = sub.add_parser("report", help="the λ-to-dollar report from rehearsal diaries")
    rep.add_argument("--diary", type=Path, action="append", required=True)
    args = parser.parse_args(argv)
    if args.command == "report":
        print(json.dumps(report_diaries(args.diary), indent=2))
        return 0
    from factorylab.runtime.worlds import load_manifest

    manifest = load_manifest(args.world)
    if args.dry_run:
        provider = ScriptedCharterProvider(manifest)
    else:
        provider = prepaid_provider(manifest, args.cap_usd)
    evidence = session(manifest, provider, args.out_dir, diaries=args.diary, seed=args.seed)
    print(json.dumps({k: evidence.get(k) for k in ("world", "approved", "charter_sha256",
                                                    "roster_sha256", "cost_micro")}))
    return 0 if evidence["approved"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
