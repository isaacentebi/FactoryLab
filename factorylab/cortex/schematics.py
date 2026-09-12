"""Public world facts and return schemas visible to every assembly."""

from __future__ import annotations

from typing import Any

from factorylab.cortex.assembly import reserved_return_fields
from factorylab.kernel.money import money_to_usd
from factorylab.runtime.observations import catalogue
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
        "amendment": {
            "kind": "amendment",
            "id": "slug",
            "add": [
                {
                    "id": "card-id",
                    "norm": "one of the charter norms",
                    "description": "what is measured",
                    "units": "…",
                    "window": "…",
                    "acceptable_region": "…",
                    "observation": "one of world.observations ids",
                    "answers_for": "producer",
                    "lambda": 0.1,
                }
            ],
            "replace": [],
            "remove": ["card-id"],
            "predicted_effect": "what you expect to change and why",
            "tick_interval": "30s",
        },
    }


    A_RETURN_MAY_INCLUDE: dict[str, str] = {
        "action": (
            '"noop" | "hold" | "order"; an "order" return also carries "coin" (one of the '
            'world\'s coins), "side" ("buy" | "sell") and "size" (base units as a decimal '
            'string, e.g. "0.005"), and is placed at market on return; limit, reduce-only, '
            "close, leverage and cancel are tool_calls on the venue.* tools"
        ),
        "order_example": '{"action": "order", "coin": "ETH", "side": "buy", "size": "0.004"}',
        "register": "a list of up to three proposals, including amendments, shaped like "
        "proposal_shapes; router add=false replaces, add=true adds a router. Learners: exp3 or "
        "blum_mansour. Assembly roles: producer, evaluator, meta, antagonist; effort: low, medium, "
        "high. Cards answer for producer, evaluator, meta or all; lambda is optional and bounded "
        "by prices.lambda_max; tick_interval is an optional duration within world.clock bounds.",
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
            "recent_mids": {c: list(v) for c, v in self.recent_mids.items()},
            "account": account,
            "tools": list(self.tool_specs.values()),
            "population_tools": {
                "available": self.tool_jail_available,
                "reason": None if self.tool_jail_available else "no jail on this host",
            },
            "observations": catalogue(),
            "reserve": {"protected": self.reserve.remaining(), "units": "micro-USD",
                        "trial_invocations": self.m.novelty.trial_invocations},
            "committee": {"min_settled": self.m.committee.min_settled,
                          "eligibility": "assemblies with at least min_settled settled decisions"},
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
                    "id": a.spec.id,
                    "role": a.spec.role,
                    "model_id": a.spec.model_id,
                    "accepts": sorted(a.spec.accepts),
                }
                for a in self.assemblies.values()
            ],
            "routers": [
                {"event_kind": st.kind, "learner": type(st.learner).__name__, "menu": st.universe}
                for st in self._all_router_states()
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
            "prices": {"lambda_max": self.m.prices.lambda_max},
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


    @staticmethod
    def _register_schema() -> dict[str, Any]:
        return {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"kind": {"enum": ["model", "assembly", "router", "tool",
                                                   "amendment"]}},
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
        answered to money."""
        ev = self.ev
        return {
            "producer_or_antagonist_return": (
                "settles on the verdict channel: the score is the verdict (0 to 1) an evaluator "
                f"gives it within {ev.verdict_timeout_events} events, less the card penalty; "
                "unjudged returns are censored (no score, no learning)"
            ),
            "antagonist_exposure": (
                "an antagonist return also settles 1 on the exposure channel when a judge's "
                "forecast about it scores worse than the prevalence baseline, else 0"
            ),
            "verdict": (
                "a verdict is also sealed as a forecast, with q = verdict, that the judged "
                "return pays off; it settles on the evaluator's consequence standing"
            ),
            "return_paid_off": (
                "1 when the lots the return's own fills opened are closed with realized gain "
                "net of fees, funding and the return's own compute cost; a return that placed "
                "no fills settles 0; lots still open after "
                f"{ev.consequence_backstop_events} events are marked to mid"
            ),
            "consequence_standing": (
                "Brier score of the evaluator's forecasts against the prevalence baseline; "
                f"it enters evaluator selection with weight {ev.consequence_share} beside the "
                "learned selection"
            ),
            "evaluator_return": (
                "settles on the conformity channel: the score a meta gives the verdict within "
                f"{ev.verdict_timeout_events} events, less the card penalty; metas judge one "
                f"verdict in every {self.m.timing.min_ratio} (with jitter) as the window's "
                "representative and its score settles the whole window"
            ),
            "meta_return": (
                "settles on the fast channel (well-formed = 1) unless a higher tier of metas "
                "exists, in which case on conformity like an evaluator"
            ),
            "card_penalty": (
                "each priced metric card subtracts its price times the window's violation "
                "from verdict and conformity scores; prices are in card_prices"
            ),
            "novelty_reserve": (
                "registrations draw on the novelty reserve at the trial amount; refused "
                "proposals carry a reason in registration_feedback"
            ),
        }
