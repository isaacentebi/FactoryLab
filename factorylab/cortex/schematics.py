"""Public world facts and return schemas visible to every assembly."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from factorylab.charter.measurement import measurement_catalogue
from factorylab.cortex.assembly import SEED_SYSTEM_PROMPT, reserved_return_fields
from factorylab.kernel.money import money_to_usd
from factorylab.runtime.cadence import tick_intervals
from factorylab.runtime.observations import window_fact_names
from factorylab.runtime.propensity import MIN_DECLARED_MASS, action_vocabulary
from factorylab.runtime.shared import work_disclosure
from factorylab.runtime.summary import _duration_str, _price_str
from factorylab.settlement.vocabulary import COMMISSIONED_JUDGE_REFUSAL

NS_PER_DAY = 86_400 * 1_000_000_000
NS_PER_HOUR = 3_600 * 1_000_000_000
#: The observed interval a burn rate is reported from. Under it, no runway is
#: asserted: a rate measured over two calls is not evidence about a week.
MIN_BURN_OBSERVATION_NS = 6 * NS_PER_HOUR
#: Spend is accumulated in half-day buckets and at most two are kept per seat, so
#: the reported window is between twelve and twenty-four hours of real spending.
SPEND_BUCKET_NS = 12 * NS_PER_HOUR
#: What ``note.list`` and ``artifact.list`` return in one page, before a cursor.
DIRECTORY_PAGE = 50
#: What the world block's own directory preview carries; the tools page the rest.
DIRECTORY_PREVIEW = 10
INSUFFICIENT = "insufficient history"

#: GPT-6 §9's accounting facts, verbatim. They say what the numbers above them
#: mean and they hold for every call in this world, so they ride in the prompt's
#: stable prefix rather than being rewritten per request.
ACCOUNTING_FACTS: tuple[str, ...] = (
    "A paid thought consumes the named budget even when no order is placed.",
    "No new order does not mean the existing portfolio is flat.",
    "Internal payments and endowment releases are not external income.",
    "Before conversion costs, only external net receipts increase total resources.",
    "Trading principal can be converted only through the permitted route and is not earnings.",
    "You may revise your subscription, defer work or decline an unaffordable request.",
    "No trade, forecast, registration, amendment or novelty quota applies.",
)


def _usd(micro: Any) -> str | None:
    """Exact USD text for integer micro-USD; ``None`` for an unobserved amount."""
    return None if type(micro) is not int else str(money_to_usd(micro))


def _utc(ns: Any) -> str | None:
    """A whole-second UTC stamp, or ``None`` when the time is not known."""
    if type(ns) is not int or ns < 0:
        return None
    return datetime.fromtimestamp(ns // 1_000_000_000, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _rail_for_model(model_id: Any) -> str:
    """Name the compute rail a model id is bought on, as registration routes it."""
    text = str(model_id)
    if text.startswith("x402:"):
        return "x402"
    if text.startswith("venice:"):
        return "venice"
    return "openrouter"


def _is_registration_feedback(item: dict) -> bool:
    """Old checkpoints and new typed refusals retain their actual operation category."""
    if "kind" in item:
        return item["kind"] == "registration.rejected"
    return not str(item.get("reason", "")).startswith(("propensity:", "judgement:", "order:"))


class SchematicsMixin:
    """Preserve runtime state and behavior for schematics operations."""

    PROPOSAL_SHAPES: dict[str, Any] = {
        "model": {
            "kind": "model",
            "openrouter_id": "vendor/model-id from the catalogue, optionally @none|@low|@high|@max "
            "for reasoning; venice:<id> for Venice; x402:<seller_url>#<model> for a seller "
            "priced per request",
        },
        "assembly": {
            "kind": "assembly",
            "id": "slug-2-to-48-chars",
            "role": "producer",
            "model_id": "a registered model id",
            "system_prompt": "text, at most 4000 chars",
            "accepts": ["Tick"],
            "emits": ["ProducerReturn"],
            "schemas": {},
            "reward_shapes": {"ProducerReturn": "judged"},
            "max_tokens": 512,
            "effort": "low",
        },
        "router": {
            "kind": "router",
            "event_kind": "Tick",
            "learner": "exp3",
            "gamma": 0.1,
            "add": False,
        },
        "retire": {"kind": "retire", "assembly_id": "an id from world.catalogue"},
        "connector": {"kind": "connector", "id": "public-source",
                      "description": "Public information", "origin": "https://example.org",
                      "preflight_path": "/data", "pay": "x402", "max_call_usd": "0.003"},
        "market": {"kind": "market", "coin": "listed perp coin; omit when using pair",
                   "pair": "listed BASE/USDC pair; omit when using coin"},
        "service": {"kind": "service", "program_id": "id of a tool you registered",
                    "price_micro": 1000, "description": "what a buyer receives"},
        "tool": {
            "kind": "tool",
            "id": "slug",
            "description": "what it computes",
            "args_schema": {"type": "object", "properties": {"x": {"type": "number"}}},
            "code": "python: read a JSON object from stdin, print a JSON object",
            "timeout_s": 2,
        },
        "program": {
            "kind": "assembly",
            "id": "slug-2-to-48-chars",
            "role": "producer",
            "model_id": "program",
            "accepts": ["Tick"],
            "emits": ["ProducerReturn"],
            "code": "python: read one JSON object from stdin with prompt, description, inputs, "
            "outcome_schema and state; print the same Return JSON a model would, plus an "
            "optional state object to keep",
            "timeout_s": 10,
            "state_policy": "private",
        },
        "observation": {
            "kind": "observation",
            "id": "slug",
            "description": "what it measures",
            "unit": "fraction | count | micro-USD | …",
            "range": [0.0, 1.0],
            "code": "python defining observe(facts) -> float over the public window facts; "
            "the same facts a closed window publishes",
        },
        "learner": {
            "kind": "learner",
            "assembly_id": "an id from world.catalogue, usually inputs.you",
            "learner": "blum_mansour",
            "actions": ["hold", "buy:BTC", "sell:BTC"],
            "gamma": 0.1,
        },
        "amendment": {
            "kind": "amendment",
            "id": "slug",
            "add": [
                {
                    "id": "card-id",
                    "norm": "one of the charter norms",
                    "description": "what is measured",
                    "units": "…",
                    "window": {"kind": "returns", "n": 100, "per": "role"},
                    "acceptable_region": "…",
                    "observation": "one of world.observations ids",
                    "answers_for": "producer",
                    "lambda": 0.1,
                }
            ],
            "replace": [],
            "remove": ["card-id"],
            "predicted_effect": {"card_id": "card-id", "direction": "increase", "window": 1},
            "tick_interval": "30s",
        },
        "challenge": {
            "kind": "challenge",
            "card_id": "a current card id",
            "evidence": "why the card measures the wrong thing, at most 4000 chars",
            "replacement": {
                "observation": "one of world.observations ids",
                "rule": "at most",
                "value": 5000,
                "window": {"kind": "returns", "n": 10, "per": "role"},
            },
            "trial_windows": 6,
        },
    }


    A_RETURN_MAY_INCLUDE: dict[str, str] = {
        "action": (
            '"noop" | "hold" | "order"; an "order" return also carries "coin" (one of the '
            'world\'s coins), "side" ("buy" | "sell") and "size" (base units as a decimal '
            'string, e.g. "0.005"), and is placed at market on return; limit, reduce-only, '
            '"market" defaults to "perp" or accepts "spot" with a configured BASE/USDC pair; '
            "close, leverage and cancel are tool_calls on the venue.* tools"
        ),
        "order_example": '{"action": "order", "coin": "ETH", "side": "buy", "size": "0.004"}',
        "verdict": (
            "evaluator returns (required): the judged return's quality against the charter, "
            "0 to 1; it settles the producer's verdict channel and is graded by meta conformity"
        ),
        "payoff": (
            "evaluator returns (required): your probability that the kernel's consequence "
            "predicate resolves true for the judged return; antagonist returns (optional): the "
            "same probability about your own return. Either is sealed as the kernel's payoff "
            "forecast and graded by Brier against the realised predicate (see scoring)"
        ),
        "about_handle": (
            "judging returns (optional): the return handle your verdict or conformity is "
            "about, exactly as it appears in the request (inputs.subject_handle when present, "
            "otherwise the delivered return); omit it to judge the delivered return. A value "
            "you cannot address here — prose, or a handle this judgement may not be about — "
            "is not used: the delivered return is judged instead and the reason appears in "
            "return_feedback"
        ),
        "propensity": (
            "optional on any return: your own distribution over the actions you were "
            "choosing among, as {action_id: probability} summing to one and including "
            "the action you took (see action_labels for the shape of an action id; the "
            "action includes what the return executed through venue and treasury tools "
            "and the children it requested). The action taken needs at least "
            f"{MIN_DECLARED_MASS} mass or is floored to it before it weights a reward. It "
            "travels forward on the request about this return, so the judges of this "
            "return read it"
        ),
        "register": "a list of up to three proposals, including amendments, shaped like "
        "proposal_shapes; router add=false replaces, add=true adds a router. Learners: exp3 or "
        "blum_mansour. Roles are descriptive labels; accepts and emits define the contract. "
        "A retire proposal names an id from world.catalogue (any assembly, the seeds "
        "included) and removes it from every router; a retired id may be "
        "registered again as its next version. Effort: low, medium, high. An assembly with "
        "model_id program is a program seat (proposal_shapes.program): its code runs in the "
        "tool jail instead of a model, reads one JSON object from stdin (prompt, description, "
        "inputs, outcome_schema, state) and prints the Return JSON a model would; it is "
        "routed, judged, paid and retired exactly like a model seat, each call costing "
        "prices.program_micro_per_call. With state_policy private the object it prints under "
        "state is archived as an artifact it owns and handed back on its next call; the "
        "artifact's sha is in the diary and artifact.get reads it, free, for any seat in the "
        "program's own lineage; other readers are refused artifact_private. Machinery "
        "you have learned belongs in a program seat, where it costs a flat call and cannot "
        "drift. An observation "
        "registers a measurement: its code runs in the tool jail over a closed window's "
        "public facts and is admitted only if it produces a finite number on the last "
        "closed window; a card may then name it, and re-registering the same id supersedes "
        "it with a new version. A learner gives one assembly (assembly_id, normally your own "
        "inputs.you) a learner over the action set "
        "it declares, trained by that assembly's declared propensities and the rewards its "
        "decisions settle at. Cards answer for any registered emitted kind, "
        "the seed aliases producer, evaluator, meta, antagonist, or all; window is "
        "{kind: returns|forecasts|windows, n: positive integer, per: role|assembly|null}. "
        "Insufficient samples are unmeasured. Lambda is optional and bounded by prices.lambda_max; "
        "tick_interval is an optional duration within world.clock bounds. A prediction names a "
        "card_id, direction (increase or decrease), and a positive window count after activation. "
        "Unmeasurable windows, duplicate role/observation bindings and unchanged amendments "
        "are refused before a vote. A connector may omit preflight_path (default /), pay, "
        "and max_call_usd; pay=x402 requires an exact max_call_usd cap. A market proposal "
        "names exactly one coin or pair from venue.instruments. A challenge names a current "
        "card, gives evidence, and offers a replacement (observation, rule: at most | at least "
        "| above | below, value, window; optionally description, units, answers_for) that "
        "keeps the card's id and norm; admission costs one novelty trial, both cards are then "
        "measured frozen for trial_windows closed windows (ledgered as challenge.window), and "
        "the committee ballots on adopting the replacement as an amendment. During the trial "
        "a connector or retire proposal may name the challenge id as its predicted_effect "
        "card_id, and is then graded on the replacement. A service proposal sells a "
        "registered tool's output to outside buyers at price_micro (integer micro-USD) per "
        "call over x402; each paid call is ledgered as income.earned and shown in pots.",
        "tool_calls": (
            'a list of {"tool": id, "args": {...}} bounded by mechanics.tools.max_tool_calls; '
            'results come back in one continuation per request'
        ),
        "emits": (
            "the selected return kind from your registered emits; optional for a single kind. "
            "ProducerReturn uses verdict feedback, Verdict uses conformity and payoff, "
            "MetaVerdict uses conformity or terminal consequence, Exposure uses exposure. "
            "A custom kind is declared in registration.schemas[kind] as a JSON object schema "
            "and declares registration.reward_shapes[kind] as judged, forecast, conformity "
            "or exposure (default judged). See world.work for the reward contracts. "
            "Event kind names keep their schema; a changed "
            "schema uses a new name. Built-in world and kernel events cannot be emitted."
        ),
        "requests": (
            'objects: {"target":"an id from world.catalogue, or self","description":"task",'
            '"inputs":{},"outcome_schema":{"type":"object"}}; children have tools and '
            'may request children to mechanics.tools.max_depth (root depth 0), with '
            'mechanics.tools.max_children children per request. Each depth has one '
            'continuation and spends within its parent\'s remaining cost ceiling. '
            'Outputs arrive in tool_results as '
            '{"tool":"assembly:<target>","args":<inputs>,"result":{"outputs":{},'
            '"status":"ok","cost_micro":0}} before your second call. '
            'Outcome schemas support object/array/scalar types, properties, required, enum, '
            'minimum, maximum, minItems, maxItems and additionalProperties.'
        ),
    }


    def _world_block(self) -> dict[str, Any]:
        """Facts about the world any assembly may see. No rules, no goals, no private state."""
        self._ensure_connector_tool()
        from factorylab.runtime.custody import custody_view
        from factorylab.runtime.notes import counts
        try:
            acct = self._tick_account()
            account = {
                "equity_usd": str(acct.equity_usd),
                "cash_usd": str(acct.cash_usd),
                "positions": [
                    {"coin": p.coin, "size": str(p.size), "entry_px": str(p.entry_px)}
                    for p in acct.positions
                ],
                "margin_used_usd": str(acct.margin_used_usd),
                "spot_balances": [{"coin": b.coin, "total": str(b.total),
                                   "available": str(b.available)} for b in acct.spot_balances],
            }
        except RuntimeError as exc:
            # A failed venue read is reported as one. It used to be answered with
            # the compute wallet's balance and an empty position set, which told
            # the population it held equity it did not hold and had no positions
            # it may well have had: GPT-6 Pro's third reading, "the account-read
            # fallback invents financial facts". Missing data stays unavailable.
            account = {"status": "unavailable", "reason": type(exc).__name__}
        # One mechanics block answers both disclosures; building it twice per request
        # only re-reads the same committed parameters.
        mechanics = self._mechanics_block()
        account["custody"] = custody_view(self)
        account["realized_pnl_usd_to_date"] = str(money_to_usd(self.realized_to_date))
        account["fees_usd_to_date"] = str(money_to_usd(self.fees_to_date))
        account["funding_usd_to_date"] = str(money_to_usd(self.funding_to_date))
        return {
            "pots": self.wallet.pots(),
            "world_resources": self._world_resources(),
            "seats": self._seat_views(),
            "continuity": self._continuity_block(),
            "accounting_facts": list(ACCOUNTING_FACTS),
            "compute_supply": {
                "openrouter": "Prepaid credit on the OpenRouter account. No tool tops it up; "
                              "when it is gone, OpenRouter model ids cannot be called.",
                "venice": "Credit on a separate Venice account. Base USDC and Venice credit "
                          "are different pots: treasury.transfer with direction to_venice "
                          "converts $5 of reserve USDC into Venice credit, which converts "
                          "principal into compute and is not income.",
                "discovery": "catalogue.search returns model prices per million tokens, and "
                             "the full args_schema of any registered tool and the full shape "
                             "of any proposal kind; world.tools and world.proposal_shapes "
                             "carry the one-line index it is searched by.",
                "selection": "venice: model ids are bought on Venice, x402: ids from a "
                             "seller, bare ids on OpenRouter. Each rail spends only its own "
                             "credit; world.world_resources.provider_inventory carries the "
                             "last observed balance of each.",
            },
            "charter_edition": self.charter.edition,
            "charter": self._charter_text(),
            "mechanics": mechanics,
            "composition": SEED_SYSTEM_PROMPT,
            "recent_mids": {c: list(v) for c, v in self.recent_mids.items()},
            "account": account,
            "venue": self._traded_instruments(),
            "venue_listing": (
                "venue is the instrument record of each market in trading_markets. The "
                "venue lists far more than those: call the venue.instruments public read "
                "for the whole listing, and register a market proposal to trade one of them. "
                "venue.mids, venue.funding, venue.candles, venue.order_book and "
                "venue.funding_history read any listed coin or pair without registering it."
            ),
            "trading_markets": {"perp": list(self.venue_tools.coins),
                                "spot": list(self.venue_tools.spot_pairs)},
            "notes": {**counts(self.notes), "max_keys": self.m.notes.max_keys,
                      "max_bytes": self.m.notes.max_bytes,
                      "micro_per_byte_day": self.m.notes.micro_per_byte_day,
                      "pricing": "Reading and writing the notebook is free of any per-byte "
                      "transfer charge; retained text pays storage rent of "
                      "micro_per_byte_day per byte by elapsed time, collected at each window "
                      "boundary. Unpaid storage rent is due before a read or overwrite; text "
                      "is retained. note.list indexes the keys.",
                      "call_price_micro": self.m.notes.byte_window_micro},
            "tools": self._published_tool_specs(),
            "connectors": {"registered": self._connector_catalogue(),
                           "max_bytes": self.m.connectors.max_bytes,
                           "timeout_s": self.m.connectors.timeout_s,
                           "call_price_micro": self.m.connectors.call_price_micro,
                           "max_calls_per_window": self.m.connectors.max_calls_per_window,
                           "window_ns": self.m.novelty.window_ns,
                           "origin_denylist": list(self.m.connectors.origin_denylist),
                           "method": "GET",
                           "optional_fields": ["pay", "max_call_usd"],
                           "payment": "pay=x402 uses max_call_usd as the seller charge cap; "
                           "the flat call price is additional. Omit pay for free sources.",
                           "result": "UTF-8 text in seen_tool_results[].result.body",
                           "tool_rounds": 2,
                           "continuation_tool_kinds": ["population", "note", "artifact"],
                           "encoding": "UTF-8 with replacement", "redirects": "refused",
                           "oversize": "refused", "credentials": False},
            "population_tools": {
                "available": self.tool_jail_available,
                "reason": None if self.tool_jail_available else "no jail on this host",
            },
            "observations": measurement_catalogue(self.observations),
            "work": work_disclosure(self._kind_rewards(), self.predicates.catalogue()),
            "observation_facts": window_fact_names(),
            "action_labels": action_vocabulary(),
            "reserve": {"protected": self.reserve.remaining(), "units": "micro-USD",
                        "trials": self.m.novelty.trials,
                        "max_lifetime_windows": self.m.novelty.max_lifetime_windows},
            "committee": dict(mechanics["committee"]),
            "pathologies": dict(self.stats.pathologies),
            "novelty_reserve_remaining_usd": str(money_to_usd(self.reserve.remaining())),
            "models": [
                {
                    "id": mid,
                    "usd_per_million_input_tokens": _price_str(p.input_micro),
                    "usd_per_million_output_tokens": _price_str(p.output_micro),
                    "per_request_micro": p.per_request_micro,
                }
                for mid, p in self.prices.prices.items()
            ],
            "sellers": [{"model_id": mid, **seller} for mid, seller in self.sellers.items()],
            "assemblies": [
                {
                    "event_kind": kind,
                    "count": sum(kind in a.spec.accepts for a in self.assemblies.values()
                                 if a.spec.id not in self.retired_assemblies),
                }
                for kind in sorted({k for a in self.assemblies.values() for k in a.spec.accepts})
            ],
            "contracts": [
                {"accepts": list(accepts), "emits": list(emits)}
                for accepts, emits in sorted({
                    (tuple(sorted(a.spec.accepts)), tuple(a.spec.emits))
                    for a in self.assemblies.values()
                    if a.spec.id not in self.retired_assemblies})
            ],
            # Ids and contracts are public schematics: every assembly can be named
            # in requests[].target, retire.assembly_id and learner.assembly_id. The
            # model behind an id, its prompt, its learner state, the routers'
            # weights and who judged whom stay sealed.
            "catalogue": [
                {"id": a.spec.id, "version": a.spec.version,
                 "accepts": sorted(a.spec.accepts), "emits": list(a.spec.emits)}
                for a in sorted(self.assemblies.values(), key=lambda a: a.spec.id)
                if a.spec.id not in self.retired_assemblies
            ],
            "addressing": (
                "inputs.you is your own assembly id. catalogue lists every live assembly "
                "as {id, version, accepts, emits}; those ids are what requests[].target, "
                "a retire proposal's assembly_id and a learner proposal's assembly_id name. "
                + COMMISSIONED_JUDGE_REFUSAL
            ),
            "event_schemas": dict(self.event_schemas),
            "routers": [
                {"event_kind": kind, "count": len(states)}
                for kind, states in sorted(self.routers.items())
            ],
            "clock": {
                "tick_interval": _duration_str(self.tick_clock.interval_ns),
                "min_tick": _duration_str(self.m.clock.min_tick_ns),
                "max_tick": _duration_str(self.m.max_tick_ns),
            },
            "governance": self.cadence.world_block(self.tick_clock),
            "tick_intervals": tick_intervals(self.tick_clock),
            "registration_feedback": [dict(f) for f in self.registration_feedback
                                      if _is_registration_feedback(f)],
            "return_feedback": [dict(f) for f in self.registration_feedback
                                if not _is_registration_feedback(f)],
            "reserved_return_fields": reserved_return_fields(
                max_children=self.m.tools.max_children,
                max_tool_calls=self.m.tools.max_tool_calls),
            "scoring": self._scoring_block(),
            # Moving by construction: the sampling actuator and the immune controller
            # change these, so they are published here and never inside the prefix.
            "adaptive_scoring": self._adaptive_scoring_block(),
            "prices": {"lambda_max": self.m.prices.lambda_max,
                       "penalty_cap": self.m.prices.penalty_cap,
                       "program_micro_per_call": self.m.prices.program_micro_per_call},
            "amendment_feedback": getattr(self, "amendment_feedback", None),
            "card_prices": [
                {
                    "card_id": cid,
                    "lambda": self.controller.price(cid),
                    "region": (
                        {"kind": r.kind, "lo": r.lo, "hi": r.hi, "scale": r.scale}
                        if (r := self.regions.get(cid)) is not None
                        else None
                    ),
                }
                for cid in sorted(self.priced)
            ],
            "event_kinds": sorted(self._event_kinds()),
            "meta_input": (
                "A meta judges the released representative verdict. Its window describes "
                "the arrivals it represents: count, mean score, min, max, and decision handles."
            ),
            "a_return_may_include": self.A_RETURN_MAY_INCLUDE,
            "proposal_shapes": self._proposal_index(),
        }

    PROPOSAL_LINES: dict[str, str] = {
        "model": "register a model id to call: OpenRouter, venice:, or an x402 seller",
        "assembly": "register a seat: id, model, system prompt, accepts and emits",
        "router": "replace or add a router over one event kind",
        "retire": "remove an assembly from every router",
        "connector": "register an outside GET source, optionally paid over x402",
        "market": "add trading permission for one listed coin or pair",
        "service": "sell a registered tool's output to outside buyers over x402",
        "tool": "register jailed code as a priced tool anyone may call",
        "program": "register a seat whose jailed code answers instead of a model",
        "observation": "register a measurement over a closed window's public facts",
        "learner": "give one assembly a learner over an action set it declares",
        "amendment": "add, replace or remove charter cards, with a predicted effect",
        "challenge": "contest a current card with evidence and a replacement",
    }

    def _proposal_index(self) -> dict[str, str]:
        """One line per proposal kind; the full shape is a ``catalogue.search`` away.

        Guarantees the index names exactly the kinds ``PROPOSAL_SHAPES`` defines,
        so nothing registrable becomes invisible by being compacted: a kind added
        to the shapes without a line here is still listed, by its own key. The
        shapes themselves are what ``catalogue.search`` returns under
        ``proposal_shapes``, unabridged and unrewritten — this only keeps some
        4,000 characters of JSON skeleton out of every prompt in the world.
        """
        return {kind: self.PROPOSAL_LINES.get(kind, kind)
                for kind in sorted(self.PROPOSAL_SHAPES)}

    def _charter_text(self) -> str:
        """Every duplicate charter disclosure is the same text, and it holds still.

        The controller re-prices every card at every closed window, so a charter
        with its lambdas written into it would be a different charter on every
        call and no prefix cache could ever hold it. The prices are published
        unabridged in ``world.card_prices``, beside each card's region, where
        they move without rewriting the disclosure that carries them.
        """
        return self.charter.render(price_label="in world.card_prices")

    def _traded_instruments(self) -> dict[str, list[dict[str, Any]]]:
        """The instrument record of each market this world may trade, and no other.

        Guarantees the returned records are exactly the venue's own for the coins
        and pairs in ``world.trading_markets``, unabridged and unrewritten, and
        that the block's size follows that permission rather than the venue's
        listing: a venue that lists a thousand more instruments adds nothing here.

        The venue's own listing runs to thousands of instruments; carrying it in
        every prompt cost about 100k input tokens a call and told an assembly
        nothing it could not read on demand. What a trading decision needs is the
        lot size, tick size and order floor of the markets it may actually send an
        order to, which is ``trading_markets``. The listing itself stays one
        ``venue.instruments`` call away, and ``world.venue_listing`` says so.

        The listing is read once a tick, not once a request. A venue's listing is
        about 260 KB and every read of it is recorded in the diary in full; a tick
        that builds a dozen prompts for producers, judges and meta judges recorded
        it a dozen times, which is how a twenty-minute rehearsal wrote 21 MB of
        the same listing. The memo sits here, above the recorded-I/O layer, so a
        replayed diary sees exactly the calls that were recorded; it is never
        saved, so the first request after a resume reads afresh and records it.
        Lot sizes and order floors do not move within one tick, and a market
        registered mid-tick still finds its record here because the raw listing,
        not the filtered block, is what is held.
        """
        tick = self.ticks_consumed
        memo = getattr(self, "_instruments_memo", None)
        if memo is None or memo[0] != tick:
            memo = (tick, self.exchange.instruments())
            self._instruments_memo = memo
        traded = {"perp": set(self.venue_tools.coins), "spot": set(self.venue_tools.spot_pairs)}
        return {market: [row for row in rows if row.get("coin") in traded.get(market, ())]
                for market, rows in memo[1].items()}

    def _published_tool_specs(self, *, full: bool = False) -> list[dict[str, Any]]:
        """Every registered tool's contract, with the venue's listing named rather than spelled.

        Guarantees each registered spec is published exactly once and changed in
        one way only: a public venue read whose ``coin`` argument enumerates the
        whole listing is disclosed as naming it instead. Nothing dispatch reads is
        touched — the registry keeps its own enum and still refuses an unlisted
        coin with a reason — so this narrows what the prompt says, never what a
        call may do.

        With ``full=False``, which is what the world block publishes, each spec is
        reduced to its affordance index entry: id, kind, its author's own one-line
        description and its price. The ``args_schema`` — some 13,000 of the world
        block's characters, and the single largest thing in every prompt this
        world sends — is retrieved by ``catalogue.search`` when a seat means to
        call the tool. The index keeps every id and every description, so nothing
        becomes undiscoverable by being compacted, and the argument names a call
        must get right are read at the moment they are needed rather than carried
        past every decision that never calls the tool.
        """
        specs: list[dict[str, Any]] = []
        for tool_id, spec in self.tool_specs.items():
            schema = spec.get("args_schema", {})
            coin = schema.get("properties", {}).get("coin", {})
            if (tool_id in self.venue_tools.PUBLIC_READS and isinstance(coin, dict)
                    and "enum" in coin):
                coin = {**{k: v for k, v in coin.items() if k != "enum"},
                        "description": "any coin or pair the venue lists; "
                                       "venue.instruments lists them"}
                schema = {**schema, "properties": {**schema["properties"], "coin": coin}}
                spec = {**spec, "args_schema": schema}
            if not full:
                spec = {"id": spec.get("id", tool_id), "kind": spec.get("kind"),
                        "description": spec.get("description", ""),
                        "price_micro_per_call": spec.get("price_micro_per_call"),
                        "args": sorted(schema.get("properties", {}))}
            specs.append(spec)
        return specs

    # --- the seat sees itself (edition 3, C4) ----------------------------------

    def _record_spend(self, assembly_id: str, micro: int) -> None:
        """Add one seat's metered spend to its burn observation.

        The only writer is the invocation record's own call site, so what is
        counted is exactly what a seat was billed, never an estimate. The
        accumulator is two half-day buckets a seat, not a log: it costs the
        checkpoint two numbers per seat instead of one row per call, and it rides
        in ``stats``, so a runtime restored from a diary reports the burn that
        diary actually recorded rather than starting the observation again.
        """
        if type(micro) is not int or micro <= 0 or not assembly_id:
            return
        stats = self.stats
        index = self.clock.now_ns // SPEND_BUCKET_NS
        buckets = stats.spend_buckets.setdefault(str(assembly_id), {})
        buckets[str(index)] = buckets.get(str(index), 0) + micro
        for stale in [key for key in buckets if int(key) < index - 1]:
            del buckets[stale]
        if stats.spend_observed_from_ns is None:
            stats.spend_observed_from_ns = self.clock.now_ns

    def _observed_spend(self, assembly_id: str) -> tuple[int, int, int]:
        """Return (micro spent, micro spent in the current bucket, observed nanoseconds).

        The observed interval never claims more history than the world has: it
        starts at the first recorded spend or at the oldest bucket still kept,
        whichever is later, so a five-minute-old factory is not told it has
        watched itself for half a day.
        """
        stats = self.stats
        since = stats.spend_observed_from_ns
        if since is None:
            return 0, 0, 0
        now = self.clock.now_ns
        buckets = stats.spend_buckets.get(assembly_id, {})
        index = now // SPEND_BUCKET_NS
        kept = {int(key): value for key, value in buckets.items() if int(key) >= index - 1}
        oldest = min(kept, default=index) * SPEND_BUCKET_NS
        observed = max(0, now - max(since, oldest))
        return sum(kept.values()), kept.get(index, 0), observed

    def _burn(self, assembly_id: str, spendable: int) -> tuple[dict[str, Any], int | None]:
        """A runway range from observed spend, or why none is asserted.

        Guarantees no runway is stated from an interval shorter than
        ``MIN_BURN_OBSERVATION_NS``: a rate measured over a handful of calls is
        not evidence about a week, and the honest answer is that the history is
        insufficient. The range is not a confidence interval — it is the two
        rates actually observed, over the whole interval and over the current
        half-day bucket — so a seat that has just started spending faster sees
        the shorter number, and both are reproducible from the same buckets.
        """
        total, recent, observed = self._observed_spend(assembly_id)
        if observed < MIN_BURN_OBSERVATION_NS or total <= 0:
            return {"days_low": INSUFFICIENT, "days_high": INSUFFICIENT,
                    "observed_over": _duration_str(observed)}, None
        rates = [Decimal(total) / Decimal(observed) * NS_PER_DAY]
        later = max(1, min(observed, self.clock.now_ns % SPEND_BUCKET_NS))
        rates.append(Decimal(recent) / Decimal(later) * NS_PER_DAY)
        fastest, slowest = max(rates), min(rates)
        days = [Decimal(spendable) / rate if rate > 0 else None for rate in (fastest, slowest)]
        micro_per_ns = fastest / NS_PER_DAY
        return {"days_low": str(round(days[0], 2)) if days[0] is not None else INSUFFICIENT,
                "days_high": str(round(days[1], 2)) if days[1] is not None else INSUFFICIENT,
                "observed_over": _duration_str(observed)}, int(micro_per_ns * NS_PER_DAY)

    def _next_release(self) -> dict[str, Any]:
        """The next scheduled tranche: when, how much, and how the split is computed."""
        wallet = self.wallet
        schedule = wallet.release_schedule
        at_ns = wallet.next_release_ns
        if schedule is None or at_ns is None:
            return {"at_utc": None, "at_ns": None, "root_amount_usd": None,
                    "head_share_usd": None,
                    "rule": "no tranche remains on this wallet's release schedule"}
        amount = schedule.releases[wallet.released_tranches][1]
        lineages = self.budget.lineages()
        numerator, denominator = self.budget.base_share.as_integer_ratio()
        shared = amount * numerator // denominator
        per_head = shared // len(lineages) if lineages else 0
        return {"at_utc": _utc(at_ns), "at_ns": at_ns,
                "root_amount_usd": _usd(amount), "head_share_usd": _usd(per_head),
                "rule": f"base_share {self.budget.base_share} of the tranche, split equally "
                        f"across the {len(lineages)} live lineages, each share to that "
                        "lineage's head; the remainder stays unallocated. Only what the "
                        "unallocated pool actually holds is split, so shared spending can "
                        "leave a tranche short of this, and a lineage registered or retired "
                        "before the release changes the divisor"}

    def _provider_inventory(self) -> dict[str, Any]:
        """Each rail's inventory: what the manifest committed, and what was last observed.

        Guarantees the prompt builder performs no network I/O: the observed
        numbers come from ``Treasury.pots``, the cached read the treasury
        refreshed on its own schedule, and an unobserved balance is published as
        ``None`` rather than as zero. The committed side is the manifest's
        ``[providers]`` block (edition 3, C5), which is what the world was funded
        with; the two are shown apart because they answer different questions and
        because neither rail's balance can refill the other.
        """
        pots = self.wallet.pots()
        sellers = pots.get("sellers") or {}
        providers = getattr(self.m, "providers", None)
        return {"openrouter_usd": _usd(pots.get("seed")),
                "venice_usd": _usd(sellers.get("venice")),
                "x402_sellers_usd": {name: _usd(value) for name, value in sorted(sellers.items())
                                     if name != "venice"},
                "committed_at_launch": {
                    "openrouter_usd": _usd(getattr(providers, "openrouter_micro", None)),
                    "venice_usd": _usd(getattr(providers, "venice_micro", None)),
                },
                "complete": bool(pots.get("complete")),
                "as_of": "observed values are the runtime's last treasury read; the prompt "
                         "reads no rail. Committed values are the manifest's [providers] "
                         "block. An OpenRouter balance cannot pay for a Venice model."}

    def _world_resources(self) -> dict[str, Any]:
        """The factory's money, by class, with principal and income kept apart."""
        pots = self.wallet.pots()
        try:
            trading = str(self._tick_account().equity_usd)
        except RuntimeError:
            # The venue would not say. The reserve's pot is not the venue's equity,
            # so nothing is substituted for it; the custody block carries the
            # unavailable account and its reason.
            trading = None
        return {
            "root_unlocked_usd": _usd(self.wallet.unlocked),
            "root_locked_usd": _usd(self.wallet.locked),
            "unallocated_usd": _usd(self.budget.unallocated()),
            "trading_equity_usd": trading,
            "external_net_income_to_date": {
                "trading_usd": str(money_to_usd(self.realized_to_date)
                                   - money_to_usd(self.fees_to_date)
                                   + money_to_usd(self.funding_to_date)),
                "services_usd": _usd(pots.get("earned_micro")),
            },
            "principal_converted_to_compute_usd": _usd(
                pots.get("converted_from_principal_micro")),
            "provider_inventory": self._provider_inventory(),
            "hosting_paid_through_utc": self._hosting_paid_through(),
        }

    def _hosting_paid_through(self) -> str | None:
        """When the host this factory runs on is paid through, when a world states it."""
        paid = getattr(self.m, "hosting_paid_through_ns", None)
        return _utc(paid) if type(paid) is int else None

    def _seat_views(self) -> list[dict[str, Any]]:
        """Each live seat's own account of itself, one row per seat.

        Rows, not a map keyed by seat id: an id that keys something is an edge
        from an assembly to a fact about it, and the schematics' disclosure
        contract is that ids key nothing (A8). ``seat_id`` is a field like any
        other, and ``Request.prompt_text`` selects the acting seat's row.

        Guarantees every number is read from the kernel rather than asserted:
        the entitlement is ``BudgetBook.entitlement`` (already net of that seat's
        holds), the reservation is ``held_by``, the bills are the wallet's own
        uncertain ones attributed through ``handle_to_assembly``, and the spend
        is metered. The map carries every seat because one world block serves
        every request built in a tick; ``Request.prompt_text`` renders only the
        acting seat's entry, so no seat is shown another's account.
        """
        bills: dict[str, int] = {}
        for bill in self.wallet.uncertain_bills.values():
            seat = self.handle_to_assembly.get(bill.get("handle"))
            if seat:
                bills[seat] = bills.get(seat, 0) + int(bill.get("provisional_micro", 0))
        inventory = self._provider_inventory()
        release = self._next_release()
        heads = set(self.budget.heads())
        views: list[dict[str, Any]] = []
        for seat in self.budget.seats():
            assembly = self.assemblies.get(seat)
            spendable = self.budget.entitlement(seat)
            rail = _rail_for_model(assembly.spec.model_id if assembly else "")
            credit = {"openrouter": inventory["openrouter_usd"],
                      "venice": inventory["venice_usd"]}.get(rail)
            runway, per_day = self._burn(seat, spendable)
            spent, _recent, observed = self._observed_spend(seat)
            share = release["head_share_usd"] if seat in heads else _usd(0)
            reachable = "unknown"
            if per_day is not None and release.get("at_ns") is not None:
                need = Decimal(max(0, release["at_ns"] - self.clock.now_ns)) / NS_PER_DAY
                reachable = "yes" if Decimal(per_day) * need <= spendable else "no"
            views.append({
                "seat_id": seat,
                "lineage_id": self.budget.lineage(seat),
                "capability_version": assembly.spec.version if assembly else None,
                "your_resources": {
                    "spendable_entitlement_usd": _usd(spendable),
                    "reserved_for_open_work_usd": _usd(self.budget.held_by(seat)),
                    "unsettled_bills_usd": _usd(bills.get(seat, 0)),
                    "unsettled_bills_note": "booked at their ceiling; the true cost is "
                                            "unknown until the provider reports it",
                    "provider_credit_available_for_this_route_usd": credit,
                    "this_route": rail,
                    "spend_last_24h_usd": (_usd(spent) if observed >= MIN_BURN_OBSERVATION_NS
                                           else INSUFFICIENT),
                    "next_endowment_release": {
                        "at_utc": release["at_utc"],
                        "root_amount_usd": release["root_amount_usd"],
                        "your_share_usd": share,
                        "rule": release["rule"],
                    },
                    "runway_at_observed_burn": runway,
                    "next_release_reachable": reachable,
                },
                "open_commitments": self._open_commitments(seat),
            })
        return views

    def _open_commitments(self, seat: str) -> dict[str, Any]:
        """This seat's outstanding decisions and sealed, unsettled forecasts.

        Handles are the seat's own, so naming them discloses nothing about
        anyone else; the forecasts carry the predicate and the event they are due
        at, which is what makes a promise checkable rather than a feeling.
        """
        decisions = [
            {"handle": d.handle, "channel": d.channel, "deadline_utc": _utc(d.deadline_ns),
             "opened_utc": _utc(d.opened_ns), "cost_ceiling_usd": _usd(d.cost_ceiling)}
            for d in self.queue.outstanding()
            if d.actor == seat or self.handle_to_assembly.get(d.handle) == seat
        ]
        forecasts = [
            {"handle": f.handle, "about_handle": f.about_handle, "predicate": f.predicate_id,
             "q": f.q, "due_at_event": f.due_at_event}
            for f in self.book.pending() if f.evaluator_id == seat
        ]
        return {"open_decisions": decisions[-DIRECTORY_PAGE:],
                "open_decision_count": len(decisions),
                "sealed_forecasts": forecasts[-DIRECTORY_PAGE:],
                "sealed_forecast_count": len(forecasts),
                "events_so_far": self.n}

    def _continuity_block(self) -> dict[str, Any]:
        """What changed in shared memory, and how old the market data is.

        The seat's own working state and its unread outcomes are request inputs
        (edition 3 C1), not world facts, and ``Request.prompt_text`` joins them to
        this block; what a world can say for everyone is what the directory holds
        and when each price was last seen.
        """
        return {
            "shared_directory_changes": self._directory_changes(),
            "market_data_as_of": self._market_data_as_of(),
        }

    def _directory_changes(self) -> dict[str, Any]:
        """A bounded preview of the shared directory; the list tools page the rest."""
        notes = sorted(
            ({"key": key, "bytes": entry["bytes"], "version": entry["version"],
              "owner": entry.get("owner"), "updated_window": entry.get("window")}
             for key, entry in self.notes.items()),
            key=lambda row: (-(row["updated_window"] or 0), row["key"]))
        artifacts = self._artifact_index()
        return {
            "notes": {"count": len(notes), "newest": notes[:DIRECTORY_PREVIEW]},
            "artifacts": {"count": len(artifacts),
                          "newest": [{k: row[k] for k in
                                      ("sha", "kind", "bytes", "owner", "public")}
                                     for row in artifacts[:DIRECTORY_PREVIEW]]},
            "paging": f"note.list and artifact.list return {DIRECTORY_PAGE} rows a page with "
                      "a cursor; both are indexes, not contents",
        }

    def _market_data_as_of(self) -> dict[str, Any]:
        """Per traded coin: when its mid was last seen, and whether that is stale or missing.

        Staleness is measured against this world's own tick, not a constant: a
        world that wakes every ten minutes calls a five-minute-old print fresh
        and a one-minute world does not.
        """
        now = self.clock.now_ns
        limit = 2 * self.tick_clock.interval_ns
        out: dict[str, Any] = {}
        for coin in sorted(self.venue_tools.coins) + sorted(self.venue_tools.spot_pairs):
            prints = self.recent_mids.get(coin)
            if not prints:
                out[coin] = {"as_of_utc": None, "age": None, "missing": True, "stale": True}
                continue
            age = max(0, now - int(prints[-1]["t_s"]) * 1_000_000_000)
            out[coin] = {"as_of_utc": _utc(int(prints[-1]["t_s"]) * 1_000_000_000),
                         "age": _duration_str(age), "missing": False, "stale": age > limit}
        return out

    def _mechanics_block(self) -> dict[str, Any]:
        """Expose the committed parameters and operative formulas without learner state.

        Guarantees every number here is one the manifest committed or an amendment
        activated, and none is one the runtime's own adaptation moves between
        calls: the sampling actuator's consequence mix and the immune
        controller's decay are named here and published in
        ``world.adaptive_scoring``, which moves with them. That is what lets this
        block sit in the prompt's stable prefix, which an adaptation must not
        invalidate.
        """
        pr, nov = self.m.prices, self.m.novelty
        return {
            "tools": {"max_depth": self.m.tools.max_depth,
                      "max_children": self.m.tools.max_children,
                      "max_tool_calls": self.m.tools.max_tool_calls,
                      "continuations_per_request": 1},
            "committee": {
                "seats": self.m.committee.seats,
                "threshold": "floor(number of seated delegates / 2) + 1 yes votes",
                "min_settled": self.m.committee.min_settled,
                "eligibility": "distinct independently requested decisions with settled "
                "consequences; the proposer's assembly is excluded",
                "liability": "yes votes forecast the predicted direction; no votes its negation. "
                "Brier = 1 - (vote - outcome)^2, measured at the declared window after activation "
                "against the pre-activation value. No activation or missing evidence is censored. "
                "Feedback returns to the voting assembly's durable identity. Retirements "
                "use the same eligibility, draw, majority and cadence; with no predicted "
                "effect in a retire proposal, their ballots are unscored and censored.",
            },
            "novelty": {"share": nov.share, "window_ns": nov.window_ns,
                        "window": _duration_str(nov.window_ns),
                        "trials": nov.trials,
                        "max_lifetime_windows": nov.max_lifetime_windows},
            "controller": {
                "eta": pr.eta, "kappa": pr.kappa, "decay": pr.decay,
                "lambda_max": pr.lambda_max, "min_window_events": pr.min_window_events,
                "penalty_cap": getattr(pr, "penalty_cap", None),
                "recurrence": "v = distance outside the inclusive region / scale; "
                "if v > 0: lambda' = clip(lambda + eta*v - kappa*max(0, v_previous-v), "
                "0, lambda_max); otherwise lambda' = max(0, lambda-decay)",
            },
            "cascade": {"min_ratio": self.m.timing.min_ratio,
                        "jitter_fraction": self.m.timing.jitter_fraction},
            "consequence_mix": self.ev.consequence_share,
            "adaptive": "the committed values are here; the two the runtime moves between "
            "calls — the consequence mix the sampling actuator raises and steps back, and "
            "the decay the immune controller borrows — are in world.adaptive_scoring, and "
            "controller.decay and consequence_mix above are what they were committed at",
            "treasury": {"max_venice_per_window_micro": self.m.treasury.max_venice_per_window,
                         "venice_tranche_usd": "5",
                         "cctp_forwarding": self.m.treasury.cctp_forwarding,
                         "max_forward_fee_micro": self.m.treasury.max_forward_fee_micro,
                         "max_forward_fees_per_window_micro":
                             self.m.treasury.max_forward_fees_per_window,
                         "forward_wait_windows": self.m.treasury.forward_wait_windows,
                         "exit_route": "to_reserve burns USDC on HyperCore and mints it on "
                         "Base. Spot HYPE in the venue account pays the Core gas charge: buy it "
                         "on HYPE/USDC; HYPE spent as that charge is not a fill. The mint is "
                         "self-paid when the reserve holds Base ETH; otherwise Circle forwards "
                         "it for the on-chain fee quoted in pots.gas, bounded per transfer and "
                         "per reserve window. pots.gas names the branch and any blocker. A "
                         "forwarded mint unobserved for forward_wait_windows reserve windows "
                         "strands recoverably (pots.stranded): its burned principal stays "
                         "held and re-checked, and new transfers are admitted.",
                         "return_route": "to_venue needs reserve Base ETH and HyperEVM HYPE "
                         "and is refused with a public reason without them; nothing buys "
                         "that gas."},
            "tick_bounds_ns": {"min": self.m.clock.min_tick_ns, "max": self.m.max_tick_ns},
            "measurement": "Select the latest n completed returns, settled forecasts or closed "
            "windows. per=null pools the factory; role/assembly partitions responders' or "
            "forecasters' own samples, filtering roles by answers_for unless all. "
            "The controller receives the equal mean of supported scopes; "
            "fewer than n samples is unmeasured. Closed-window ratios recompute "
            "their denominators.",
        }

    def _adaptive_scoring_block(self) -> dict[str, Any]:
        """The scoring values in force this window: the ones the runtime's adaptation moves.

        Guarantees every value an actuator or a controller can change between two
        calls of one charter edition is published here and inlined nowhere in the
        stable world block, so a live adaptation is visible to the population in
        the same call it takes effect and still leaves the prompt's cached prefix
        byte-identical. What each value was committed at stays in
        ``world.mechanics``.
        """
        return {
            "consequence_mix": getattr(self, "consequence_mix", self.ev.consequence_share),
            "controller_decay": self.controller.snapshot()["parameters"]["decay"],
            "committed": "world.mechanics carries the committed value of each of these; a "
            "difference is this runtime's own adaptation, not an amendment",
        }

    @staticmethod
    def _register_schema() -> dict[str, Any]:
        return {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"kind": {"enum": ["model", "assembly", "router", "tool",
                                                   "observation", "predicate", "learner",
                                                   "amendment", "retire", "connector",
                                                   "market", "challenge", "service"]}},
                "required": ["kind"],
            },
        }


    def _forecast_schema(self) -> dict[str, Any]:
        return {
            "type": "array",
            "maxItems": self.ev.max_forecasts_per_verdict,
            "items": {
                "type": "object",
                "properties": {
                    "predicate": {"enum": [p.id for p in self.predicates.all()]},
                    "params": {
                        "type": "object",
                        "properties": {"horizon_events": {"type": "integer", "minimum": 1}},
                    },
                    "q": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["predicate", "params", "q"],
            },
        }


    def _scoring_block(self) -> dict[str, Any]:
        """How decisions settle, stated as facts about the world (schematics are
        public; no goals). Run 7 showed judges grading conformity alone because nothing told
        them a verdict is also a forecast, and producers reinforced by verdicts that never
        answered to money. Every formula here is the one the runtime applies.

        Guarantees the formulas name the weights the runtime's adaptation moves
        rather than quoting them, so this block holds still between calls of one
        charter edition and can sit in the prompt's stable prefix; the weight in
        force is in ``world.adaptive_scoring``.
        """
        ev = self.ev
        return {
            "producer_or_antagonist_return": (
                "ProducerReturn and custom return kinds settle on the verdict channel: "
                "the score is the verdict (0 to 1) a judging return "
                f"gives it within {ev.verdict_timeout_events} events, less the card penalty; "
                "unjudged returns are censored (no score, no learning)"
            ),
            "antagonist_exposure": (
                "an Exposure return settles on the exposure channel, less the antagonist's "
                "card penalty: 1 only when the judge's mandatory payoff forecast about it "
                "scored a worse Brier than the prevalence baseline and the antagonist's own "
                "payoff forecast about it scored better; a judge's optional forecasts never "
                "count; otherwise 0 once nothing about the return is pending"
            ),
            "antagonist_routing": (
                "router probability mass on contracts declaring Exposure is renormalised to "
                "at most "
                f"{ev.adversarial_share} before every draw"
            ),
            "verdict_and_payoff": (
                "an evaluator gives two numbers: verdict (charter quality) settles the judged "
                "return and is graded by meta conformity; payoff is sealed as a forecast with "
                "q = payoff that return_paid_off resolves true for the judged return and is "
                "graded by Brier against the realised predicate; the two never substitute for "
                "each other"
            ),
            "return_paid_off": (
                "the kernel's consequence predicate about a return, resolved 1 or 0 by the "
                "runtime once the return's consequence is fixed; the payoff field is the only "
                "forecast sealed about it and it cannot be proposed"
            ),
            "payoff_standing": (
                "mean Brier of the evaluator's payoff forecasts minus the prevalence "
                "baseline's, capped below minimum coverage; it enters selection among contracts "
                "declaring Verdict on any accepted event kind with "
                f"weight consequence_mix (committed {ev.consequence_share}; the weight in "
                "force this window is world.adaptive_scoring.consequence_mix) beside the "
                "learned selection; when the verdict mean "
                f"rises while payoff skill falls over {self.m.immune.k} windows the mix rises by "
                f"{ev.sampling_step} for the next window, capped at {ev.sampling_cap}, and "
                "steps back otherwise"
            ),
            "evaluator_return": (
                "settles on the conformity channel: the score a meta gives the verdict within "
                f"{ev.verdict_timeout_events} events, less the card penalty; metas judge one "
                f"verdict in every {self.m.timing.min_ratio} (with jitter) as the window's "
                "representative; the representative settles at the meta's score and each "
                f"unread sibling at {ev.sibling_share} of it"
            ),
            "meta_return": (
                "a top-tier meta's conformity c is graded by Brier 1 - (c - y)^2 where y = 1 "
                "when the judged verdict's payoff forecast scored at least the prevalence "
                "baseline; a malformed conformity settles 0; a lower tier settles on "
                "conformity like an evaluator"
            ),
            "card_penalty": (
                "v_j = distance outside card j's inclusive region / observation.scale; "
                "S = sum(lambda_j * v_j) over cards for the settlement's role or all; "
                "each role's cards use their declared typed windows. "
                f"penalty = min(S, {self.m.prices.penalty_cap}) * share; "
                "share = sum(lambda_j * v_j * share_j) / S (zero when S = 0). "
                "share_j is the decision's own cost, malformed-return deficit (well-formed "
                "count for an upper-bound violation), tool attempts or filled notional "
                "divided by that observation's window total; otherwise "
                "1/n decisions for that role. A zero total contributes zero. "
                "Closed decision windows retain their observations; open windows use the last "
                "closed observations with current contribution totals. "
                "score = clip(raw_score - penalty, 0, 1). Prices and region scales are in "
                "card_prices. "
                "Stable failure halves effective lambda on violated cards for the next window; "
                "underlying duration pressure is retained. Duplicate observations on overlapping "
                "roles are refused in amendments."
            ),
            "propensity": (
                "every decision carries two propensities: the router's distribution over "
                "which assembly to wake, and the woken assembly's own distribution over its "
                "own actions, as the return declared it. It is logged on the decision's "
                "handle, it travels forward on the request about that return, and where the "
                "assembly registered a learner it is the behaviour policy that learner is trained "
                "against: reward r on action a updates it with weight r / propensity(a)"
            ),
            "observations": (
                "a card's observation is a seed measurement or one the population "
                "registered. A registered observation's code runs in the tool jail over "
                "the public facts of a closed window, with no attribution and no private "
                "learner state in them; it is admitted only after it returns a finite "
                "number for the last closed window, and its declared range is the scale a "
                "card's violation is divided by. A card naming an unregistered observation "
                "is refused before the vote"
                ". Window facts include mids (micro-USD), funding (dimensionless), "
                "wallet_balance_micro and tick_timestamps_ns. Coin series carry nanosecond "
                "timestamps. Paid venue reads also contribute books (price in micro-USD, "
                "size in base units) and funding history for listed markets; each series "
                "retains at most 1024 samples. venue.instruments lists public markets; "
                "a market proposal adds trading permission for a listed coin or pair"
            ),
            "revision": (
                "a producer return counts as a revision only when a registration it carried "
                "was accepted; an activated amendment counts in its window; refused proposals "
                "and tool calls do not"
            ),
            "venue_and_treasury_writes": (
                "venue.place_market, venue.place_limit, venue.close, venue.cancel, "
                "venue.set_leverage and treasury.transfer act only for a decision with an open "
                "consequence account and producing return kind; judging decisions and their "
                "children cannot write to the venue or treasury. Nothing judges its own "
                "output, its ancestors' output or a descendant it requested"
            ),
            "policy": self._mechanics_block()["committee"]["liability"],
            "novelty_reserve": (
                "registrations draw on the novelty reserve at the trial amount; a refused "
                "proposal returns its trial to the window and carries a reason in "
                "registration_feedback; a registered assembly keeps protected compute until "
                f"{self.m.novelty.trials} settled consequences have been delivered to it or "
                f"{self.m.novelty.max_lifetime_windows} windows have passed since registration "
                "(continuations and children do not count; a learning-death window grants one "
                "more)"
            ),
        }
