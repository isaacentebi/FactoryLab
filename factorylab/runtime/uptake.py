"""Anticipatory settlement for the factory's non-market exploration (time audit T18).

Essay II.IV.b: "the compensation period of any exploratory learner must be shorter
than the lifetime of the things it is being compensated for discovering, which can
either be engineered through anticipatory settlement (the futarchic prediction
market) or through engineering a kind of guaranteed patience". Venue and event lots
already settle early at their mark; a registration (a tool, an observation, an
assembly or service) was paid only by its trials and by realized uptake, long after
the exploration that made it.

Here a registration is forecast. Judging seats post ``uptake_forecasts``: q, the
probability that another lineage takes the registration up before its patience
ends (a tool called by another lineage, an observation a charter card names, an
assembly invoked). Each post is its own policy decision, scored by Brier when the
world resolves it, as the charter's motion forecasts are. The registering seat
holds its own uptake decision, which settles early, at the first window close
with a forecast, at the forecasters' standing-weighted median q; on realization a
correction decision settles ``1/2 + (y - q)/2``, so the early payment is corrected
and never paid twice. With no forecast before realization, the uptake decision
settles at the outcome itself. Every score reaches its seat's durable identity
through the ordinary reward channel and nowhere else. The standing weights and
the median are the λ market's (``charter.market``).
"""

from __future__ import annotations

from math import isfinite
from typing import Any

from factorylab.charter.market import brier, standing, weighted_median
from factorylab.cortex.registration import (
    AssemblyProposal,
    ObservationProposal,
    ServiceProposal,
    ToolProposal,
    measured_role,
)
from factorylab.kernel.queue import PropensityRecord, SettleStatus

UPTAKE_FORECAST = "uptake-forecast-brier-v1"
UPTAKE_ANTICIPATED = "uptake-anticipated-v1"
UPTAKE_CORRECTION = "uptake-correction-v1"
UPTAKE_REALIZED = "uptake-realized-v1"
#: The roles whose seats judge: the forecasters of uptake ("judges' payoff forecasts").
JUDGING_ROLES = frozenset({"evaluator", "meta", "adversary"})

UPTAKE_RETURN_FIELDS = {
    "uptake_forecasts": (
        "optional on a judging seat's return: a list of {registration, q}; registration "
        "is an entry of world.uptake, q the probability in [0, 1] that another lineage "
        "takes it up before its until_tick. One per seat and registration. Each opens "
        "its own policy decision; its score is in world.mechanics.uptake"
    ),
}


def _subject(prop: Any) -> tuple[str, str] | tuple[None, None]:
    """The registration a proposal makes, as (kind, id), or (None, None)."""
    if isinstance(prop, ToolProposal):
        return "tool", prop.id
    if isinstance(prop, ObservationProposal):
        return "observation", prop.id
    if isinstance(prop, AssemblyProposal):
        return "assembly", prop.id
    if isinstance(prop, ServiceProposal):
        return "assembly", prop.program_id
    return None, None


class UptakeMixin:
    """Forecasts on registrations' uptake, and the registering seat's early settlement."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # Open registrations by "kind:id", and each forecaster's settled record (n, sum).
        self.uptake: dict[str, dict[str, Any]] = {}
        self.uptake_standing: dict[str, tuple[int, float]] = {}
        self.A_RETURN_MAY_INCLUDE = {**self.A_RETURN_MAY_INCLUDE, **UPTAKE_RETURN_FIELDS}

    # --- registrations open, uptake is observed ------------------------------------

    def _register(self, handle: str, prop: Any, *, predicted_effect: Any = None) -> None:
        """A registration that succeeded opens its uptake record and its builder's decision."""
        super()._register(handle, prop, predicted_effect=predicted_effect)
        kind, ident = _subject(prop)
        builder = self._proposer_assembly(handle)
        if kind is not None and builder in self.assemblies:
            self._open_uptake(kind, ident, builder)

    def _open_uptake(self, kind: str, ident: str, builder: str) -> None:
        """Open a registration's uptake record, once, with its builder's uptake decision."""
        key = f"{kind}:{ident}"
        if key in self.uptake:
            return  # a new version keeps the exploration already open
        now = self.ticks_consumed
        until = now + self._patience()
        record = {"kind": kind, "id": ident, "builder": builder,
                  "lineage": self.budget.lineage(builder), "opened_tick": now,
                  "until_tick": until, "forecasts": [], "early": None, "taken": False,
                  "decision": self._uptake_decision(builder, f"uptake-{key}", until)}
        self.uptake[key] = record
        self.ledger.append({"kind": "uptake.open", "registration": key, "builder": builder,
                            "handle": record["decision"], "until_tick": until,
                            "ts": self.clock.now_ns})

    def _uptake_decision(self, assembly: str, event_id: str, until: int) -> str:
        """A policy decision owned by ``assembly``'s durable identity, open until ``until``."""
        lid = f"assembly:{assembly}"
        handle = self.queue.open(
            actor=lid, event_id=event_id,
            propensity=PropensityRecord((assembly,), (1.0,), assembly, 0, lid, "direct-uptake"),
            channel="policy", horizon_ticks=max(1, until - self.ticks_consumed),
            parent_handle=None, cost_ceiling=0)
        self.handle_to_assembly[handle] = assembly
        return handle

    def _taken_up(self, kind: str, ident: str, by: str) -> None:
        """Another lineage used an open registration: its uptake is realized."""
        record = self.uptake.get(f"{kind}:{ident}")
        if (record is not None and not record["taken"] and by in self.assemblies
                and self.budget.lineage(by) != record["lineage"]):
            record["taken"] = True
            self.ledger.append({"kind": "uptake.taken", "registration": f"{kind}:{ident}",
                                "tick": self.ticks_consumed, "ts": self.clock.now_ns})

    def _run_tool(self, action_id: str, handle: str, call: dict[str, Any], *,
                  slot: str = "tool:0") -> tuple[dict, int]:
        result = super()._run_tool(action_id, handle, call, slot=slot)
        self._taken_up("tool", str(call.get("tool")), action_id)
        return result

    def _invoke(self, action_id, req, role, *, child=False):
        record = self.uptake.get(f"assembly:{action_id}")
        if record is not None and not record["taken"]:
            # A registered assembly drawn to work: someone other than its builder took
            # it up (the router or a requester, never the builder choosing itself).
            record["taken"] = True
            self.ledger.append({"kind": "uptake.taken", "registration": f"assembly:{action_id}",
                                "tick": self.ticks_consumed, "ts": self.clock.now_ns})
        return super()._invoke(action_id, req, role, child=child)

    # --- forecasts -------------------------------------------------------------------

    def _apply_registrations(self, handle: str, ret: Any) -> None:
        """Every ordinary return may carry uptake forecasts, read after its registrations."""
        super()._apply_registrations(handle, ret)
        if ret.status == "ok" and "uptake_forecasts" in ret.outputs:
            self._post_uptake_forecasts(handle, ret.outputs["uptake_forecasts"])

    def _post_uptake_forecasts(self, handle: str, raw: Any) -> None:
        """Admit each valid forecast as its own policy decision; refuse each invalid one."""
        seat = self._proposer_assembly(handle)
        if seat is None or seat not in self.assemblies:
            self._market_refused(handle, "uptake_forecast", "no forecasting seat")
            return
        if measured_role(self.assemblies[seat].spec.emits) not in JUDGING_ROLES:
            self._market_refused(handle, "uptake_forecast",
                                 "uptake is forecast by judging seats")
            return
        if not isinstance(raw, list) or not raw:
            self._market_refused(handle, "uptake_forecast",
                                 "uptake_forecasts must be a list of {registration, q}")
            return
        for item in raw:
            if not isinstance(item, dict) or set(item) != {"registration", "q"}:
                self._market_refused(handle, "uptake_forecast",
                                     "a forecast is exactly {registration, q}")
                continue
            key, q = item["registration"], item["q"]
            record = self.uptake.get(key) if isinstance(key, str) else None
            if record is None:
                self._market_refused(handle, "uptake_forecast",
                                     "registration must be open on world.uptake")
                continue
            if type(q) not in (int, float) or not isfinite(q) or not 0 <= q <= 1:
                self._market_refused(handle, "uptake_forecast", "q is a probability in [0, 1]")
                continue
            if self.budget.lineage(seat) == record["lineage"]:
                self._market_refused(handle, "uptake_forecast",
                                     "a lineage does not forecast its own registration")
                continue
            if any(f["assembly"] == seat for f in record["forecasts"]):
                self._market_refused(handle, "uptake_forecast",
                                     "one forecast per seat and registration")
                continue
            post = self._uptake_decision(seat, f"uptake-forecast-{key}", record["until_tick"])
            record["forecasts"].append({"handle": post, "assembly": seat, "q": float(q)})
            self.ledger.append({"kind": "uptake.forecast", "registration": key,
                                "handle": post, "parent": handle, "q": float(q),
                                "ts": self.clock.now_ns})

    # --- settlement ------------------------------------------------------------------

    def _close_price_window(self) -> None:
        """Close the window, then settle early, or on realization, what fell due."""
        super()._close_price_window()
        cards = {c.observation.strip().lower() for c in self.charter.cards}
        for key in list(self.uptake):
            record = self.uptake[key]
            if record["kind"] == "observation" and record["id"].strip().lower() in cards:
                record["taken"] = True
            if record["taken"] or self.ticks_consumed >= record["until_tick"]:
                self._realize_uptake(key, int(record["taken"]))
            elif record["forecasts"] and record["early"] is None:
                self._anticipate_uptake(key)

    def _market_q(self, record: dict) -> float:
        """The forecasters' standing-weighted median q (``charter.market``)."""
        return weighted_median([(f["q"], standing(self.uptake_standing.get(f["assembly"])))
                                for f in record["forecasts"]])

    def _settle_uptake(self, handle: str, score: float, definition: str) -> None:
        if self.queue.get(handle).status not in (SettleStatus.PENDING, SettleStatus.TIMED_OUT):
            return
        self.queue.settle(handle, channel="policy", score=min(1.0, max(0.0, score)),
                          status=SettleStatus.SETTLED, definition_version=definition,
                          sampling_ref=None)

    def _anticipate_uptake(self, key: str) -> None:
        """Pay the explorer now, at the market's q, and open the decision that corrects it."""
        record = self.uptake[key]
        q = self._market_q(record)
        self._settle_uptake(record["decision"], q, UPTAKE_ANTICIPATED)
        correction = self._uptake_decision(record["builder"], f"uptake-correction-{key}",
                                           record["until_tick"])
        record["early"] = {"q": q, "handle": correction}
        self.ledger.append({"kind": "uptake.anticipated", "registration": key, "q": q,
                            "forecasts": len(record["forecasts"]),
                            "handle": record["decision"], "correction": correction,
                            "tick": self.ticks_consumed, "ts": self.clock.now_ns})

    def _realize_uptake(self, key: str, y: int) -> None:
        """Score every forecast by Brier, and settle or correct the explorer's payment."""
        record = self.uptake.pop(key)
        for forecast in record["forecasts"]:
            score = brier(forecast["q"], y)
            self._settle_uptake(forecast["handle"], score, UPTAKE_FORECAST)
            n, total = self.uptake_standing.get(forecast["assembly"], (0, 0.0))
            self.uptake_standing[forecast["assembly"]] = (n + 1, total + score)
        early = record["early"]
        if early is not None:
            self._settle_uptake(early["handle"], 0.5 + 0.5 * (y - early["q"]), UPTAKE_CORRECTION)
        else:
            self._settle_uptake(record["decision"], float(y), UPTAKE_REALIZED)
        self.ledger.append({"kind": "uptake.settled", "registration": key, "taken": bool(y),
                            "early_q": None if early is None else early["q"],
                            "forecasts": len(record["forecasts"]),
                            "tick": self.ticks_consumed, "ts": self.clock.now_ns})

    # --- publication -----------------------------------------------------------------

    def _world_block(self) -> dict[str, Any]:
        """``world.uptake``: the open registrations a judging seat may forecast."""
        block = super()._world_block()
        if self.uptake:
            block["uptake"] = [{"registration": key, "until_tick": r["until_tick"],
                                "forecasts": len(r["forecasts"])}
                               for key, r in sorted(self.uptake.items())]
        return block

    def _mechanics_block(self) -> dict[str, Any]:
        """The uptake market's formulas beside the committee's."""
        block = super()._mechanics_block()
        block["uptake"] = (
            "a registration (tool, observation, assembly or service) is open for "
            "timing.min_ratio measured consequence periods, in world ticks, from "
            "registration (world.uptake, until_tick). It is taken up when another lineage "
            "calls the tool, a charter card names the observation, or the assembly is "
            "invoked; y = 1 then, y = 0 at until_tick. A judging seat's forecast q scores "
            "1 - (q - y)^2 on its own policy decision. The registering seat's uptake "
            "decision settles at the first window close after a forecast at Q, the median "
            "of the forecasts' q weighted by each forecaster's (1/2 + sum of its settled "
            "uptake scores) / (1 + their count); at y its correction decision settles "
            "1/2 + (y - Q)/2. With no forecast before y the uptake decision settles y. "
            "Each score returns to the seat's durable identity")
        return block
