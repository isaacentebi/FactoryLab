"""Public world facts and return schemas visible to every assembly."""

from __future__ import annotations

from typing import Any

from factorylab.charter.measurement import measurement_catalogue
from factorylab.cortex.assembly import reserved_return_fields
from factorylab.kernel.money import money_to_usd
from factorylab.runtime.observations import window_fact_names
from factorylab.runtime.propensity import action_vocabulary
from factorylab.runtime.shared import PRODUCER_KINDS
from factorylab.runtime.summary import _duration_str, _price_str
from factorylab.settlement import SEED_VOCABULARY


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
        "tool": {
            "kind": "tool",
            "id": "slug",
            "description": "what it computes",
            "args_schema": {"type": "object", "properties": {"x": {"type": "number"}}},
            "code": "python: read a JSON object from stdin, print a JSON object",
            "timeout_s": 2,
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
            "assembly_id": "a registered assembly id",
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
        "propensity": (
            "optional on any return: your own distribution over the actions you were "
            "choosing among, as {action_id: probability} summing to one and including "
            "the action you took (see action_labels for the shape of an action id). It "
            "travels forward on the request about this return, so the judges of this "
            "return read it"
        ),
        "register": "a list of up to three proposals, including amendments, shaped like "
        "proposal_shapes; router add=false replaces, add=true adds a router. Learners: exp3 or "
        "blum_mansour. Assembly roles: producer, evaluator, meta, antagonist; effort: low, medium, "
        "high. An observation registers a measurement: its code runs in the tool jail over a "
        "closed window's public facts and is admitted only if it produces a finite number on "
        "the last closed window; a card may then name it, and re-registering the same id "
        "supersedes it with a new version. A learner gives one assembly a learner over the "
        "action set it declares, trained by that assembly's declared propensities and the "
        "rewards its decisions settle at. Cards answer for producer, evaluator, meta, "
        "antagonist or all; window is "
        "{kind: returns|forecasts|windows, n: positive integer, per: role|assembly|null}. "
        "Insufficient samples are unmeasured. Lambda is optional and bounded by prices.lambda_max; "
        "tick_interval is an optional duration within world.clock bounds. A prediction names a "
        "card_id, direction (increase or decrease), and a positive window count after activation. "
        "Unmeasurable windows, duplicate role/observation bindings and unchanged amendments "
        "are refused before a vote.",
        "tool_calls": (
            'a list of {"tool": id, "args": {...}} (max 4); results come back in a second call'
        ),
        "requests": (
            'up to two objects: {"target":"assembly-id or self","description":"task",'
            '"inputs":{},"outcome_schema":{"type":"object"}}; targets answer once, '
            'without further requests. Outputs arrive in tool_results as '
            '{"tool":"assembly:<target>","args":<inputs>,"result":{"outputs":{},'
            '"status":"ok","cost_micro":0}} before your second call. '
            'Outcome schemas support object/array/scalar types, properties, required, enum, '
            'minimum, maximum, minItems, maxItems and additionalProperties.'
        ),
    }


    def _world_block(self) -> dict[str, Any]:
        """Facts about the world any assembly may see. No rules, no goals, no private state."""
        try:
            acct = self.exchange.account()
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
        except RuntimeError:
            account = {"equity_usd": str(money_to_usd(self.wallet.balance)), "positions": []}
        account["realized_pnl_usd_to_date"] = str(money_to_usd(self.realized_to_date))
        account["fees_usd_to_date"] = str(money_to_usd(self.fees_to_date))
        account["funding_usd_to_date"] = str(money_to_usd(self.funding_to_date))
        return {
            "wallet_balance_usd": str(money_to_usd(self.wallet.balance)),
            "pots": self.wallet.pots(),
            "charter_edition": self.charter.edition,
            "charter": self._charter_text(),
            "mechanics": self._mechanics_block(),
            "recent_mids": {c: list(v) for c, v in self.recent_mids.items()},
            "account": account,
            "venue": self.exchange.instruments(),
            "tools": list(self.tool_specs.values()),
            "population_tools": {
                "available": self.tool_jail_available,
                "reason": None if self.tool_jail_available else "no jail on this host",
            },
            "observations": measurement_catalogue(self.observations),
            "observation_facts": window_fact_names(),
            "action_labels": action_vocabulary(),
            "reserve": {"protected": self.reserve.remaining(), "units": "micro-USD",
                        "trials": self.m.novelty.trials,
                        "max_lifetime_windows": self.m.novelty.max_lifetime_windows},
            "committee": self._mechanics_block()["committee"],
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
                    "count": sum(kind in a.spec.accepts for a in self.assemblies.values()),
                }
                for kind in sorted({k for a in self.assemblies.values() for k in a.spec.accepts})
            ],
            "routers": [
                {"event_kind": kind, "count": len(states)}
                for kind, states in sorted(self.routers.items())
            ],
            "clock": {
                "tick_interval": _duration_str(self.tick_clock.interval_ns),
                "min_tick": _duration_str(self.m.clock.min_tick_ns),
                "max_tick": _duration_str(self.m.max_tick_ns),
            },
            "governance": self.cadence.world_block(self.tick_clock.interval_ns),
            "registration_feedback": list(self.registration_feedback),
            "reserved_return_fields": reserved_return_fields(),
            "scoring": self._scoring_block(),
            "prices": {"lambda_max": self.m.prices.lambda_max,
                       "penalty_cap": self.m.prices.penalty_cap},
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
            "event_kinds": sorted(PRODUCER_KINDS | {"ProducerReturn", "Verdict", "MetaVerdict"}),
            "meta_input": (
                "A meta judges the released representative verdict. Its window describes "
                "the arrivals it represents: count, mean score, min, max, and decision handles."
            ),
            "a_return_may_include": self.A_RETURN_MAY_INCLUDE,
            "proposal_shapes": self.PROPOSAL_SHAPES,
        }

    def _charter_text(self) -> str:
        """Every duplicate charter disclosure uses the same current controller prices."""
        return self.charter.render({c.id: self.controller.price(c.id) for c in self.charter.cards})

    def _mechanics_block(self) -> dict[str, Any]:
        """Expose the committed parameters and operative formulas without learner state."""
        pr, nov = self.m.prices, self.m.novelty
        return {
            "committee": {
                "seats": self.m.committee.seats,
                "threshold": "floor(number of seated delegates / 2) + 1 yes votes",
                "min_settled": self.m.committee.min_settled,
                "eligibility": "distinct independently requested decisions with settled "
                "consequences; the proposer's assembly is excluded",
                "liability": "yes votes forecast the predicted direction; no votes its negation. "
                "Brier = 1 - (vote - outcome)^2, measured at the declared window after activation "
                "against the pre-activation value. No activation or missing evidence is censored. "
                "Feedback returns to the voting assembly's durable identity.",
            },
            "novelty": {"share": nov.share, "window_ns": nov.window_ns,
                        "window": _duration_str(nov.window_ns),
                        "trials": nov.trials,
                        "max_lifetime_windows": nov.max_lifetime_windows},
            "controller": {
                "eta": pr.eta, "kappa": pr.kappa, "decay": self.controller.snapshot()[
                    "parameters"]["decay"],
                "lambda_max": pr.lambda_max, "min_window_events": pr.min_window_events,
                "penalty_cap": getattr(pr, "penalty_cap", None),
                "recurrence": "v = distance outside the inclusive region / scale; "
                "if v > 0: lambda' = clip(lambda + eta*v - kappa*max(0, v_previous-v), "
                "0, lambda_max); otherwise lambda' = max(0, lambda-decay)",
            },
            "cascade": {"min_ratio": self.m.timing.min_ratio,
                        "jitter_fraction": self.m.timing.jitter_fraction},
            "consequence_mix": getattr(self, "consequence_mix", self.ev.consequence_share),
            "treasury": {"max_venice_per_window_micro": self.m.treasury.max_venice_per_window,
                         "venice_tranche_usd": "5"},
            "tick_bounds_ns": {"min": self.m.clock.min_tick_ns, "max": self.m.max_tick_ns},
            "measurement": "Select the latest n completed returns, settled forecasts or closed "
            "windows. per=null pools the factory; role/assembly partitions responders' or "
            "forecasters' own samples, filtering roles by answers_for unless all. "
            "The controller receives the equal mean of supported scopes; "
            "fewer than n samples is unmeasured. Closed-window ratios recompute "
            "their denominators.",
        }


    @staticmethod
    def _register_schema() -> dict[str, Any]:
        return {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"kind": {"enum": ["model", "assembly", "router", "tool",
                                                   "observation", "learner", "amendment"]}},
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
                    "predicate": {"enum": [p.id for p in SEED_VOCABULARY]},
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
        """How decisions settle, stated as facts about the world (v0.4 §1.6: schematics are
        public; no goals). Run 7 showed judges grading conformity alone because nothing told
        them a verdict is also a forecast, and producers reinforced by verdicts that never
        answered to money. Every formula here is the one the runtime applies."""
        ev = self.ev
        return {
            "producer_or_antagonist_return": (
                "settles on the verdict channel: the score is the verdict (0 to 1) an evaluator "
                f"gives it within {ev.verdict_timeout_events} events, less the card penalty; "
                "unjudged returns are censored (no score, no learning)"
            ),
            "antagonist_exposure": (
                "an antagonist return settles on the exposure channel, less the antagonist's "
                "card penalty: 1 only when the judge's mandatory payoff forecast about it "
                "scored a worse Brier than the prevalence baseline and the antagonist's own "
                "payoff forecast about it scored better; a judge's optional forecasts never "
                "count; otherwise 0 once nothing about the return is pending"
            ),
            "antagonist_routing": (
                "router probability mass on antagonist assemblies is renormalised to at most "
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
                "baseline's, capped below minimum coverage; it enters evaluator selection with "
                f"weight consequence_mix (now {self.consequence_mix}, manifest "
                f"{ev.consequence_share}) beside the learned selection; when the verdict mean "
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
            ),
            "revision": (
                "a producer return counts as a revision only when a registration it carried "
                "was accepted; an activated amendment counts in its window; refused proposals "
                "and tool calls do not"
            ),
            "venue_and_treasury_writes": (
                "venue.place_market, venue.place_limit, venue.close, venue.cancel, "
                "venue.set_leverage and treasury.transfer act only for a decision with an open "
                "consequence account (producer, antagonist and child returns); a judging "
                "decision is refused; nothing judges its own output or its child's"
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
