"""Runtime governance method group."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from statistics import fmean
from typing import Any

from factorylab.charter.amendment import Amendment, PredictedEffect
from factorylab.charter.book import Refusal
from factorylab.charter.charter import Charter, MetricCard
from factorylab.charter.controller import promise_kept
from factorylab.charter.measurement import measure_card, preflight_measurement
from factorylab.cortex.assembly import AssemblySpec
from factorylab.cortex.registration import (
    MAX_PROPOSALS_PER_RETURN,
    AssemblyProposal,
    ChallengeProposal,
    ConnectorProposal,
    LearnerProposal,
    MarketProposal,
    ModelProposal,
    ObservationProposal,
    PredicateProposal,
    RetireProposal,
    ServiceProposal,
    ToolProposal,
    parse_proposals,
    reward_contracts,
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
from factorylab.runtime.shared import PredicateRunner, _to_plain, assembly_rewards
from factorylab.runtime.summary import _assembly_contract, _model_contract
from factorylab.settlement.vocabulary import COMMISSIONED_JUDGE_REFUSAL
from factorylab.world.x402 import X402Error

#: What a challenge ballot shows its voter beside the amendment: the evidence the
#: challenger gave, cut to this many characters, and the last this many trial
#: windows of the two series, each scope list cut to this many entries. The
#: ledger keeps the whole of all three; the ballot is a bounded view of it.
BALLOT_EVIDENCE_CHARS = 2000
BALLOT_SERIES_WINDOWS = 24
BALLOT_SERIES_SCOPES = 8


@dataclass(frozen=True)
class WorkAssemblySpec(AssemblySpec):
    """Population work retains its admitted reward shapes across checkpoints."""

    reward_shapes: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        super().__post_init__()
        object.__setattr__(self, "reward_shapes", reward_contracts(self.emits, self.reward_shapes))


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
        from factorylab.runtime.resume import JournalProxy

        self.registered_predicates = {}
        self.kind_reward_shapes = {}
        self.forecast_returns = {}
        # challenge id -> frozen incumbent and replacement cards, trial series, status
        self.challenges: dict[str, dict] = {}
        self.predicate_runner = JournalProxy(PredicateRunner(), self.ledger, "predicate")
        self.observer.predicates = self.predicates
        self.PROPOSAL_SHAPES = deepcopy(self.PROPOSAL_SHAPES)
        for kind in ("connector", "retire"):
            self.PROPOSAL_SHAPES[kind]["predicted_effect"] = {
                "card_id": "a current card id", "direction": "decrease", "window": 1}

    def _commissioned_judge_refusal(self, target: str) -> str | None:
        """Name why a judging contract cannot be commissioned as a child, or None.

        GPT-6 Pro's third reading, §3 further: "the commissioned-child-judge path
        has incompatible exclusions (remove the suggestion it is usable)"; §7:
        "Remove promises of commissioned judges and funding routes that cannot
        execute." The two exclusions really are incompatible. A requested judge
        may only address the chain that requested it (``_child_subject_refusal``),
        and it may not judge that chain, because nothing judges its own output or
        its ancestors' (``_judged_event``). Between them there is no return left
        for it to judge, so the route could be bought, paid for, and never
        executed. Until a commissioned judge can execute without self-judgement,
        it is refused before the money is spent, with the reason in public.

        Judging work still reaches a seat the three ways it always did: the
        router's sampling, the adversarial share and the cascade.
        """
        from factorylab.runtime.shared import assembly_rewards

        assembly = self.assemblies.get(target)
        if assembly is None:
            return None
        kinds = set(assembly.spec.emits or ())
        shapes = set(assembly_rewards(assembly.spec).values())
        if kinds & {"Verdict", "MetaVerdict"} or "conformity" in shapes:
            return COMMISSIONED_JUDGE_REFUSAL
        return None

    def _refuse_commissioned_judge(self, parent, item, target: str, reason: str) -> tuple:
        """Refuse the request before a decision is opened or a call is made."""
        self.ledger.append({"kind": "requests.refused", "handle": parent.handle,
                            "target": target, "reason": reason, "ts": self.clock.now_ns})
        self._refusal_to_owner(parent.handle, "request_refused", reason)
        return {"tool": f"assembly:{target}", "args": item.inputs,
                "result": {"error": reason}}, 0

    def _invoke_child(self, action_id, parent, item, ceiling):
        """A judging contract is never commissioned as a child; everything else proceeds."""
        target = action_id if item.target == "self" else item.target
        reason = self._commissioned_judge_refusal(target)
        if reason is not None:
            return self._refuse_commissioned_judge(parent, item, target, reason)
        return super()._invoke_child(action_id, parent, item, ceiling)

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
                        known_reward_shapes=self._kind_rewards(),
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
        """Ledger a refused proposal and address the reason to its proposer's inbox."""
        self.stats.registrations_rejected += 1
        item = {"kind": "registration.rejected", "handle": handle, "reason": reason}
        if index is not None:
            item["index"] = index
        self.ledger.append({**item, "ts": self.clock.now_ns})
        self.window.registration_rejections += 1
        self._refusal_to_owner(handle, "registration_rejected", reason,
                               **({"index": index} if index is not None else {}))

    def _kind_rewards(self) -> dict[str, str]:
        """Kind meanings outlive the assemblies that first declared them."""
        return {**{k: v for a in self.assemblies.values()
                   for k, v in assembly_rewards(a.spec).items()}, **self.kind_reward_shapes}

    def _validate_output_contract(self, parsed, req) -> None:
        """Registered predicates extend forecast validation without relaxing any return schema.

        The fault a rejected forecast raises stays scoped to that forecast. The
        inherited validator names the section with a ``SectionError``, which is a
        ``ValueError`` and is caught here so a registered predicate can answer for
        a predicate the seed vocabulary does not know; when it cannot, the failure
        is re-raised as a ``SectionError`` on the same item. A plain ``ValueError``
        here would void the whole return, discarding a valid order because one
        optional forecast beside it was bad.
        """
        from factorylab.cortex.assembly import SectionError

        try:
            super()._validate_output_contract(parsed, req)
        except ValueError as exc:
            if str(exc) != "unknown forecast predicate":
                raise
            # The inherited validator has already checked the complete return,
            # binding, custom schema and tool arguments before its seed lookup.
            from factorylab.settlement.vocabulary import _validate_params

            for index, forecast in enumerate(parsed.get("forecasts", [])):
                try:
                    # The lookup answers for the forecast too: an id the book cannot
                    # resolve (empty, or a stored definition that no longer builds) is
                    # that forecast's fault, not the return's.
                    predicate = self.predicates.get(forecast["predicate"])
                    _validate_params(forecast["predicate"], forecast["params"],
                                     predicate=predicate)
                except (ValueError, TypeError, ArithmeticError, RecursionError) as fault:
                    raise SectionError("forecasts", str(fault), index) from None

    @property
    def predicates(self):
        """Predicate resolution always reads this world's current persisted history."""
        from factorylab.settlement.vocabulary import PredicateBook

        return PredicateBook(self.registered_predicates,
                             run=lambda code, facts: self.predicate_runner.run(code, facts))

    def _register_predicate(self, handle: str, prop: PredicateProposal) -> None:
        """Only jailed boolean preflights and durable admission publish a predicate version."""
        if not self.tool_jail_available:
            raise Infeasible("no jail on this host")
        closed = self.card_samples.windows[-1] if self.card_samples.windows else None

        def persist(predicate):
            contract = Contract(
                id=f"predicate:{prop.id}", version=predicate.version, kind="observation",
                description=prop.description, input_schema={"type": "object"},
                output_schema={"type": "boolean"}, price=PriceSpec({}),
                permissions=frozenset({"sandbox.run"}),
                resource_bounds=ResourceBounds(
                    max_duration_ns=OBSERVATION_TIMEOUT_S * 1_000_000_000))
            self._register_with_trial(contract, handle, self.ev.trial_amount_micro)

        predicate = self.predicates.register(
            prop.id, prop.description, prop.code,
            facts=window_facts(closed) if closed is not None else None,
            persist=persist, provenance=handle,
            preflight=lambda p, value, error: self.ledger.append({
                "kind": "predicate.preflight", "handle": handle, "predicate": p.id,
                "version": p.version, "window": closed["index"],
                "value": value, "error": error, "ts": self.clock.now_ns}))
        self._emit(EventKind.REGISTERED, {"kind": "predicate", "id": predicate.id,
                                          "version": predicate.version})

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
        from factorylab.runtime.propensity import canonical_label

        actions = tuple(canonical_label(a) for a in prop.actions)
        if len(set(actions)) != len(actions):
            raise ValueError("learner actions overlap after canonicalization")
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
        self._require_trial(handle, self.ev.trial_amount_micro)
        self._register_with_trial(contract, handle, self.ev.trial_amount_micro)
        self._move_trial(handle, self.ev.trial_amount_micro, to=None, reason="trial:learner")
        lid = self._assembly_learner_id(prop.assembly_id)
        if prop.learner == "blum_mansour":
            from factorylab.learners.blum_mansour import BlumMansour

            inner = BlumMansour(lambda acts: EXP3(acts, prop.gamma), actions, id=lid)
        else:
            inner = EXP3(actions, prop.gamma, id=lid)
        self.assembly_learners[prop.assembly_id] = SnapshotLearner(inner, id=lid)
        self.stats.assembly_learners_registered += 1
        self._emit(EventKind.REGISTERED, {"kind": "learner", "id": prop.assembly_id,
                                          "learner": prop.learner,
                                          "actions": list(actions)})

    def _policy_prediction(self, value: Any) -> PredictedEffect:
        """Connector and retirement promises bind to an existing measurable charter card.

        While a challenge is being trialled, or awaits its ballot, a promise may
        name the challenge id instead: it is then graded on the replacement
        card the challenge offered, frozen as it was admitted, and not on the
        card it challenges.
        """
        prediction = PredictedEffect.parse(value)
        challenge = self._live_challenges().get(prediction.card_id)
        if challenge is not None:
            preflight_measurement(challenge["replacement"], self._policy_observations(challenge),
                                  registered_kinds=frozenset(self._kind_rewards()))
            return prediction
        card = next((c for c in self.charter.cards if c.id == prediction.card_id), None)
        if card is None:
            raise ValueError("predicted_effect.card_id must name a current card")
        preflight_measurement(card, self.observations,
                              registered_kinds=frozenset(self._kind_rewards()))
        return prediction

    def _live_challenges(self) -> dict[str, dict]:
        """Challenges a promise may still name: in trial, due for ballot, or balloted."""
        return {cid: ch for cid, ch in self.challenges.items()
                if ch["status"] in ("trial", "due", "balloted")}

    def _challenge_cards(self) -> dict[str, MetricCard]:
        """The replacement cards under trial or ballot, keyed by challenge id."""
        return {cid: ch["replacement"] for cid, ch in self._live_challenges().items()}

    def _register_challenge(self, handle: str, prop: ChallengeProposal) -> None:
        """Admit a metric challenge: one novelty trial buys a frozen side-by-side trial.

        The replacement keeps the challenged card's id and norm, so adopting it
        is the ordinary replace amendment. Both cards and the observation
        definitions behind them are frozen here; the incumbent keeps pricing
        the live charter throughout, so commitments incurred under it settle
        under it. The challenge is ledgered before it exists in state.
        """
        from factorylab.charter.amendment import proposed_answers_for
        from factorylab.charter.book import validate_observation_bindings
        from factorylab.runtime.cards import parses

        incumbent = next((c for c in self.charter.cards if c.id == prop.card_id), None)
        if incumbent is None:
            raise ValueError("challenge card_id must name a current card")
        if any(ch["card_id"] == prop.card_id for ch in self._live_challenges().values()):
            raise ValueError("card is already under challenge")
        spec = prop.replacement
        answers_for = spec.get("answers_for", incumbent.answers_for)
        if answers_for not in self._kind_rewards():
            answers_for = proposed_answers_for(answers_for, incumbent.id)
        replacement = MetricCard(
            incumbent.id, incumbent.norm,
            str(spec.get("description", incumbent.description)),
            str(spec.get("units", incumbent.units)),
            spec["window"], f"{spec['rule']} {spec['value']}", str(spec["observation"]),
            answers_for,
        )
        if replacement == incumbent:
            raise ValueError("challenge leaves the card unchanged")
        replacement.validate_answers_for(frozenset(self._kind_rewards()))
        if not parses(replacement):
            raise ValueError("replacement acceptable_region has no finite usable bounds")
        region_for(replacement, rolling={}, observations=self.observations)
        preflight_measurement(replacement, self.observations,
                              registered_kinds=frozenset(self._kind_rewards()))
        validate_observation_bindings(tuple(
            replacement if c.id == incumbent.id else c for c in self.charter.cards))
        slug = "".join(ch if ch.isalnum() else "-" for ch in incumbent.id.lower())
        challenge_id = f"challenge-{len(self.challenges) + 1}-{slug}"[:48].rstrip("-")
        definitions = {}
        for card in (incumbent, replacement):
            observation = self.observations.get(card.observation)
            if observation is not None and observation.registered:
                definitions[observation.id] = deepcopy(
                    self.registered_observations[observation.id])
        contract = Contract(
            id=f"challenge:{challenge_id}",
            version=1,
            kind="tool",
            description="metric challenge",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            price=PriceSpec({}),
            permissions=frozenset(),
            resource_bounds=ResourceBounds(),
        )
        self._register_proposal(contract, handle, reason="trial:challenge")
        record = {
            "id": challenge_id, "handle": handle, "card_id": incumbent.id,
            "evidence": prop.evidence, "incumbent": incumbent, "replacement": replacement,
            "trial_windows": prop.trial_windows, "start_window": self.window.index,
            "observations": definitions, "series": [], "status": "trial",
            "amendment_id": None,
        }
        self.ledger.append({"kind": "challenge.proposed",
                            **{k: v for k, v in record.items() if k != "series"},
                            "edition": self.charter.edition, "ts": self.clock.now_ns})
        self.challenges[challenge_id] = record

    def _close_challenge_window(self, index: int) -> None:
        """Measure every trialled pair on the closing window and ledger both series.

        Each closed window of a trial adds one ``challenge.window`` item holding
        the incumbent's and the replacement's measurements, read with the
        observation definitions frozen at admission. The trial ends when it has
        its declared number of windows; the ballot waits for the next boundary.
        """
        for challenge in self.challenges.values():
            if challenge["status"] != "trial":
                continue
            observations = self._policy_observations(challenge)
            row = {"window": index}
            for side in ("incumbent", "replacement"):
                scopes = measure_card(challenge[side], self.card_samples, observations)
                row[side] = {"card_id": challenge[side].id,
                             "observation": challenge[side].observation,
                             "value": fmean(scopes.values()) if scopes else None,
                             "scopes": scopes}
            self.ledger.append({"kind": "challenge.window", "challenge_id": challenge["id"],
                                **row, "ts": self.clock.now_ns})
            challenge["series"].append(row)
            if len(challenge["series"]) >= challenge["trial_windows"]:
                self.ledger.append({"kind": "challenge.trial_complete",
                                    "challenge_id": challenge["id"], "window": index,
                                    "windows": len(challenge["series"]),
                                    "ts": self.clock.now_ns})
                challenge["status"] = "due"

    def _ballot_due_challenges(self) -> None:
        """A completed trial goes to the existing amendment ballot at the next boundary.

        The adoption candidate is the ordinary replace amendment, proposed under
        the challenge's id with the observation bindings frozen at admission, so
        a definition that drifted during the trial refuses activation exactly
        as it would for any amendment. The challenge's own promise is the
        replacement holding inside its region one window after activation.
        """
        if getattr(self, "dormancy", None) is not None:
            # Dormant (C2): no paid cognition, so a completed trial waits for the
            # first window boundary after the world wakes; its status stays due.
            return
        for challenge in self.challenges.values():
            if challenge["status"] != "due":
                continue
            replacement = challenge["replacement"]
            direction = ("increase" if replacement.acceptable_region.startswith(
                ("at least", "above")) else "decrease")
            try:
                am = Amendment(
                    id=challenge["id"], proposer_handle=challenge["handle"],
                    edition_base=self.charter.edition, add=(), replace=(replacement,),
                    remove=(),
                    predicted_effect=PredictedEffect(replacement.id, direction, 1),
                )
                self.charter_book.propose(am, self._policy_observations(challenge))
            except ValueError as exc:
                self.ledger.append({"kind": "challenge.refused", "challenge_id": challenge["id"],
                                    "reason": str(exc)[:300], "ts": self.clock.now_ns})
                challenge["status"] = "refused"
                continue
            self.ledger.append({"kind": "challenge.balloted", "challenge_id": challenge["id"],
                                "amendment_id": am.id, "windows": len(challenge["series"]),
                                "ts": self.clock.now_ns})
            challenge["status"] = "balloted"
            challenge["amendment_id"] = am.id
            self.stats.amendments_proposed += 1
            self.window.amendments_proposed += 1
            eligible = self._committee_eligible()
            proposer = self.handle_to_assembly.get(challenge["handle"])
            if proposer is None:
                try:
                    proposer = self.queue.get(challenge["handle"]).propensity.chosen
                except KeyError:
                    pass
            eligible.pop(proposer, None)
            committee = self.charter_book.seat(am.id, eligible, self.rng,
                                               size=self.m.committee.seats)
            self._hold_vote(am, committee)
    def _register_proposal(self, contract: Contract, handle: str, *, reason: str) -> None:
        """Register a proposal's contract and charge its proposer the trial, as any
        registration is charged.

        Each voter a proposal summons pays for its own ballot; the proposer that
        chose to summon them pays its trial from its own entitlement (C10). One
        that cannot is refused before anything is registered or any committee is
        drawn, and a refused registration returns its receipt to the window.
        """
        amount = self.ev.trial_amount_micro
        self._require_trial(handle, amount)
        self._register_with_trial(contract, handle, amount)
        self._move_trial(handle, amount, to=None, reason=reason)

    def _trial_proposer(self, handle: str) -> str | None:
        """The seat whose entitlement pays a registration's trial, when the handle has one."""
        owner = self.handle_to_assembly.get(handle)
        return owner if owner in self.assemblies else None

    def _require_trial(self, handle: str, amount: int) -> None:
        """A proposer must be able to pay the trial from its own entitlement (C10)."""
        proposer = self._trial_proposer(handle)
        if proposer is not None and self.budget.entitlement(proposer) < amount:
            raise Infeasible("proposer's entitlement is below the trial amount")

    def _require_founder_endowment(self, handle: str, amount: int, target: str,
                                   *, explicit: bool) -> None:
        """Require a known founder's free entitlement before admission.

        Guarantees an explicit endowment cannot fall back to the commons or double-credit
        the founder. Legacy trial registrations retain their historical pool fallback when
        no proposer seat is known.
        """
        proposer = self._trial_proposer(handle)
        if proposer is None:
            if explicit:
                raise Infeasible("explicit endowment requires a known founder")
            return
        if target == proposer:
            raise ValueError("founder cannot endow itself")
        if self.budget.entitlement(proposer) < amount:
            raise Infeasible("founder's entitlement is below the endowment")

    def _preflight_assembly(self, spec: AssemblySpec) -> None:
        """Validate assembly state that can fail before consuming its novelty receipt."""
        self._check_event_schemas(spec)
        if spec.model_id != "program" and spec.model_id not in self.prices.prices:
            raise ValueError("assembly model is not priced in this world")

    def _move_trial(self, handle: str, amount: int, *, to: str | None, reason: str,
                    allow_pool: bool = True) -> None:
        """Move an admitted trial from the proposer to the child seat, or to the pool.

        A legacy child registered without a known proposer is endowed from the pool when
        the pool can cover it; explicit founder endowments refuse that path.
        """
        proposer = self._trial_proposer(handle)
        if to is not None:
            # A child belongs to its proposer's lineage: releases are split per
            # lineage, so registering children never buys a larger share (C10).
            self.budget.adopt(to, proposer, reason)
        if proposer is None:
            if to is not None:
                if not allow_pool:
                    raise Infeasible("explicit endowment requires a known founder")
                try:
                    self.budget.grant(to, amount, reason)
                except Infeasible:
                    pass
            return
        if to == proposer:
            raise ValueError("founder cannot endow itself")
        if to is None:
            self.budget.debit(proposer, amount, reason)
        elif to != proposer:
            self.budget.transfer(proposer, to, amount, reason)

    def _challenge_ballot_inputs(self, amendment_id: str) -> dict | None:
        """The evidence a challenge ballot shows its voter, or None for an ordinary amendment.

        A challenge-originated amendment carries the challenger's evidence and
        the two measured series side by side, one row per trial window with the
        incumbent's and the replacement's value and scopes, so a voter judges
        the trial rather than the prose. Bounded: the evidence is cut to
        ``BALLOT_EVIDENCE_CHARS``, the series to their last
        ``BALLOT_SERIES_WINDOWS`` rows and each scope list to
        ``BALLOT_SERIES_SCOPES`` entries; the ledger's ``challenge.window``
        items keep the whole series.
        """
        challenge = self.challenges.get(amendment_id)
        if challenge is None or challenge.get("amendment_id") != amendment_id:
            return None

        def side(row: dict, name: str) -> dict:
            measured = row.get(name) or {}
            scopes = measured.get("scopes") or {}
            return {"observation": measured.get("observation"),
                    "value": measured.get("value"),
                    "scopes": {str(k): v for k, v in sorted(scopes.items(),
                                                           key=lambda kv: str(kv[0]))
                               [:BALLOT_SERIES_SCOPES]}}

        series = list(challenge.get("series") or [])
        rows = series[-BALLOT_SERIES_WINDOWS:]
        evidence = str(challenge.get("evidence") or "")
        return {
            "id": challenge["id"],
            "card_id": challenge["card_id"],
            "evidence": evidence[:BALLOT_EVIDENCE_CHARS],
            "evidence_truncated": len(evidence) > BALLOT_EVIDENCE_CHARS,
            "incumbent": asdict(challenge["incumbent"]),
            "replacement": asdict(challenge["replacement"]),
            "trial_windows": challenge["trial_windows"],
            "windows_measured": len(series),
            "windows_shown": len(rows),
            "series": [{"window": row.get("window"),
                        "incumbent": side(row, "incumbent"),
                        "replacement": side(row, "replacement")} for row in rows],
        }

    def _register(self, handle: str, prop: Any, *,
                  predicted_effect: PredictedEffect | None = None) -> None:
        amount = self.ev.trial_amount_micro
        if isinstance(prop, ChallengeProposal):
            self._register_challenge(handle, prop)
            return
        if isinstance(prop, MarketProposal):
            self._register_market(handle, prop)
            return
        if isinstance(prop, RetireProposal):
            self._propose_retirement(handle, prop, predicted_effect=predicted_effect)
            return
        if isinstance(prop, ConnectorProposal):
            self._register_connector(handle, prop, predicted_effect=predicted_effect)
            return
        if isinstance(prop, ObservationProposal):
            self._register_observation(handle, prop)
            return
        if isinstance(prop, PredicateProposal):
            self._register_predicate(handle, prop)
            return
        if isinstance(prop, LearnerProposal):
            self._register_learner(handle, prop)
            return
        if isinstance(prop, ServiceProposal):
            self._register_service(handle, prop)
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
            self._require_trial(handle, amount)
            self._register_with_trial(contract, handle, amount)
            self._move_trial(handle, amount, to=None, reason="trial:tool")
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
            limit = self._provider_completion_limit(base)
            if limit is not None:
                self.catalogue_completion_limits[prop.openrouter_id] = limit
            self._emit(
                EventKind.REGISTERED, {"kind": "model", "id": prop.openrouter_id}
            )
        elif isinstance(prop, AssemblyProposal):
            # A program seat (C8) is admitted like a model seat — same trial, same
            # contract, same routers — but its executor is jailed code, so a host
            # without the jail refuses it before the trial is spent.
            program = prop.model_id == "program"
            if program and not self.tool_jail_available:
                raise Infeasible("no jail on this host")
            live = prop.id in self.assemblies and prop.id not in self.retired_assemblies
            version = (self.assemblies[prop.id].spec.version + 1
                       if prop.id in self.assemblies else 1)
            emits = prop.emits or None
            declared_emits = prop.emits or tuple(prop.reward_shapes)
            shapes = reward_contracts(declared_emits, prop.reward_shapes,
                                      registered=self._kind_rewards())
            custom = any(k not in ("ProducerReturn", "Verdict", "MetaVerdict", "Exposure")
                         for k in declared_emits)
            if program:
                from factorylab.cortex.assembly import ProgramAssemblySpec

                spec_class, extra = ProgramAssemblySpec, {
                    "code": prop.code, "timeout_s": prop.timeout_s,
                    "state_policy": prop.state_policy,
                    # A watcher's predicate (edition 3, C2) is part of its spec.
                    **({"trigger": dict(prop.trigger)} if prop.trigger else {}),
                    **({"reward_shapes": shapes} if custom else {})}
            else:
                spec_class = WorkAssemblySpec if custom else AssemblySpec
                extra = {"reward_shapes": shapes} if custom else {}
            max_tokens = self._resolve_max_tokens(
                prop.model_id, prop.max_tokens, program=program
            )
            spec = spec_class(
                id=prop.id, version=version, model_id=prop.model_id,
                system_prompt=prop.system_prompt, max_tokens=max_tokens,
                effort=prop.effort, accepts=frozenset(prop.accepts), role=prop.role,
                emits=emits, schemas=prop.schemas, **extra)
            contract = _assembly_contract(prop.id, prop.role, prop.accepts, max_tokens,
                                         emits=spec.emits, schemas=spec.schemas, version=version)
            amount = (prop.endowment_micro if prop.endowment_micro is not None
                      else self.ev.trial_amount_micro)
            if type(amount) is not int or amount <= 0:
                raise ValueError("endowment_micro must be a positive integer")
            self._preflight_assembly(spec)
            self._require_founder_endowment(
                handle, amount, prop.id, explicit=prop.endowment_micro is not None)
            # Novelty admission remains the fixed registration trial. A founder's
            # chosen endowment is a conserved transfer after admission, not a demand
            # on the shared novelty runway.
            self._register_with_trial(
                contract, handle, self.ev.trial_amount_micro,
                refuse=("id already registered: a live assembly is retired by vote before "
                        "its id takes a next version") if live else "")
            self._instantiate(spec)
            if getattr(spec, "trigger", None):
                # A watcher answers to the seat that registered it: the kernel wakes
                # that seat with the trigger fact, whatever it deferred (C2).
                self.subscription_book.watch(
                    prop.id, owner=self.handle_to_assembly.get(handle),
                    trigger=dict(spec.trigger))
            self._move_trial(
                handle, amount, to=prop.id, reason="trial:assembly",
                allow_pool=prop.endowment_micro is None)
            if custom:
                self.kind_reward_shapes.update(shapes)
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
                    **({"reward_shapes": shapes} if custom else {}),
                    **({"program": True, "state_policy": prop.state_policy} if program else {}),
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
        if prop.pay == "x402" and prop.max_call_micro > self.m.treasury.max_request_micro:
            raise ValueError("connector cap exceeds treasury.max_request_micro")
        owner = self.handle_to_assembly.get(handle)
        if owner is None:
            try:
                owner = self.queue.get(handle).propensity.chosen
            except KeyError:
                raise ValueError("connector proposal needs a caller decision") from None
        result, _ = self._fetch_connector(
            owner, handle, {"id": prop.id, "path": prop.preflight_path}, origin=prop.origin)
        # Preflight establishes bounds, not information for the proposer.
        if "error" in result:
            raise ValueError(f"connector preflight: {result['error']}")
        try:
            version = self.registry.get(f"connector:{prop.id}").version + 1
        except KeyError:
            version = 1
        # The proposer pays its trial (defect 13): one that cannot is refused before
        # a committee is drawn and its ballots are bought.
        self._require_trial(handle, self.ev.trial_amount_micro)
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
            description=prop.description, input_schema={
                "origin": prop.origin, "preflight_path": prop.preflight_path,
                "pay": prop.pay, "max_call_micro": prop.max_call_micro},
            output_schema={"type": "string"},
            price=PriceSpec({"call": self.m.connectors.call_price_micro}),
            permissions=frozenset({"connector.fetch"}),
            resource_bounds=ResourceBounds(
                max_duration_ns=self.m.connectors.timeout_s * 1_000_000_000,
                max_memory_bytes=self.m.connectors.max_bytes),
        )
        try:
            self._register_proposal(contract, handle, reason="trial:connector")
        except (Infeasible, ValueError, PermissionError):
            self._censor_ballots(vote_id)
            raise
        self.ledger.append({"kind": "connector.registered", "id": prop.id,
                            "version": version, "description": prop.description,
                            "origin": prop.origin, "handle": handle, "vote_id": vote_id,
                            "preflight_path": prop.preflight_path, "pay": prop.pay,
                            "max_call_micro": prop.max_call_micro,
                            "predicted_effect": asdict(predicted_effect),
                            "ts": self.clock.now_ns})
        self._activate_policy_ballots(vote_id)
        self._emit(EventKind.REGISTERED, {"kind": "connector", "id": prop.id,
                                          "version": version, "origin": prop.origin})

    def _register_service(self, handle: str, prop: ServiceProposal) -> None:
        """One novelty trial publishes a paid endpoint for a registered population program.

        The program is frozen at registration: the ledger item carries the exact
        source the wake host serves, so a later tool version never changes what a
        buyer already paid for. Nothing here opens a socket; ``deploy/serve.py``
        reads this item and ``runtime/seller.py`` verifies each payment.
        """
        from factorylab.runtime.seller import service_contract

        tool = self.population_tools.get(prop.program_id)
        if tool is None:
            raise ValueError("program_id must name a registered population tool")
        if not self.tool_jail_available:
            raise Infeasible("no jail on this host")
        try:
            version = self.registry.get(f"service:{prop.program_id}").version + 1
        except KeyError:
            version = 1
        contract = service_contract(prop, version, timeout_s=tool.timeout_s)
        self._register_with_trial(contract, handle, self.ev.trial_amount_micro)
        owner = self.handle_to_assembly.get(handle)
        # The service's paid calls are this return's economic consequence (C11).
        self.consequences.bind_service(prop.program_id, handle, self.n)
        self.ledger.append({
            "kind": "service.registered", "id": prop.program_id, "version": version,
            "program_id": prop.program_id, "price_micro": prop.price_micro,
            "description": prop.description, "handle": handle, "owner": owner,
            "code": tool.code, "args_schema": _to_plain(tool.args_schema),
            "timeout_s": tool.timeout_s, "ts": self.clock.now_ns,
        })
        self._emit(EventKind.REGISTERED, {"kind": "service", "id": prop.program_id,
                                          "version": version,
                                          "price_micro": prop.price_micro})

    def _register_market(self, handle: str, prop: MarketProposal) -> None:
        """One novelty trial admits a listed market, with durable identity before effects."""
        listed = self.exchange.instruments()
        if prop.coin not in {row["coin"] for row in listed.get(prop.market, [])}:
            raise ValueError("market is not listed by the venue")
        allowed = self.venue_tools.spot_pairs if prop.market == "spot" else self.venue_tools.coins
        if prop.coin in allowed:
            raise ValueError("market is already registered for trading")
        contract = Contract(
            id=f"market:{prop.market}:{prop.coin}", version=1, kind="exchange",
            description=f"Trade {prop.market} {prop.coin}",
            input_schema={"coin": prop.coin, "market": prop.market},
            output_schema={}, price=PriceSpec({}), permissions=frozenset(),
            resource_bounds=ResourceBounds())
        self._register_with_trial(contract, handle, self.ev.trial_amount_micro)
        self.ledger.append({"kind": "market.registered", "coin": prop.coin,
                            "market": prop.market, "handle": handle, "ts": self.clock.now_ns})
        self._admit_market(prop.coin, prop.market)
        self._emit(EventKind.REGISTERED, {"kind": "market", "coin": prop.coin,
                                         "market": prop.market, "version": 1})

    def _admit_market(self, coin: str, market: str) -> None:
        """Rebuilding trading permission requires no new external write or payment."""
        self.venue_tools.admit_market(coin, market)
        attr = "spot_pairs" if market == "spot" else "coins"
        setattr(self.exchange, attr, tuple(dict.fromkeys((*getattr(self.exchange, attr), coin))))
        self._refresh_venue_schemas()

    def _refresh_venue_schemas(self) -> None:
        """Current market permissions preserve existing tool examples and prices."""
        for spec in self.venue_tools.contracts():
            if spec.id in self.tool_specs:
                self.tool_specs[spec.id]["args_schema"].update(_to_plain(spec.args_schema))

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
                        (c.get("answers_for") if isinstance(c.get("answers_for"), str)
                         and c.get("answers_for") in self._kind_rewards()
                         else proposed_answers_for(c.get("answers_for"), str(c.get("id", "")))),
                    )
                )
            from factorylab.runtime.cards import parses

            for card in out:
                card.validate_answers_for(frozenset(self._kind_rewards()))
                if not parses(card):
                    raise ValueError("card acceptable_region has no finite usable bounds")
                # A card may name a registered observation; an unregistered one
                # is refused here, before a vote, with the reason.
                region_for(card, rolling={}, observations=self.observations)
                preflight_measurement(card, self.observations,
                                      registered_kinds=frozenset(self._kind_rewards()))
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
        self._register_proposal(contract, handle, reason="trial:amendment")
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
        """Distinct independently requested decisions need observed consequences to qualify.

        An outcome the world never let anyone observe (a censored payoff) is not a
        settled decision: it qualifies nobody, however many of them a seat has.
        """
        from collections import Counter

        from factorylab.charter.committee import experienced

        evidence = {(r.handle, self.handle_to_assembly.get(r.handle))
                    for r in self.consequences.table.returns
                    if r.payoff is not None and r.payoff.censored is None
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
        self._register_proposal(contract, handle, reason="trial:retirement")
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
        waiting = self.cadence.world_block(self.tick_clock)["waiting"]
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
                                      tick_interval_ns=self.tick_clock,
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
            self.cadence.activated(motion.id, self.clock.now_ns, self.tick_clock)
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
        # A challenge-originated amendment shows its voters the trial (C7); an
        # ordinary amendment's ballot is unchanged.
        challenge_inputs = (None if retiring or connector is not None
                            else self._challenge_ballot_inputs(am.id))
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
                self.events_budget + self.ev.consequence_backstop_ticks
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
                          # A ballot reads the motion like a machine (C7): its own
                          # operating access, not the whole world block.
                          "actor_context": self._operating_context(assembly_id,
                                                                   self._world_block()),
                          "charter": self._charter_text()}
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
                    **({"challenge": challenge_inputs} if challenge_inputs else {}),
                    "charter": self._charter_text(),
                    "actor_context": self._operating_context(assembly_id,
                                                             self._world_block()),
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
                 "Vote on an amendment to the charter's metric cards proposed by a metric "
                 "challenge: inputs.challenge carries the challenger's evidence and both "
                 "measured series, incumbent and replacement per trial window."
                 if challenge_inputs else
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
                tick_interval_ns=self.tick_clock,
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
            tick_interval_ns=self.tick_clock,
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
            self.cadence.activated(am.id, self.clock.now_ns, self.tick_clock)
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
        self._ballot_due_challenges()
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
        cards.update(self._challenge_cards())  # a promise may name a challenge under trial
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
                          definition_version="policy-promise-brier-v2", sampling_ref=None)

    def _close_policy_window(self, index: int) -> None:
        """Each vote is graded once at its declared post-activation boundary, or censored."""
        self._close_challenge_window(index)  # both series of every trial, ledgered
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
            resolution = self.m.committee.promise_resolution
            status = (SettleStatus.CENSORED
                      if value is None or region is None or baseline is None
                      else SettleStatus.SETTLED)
            # The outcome is the promise, not compliance: a change whose value went the
            # wrong way is a broken promise even when the region still holds.
            outcome = (promise_kept(effect.direction, baseline, value, region,
                                    resolution=resolution)
                       if status is SettleStatus.SETTLED else None)
            score = float(vote["vote"] == outcome) if outcome is not None else 0.0
            self.ledger.append({"kind": "policy.outcome", "handle": vote["handle"],
                                "amendment_id": vote["amendment_id"], "baseline": baseline,
                                "direction": effect.direction, "resolution": resolution,
                                "region": asdict(region) if region is not None else None,
                                "observation_id": vote["observation_id"],
                                "observation_version": vote["observation_version"],
                                "value": value, "window": index, "y": outcome,
                                "score": score, "status": str(status)})
            self._settle_policy(vote["handle"], score, status)
        self.pending_votes[:] = remaining
        pending_handles = {self.queue.get(f.handle).parent_handle for f in self.book.pending()}
        self.card_samples.prune((*self.charter.cards, *(v["card"] for v in remaining),
                                 *self._challenge_cards().values()),
                                pending_handles=pending_handles)

    def _record_card_forecasts(self, pending, baseline) -> None:
        """Resolved samples retain distinct forecaster and judged-return identities."""
        from factorylab.charter.measurement import record_card_forecasts

        record_card_forecasts(self, pending, baseline)
