"""Runtime governance method group."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from statistics import fmean
from typing import Any

from factorylab.charter.amendment import Amendment, PredictedEffect
from factorylab.charter.book import Refusal
from factorylab.charter.charter import Charter
from factorylab.charter.controller import violation
from factorylab.charter.measurement import measure_card, preflight_measurement
from factorylab.cortex.assembly import AssemblySpec
from factorylab.cortex.registration import (
    MAX_PROPOSALS_PER_RETURN,
    AssemblyProposal,
    ConnectorProposal,
    LearnerProposal,
    ModelProposal,
    ObservationProposal,
    RetireProposal,
    ToolProposal,
    parse_proposals,
)
from factorylab.cortex.request import Return
from factorylab.cortex.tools import PopulationTool, as_spec
from factorylab.kernel.events import EventKind
from factorylab.kernel.queue import PropensityRecord, SettleStatus
from factorylab.kernel.registry import Contract, PriceSpec, ResourceBounds
from factorylab.kernel.wallet import Infeasible
from factorylab.runtime.cards import region_for
from factorylab.runtime.observations import (
    OBSERVATION_TIMEOUT_S,
    SEED_IDS,
    ObservationBook,
    window_facts,
)
from factorylab.runtime.shared import _to_plain
from factorylab.runtime.summary import _assembly_contract, _model_contract
from factorylab.world.x402 import X402Error


@dataclass(frozen=True)
class Retirement:
    """A vote targets one frozen assembly version and retains the proposing decision."""

    id: str
    proposer_handle: str
    assembly_id: str
    version: int
    predicted_effect: PredictedEffect


class GovernanceMixin:
    """Preserve runtime state and behavior for governance operations."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.charter_book.bind_observations(lambda: self.observations)
        self.PROPOSAL_SHAPES = deepcopy(self.PROPOSAL_SHAPES)
        for kind in ("connector", "retire"):
            self.PROPOSAL_SHAPES[kind]["predicted_effect"] = {
                "card_id": "a current card id", "direction": "decrease", "window": 1}

    def _apply_registrations(self, handle: str, ret: Return) -> None:
        if ret.status != "ok":
            return
        raw = ret.outputs.get("register")
        if raw is None:
            return
        if not isinstance(raw, list):
            self._reject_registration(handle, "register must be a list", -1)
            return
        for index, item in enumerate(raw):
            if index >= MAX_PROPOSALS_PER_RETURN:
                self._reject_registration(handle, "proposal cap reached for this return", index)
                continue
            try:
                from factorylab.cortex.assembly import validate_proposal

                validate_proposal(item)
                prediction = None
                if item.get("kind") in ("connector", "retire"):
                    prediction = self._policy_prediction(item.get("predicted_effect"))
                if isinstance(item, dict) and item.get("kind") == "amendment":
                    self._propose_amendment(handle, item)
                else:
                    mid = item.get("openrouter_id") if isinstance(item, dict) else None
                    namespaced = (isinstance(mid, str) and item.get("kind") == "model"
                                  and mid.startswith(("x402:", "venice:")))
                    adapted = {**item, "openrouter_id": "namespace/model"} if namespaced else item
                    if item.get("kind") == "connector":
                        adapted = {k: v for k, v in adapted.items() if k != "predicted_effect"}
                    accepted, rejected = parse_proposals(
                        {"register": [adapted]},
                        event_kinds=self._event_kinds(),
                        known_models=frozenset(self.prices.prices),
                        known_assemblies=frozenset(self.assemblies),
                        known_tools=frozenset(self.tool_specs),
                        tool_jail=self.tool_jail_available,
                        retired_assemblies=frozenset(self.retired_assemblies),
                        seed_observations=SEED_IDS,
                    )
                    if rejected:
                        if item.get("kind") == "connector":
                            self._connector_refused(handle, rejected[0].reason)
                        self._reject_registration(handle, rejected[0].reason, index)
                        continue
                    prop = ModelProposal(mid) if namespaced else accepted[0]
                    if prediction is not None:
                        self._register(handle, prop, predicted_effect=prediction)
                    else:
                        self._register(handle, prop)
                    # Only an accepted registration is a revision; proposals and
                    # tool calls that changed nothing do not count.
                    if not isinstance(prop, RetireProposal):
                        self.window.revision_handles.add(handle)
                self.stats.registrations_accepted += 1
                self.window.registrations += 1
                if item.get("kind") not in ("amendment", "retire"):
                    self.card_samples.revised(handle)
            except (Infeasible, PermissionError, ValueError, OverflowError, KeyError,
                    TypeError, X402Error) as exc:
                if isinstance(item, dict) and item.get("kind") == "connector":
                    self._connector_refused(handle, str(exc)[:300])
                self._reject_registration(handle, f"{type(exc).__name__}: {exc}"[:300], index)

    def _reject_registration(self, handle: str, reason: str, index: int | None) -> None:
        """Ledger a refused proposal and keep the reason public: a proposer that cannot see
        why it was refused re-proposes the same thing (run 6, eleven times)."""
        self.stats.registrations_rejected += 1
        item = {"kind": "registration.rejected", "handle": handle, "reason": reason}
        if index is not None:
            item["index"] = index
        self.ledger.append({**item, "ts": self.clock.now_ns})
        self.window.registration_rejections += 1
        self.registration_feedback.append({k: v for k, v in item.items()
                                           if k not in ("kind", "handle")})

    def _register_observation(self, handle: str, prop: Any) -> None:
        """Admit a population measurement only after it measures the last closed window.

        The code is the population's; the preflight is the kernel's, and it is the
        same execution the pricing path will make: the last closed window's public
        facts as JSON, the tool jail, the tool limits. A definition that cannot
        produce a finite number on real evidence is refused with the reason. A new
        version of an already registered observation supersedes it; a seed id is
        not redefinable, because the charter's own cards are measured by those.
        """
        if not self.tool_jail_available:
            raise Infeasible("no jail on this host")
        if prop.id in SEED_IDS:
            raise ValueError("seed observation ids cannot be redefined")
        closed = self.card_samples.windows[-1] if self.card_samples.windows else None
        if closed is None:
            raise ValueError("no closed window to preflight the observation against")
        value, error = self.observation_runner.run(prop.code, window_facts(closed))
        self.ledger.append({"kind": "observation.preflight", "handle": handle,
                            "observation": prop.id, "window": closed.get("index"),
                            "value": value, "error": error, "ts": self.clock.now_ns})
        if value is None:
            raise ValueError(f"observation preflight failed: {error}")
        lo, hi = prop.unit_range
        if not lo <= value <= hi:
            # A declared range is the scale a card's violation is divided by, so a
            # measurement outside it is not a measurement of what was declared.
            raise ValueError(
                f"observation preflight value {value} is outside its declared range [{lo}, {hi}]"
            )
        version = len(self.registered_observations.get(prop.id, {}).get("history", ())) + 1
        contract = Contract(
            id=f"observation:{prop.id}",
            version=version,
            kind="observation",
            description=prop.description,
            input_schema={"type": "object", "description": "public per-window facts"},
            output_schema={"type": "number", "minimum": prop.unit_range[0],
                           "maximum": prop.unit_range[1]},
            price=PriceSpec({}),
            permissions=frozenset({"sandbox.run"}),
            resource_bounds=ResourceBounds(max_duration_ns=OBSERVATION_TIMEOUT_S * 1_000_000_000),
        )
        self._register_with_trial(contract, handle, self.ev.trial_amount_micro)
        history = list(self.registered_observations.get(prop.id, {}).get("history", ()))
        history.append(version)
        self.registered_observations[prop.id] = {
            "description": prop.description, "units": prop.unit,
            "unit_range": [prop.unit_range[0], prop.unit_range[1]], "code": prop.code,
            "version": version, "provenance": "population", "history": history,
        }
        self.stats.observations_registered += 1
        self._emit(EventKind.REGISTERED, {"kind": "observation", "id": prop.id,
                                          "version": version, "preflight_value": value})

    def _register_learner(self, handle: str, prop: Any) -> None:
        """Give an assembly a learner over its own declared action set.

        Blum--Mansour builds a no-swap-regret learner out of one ordinary learner
        per action (essay II.I.a), so the action set has to be declared before the
        first round. From here the assembly's returns declare a propensity over
        that set and the reward that settles each decision trains the learner
        off-policy through the declared propensity.
        """
        from factorylab.learners.delayed import SnapshotLearner
        from factorylab.learners.exp3 import EXP3

        if prop.assembly_id not in self.assemblies:
            raise ValueError("assembly_id must name a registered assembly")
        if prop.assembly_id in self.assembly_learners:
            raise ValueError("assembly already has a learner")
        contract = Contract(
            id=f"learner:{prop.assembly_id}",
            version=1,
            kind="router",
            description=f"{prop.learner} over {prop.assembly_id}'s declared action set",
            input_schema={"type": "object", "properties": {"actions": {"type": "array"}}},
            output_schema={"type": "object"},
            price=PriceSpec({}),
            permissions=frozenset(),
            resource_bounds=ResourceBounds(),
        )
        self._register_with_trial(contract, handle, self.ev.trial_amount_micro)
        lid = self._assembly_learner_id(prop.assembly_id)
        if prop.learner == "blum_mansour":
            from factorylab.learners.blum_mansour import BlumMansour

            inner = BlumMansour(lambda acts: EXP3(acts, prop.gamma), prop.actions, id=lid)
        else:
            inner = EXP3(prop.actions, prop.gamma, id=lid)
        self.assembly_learners[prop.assembly_id] = SnapshotLearner(inner, id=lid)
        self.stats.assembly_learners_registered += 1
        self._emit(EventKind.REGISTERED, {"kind": "learner", "id": prop.assembly_id,
                                          "learner": prop.learner,
                                          "actions": list(prop.actions)})

    def _policy_prediction(self, value: Any) -> PredictedEffect:
        """Connector and retirement promises bind to an existing measurable charter card."""
        prediction = PredictedEffect.parse(value)
        card = next((c for c in self.charter.cards if c.id == prediction.card_id), None)
        if card is None:
            raise ValueError("predicted_effect.card_id must name a current card")
        preflight_measurement(card, self.observations)
        return prediction

    def _register(self, handle: str, prop: Any, *,
                  predicted_effect: PredictedEffect | None = None) -> None:
        amount = self.ev.trial_amount_micro
        if isinstance(prop, RetireProposal):
            self._propose_retirement(handle, prop, predicted_effect=predicted_effect)
            return
        if isinstance(prop, ConnectorProposal):
            self._register_connector(handle, prop, predicted_effect=predicted_effect)
            return
        if isinstance(prop, ObservationProposal):
            self._register_observation(handle, prop)
            return
        if isinstance(prop, LearnerProposal):
            self._register_learner(handle, prop)
            return
        if isinstance(prop, ToolProposal):
            if not self.tool_jail_available:
                raise Infeasible("no jail on this host")
            contract = Contract(
                id=f"tool:{prop.id}",
                version=1,
                kind="tool",
                description=prop.description,
                input_schema=_to_plain(prop.args_schema),
                output_schema={"type": "object"},
                price=PriceSpec({"call": self.m.tools.population_tool_micro_per_call}),
                permissions=frozenset({"sandbox.run"}),
                resource_bounds=ResourceBounds(max_duration_ns=prop.timeout_s * 1_000_000_000),
            )
            self._register_with_trial(contract, handle, amount)
            tool = PopulationTool(
                prop.id, prop.description, prop.args_schema, prop.code, prop.timeout_s, handle
            )
            self.population_tools[prop.id] = tool
            owner = self.handle_to_assembly.get(handle)
            if owner is not None:
                self.tool_owner[prop.id] = owner
            self.tool_specs[prop.id] = as_spec(tool, self.m.tools.population_tool_micro_per_call)
            self.stats.population_tools_registered += 1
            self._emit(EventKind.REGISTERED, {"kind": "tool", "id": prop.id})
            return
        if isinstance(prop, ModelProposal):
            if prop.openrouter_id in self.prices.prices:
                raise ValueError("model version already registered")
            if prop.openrouter_id.startswith("x402:"):
                price, seller = self._seller_price(prop.openrouter_id)
                contract = _model_contract(prop.openrouter_id, price, "x402")
                self._register_with_trial(contract, handle, amount)
                self._record_seller(prop.openrouter_id, price, seller)
                self._emit(
                    EventKind.REGISTERED,
                    {
                        "kind": "model",
                        "id": prop.openrouter_id,
                        "network": seller["network"],
                        "per_request_micro": price.per_request_micro,
                    },
                )
                return
            base, _, effort = prop.openrouter_id.partition("@")
            if not base or len(base) > 4096 or any(c.isspace() for c in base):
                raise ValueError("invalid model id")
            if effort and effort not in (
                "none",
                "minimal",
                "low",
                "medium",
                "high",
                "xhigh",
                "max",
            ):
                raise ValueError("reasoning level must be none|minimal|low|medium|high|xhigh|max")
            if base in self.prices.prices:
                price = self.prices.price(base)
            elif self.catalogue is not None and base in self.catalogue:
                price = self.catalogue[base]
            else:
                raise ValueError("no catalogue entry for that model in this world")
            provider = "venice" if base.startswith("venice:") else "openrouter"
            contract = _model_contract(prop.openrouter_id, price, provider)
            self._register_with_trial(contract, handle, amount)
            self.prices.register(prop.openrouter_id, price)
            self._emit(
                EventKind.REGISTERED, {"kind": "model", "id": prop.openrouter_id}
            )
        elif isinstance(prop, AssemblyProposal):
            live = prop.id in self.assemblies and prop.id not in self.retired_assemblies
            version = (self.assemblies[prop.id].spec.version + 1
                       if prop.id in self.assemblies else 1)
            emits = prop.emits or None
            spec = AssemblySpec(
                id=prop.id, version=version, model_id=prop.model_id,
                system_prompt=prop.system_prompt, max_tokens=prop.max_tokens,
                effort=prop.effort, accepts=frozenset(prop.accepts), role=prop.role,
                emits=emits, schemas=prop.schemas)
            self._check_event_schemas(spec)
            contract = _assembly_contract(prop.id, prop.role, prop.accepts, prop.max_tokens,
                                         emits=spec.emits, schemas=spec.schemas, version=version)
            self._register_with_trial(
                contract, handle, amount,
                refuse=("id already registered: a live assembly is retired by vote before "
                        "its id takes a next version") if live else "")
            self._instantiate(spec)
            self.retired_assemblies.discard(prop.id)
            for kind in prop.accepts:
                self._open_epoch(kind)
            self._emit(
                EventKind.REGISTERED,
                {
                    "kind": "assembly",
                    "id": prop.id,
                    "role": prop.role,
                    "accepts": list(prop.accepts),
                    "emits": list(spec.emits),
                    "schemas": spec.schemas,
                    "version": version,
                },
            )
        else:
            # Everything that can refuse this router is checked before the receipt is
            # spent: a registry entry cannot be withdrawn, so a later failure would leave
            # an orphan contract and a burnt novelty trial.
            if prop.add and (
                len(self.routers.get(prop.event_kind, [])) >= self.m.tools.max_routers_per_kind
            ):
                raise ValueError("router cap reached for this event kind")
            contract = Contract(
                id=f"router:{prop.event_kind}:{prop.learner}:{self.n}",
                version=1,
                kind="router",
                description=f"{prop.learner} router for {prop.event_kind}",
                input_schema={"type": "object", "properties": {"kind": {"const": prop.event_kind}}},
                output_schema={"type": "object"},
                price=PriceSpec({}),
                permissions=frozenset(),
                resource_bounds=ResourceBounds(),
            )
            self._register_with_trial(contract, handle, amount)
            self._build_router(prop.event_kind, prop.learner, prop.gamma, replace=not prop.add)
            self.stats.routers_replaced += 1
            self._emit(
                EventKind.ROUTER_REPLACED,
                {
                    "event_kind": prop.event_kind,
                    "learner": prop.learner,
                    "gamma": prop.gamma,
                    "added": prop.add,
                    "by": handle,
                },
            )

    def _register_connector(self, handle: str, prop: ConnectorProposal, *,
                            predicted_effect: PredictedEffect | None = None) -> None:
        """Only bounded preflight and a proposer-excluding sortition majority admit an origin."""
        from types import SimpleNamespace

        from factorylab.charter.committee import Committee, draw

        predicted_effect = self._policy_prediction(predicted_effect)
        owner = self.handle_to_assembly.get(handle)
        if owner is None:
            try:
                owner = self.queue.get(handle).propensity.chosen
            except KeyError:
                raise ValueError("connector proposal needs a caller decision") from None
        result, _ = self._fetch_connector(owner, handle, {"id": prop.id, "path": "/"},
                                          origin=prop.origin)
        # Preflight establishes bounds, not information for the proposer.
        if "error" in result:
            raise ValueError(f"connector preflight: {result['error']}")
        try:
            version = self.registry.get(f"connector:{prop.id}").version + 1
        except KeyError:
            version = 1
        eligible = self._committee_eligible()
        eligible.pop(owner, None)
        vote_id = f"connector:{prop.id}:v{version}:{handle}"
        committee = Committee(vote_id, 1, draw(eligible, self.rng, self.m.committee.seats))
        self.ledger.append({"kind": "connector.seated", "id": prop.id,
                            "vote_id": vote_id, "handle": handle,
                            "seats": [seat._asdict() for seat in committee.seats]})
        proposal = SimpleNamespace(id=vote_id, proposer_handle=handle,
                                   predicted_effect=predicted_effect)
        if not self._hold_vote(proposal, committee, connector=prop):
            raise ValueError("connector sortition vote did not reach a majority")
        contract = Contract(
            id=f"connector:{prop.id}", version=version, kind="connector",
            description=prop.description, input_schema={"origin": prop.origin},
            output_schema={"type": "string"},
            price=PriceSpec({"call": self.m.connectors.call_price_micro}),
            permissions=frozenset({"connector.fetch"}),
            resource_bounds=ResourceBounds(
                max_duration_ns=self.m.connectors.timeout_s * 1_000_000_000,
                max_memory_bytes=self.m.connectors.max_bytes),
        )
        try:
            self._register_with_trial(contract, handle, self.ev.trial_amount_micro)
        except (Infeasible, ValueError, PermissionError):
            self._censor_ballots(vote_id)
            raise
        self.ledger.append({"kind": "connector.registered", "id": prop.id,
                            "version": version, "description": prop.description,
                            "origin": prop.origin, "handle": handle, "vote_id": vote_id,
                            "predicted_effect": asdict(predicted_effect),
                            "ts": self.clock.now_ns})
        self._activate_policy_ballots(vote_id)
        self._emit(EventKind.REGISTERED, {"kind": "connector", "id": prop.id,
                                          "version": version, "origin": prop.origin})

    def _propose_amendment(self, handle: str, item: dict[str, Any]) -> None:
        from factorylab.charter.amendment import (
            proposed_answers_for,
            proposed_price,
            proposed_tick_interval,
        )
        from factorylab.charter.charter import MetricCard

        tick_interval = None
        if "tick_interval" in item:
            try:
                proposed_tick_interval(
                    item["tick_interval"], self.m.clock.min_tick_ns, self.m.max_tick_ns
                )
            except ValueError as exc:
                feedback = {"id": str(item.get("id", "")), "reason": str(exc)}
                self.ledger.append({"kind": "amendment.rejected", **feedback})
                self.amendment_feedback = feedback
                raise
            tick_interval = item["tick_interval"]
        prices = []

        def cards(key: str) -> tuple[MetricCard, ...]:
            raw = item.get(key) or []
            if not isinstance(raw, list):
                raise ValueError(f"{key} must be a list")
            out = []
            for c in raw:
                if not isinstance(c, dict):
                    raise ValueError(f"{key} entries must be objects")
                if "lambda" in c:
                    try:
                        value = proposed_price(c["lambda"], self.m.prices.lambda_max)
                    except ValueError as exc:
                        feedback = {"id": str(item.get("id", "")), "reason": str(exc)}
                        self.ledger.append({"kind": "amendment.rejected", **feedback})
                        self.amendment_feedback = feedback
                        raise
                    prices.append((str(c.get("id", "")), value))
                out.append(
                    MetricCard(
                        str(c.get("id", "")),
                        str(c.get("norm", "")),
                        str(c.get("description", "")),
                        str(c.get("units", "")),
                        c.get("window"),
                        str(c.get("acceptable_region", "")),
                        str(c.get("observation", "")),
                        proposed_answers_for(c.get("answers_for"), str(c.get("id", ""))),
                    )
                )
            from factorylab.runtime.cards import parses

            for card in out:
                if not parses(card):
                    raise ValueError("card acceptable_region has no finite usable bounds")
                # A card may name a registered observation; an unregistered one
                # is refused here, before a vote, with the reason.
                region_for(card, rolling={}, observations=self.observations)
                preflight_measurement(card, self.observations)
            return tuple(out)

        remove = item.get("remove") or []
        if not isinstance(remove, list) or any(not isinstance(r, str) for r in remove):
            raise ValueError("remove must be a list of card ids")
        am = Amendment(
            id=str(item.get("id", "")),
            proposer_handle=handle,
            edition_base=self.charter.edition,
            add=cards("add"),
            replace=cards("replace"),
            remove=tuple(remove),
            predicted_effect=item.get("predicted_effect"),
            proposed_prices=tuple(prices),
            tick_interval=tick_interval,
        )
        self.charter_book.validate(am, self.observations)
        resulting = {c.id: c for c in self.charter.cards if c.id not in am.remove}
        resulting.update((c.id, c) for c in (*am.replace, *am.add))
        clock_changed = am.tick_interval is not None and proposed_tick_interval(
            am.tick_interval, self.m.clock.min_tick_ns, self.m.max_tick_ns
        ) != self.tick_clock.interval_ns
        if (resulting == {c.id: c for c in self.charter.cards} and not clock_changed
                and not any(self.controller.price(cid) != value for cid, value in prices)):
            raise ValueError("amendment leaves the charter unchanged")
        contract = Contract(
            id=f"amendment:{am.id}",
            version=1,
            kind="tool",
            description="charter amendment proposal",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            price=PriceSpec({}),
            permissions=frozenset(),
            resource_bounds=ResourceBounds(),
        )
        self._register_with_trial(contract, handle, self.ev.trial_amount_micro)
        self.charter_book.propose(am, self.observations)
        self.stats.amendments_proposed += 1
        self.window.amendments_proposed += 1
        eligible = self._committee_eligible()
        proposer = self.handle_to_assembly.get(handle)
        if proposer is None:
            try:
                proposer = self.queue.get(handle).propensity.chosen
            except KeyError:
                pass
        eligible.pop(proposer, None)
        committee = self.charter_book.seat(am.id, eligible, self.rng, size=self.m.committee.seats)
        self._hold_vote(am, committee)

    def _committee_eligible(self) -> dict[str, str]:
        """Distinct independently requested decisions need settled consequences to qualify."""
        from collections import Counter

        from factorylab.charter.committee import experienced

        evidence = {(r.handle, self.handle_to_assembly.get(r.handle))
                    for r in self.consequences.table.returns if r.payoff is not None
                    and self.queue.get(r.handle).channel in ("verdict", "exposure")}
        from factorylab.runtime.shared import DEF_CONFORMITY, DEF_META_CONSEQUENCE

        for decision in self.queue.state()["decisions"].values():
            if any(r.status is SettleStatus.SETTLED and r.definition_version in (
                    DEF_META_CONSEQUENCE, DEF_CONFORMITY)
                   for r in self.queue.history(decision.handle)):
                evidence.add((decision.handle, self.handle_to_assembly.get(decision.handle)))
            if decision.channel == "consequence" and decision.status is SettleStatus.SETTLED:
                if decision.parent_handle:
                    evidence.add((decision.parent_handle, self.handle_to_assembly.get(
                        decision.parent_handle, decision.actor)))
        settled = Counter(assembly for handle, assembly in evidence
                          if assembly is not None and self._independent_decision(handle, assembly))
        return experienced({a.spec.id: a.spec.role for a in self.assemblies.values()
                            if a.spec.id not in self.retired_assemblies},
                           settled, self.m.committee.min_settled)

    def _propose_retirement(self, handle: str, proposal: RetireProposal, *,
                            predicted_effect: PredictedEffect | None = None) -> None:
        """Any assembly may request a seed or population retirement through the amendment draw."""
        from factorylab.charter.committee import Committee, draw

        predicted_effect = self._policy_prediction(predicted_effect)
        target = proposal.assembly_id
        if target not in self.assemblies or target in self.retired_assemblies:
            raise ValueError("assembly is unavailable or already retired")
        version = self.assemblies[target].spec.version
        if any(row["proposal"].assembly_id == target and row["proposal"].version == version
               and row["status"] in ("voting", "passed")
               for row in self.retirement_proposals.values()):
            raise ValueError("retirement is already pending for this assembly version")
        # The motion id reaches the public wake through the governance queue, and
        # the wake never names an assembly — so the id identifies the proposal, not its
        # target. The target is on the sealed ledger row below.
        motion = Retirement(f"retire:{handle}:{len(self.retirement_proposals) + 1}",
                            handle, target, version, predicted_effect)
        contract = Contract(
            id=motion.id, version=1, kind="tool", description="assembly retirement proposal",
            input_schema={"type": "object"}, output_schema={"type": "object"},
            price=PriceSpec({}), permissions=frozenset(), resource_bounds=ResourceBounds())
        self._register_with_trial(contract, handle, self.ev.trial_amount_micro)
        eligible = self._committee_eligible()
        proposer = self.handle_to_assembly.get(handle, self.queue.get(handle).propensity.chosen)
        eligible.pop(proposer, None)
        eligible.pop(target, None)
        committee = Committee(motion.id, len(self.retirement_proposals) + 1,
                              draw(eligible, self.rng, self.m.committee.seats))
        self.ledger.append({"kind": "retirement.proposed", **asdict(motion),
                            "committee": asdict(committee)})
        self.retirement_proposals[motion.id] = {
            "proposal": motion, "committee": committee, "ballots": {}, "status": "voting"}
        self._hold_vote(motion, committee)

    def _prune_cadence_waiting(self) -> list[str]:
        """Spent or stale heads cannot block another approved proposal."""
        active = {am.id for am in self.charter_book.pending()}
        active.update(pid for pid, row in self.retirement_proposals.items()
                      if row["status"] == "passed")
        waiting = self.cadence.world_block(self.tick_clock.interval_ns)["waiting"]
        for proposal_id in waiting:
            if proposal_id not in active:
                self.cadence.refused(proposal_id, "proposal is no longer pending")
        return [pid for pid in waiting if pid in active]

    def _activate_retirements_if_due(self) -> None:
        """Retire one approved version at the same cadence boundary used by amendments."""
        waiting = self._prune_cadence_waiting()
        for row in self.retirement_proposals.values():
            if row["status"] != "passed":
                continue
            if waiting and waiting[0] != row["proposal"].id:
                continue
            if not self.cadence.ready(now_ns=self.clock.now_ns,
                                      tick_interval_ns=self.tick_clock.interval_ns,
                                      window=self.stats.reserve_windows):
                return
            motion = row["proposal"]
            if (motion.assembly_id in self.retired_assemblies
                    or self.assemblies[motion.assembly_id].spec.version != motion.version):
                self.ledger.append({"kind": "retirement.stale", "proposal_id": motion.id})
                row["status"] = "stale"
                self.cadence.refused(motion.id, "retirement target version is stale")
                self._censor_ballots(motion.id)
                waiting = self._prune_cadence_waiting()
                continue
            self._retire_assembly(motion.assembly_id, motion.id)
            row["status"] = "activated"
            self._activate_policy_ballots(motion.id)
            self.cadence.activated(motion.id, self.clock.now_ns, self.tick_clock.interval_ns)
            self.card_samples.revised(motion.proposer_handle)
            self.window.revision_returns += 1
            return

    def _independent_decision(self, handle: str, assembly: str) -> bool:
        """A self-request anywhere in the decision's ancestry cannot manufacture eligibility."""
        try:
            original = self.queue.get(handle)
            if (original.parent_handle is not None or original.propensity.source != "sampled"
                    or original.propensity.chosen != assembly
                    or original.propensity.learner_id != original.actor
                    or original.channel == "policy"):
                return False
            parent = original.parent_handle
            while parent:
                decision = self.queue.get(parent)
                if self.handle_to_assembly.get(parent, decision.propensity.chosen) == assembly:
                    return False
                parent = decision.parent_handle
        except KeyError:
            return False
        return True

    def _hold_vote(self, am: Any, committee: Any, *,
                   connector: ConnectorProposal | None = None) -> bool | None:
        """Amendments and connectors share seats, metered ballot requests and majority counting."""
        if am.id in self.voted_amendments:
            return
        retiring = isinstance(am, Retirement)
        prices = {} if retiring or connector is not None else dict(am.proposed_prices)
        connector_yes = 0
        for seat in committee.seats:
            if self.wallet.dead:
                break
            alias, assembly_id = seat[0], seat[1]
            event_id = f"vote-{am.id}-{alias}"
            if event_id in self.vote_handles:
                continue
            parent = getattr(am, "proposer_handle", None)
            try:
                self.queue.get(parent)
            except KeyError:
                parent = None
            lid = f"assembly:{assembly_id}"
            # Policy outcomes may await cadence and then a declared number of windows.
            # This covers the remaining experiment, rather than expiring after the ballot call.
            deadline = self.clock.now_ns + (
                self.events_budget + self.ev.consequence_backstop_events
            ) * self.m.max_tick_ns + (
                am.predicted_effect.window * self.m.novelty.window_ns)
            handle = self.queue.open(
                actor=lid, event_id=event_id,
                propensity=PropensityRecord((assembly_id,), (1.,), assembly_id, 0, lid,
                                            "direct-committee-seat"),
                channel="policy", deadline_ns=deadline,
                parent_handle=parent, cost_ceiling=max(0, self.wallet.available),
            )
            self.ledger.append({"kind": "committee.decision", "event_id": event_id,
                                "handle": handle})
            self.vote_handles[event_id] = handle
            self.handle_to_assembly[handle] = assembly_id
            self._start_return(handle)
            self.stats.decisions += 1
            if connector is not None:
                inputs = {"connector": {**asdict(connector),
                                        "predicted_effect": asdict(am.predicted_effect)},
                          "world": self._world_block(), "charter": self._charter_text()}
            else:
                inputs = {
                    ("retirement" if retiring else "amendment"): ({
                        "assembly_id": am.assembly_id, "version": am.version,
                        "predicted_effect": asdict(am.predicted_effect),
                    } if retiring else {
                        "id": am.id,
                        "add": [
                            {**asdict(c), **({"lambda": prices[c.id]} if c.id in prices else {})}
                            for c in am.add
                        ],
                        "replace": [
                            {**asdict(c), **({"lambda": prices[c.id]} if c.id in prices else {})}
                            for c in am.replace
                        ],
                        "remove": list(am.remove),
                        "predicted_effect": asdict(am.predicted_effect),
                        **({"tick_interval": am.tick_interval}
                           if am.tick_interval is not None else {}),
                    }),
                    "charter": self._charter_text(),
                    "world": self._world_block(),
                    "your_policy_returns": [asdict(lr) for lr in self.queue.returns_for(lid)
                                            if lr.channel == "policy"],
                }
            inputs["your_policy_returns"] = [asdict(lr) for lr in self.queue.returns_for(lid)
                                             if lr.channel == "policy"]
            schema = {
                "type": "object",
                "properties": {"vote": {"type": "boolean"}, "reason": {"type": "string"}},
                "required": ["vote", "reason"],
            }
            req = self._request(
                handle,
                ("Vote on a connector registration." if connector is not None else
                 "Vote on retiring the named assembly version." if retiring else
                 "Vote on an amendment to the charter's metric cards."),
                inputs,
                schema,
                self.clock.now_ns + self.tick_clock.interval_ns * 10,
                "policy",
            )
            asm = self.assemblies.get(assembly_id)
            if asm is None:
                ret = Return(handle, {"reason": "assembly unavailable"}, 0, "failed")
            else:
                # A ballot is an ordinary metered return: a voter may reach a
                # registered connector before it decides, and its sample is typed
                # from the contract it emits (the _invoke override records it).
                ret = self._invoke(assembly_id, req, "voter", child=True)
            self.consequences.finish(handle, ret.cost)
            if asm is None:
                self.card_samples.returned(handle=handle, assembly=assembly_id, role="other",
                                           window=self.window.index, ret=ret)
            self._compute_routed = True
            vote = ret.outputs.get("vote") if ret.status == "ok" else None
            if connector is not None:
                ballot = vote if type(vote) is bool else None
                self.ledger.append({"kind": "connector.vote", "vote_id": am.id,
                                    "alias": alias, "handle": handle, "vote": ballot,
                                    "reason": str(ret.outputs.get("reason", ""))[:1000]})
                connector_yes += ballot is True
                self.stats.votes_cast += ballot is not None
                self._record_policy_ballot(am, handle, assembly_id, ballot)
                continue
            if retiring:
                ballot = vote if type(vote) is bool else None
                self.ledger.append({"kind": "retirement.ballot", "proposal_id": am.id,
                                    "alias": alias, "vote": ballot,
                                    "reason": str(ret.outputs.get("reason", ""))[:1000]})
                self.retirement_proposals[am.id]["ballots"][alias] = ballot
                self.stats.votes_cast += int(ballot is not None)
                self._record_policy_ballot(am, handle, assembly_id, ballot)
                continue
            if isinstance(vote, bool):
                self.charter_book.vote(
                    committee, alias, vote, str(ret.outputs.get("reason", ""))[:1000]
                )
                self.stats.votes_cast += 1
                self._record_policy_ballot(am, handle, assembly_id, vote)
            else:
                self.charter_book.abstain(committee, alias)
                self._settle_policy(handle, 0.0, SettleStatus.CENSORED)
        self.ledger.append({"kind": "committee.completed", "amendment_id": am.id})
        self.voted_amendments.add(am.id)
        if connector is not None:
            passed = connector_yes >= len(committee.seats) // 2 + 1
            self.ledger.append({"kind": "connector.tally", "vote_id": am.id,
                                "yes": connector_yes, "seats": len(committee.seats),
                                "passed": passed})
            if not passed:
                self._censor_ballots(am.id)
            return passed
        if retiring:
            row = self.retirement_proposals[am.id]
            yes = sum(v is True for v in row["ballots"].values())
            outcome = "passed" if yes >= len(committee.seats) // 2 + 1 else "failed"
            self.ledger.append({"kind": "retirement.tally", "proposal_id": am.id,
                                "outcome": outcome})
            row["status"] = outcome
        else:
            outcome = self.charter_book.tally(committee)
        if outcome == "passed":
            self.cadence.approve(am.id)
            self.cadence.ready(
                now_ns=self.clock.now_ns,
                tick_interval_ns=self.tick_clock.interval_ns,
                window=self.stats.reserve_windows,
            )
            if not retiring:
                self.stats.amendments_passed += 1
        elif outcome == "failed":
            self._censor_ballots(am.id)

    def _next_charter_activation(self) -> Charter | None:
        """Activate only at a boundary that meets the measured governance separation."""
        waiting = self._prune_cadence_waiting()
        if waiting and waiting[0] in self.retirement_proposals:
            return None
        if not self.cadence.ready(
            now_ns=self.clock.now_ns,
            tick_interval_ns=self.tick_clock.interval_ns,
            window=self.stats.reserve_windows,
        ):
            return None
        new = self.charter_book.activate_due(self.clock.now_ns)
        while isinstance(new, Refusal):
            self._close_refused_ballots(new)
            waiting = self._prune_cadence_waiting()
            if waiting and waiting[0] in self.retirement_proposals:
                return None
            new = self.charter_book.activate_due(self.clock.now_ns)
        if new is not None:
            am = self.charter_book.activated_amendment(new.edition)
            self._activate_policy_ballots(am.id)
            self.cadence.activated(am.id, self.clock.now_ns, self.tick_clock.interval_ns)
        return new

    def _close_refused_ballots(self, refusal: Any) -> None:
        """A refused activation leaves nothing to grade: close its ballots, release its card."""
        self.ledger.append({"kind": "policy.refused", "amendment_id": refusal.amendment_id,
                            "reason": refusal.reason, "window": self.window.index})
        self._censor_ballots(refusal.amendment_id)
        self.cadence.refused(refusal.amendment_id, refusal.reason)

    def _censor_ballots(self, amendment_id: str) -> None:
        """Settle every pending ballot on an amendment that will never take effect."""
        for vote in self.pending_votes:
            if vote["amendment_id"] == amendment_id:
                self._settle_policy(vote["handle"], 0.0, SettleStatus.CENSORED)
        self.pending_votes[:] = [v for v in self.pending_votes
                                 if v["amendment_id"] != amendment_id]

    def _activate_charter_if_due(self) -> None:
        self._activate_retirements_if_due()
        new = self._next_charter_activation()
        while new is not None:
            self.charter = new
            am = self.charter_book.activated_amendment(new.edition)
            for card_id in sorted(self.priced - {c.id for c in new.cards}):
                self.controller.remove(card_id, amendment_id=am.id)
                self.priced.remove(card_id)
                self.regions.pop(card_id, None)
            self._derive_regions()
            for card_id, value in am.proposed_prices:
                if card_id not in self.priced:
                    self.controller.register_pending(card_id)
                    self.priced.add(card_id)
                self.controller.set_price(card_id, value, amendment_id=am.id)
            if am.tick_interval is not None:
                from factorylab.charter.amendment import proposed_tick_interval

                interval = proposed_tick_interval(
                    am.tick_interval, self.m.clock.min_tick_ns, self.m.max_tick_ns
                )
                if interval != self.tick_clock.interval_ns:
                    self.ledger.append(
                        {
                            "kind": "clock.changed",
                            "edition": new.edition,
                            "old_ns": self.tick_clock.interval_ns,
                            "new_ns": interval,
                        }
                    )
                    self.tick_clock.set_interval(interval)
                    self.stats.clock_changes += 1
            self.stats.amendments_activated += 1
            self.window.amendments_activated += 1
            self.card_samples.revised(am.proposer_handle)
            self.window.revision_returns += 1  # An activated amendment is a revision
            new = self._next_charter_activation()

    def _record_policy_ballot(self, proposal, handle: str, assembly: str,
                              vote: bool | None) -> None:
        """Freeze one ballot's card, observation version and acceptable region before activation."""
        if vote is None:
            self._settle_policy(handle, 0.0, SettleStatus.CENSORED)
            return
        cards = {c.id: c for c in (*self.charter.cards, *getattr(proposal, "replace", ()),
                                   *getattr(proposal, "add", ()))}
        card = cards[proposal.predicted_effect.card_id]
        observation = self.observations.get(card.observation)
        definitions = ({observation.id: deepcopy(self.registered_observations[observation.id])}
                       if observation.registered else {})
        ballot = {
            "handle": handle, "assembly": assembly, "amendment_id": proposal.id,
            "vote": vote, "prediction": proposal.predicted_effect, "card": card,
            "observation_id": observation.id, "observation_version": observation.version,
            "observations": definitions,
            "region": region_for(card, rolling=self.rolling, observations=self.observations),
            "activation_window": None, "baseline": None,
        }
        self.ledger.append({"kind": "policy.promised", **ballot})
        self.pending_votes.append(ballot)

    def _policy_observations(self, vote: dict) -> ObservationBook:
        """A later observation registration cannot rewrite a frozen ballot's measurement."""
        return ObservationBook(vote["observations"], run=self.observation_runner.run,
                               reject=self._observation_out_of_range)

    def _activate_policy_ballots(self, proposal_id: str) -> None:
        """All proposal kinds start their promised horizon only when their change takes effect."""
        for vote in self.pending_votes:
            if vote["amendment_id"] != proposal_id:
                continue
            observations = self._policy_observations(vote)
            values = measure_card(vote["card"], self.card_samples, observations)
            activated = {**vote, "baseline": fmean(values.values()) if values else None,
                         "activation_window": self.window.index,
                         "region": vote["region"] or region_for(
                             vote["card"], rolling=self.rolling, observations=observations)}
            self.ledger.append({"kind": "policy.activated", **activated})
            vote.update(activated)

    def _settle_policy(self, handle, score, status) -> None:
        """Policy feedback reaches the original assembly's durable, private return channel."""
        self.queue.settle(handle, channel="policy", score=score, status=status,
                          definition_version="policy-region-brier-v1", sampling_ref=None)

    def _close_policy_window(self, index: int) -> None:
        """Each vote is graded once at its declared post-activation boundary, or censored."""
        remaining = []
        for vote in self.pending_votes:
            activation = vote["activation_window"]
            effect = vote["prediction"]
            if activation is None or index < activation + effect.window - 1:
                remaining.append(vote)
                continue
            values = measure_card(vote["card"], self.card_samples, self._policy_observations(vote))
            value = fmean(values.values()) if values else None
            baseline = vote["baseline"]
            region = vote["region"]
            status = (SettleStatus.CENSORED if value is None or region is None
                      else SettleStatus.SETTLED)
            outcome = (violation(region, value) == 0
                       if status is SettleStatus.SETTLED else None)
            score = float(vote["vote"] == outcome) if outcome is not None else 0.0
            self.ledger.append({"kind": "policy.outcome", "handle": vote["handle"],
                                "amendment_id": vote["amendment_id"], "baseline": baseline,
                                "region": asdict(region) if region is not None else None,
                                "observation_id": vote["observation_id"],
                                "observation_version": vote["observation_version"],
                                "value": value, "window": index, "y": outcome,
                                "score": score, "status": str(status)})
            self._settle_policy(vote["handle"], score, status)
        self.pending_votes[:] = remaining
        pending_handles = {self.queue.get(f.handle).parent_handle for f in self.book.pending()}
        self.card_samples.prune((*self.charter.cards, *(v["card"] for v in remaining)),
                                pending_handles=pending_handles)

    def _record_card_forecasts(self, pending, baseline) -> None:
        """Resolved samples retain distinct forecaster and judged-return identities."""
        from factorylab.charter.measurement import record_card_forecasts

        record_card_forecasts(self, pending, baseline)
