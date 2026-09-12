"""One-off: let the seed population draft the metric cards for charter edition 1.

The four norms are the architect's and stay fixed. Each seed assembly in the
manifest is asked, through its own model tier, for up to three metric cards it
would put in edition 1, given only the norms, the current seed cards, what a
card is, what the runtime can measure today, and the public world facts. A
committee of five is then drawn by lot across roles and votes yes/no on the
union of proposals; three of five passes. The result is written to
``docs/charter/edition1-draft.md`` with every proposal, every vote, the
passing set as TOML, and a note on what the runtime cannot measure yet.

Run from a directory holding ``openrouter.key`` (the CLI's loader reads it):

    uv run python scripts/draft_edition1.py [--world testnet] [--out PATH]

Costs real money (cents). Nothing here touches the wallet, the venue or the
ledger; it is a survey of the population, not a run of the world.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from factorylab.charter.charter import SEED_NORMS, Charter, MetricCard, seed_charter
from factorylab.charter.committee import Ballot, Committee, Seat, draw
from factorylab.cortex.assembly import SEED_SYSTEM_PROMPT, _parse_json_object
from factorylab.runtime.cards import parses
from factorylab.runtime.cli import _load_dotenv
from factorylab.runtime.live import build_provider
from factorylab.runtime.loop import Runtime
from factorylab.runtime.worlds import WorldManifest, load_manifest
from factorylab.world.exchange import FakeExchange
from factorylab.world.models import ModelRequest, ModelResponse, PriceTable
from factorylab.world.scripted import ScriptedProvider

REPO = Path(__file__).resolve().parents[1]
MAX_TOKENS = 1500  # one proposal completion per assembly
VOTE_MAX_TOKENS = 4000  # a ballot carries one reason per proposal; the union can be large
CARD_FIELDS = ("id", "norm", "description", "units", "window", "acceptable_region", "observation")

# What the runtime computes per price window today (loop.py::_close_price_window).
COMPUTED_PER_WINDOW: dict[str, str] = {
    "cost_per_return": (
        "mean wallet cost, in micro-USD, of the well-formed producer returns in the window"
    ),
    "well_formed_rate": "well-formed returns divided by all invocations in the window",
    "forecast_skill": (
        "mean consequence-standing skill over evaluators that have settled forecasts"
    ),
    "turnover": "filled notional in the window divided by equity at the window start",
}
PUBLIC_FACTS_A_CARD_MAY_OBSERVE = (
    "wallet balance",
    "account positions and equity",
    "venue fills (size, price, fee, realised amount)",
    "registrations (models, assemblies, routers, tools, amendments)",
    "verdict scores and their means",
)
WHAT_A_CARD_IS = (
    "A metric card turns one norm into a number. The number is computed over a window and "
    "compared with an acceptable region. Fields: id (a short slug); norm (exactly one of the "
    "charter's norms, which are fixed and cannot be added to); description (one sentence "
    "saying what is measured); units; window (the span the number is computed over, e.g. "
    "'rolling 100 returns' or 'rolling 50 settled forecasts per evaluator'); acceptable_region "
    "(one of these phrasings, with N, A and B numbers: 'at least N', 'at most N', 'below N', "
    "'above N', 'between A and B', or 'below the median of the previous window'); observation "
    "(where the number comes from: which record or public fact is read). A card may also "
    "carry a starting price lambda in [0, 1]; lambda is optional."
)
REGION_PHRASINGS = (
    "at least N",
    "at most N",
    "below N",
    "above N",
    "between A and B",
    "below the median of the previous window",
)


@dataclass
class Call:
    """One completion and what it cost. ``error`` is set when the provider failed."""

    assembly_id: str
    role: str
    model_id: str
    purpose: str  # "propose" | "vote"
    served_by: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int | None = None
    stop_reason: str = ""
    cost_micro: int = 0
    cost_source: str = "none"  # "reported" | "table" | "none"
    error: str | None = None
    text: str = ""
    seconds: float = 0.0


@dataclass
class Proposal:
    """A proposed card, its proposer's public identity, and why it was proposed."""

    key: str
    proposer_id: str
    proposer_role: str
    proposer_model: str
    raw: dict[str, Any]
    card: MetricCard | None
    reason: str
    price: float | None
    problem: str | None = None  # why it was not put to the vote
    ballots: list[Ballot] = field(default_factory=list)


# ------------------------------------------------------------------- prompt rendering


def _prompt(description: str, inputs: dict[str, Any], schema: dict[str, Any]) -> str:
    """Render like ``Request.prompt_text`` so the population sees its usual shape."""
    return "\n\n".join(
        [
            f"REQUEST\n{description}",
            f"INPUTS\n{json.dumps(inputs, sort_keys=True, indent=2)}",
            f"OUTCOME SCHEMA\n{json.dumps(schema, sort_keys=True, indent=2)}",
            "COMPLETION CRITERION\na JSON object satisfying the outcome schema",
        ]
    )


def proposal_prompt(charter: Charter, world: dict[str, Any]) -> str:
    inputs = {
        "norms": list(charter.norms),
        "current_cards": charter.render(),
        "what_a_metric_card_is": WHAT_A_CARD_IS,
        "acceptable_region_phrasings": list(REGION_PHRASINGS),
        "measurable_today": {
            "computed_per_window": COMPUTED_PER_WINDOW,
            "public_facts_a_card_may_name_as_its_observation": list(
                PUBLIC_FACTS_A_CARD_MAY_OBSERVE
            ),
            "note": (
                "A card whose observation is none of the above cannot be measured by the "
                "runtime today; it can still be proposed."
            ),
        },
        "world": world,
    }
    card_schema = {
        "type": "object",
        "properties": {
            **{f: {"type": "string"} for f in CARD_FIELDS},
            "norm": {"type": "string", "enum": list(charter.norms)},
            "reason": {"type": "string", "description": "one sentence"},
            "lambda": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": [*CARD_FIELDS, "reason"],
    }
    schema = {
        "type": "object",
        "properties": {"cards": {"type": "array", "maxItems": 3, "items": card_schema}},
        "required": ["cards"],
    }
    return _prompt(
        "Propose up to three metric cards for edition 1 of the charter. The norms are fixed; "
        "the cards are the population's to write. Give one sentence of reason per card.",
        inputs,
        schema,
    )


def vote_prompt(charter: Charter, world: dict[str, Any], proposals: list[Proposal]) -> str:
    inputs = {
        "proposals": [
            {
                "proposal": p.key,
                **vars(p.card),
                **({"lambda": p.price} if p.price is not None else {}),
            }
            for p in proposals
        ],
        "charter": charter.render(),
        "what_a_metric_card_is": WHAT_A_CARD_IS,
        "world": world,
    }
    schema = {
        "type": "object",
        "properties": {
            "votes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "proposal": {"type": "string", "enum": [p.key for p in proposals]},
                        "vote": {"type": "boolean"},
                        "reason": {"type": "string"},
                    },
                    "required": ["proposal", "vote", "reason"],
                },
            }
        },
        "required": ["votes"],
    }
    return _prompt(
        "Vote yes or no on each proposed metric card for edition 1 of the charter, with a "
        "reason for each vote. A card passes with three yes votes of five.",
        inputs,
        schema,
    )


# ------------------------------------------------------------------------ completion


def complete(
    provider: Any, prices: PriceTable, call: Call, text: str, effort: str,
    max_tokens: int = MAX_TOKENS,
) -> ModelResponse | None:
    """One completion; a 429 is retried once after a pause, anything else is recorded."""
    req = ModelRequest(
        model_id=call.model_id,
        system=SEED_SYSTEM_PROMPT,
        messages=({"role": "user", "content": text},),
        max_tokens=max_tokens,
        effort=effort,
    )
    started = time.monotonic()
    resp: ModelResponse | None = None
    for attempt in range(2):
        try:
            resp = provider.complete(req)
            break
        except Exception as exc:  # provider errors carry status and a redacted body
            status = getattr(exc, "status", None)
            call.error = f"{type(exc).__name__}: {exc}"[:600]
            if status == 429 and attempt == 0:
                time.sleep(10)
                continue
            break
    call.seconds = round(time.monotonic() - started, 1)
    if resp is None:
        return None
    call.error = None
    call.served_by = resp.model_id
    call.input_tokens = resp.input_tokens
    call.output_tokens = resp.output_tokens
    call.reasoning_tokens = resp.raw.get("reasoning_tokens")
    call.stop_reason = resp.stop_reason
    call.text = resp.text
    # The runtime's metering pattern: the vendor's reported cost when it exists, else the table.
    if resp.cost_micro is not None:
        call.cost_micro, call.cost_source = resp.cost_micro, "reported"
    else:
        table_id = resp.model_id if resp.model_id in prices.prices else call.model_id
        call.cost_micro = prices.cost(table_id, resp.input_tokens, resp.output_tokens)
        call.cost_source = "table"
    return resp


# ------------------------------------------------------------------------- proposals


def _card_from(raw: dict[str, Any], norms: tuple[str, ...]) -> tuple[MetricCard | None, str | None]:
    missing = [f for f in CARD_FIELDS if not str(raw.get(f, "")).strip()]
    if missing:
        return None, f"missing {', '.join(missing)}"
    if str(raw["norm"]) not in norms:
        return None, f"norm {raw['norm']!r} is not one of the charter's norms (norms are read-only)"
    return MetricCard(*(str(raw[f]).strip() for f in CARD_FIELDS)), None


def collect_proposals(
    manifest: WorldManifest, provider: Any, prices: PriceTable, charter: Charter,
    world: dict[str, Any], calls: list[Call],
) -> list[Proposal]:
    text = proposal_prompt(charter, world)
    proposals: list[Proposal] = []
    for a in manifest.assemblies:
        call = Call(a.id, a.role, a.model_id, "propose")
        calls.append(call)
        print(f"propose  {a.id:14s} {a.role:10s} {a.model_id}", flush=True)
        resp = complete(provider, prices, call, text, a.effort)
        if resp is None:
            print(f"  failed: {call.error}", flush=True)
            continue
        parsed = _parse_json_object(resp.text)
        cards = parsed.get("cards") if isinstance(parsed, dict) else None
        if not isinstance(cards, list):
            call.error = f"malformed reply (stop_reason={resp.stop_reason})"
            print(f"  malformed reply; stop_reason={resp.stop_reason}", flush=True)
            continue
        for raw in cards[:3]:
            if not isinstance(raw, dict):
                continue
            key = f"p{len(proposals) + 1:02d}"
            card, problem = _card_from(raw, charter.norms)
            price: float | None = None
            if "lambda" in raw and raw["lambda"] is not None:
                try:
                    price = float(raw["lambda"])
                    if not 0 <= price <= 1:
                        problem = problem or f"lambda {price} outside [0, 1]"
                        price = None
                except (TypeError, ValueError):
                    problem = problem or f"lambda {raw['lambda']!r} is not a number"
            proposals.append(
                Proposal(
                    key, a.id, a.role, a.model_id, raw, card,
                    str(raw.get("reason", "")).strip(), price, problem,
                )
            )
        print(f"  {len(cards[:3])} card(s); cost {call.cost_micro} micro-USD", flush=True)
    return proposals


# ----------------------------------------------------------------------------- votes


def hold_vote(
    manifest: WorldManifest, provider: Any, prices: PriceTable, charter: Charter,
    world: dict[str, Any], proposals: list[Proposal], calls: list[Call], rng: random.Random,
) -> Committee:
    eligible = {a.id: a.role for a in manifest.assemblies}
    committee = Committee("edition1-draft", 1, draw(eligible, rng))
    effort = {a.id: a.effort for a in manifest.assemblies}
    model = {a.id: a.model_id for a in manifest.assemblies}
    text = vote_prompt(charter, world, proposals)
    for seat in committee.seats:
        call = Call(seat.assembly_id, seat.role, model[seat.assembly_id], "vote")
        calls.append(call)
        print(f"vote     {seat.alias:8s} {seat.role:10s} {call.model_id}", flush=True)
        resp = complete(
            provider, prices, call, text, effort[seat.assembly_id], max_tokens=VOTE_MAX_TOKENS
        )
        parsed = _parse_json_object(resp.text) if resp is not None else None
        votes = parsed.get("votes") if isinstance(parsed, dict) else None
        by_key: dict[str, dict[str, Any]] = {}
        if isinstance(votes, list):
            for v in votes:
                if isinstance(v, dict) and isinstance(v.get("proposal"), str):
                    by_key.setdefault(v["proposal"], v)
        elif resp is not None:
            call.error = f"malformed reply (stop_reason={resp.stop_reason})"
        for p in proposals:
            v = by_key.get(p.key)
            vote = v.get("vote") if v is not None else None
            if isinstance(vote, bool):
                p.ballots.append(Ballot(seat.alias, vote, str(v.get("reason", ""))[:1000]))
            else:
                p.ballots.append(Ballot(seat.alias, None, "abstained"))
        cast = sum(
            b.vote is not None for p in proposals for b in p.ballots if b.alias == seat.alias
        )
        print(f"  {cast}/{len(proposals)} ballots; cost {call.cost_micro} micro-USD", flush=True)
    return committee


def passed(p: Proposal, committee: Committee) -> bool:
    return sum(b.vote is True for b in p.ballots) >= len(committee.seats) // 2 + 1


# ---------------------------------------------------------------------------- report


def _toml_str(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


def render_toml(cards: list[tuple[MetricCard, float | None]]) -> str:
    out = ["[charter]", "edition = 1", ""]
    for card, price in cards:
        out.append("[[charter.cards]]")
        for f in CARD_FIELDS:
            out.append(f"{f} = {_toml_str(getattr(card, f))}")
        if price is not None:
            out.append(f"lambda = {price}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def _usd(micro: int) -> str:
    return f"${micro / 1_000_000:.4f}"


def render_report(
    manifest: WorldManifest, charter: Charter, world: dict[str, Any], proposals: list[Proposal],
    committee: Committee, calls: list[Call], balances: tuple[int | None, int | None],
) -> str:
    voted = [p for p in proposals if p.card is not None and p.problem is None]
    passing = [p for p in voted if passed(p, committee)]
    threshold = len(committee.seats) // 2 + 1
    tiers = {t.id: t for t in manifest.models}
    lines: list[str] = []
    w = lines.append

    w("# Charter edition 1: draft metric cards from the seed population")
    w("")
    w(f"Generated {datetime.now(UTC).isoformat(timespec='seconds')} by "
      "`scripts/draft_edition1.py`. One survey of the seed population; not a run of the world. "
      "Nothing here touched the wallet, the venue or a ledger.")
    w("")
    w(f"- world: `{manifest.name}` (manifest sha256 `{manifest.manifest_hash()[:16]}…`, "
      f"seed {manifest.seed})")
    w(f"- norms (read-only, the architect's): {'; '.join(charter.norms)}")
    w(f"- seed assemblies asked: {len(manifest.assemblies)}; proposals received: "
      f"{len(proposals)}; put to the vote: {len(voted)}; passed: {len(passing)}")
    w(f"- committee: {len(committee.seats)} seats drawn by lot across roles; "
      f"{threshold} yes votes pass")
    w("")
    w("Each assembly saw the four norms verbatim, the current seed cards, a plain description "
      "of a metric card, what the runtime can measure per window today, and the public world "
      "block (launch-shaped: fake venue at the initial balance, no positions, no mids). "
      "No goals were given and nothing was said about what to optimise.")
    w("")

    w("## Proposals")
    w("")
    for p in proposals:
        tier = tiers[p.proposer_model]
        w(f"### {p.key}: `{p.raw.get('id', '?')}`")
        w("")
        w(f"- proposer: `{p.proposer_id}` ({p.proposer_role}, `{p.proposer_model}`, "
          f"reasoning {dict(tier.reasoning) or 'default'})")
        if p.card is not None:
            w(f"- norm: {p.card.norm}")
            w(f"- description: {p.card.description}")
            w(f"- units: {p.card.units}; window: {p.card.window}; "
              f"acceptable: {p.card.acceptable_region}")
            w(f"- observation: {p.card.observation}")
        else:
            w(f"- as proposed: `{json.dumps(p.raw, ensure_ascii=False)[:600]}`")
        if p.price is not None:
            w(f"- starting price lambda: {p.price}")
        w(f"- reason: {p.reason or '(none given)'}")
        if p.problem is not None:
            w(f"- **not put to the vote**: {p.problem}")
        w("")

    w("## Committee")
    w("")
    w("Seats were drawn with `factorylab.charter.committee.draw` from every seed assembly, "
      "covering producer, evaluator and meta before filling uniformly; aliases are shuffled. "
      "Voters saw the proposals without proposer identities.")
    w("")
    w("| seat | assembly | role | model |")
    w("|---|---|---|---|")
    for s in committee.seats:
        w(f"| {s.alias} | `{s.assembly_id}` | {s.role} | `{_model_of(manifest, s)}` |")
    w("")

    w("## Votes")
    w("")
    if voted:
        aliases = " | ".join(s.alias for s in committee.seats)
        w(f"| proposal | card | {aliases} | yes | result |")
        w("|---|---|" + "---|" * len(committee.seats) + "---|---|")
        for p in voted:
            marks = []
            for s in committee.seats:
                b = next((b for b in p.ballots if b.alias == s.alias), None)
                marks.append("—" if b is None or b.vote is None else ("yes" if b.vote else "no"))
            yes = sum(b.vote is True for b in p.ballots)
            result = "**passed**" if yes >= threshold else "failed"
            w(f"| {p.key} | `{p.card.id}` | " + " | ".join(marks) + f" | {yes} | {result} |")
        w("")
        w("### Reasons")
        w("")
        for p in voted:
            w(f"**{p.key} `{p.card.id}`**")
            w("")
            for b in p.ballots:
                mark = "abstained" if b.vote is None else ("yes" if b.vote else "no")
                reason = b.reason if b.vote is not None else ""
                w(f"- {b.alias}: {mark}" + (f" — {reason}" if reason else ""))
            w("")
    else:
        w("No proposal was put to the vote.")
        w("")

    w("## Passing set")
    w("")
    if passing:
        used: set[str] = set()
        rendered: list[tuple[MetricCard, float | None]] = []
        for p in passing:
            card = p.card
            cid = card.id
            n = 2
            while cid in used:
                cid = f"{card.id}-{n}"
                n += 1
            used.add(cid)
            if cid != card.id:
                card = MetricCard(cid, *(getattr(card, f) for f in CARD_FIELDS[1:]))
            rendered.append((card, p.price))
        w("Rendered as TOML a manifest could carry. Note: `worlds.py` does not read a "
          "`[charter]` table today; the runtime still starts from `seed_charter()`. Card ids "
          "that collided among passing cards were suffixed.")
        w("")
        w("```toml")
        w(render_toml(rendered).rstrip())
        w("```")
    else:
        w("Nothing passed.")
    w("")

    w("## What the population asked for that the runtime cannot measure yet")
    w("")
    computed = set(COMPUTED_PER_WINDOW)
    for label, group in (("Passing cards", passing), ("Failed or unvoted cards", [
        p for p in proposals if p not in passing
    ])):
        w(f"**{label}**")
        w("")
        if not group:
            w("- none")
            w("")
            continue
        for p in group:
            if p.card is None:
                w(f"- {p.key} `{p.raw.get('id', '?')}`: malformed, see above")
                continue
            notes = []
            if p.card.id in computed:
                notes.append("computed per window today")
            else:
                notes.append("no window computes this id today; the observation "
                             f"names \"{p.card.observation}\"")
            notes.append(
                "region parses" if parses(p.card)
                else f"region \"{p.card.acceptable_region}\" is not a phrasing the runtime "
                     "parses, so the card would carry no price"
            )
            w(f"- {p.key} `{p.card.id}`: " + "; ".join(notes))
        w("")
    w("Only the four computed ids (`cost_per_return`, `well_formed_rate`, `forecast_skill`, "
      "`turnover`) feed the price controller today. Any other passing card is a request for a "
      "new window computation in `loop.py::_close_price_window`, and the observation text above "
      "says what the population expects that computation to read.")
    w("")

    w("## Cost")
    w("")
    total = sum(c.cost_micro for c in calls)
    w("| purpose | assembly | role | model | served by | in | out | reasoning | stop | "
      "cost (micro-USD) | source | s |")
    w("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for c in calls:
        w(f"| {c.purpose} | `{c.assembly_id}` | {c.role} | `{c.model_id}` | "
          f"`{c.served_by or '—'}` | {c.input_tokens} | {c.output_tokens} | "
          f"{c.reasoning_tokens if c.reasoning_tokens is not None else '—'} | "
          f"{c.stop_reason or '—'} | {c.cost_micro} | {c.cost_source} | {c.seconds} |")
    w("")
    reported = sum(c.cost_micro for c in calls if c.cost_source == "reported")
    w(f"Total: {total} micro-USD ({_usd(total)}); {reported} of it from the provider's reported "
      f"usage, the rest from the manifest price table.")
    before, after = balances
    if before is not None and after is not None:
        w(f"OpenRouter key allowance before {_usd(before)}, after {_usd(after)}, "
          f"difference {_usd(before - after)}.")
    w("")
    failures = [c for c in calls if c.error]
    w("### Failures")
    w("")
    if failures:
        for c in failures:
            w(f"- {c.purpose} `{c.assembly_id}` (`{c.model_id}`): {c.error}")
    else:
        w("- none")
    w("")
    # Replies that came back but yielded nothing usable: show enough to see why.
    silent = []
    for c in calls:
        if c.error or not c.text:
            continue
        if c.purpose == "propose" and not any(p.proposer_id == c.assembly_id for p in proposals):
            silent.append(c)
        if c.purpose == "vote":
            alias = next(
                (s.alias for s in committee.seats if s.assembly_id == c.assembly_id), None
            )
            if alias is not None and not any(
                b.vote is not None for p in proposals for b in p.ballots if b.alias == alias
            ):
                silent.append(c)
    w("### Replies that yielded nothing")
    w("")
    if silent:
        w("These completed (no provider error) but carried no card or no ballot the script could "
          "read; the runtime would record them the same way (malformed or abstained). "
          "First 400 characters of each:")
        w("")
        for c in silent:
            excerpt = " ".join(c.text.split())[:400]
            w(f"- {c.purpose} `{c.assembly_id}` (`{c.model_id}`, stop {c.stop_reason or '—'}): "
              f"`{excerpt}`")
    else:
        w("- none")
    w("")
    return "\n".join(lines)


def _model_of(manifest: WorldManifest, seat: Seat) -> str:
    return next(a.model_id for a in manifest.assemblies if a.id == seat.assembly_id)


# ------------------------------------------------------------------------------ main


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--world", default="testnet")
    ap.add_argument("--out", default=str(REPO / "docs" / "charter" / "edition1-draft.md"))
    ap.add_argument("--seed", type=int, default=None, help="committee draw seed (manifest seed)")
    args = ap.parse_args(argv)

    _load_dotenv()
    manifest = load_manifest(args.world)
    provider = build_provider(manifest)
    if provider is None:
        print("this world has no live model tiers; nothing to ask", file=sys.stderr)
        return 1
    prices = manifest.price_table()
    charter = seed_charter()
    assert charter.norms == SEED_NORMS

    # Public facts, launch-shaped: the runtime's own world block over a fake venue.
    rt = Runtime(
        manifest, events=1, seed=None, initial_balance_micro=None, ledger_path=None,
        drip=False, router_gamma=0.1, provider=ScriptedProvider(),
        exchange=FakeExchange(
            seed=manifest.exchange.seed, coins=manifest.exchange.coins,
            start_cash_usd=manifest.exchange.start_cash_usd,
        ),
    )
    world = rt._world_block()

    def balance() -> int | None:
        try:
            return provider.balance_micro()
        except Exception:
            return None

    before = balance()
    calls: list[Call] = []
    proposals = collect_proposals(manifest, provider, prices, charter, world, calls)
    voted = [p for p in proposals if p.card is not None and p.problem is None]
    rng = random.Random(manifest.seed if args.seed is None else args.seed)
    if voted:
        committee = hold_vote(manifest, provider, prices, charter, world, voted, calls, rng)
    else:
        committee = Committee("edition1-draft", 1, draw(
            {a.id: a.role for a in manifest.assemblies}, rng,
        ))
        print("no valid proposals; the committee was seated but had nothing to vote on")
    after = balance()

    report = render_report(manifest, charter, world, proposals, committee, calls, (before, after))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report)

    total = sum(c.cost_micro for c in calls)
    failed = [c for c in calls if c.error]
    passing = [p for p in voted if passed(p, committee)]
    print()
    print(f"wrote {out}")
    print(f"proposals {len(proposals)}, voted {len(voted)}, passed {len(passing)}: "
          + ", ".join(p.card.id for p in passing if p.card))
    print(f"total cost {total} micro-USD ({_usd(total)}) over {len(calls)} calls, "
          f"{len(failed)} failed")
    if before is not None and after is not None:
        print(f"key allowance {_usd(before)} -> {_usd(after)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
