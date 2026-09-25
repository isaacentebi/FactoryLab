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
from factorylab.charter.market import MOTION_FORECAST_DEFINITION, brier
from factorylab.charter.measurement import measure_card, preflight_measurement
from factorylab.cortex.assembly import AssemblySpec
from factorylab.cortex.registration import (
    BUILTIN_RETURNS,
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
from factorylab.runtime.immune import configuration_changed
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
        # Charter motions decided so far by branch: the factory's realized enactment
        # rate, which weighs a seat's pair of conditional forecasts (charter.market).
        self.motion_tally: dict[str, int] = {"passed": 0, "failed": 0}
        self.predicate_runner = JournalProxy(PredicateRunner(), self.ledger, "predicate")
        # The norm house's files beside the ledger, read only at a governance
        # boundary and through the journal, so a resumed world reads what the
        # original read (charter audit M4).
        from factorylab.charter.norm_edition import NormInbox

        self.norm_inbox = JournalProxy(NormInbox(getattr(self.ledger, "path", None)),
                                       self.ledger, "norms")
        self.observer.predicates = self.predicates
        self.PROPOSAL_SHAPES = deepcopy(self.PROPOSAL_SHAPES)
        for kind in ("connector", "retire", "challenge"):
            self.PROPOSAL_SHAPES[kind]["predicted_effect"] = {
                "card_id": "a current card id", "direction": "decrease", "window": 1}
        # Charter audit P2: a card's region is typed data; the sentence is derived.
        # Charter audit M3: a holdout motion appends one registered predicate to a card.
        shape = self.PROPOSAL_SHAPES["amendment"]
        for card in shape.get("add", ()):
            card.pop("acceptable_region", None)
            card["region"] = {"rule": "at most | at least | above | below | between | "
                              "below the median of the previous window",
                              "lo": "number, for at least, above and between",
                              "hi": "number, for at most, below and between"}
        shape["holdout"] = {"card_id": "a current card id",
                            "predicate": "a registered predicate id",
                            "evidence": "why the card needs it, at most 4000 chars",
                            "trial_windows": 3}

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
        if kinds & {"Verdict", "MetaVerdict", "CounterVerdict"} or shapes & {"conformity",
                                                                             "counter"}:
            return COMMISSIONED_JUDGE_REFUSAL
        return None

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
                elif isinstance(item, dict) and item.get("kind") == "challenge":
                    # Charter audit P2: a challenge declares its own promise; the
                    # runtime never infers it from the replacement's wording.
                    prediction = PredictedEffect.parse(item.get("predicted_effect"))
                if isinstance(item, dict) and item.get("kind") == "amendment":
                    self._propose_amendment(handle, item)
                else:
                    mid = item.get("openrouter_id") if isinstance(item, dict) else None
                    namespaced = (isinstance(mid, str) and item.get("kind") == "model"
                                  and mid.startswith(("x402:", "venice:")))
                    adapted = {**item, "openrouter_id": "namespace/model"} if namespaced else item
                    if item.get("kind") in ("connector", "challenge"):
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
        from factorylab.runtime.polymarket import vocabulary
        from factorylab.settlement.vocabulary import PredicateBook

        return PredicateBook(self.registered_predicates,
                             run=lambda code, facts: self.predicate_runner.run(code, facts),
                             world=vocabulary(self.m))

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

    def _register_challenge(self, handle: str, prop: ChallengeProposal, *,
                            predicted_effect: PredictedEffect | None = None) -> None:
        """Admit a metric challenge: one novelty trial buys a frozen side-by-side trial.

        The replacement keeps the challenged card's id and norm, so adopting it
        is the ordinary replace amendment. Both cards and the observation
        definitions behind them are frozen here; the incumbent keeps pricing
        the live charter throughout, so commitments incurred under it settle
        under it. The challenge is ledgered before it exists in state.

        Charter audit P2: the replacement's region is built as typed data from
        the challenge's rule and value, and the promise its ballot is graded on
        is the one the challenger declared, which names the challenged card.
        """
        from factorylab.charter.amendment import proposed_answers_for
        from factorylab.charter.book import validate_observation_bindings
        from factorylab.runtime.cards import parses

        incumbent = next((c for c in self.charter.cards if c.id == prop.card_id), None)
        if incumbent is None:
            raise ValueError("challenge card_id must name a current card")
        if any(ch["card_id"] == prop.card_id for ch in self._live_challenges().values()):
            raise ValueError("card is already under challenge")
        if predicted_effect is None:
            raise ValueError("a challenge declares its predicted_effect")
        if predicted_effect.card_id != incumbent.id:
            raise ValueError("a challenge's predicted_effect names the challenged card")
        spec = prop.replacement
        answers_for = spec.get("answers_for", incumbent.answers_for)
        if answers_for not in self._kind_rewards():
            answers_for = proposed_answers_for(answers_for, incumbent.id)
        rule = str(spec["rule"])
        bound = "lo" if rule in ("at least", "above") else "hi"
        replacement = MetricCard(
            incumbent.id, incumbent.norm,
            str(spec.get("description", incumbent.description)),
            str(spec.get("units", incumbent.units)),
            spec["window"], {"rule": rule, bound: spec["value"]}, str(spec["observation"]),
            answers_for, holdout=incumbent.holdout,
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
            "amendment_id": None, "predicted_effect": predicted_effect,
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
            if challenge.get("holdout") and self.card_samples.windows:
                # A holdout trial also resolves the appended predicate on the window.
                row["holdout"] = {"predicate": challenge["holdout"],
                                  "held": self._resolve_holdout(
                                      challenge["holdout"],
                                      window_facts(self.card_samples.windows[-1]))}
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
        """A completed trial goes on the agenda of the next standing committee.

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
            effect = challenge.get("predicted_effect")
            if effect is None:
                # A challenge admitted before challenges declared their own promise
                # (charter audit P2) keeps the one it was always going to be balloted on.
                effect = PredictedEffect(replacement.id, "increase" if replacement.rule
                                         is not None and replacement.rule.kind == "min"
                                         else "decrease", 1)
            holdout = challenge.get("holdout")
            try:
                # A holdout motion appends to the card as it stands when it activates
                # (charter audit M3): a replace frozen at admission would revert any
                # cards motion that activated during the trial.
                am = Amendment(
                    id=challenge["id"], proposer_handle=challenge["handle"],
                    edition_base=self.charter.edition, add=(),
                    replace=() if holdout else (replacement,), remove=(),
                    predicted_effect=effect,
                    holdout=(challenge["card_id"], holdout) if holdout else None,
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
            **({"holdout": challenge["holdout"]} if challenge.get("holdout") else {}),
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
                        "replacement": side(row, "replacement"),
                        **({"holdout": row["holdout"]} if "holdout" in row else {})}
                       for row in rows],
        }

    def _register(self, handle: str, prop: Any, *,
                  predicted_effect: PredictedEffect | None = None) -> None:
        amount = self.ev.trial_amount_micro
        if isinstance(prop, ChallengeProposal):
            self._register_challenge(handle, prop, predicted_effect=predicted_effect)
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
                output_schema=_to_plain(prop.returns_schema or {"type": "object"}),
                # A tool runs in the world's own jail and pays no one (the wallet
                # moves only when money moves), so its call is free.
                price=PriceSpec({"call": 0}),
                permissions=frozenset({"sandbox.run"}),
                resource_bounds=ResourceBounds(max_duration_ns=prop.timeout_s * 1_000_000_000),
            )
            self._require_trial(handle, amount)
            self._register_with_trial(contract, handle, amount)
            self._move_trial(handle, amount, to=None, reason="trial:tool")
            tool = PopulationTool(
                prop.id, prop.description, prop.args_schema, prop.code, prop.timeout_s, handle,
                prop.returns_schema,
            )
            self.population_tools[prop.id] = tool
            owner = self.handle_to_assembly.get(handle)
            if owner is not None:
                self.tool_owner[prop.id] = owner
            self.tool_specs[prop.id] = as_spec(tool)
            self.stats.population_tools_registered += 1
            self._emit(EventKind.REGISTERED, {
                "kind": "tool", "id": prop.id,
                **({"returns_schema": _to_plain(prop.returns_schema)}
                   if prop.returns_schema is not None else {})})
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
            custom = any(k not in BUILTIN_RETURNS for k in declared_emits)
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
                emits=emits, schemas=prop.schemas, description=prop.description, **extra)
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
            # Ownership is by lineage key, never by id string (the owner is the seat
            # whose current key registered the id's current version, or the id
            # itself). A retired id takes its next version only from its owner, so no
            # other lineage ever holds an id whose records (head, inbox, archived
            # rationales, artifacts) are another's private state (essay II.I.b). Ids
            # are public, so the refusal discloses nothing.
            proposer = self._trial_proposer(handle)
            proposer_key = self.lineage_keys.get(proposer) if proposer is not None else None
            owner = proposer is not None and (
                proposer == prop.id
                or (proposer_key is not None
                    and proposer_key == self.registrants.get(prop.id)))
            # A live id is never re-versioned and an invocation runs to its end within
            # one event, so no round in flight ever straddles two versions of an id.
            refuse = ("id already registered: a live assembly is retired by vote before "
                      "its id takes a next version") if live else ""
            if not refuse and prop.id in self.assemblies and not owner:
                refuse = "a retired id takes its next version only from its owner"
            self._register_with_trial(contract, handle, self.ev.trial_amount_micro,
                                      refuse=refuse)
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
            if prop.id in self.retirement_order:
                self.retirement_order.remove(prop.id)
            # Only the owner re-versions an id (above), so the id keeps its head, its
            # inbox and its key. A program's next version is new code, which cannot be
            # assumed to read the old code's state, so it never inherits that: it is
            # superseded, released through the journaled release (ledger before
            # index), and neither lingers unreachable nor holds capacity.
            for sha, kind in self.artifacts.private_holdings(prop.id):
                if kind == "program.state":
                    self.artifacts.release(sha, owner=prop.id, kind=kind,
                                           cause="superseded")
            if prop.id not in self.lineage_keys:
                self.registration_serial += 1
                self.lineage_keys[prop.id] = self.registration_serial
            self.registrants[prop.id] = proposer_key
            # Seats already waiting are served first, in order; a new registration
            # joins the back of the queue while anyone waits.
            self._assign_waiting_readers()
            if self.slot_waiting or not self._assign_reader_slot(prop.id):
                # No venue read slot is free: the seat is admitted all the same,
                # without the venue reads, gets the next slot to come free (ledgered
                # then), and its proposer's receipt says so.
                if prop.id not in self.slot_waiting:
                    self.slot_waiting.append(prop.id)
                self.ledger.append({"kind": "venue.reader_slot", "assembly_id": prop.id,
                                    "slot": False, "ts": self.clock.now_ns})
                self._note_to_owner(handle, "registration_admitted", id=prop.id,
                                    venue_reads="no venue read slot is free: this seat "
                                    "holds no venue read tools until one is")
            # Time audit T14: a contract version replaces the seat's configuration; its
            # decisions are corrected on the consequence loop.
            configuration_changed(self, f"seat:{prop.id}", self._consequence_period())
            self._watch_evaluator_majority(f"register:{prop.id}")
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
                    **({"description": spec.description} if spec.description else {}),
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

        predicted_effect = self._policy_prediction(predicted_effect)
        if prop.pay == "x402" and prop.max_call_micro > self.m.treasury.max_request_micro:
            raise ValueError("connector cap exceeds treasury.max_request_micro")
        owner = self.handle_to_assembly.get(handle)
        if owner is None:
            try:
                owner = self.queue.get(handle).propensity.chosen
            except KeyError:
                raise ValueError("connector proposal needs a caller decision") from None
        eligible = self._committee_eligible()
        eligible.pop(owner, None)
        if len(eligible) < self.m.committee.quorum:
            raise ValueError(f"{len(eligible)} eligible seats are fewer than "
                             f"committee.quorum {self.m.committee.quorum}")
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
        vote_id = f"connector:{prop.id}:v{version}:{handle}"
        committee, strata = self._seat_internal(vote_id, eligible)
        self.ledger.append({"kind": "connector.seated", "id": prop.id,
                            "vote_id": vote_id, "handle": handle,
                            "seats": [seat._asdict() for seat in committee.seats],
                            "coverage": strata})
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
            # A fetch pays no one; a paid source's price is its seller's own.
            price=PriceSpec({"call": 0}),
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

    def _refuse_amendment(self, item: dict[str, Any], reason: str) -> None:
        """Ledger a refused motion and publish its reason, then raise it."""
        feedback = {"id": str(item.get("id", "")), "reason": reason}
        self.ledger.append({"kind": "amendment.rejected", **feedback})
        self.amendment_feedback = feedback
        raise ValueError(reason)

    def _burn_observations(self) -> frozenset[str]:
        """The observations a clock motion may predict on: the seed burn and any registered.

        Essay II.IV: speed "is categorically indistinguishable from a specific
        approach to cash burn". The seed is ``burn_per_window``; the population
        may register its own measure of burn and name that instead.
        """
        from factorylab.charter.amendment import BURN_OBSERVATION

        return frozenset({BURN_OBSERVATION, *(o.id for o in self.observations.all()
                                              if o.registered)})

    def _propose_amendment(self, handle: str, item: dict[str, Any]) -> None:
        """Admit one motion to the agenda; the next standing committee votes on it.

        A motion carries one change class (charter audit P3): cards, lambda
        (``{"lambda": {card_id: value}}`` over current cards) or clock
        (``tick_interval``), each with its own prediction; a clock motion's names
        a burn observation (M6). Nothing is seated here: a motion arriving between
        governance boundaries waits for the next committee (C1).
        """
        from factorylab.charter.amendment import (
            CHANGE_CLASSES,
            proposed_answers_for,
            proposed_price,
            proposed_tick_interval,
        )
        from factorylab.charter.charter import MetricCard, stated_region

        if "holdout" in item:
            self._propose_holdout(handle, item)
            return
        present = {"cards": any(item.get(key) for key in ("add", "replace", "remove")),
                   "lambda": "lambda" in item, "clock": "tick_interval" in item}
        classes = [name for name in CHANGE_CLASSES if present[name]]
        if len(classes) > 1:
            self._refuse_amendment(
                item, "a motion carries one change class (cards, lambda or clock); "
                f"this one carries {' and '.join(classes)}")
        tick_interval = None
        if "tick_interval" in item:
            try:
                proposed_tick_interval(
                    item["tick_interval"], self.m.clock.min_tick_ns, self.m.max_tick_ns
                )
            except ValueError as exc:
                self._refuse_amendment(item, str(exc))
            tick_interval = item["tick_interval"]
            effect = item.get("predicted_effect")
            observation = effect.get("observation") if isinstance(effect, dict) else None
            if observation not in self._burn_observations():
                self._refuse_amendment(
                    item, "a clock motion's predicted_effect names a burn observation: "
                    f"{', '.join(sorted(self._burn_observations()))}")
        prices = []
        if "lambda" in item:
            raw = item["lambda"]
            if not isinstance(raw, dict) or not raw:
                self._refuse_amendment(item, "lambda must map current card ids to prices")
            for card_id, value in raw.items():
                if value == "posted":
                    # Charter audit M1: the committee adopts the factory's posted price,
                    # read and frozen now, so the motion states the number it sets.
                    posted = self._posted_lambda(str(card_id))
                    if posted is None:
                        self._refuse_amendment(item, f"no posted lambda for card {card_id}")
                    self.ledger.append({"kind": "lambda_post.adopted", "motion": item.get("id"),
                                        "card_id": str(card_id), **posted,
                                        "ts": self.clock.now_ns})
                    value = posted["lambda"]
                try:
                    prices.append((str(card_id), proposed_price(value)))
                except ValueError as exc:
                    self._refuse_amendment(item, str(exc))

        def cards(key: str) -> tuple[MetricCard, ...]:
            raw = item.get(key) or []
            if not isinstance(raw, list):
                raise ValueError(f"{key} must be a list")
            out = []
            for c in raw:
                if not isinstance(c, dict):
                    raise ValueError(f"{key} entries must be objects")
                if "lambda" in c:
                    self._refuse_amendment(
                        item, "a card carries no lambda: a motion carries one change class "
                        "(cards, lambda or clock)")
                holdout = tuple(c.get("holdout") or ())
                incumbent = next((x for x in self.charter.cards if x.id == c.get("id")), None)
                kept = incumbent.holdout if incumbent is not None and key == "replace" else ()
                if not set(holdout) <= set(kept):
                    # Charter audit M3: a holdout is appended by its own motion, with a
                    # trial; a cards motion may keep or drop a card's holdouts only.
                    self._refuse_amendment(
                        item, "a cards motion keeps or drops a card's holdouts; a holdout "
                        "is appended by a holdout motion")
                out.append(
                    MetricCard(
                        str(c.get("id", "")),
                        str(c.get("norm", "")),
                        str(c.get("description", "")),
                        str(c.get("units", "")),
                        c.get("window"),
                        stated_region(c),
                        str(c.get("observation", "")),
                        (c.get("answers_for") if isinstance(c.get("answers_for"), str)
                         and c.get("answers_for") in self._kind_rewards()
                         else proposed_answers_for(c.get("answers_for"), str(c.get("id", "")))),
                        holdout=holdout,
                    )
                )
            from factorylab.runtime.cards import parses

            for card in out:
                card.validate_answers_for(frozenset(self._kind_rewards()))
                if not parses(card):
                    raise ValueError("card region has no finite usable bounds")
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

    def _propose_holdout(self, handle: str, item: dict[str, Any]) -> None:
        """Admit a holdout motion to a trial; its ballot waits for the trial to end.

        Essay II.IV.a: the factory's evaluatory layer may "continuously add
        holdout test criteria to a given charter (based on adversarially induced
        conditions or just real production traffic)". A holdout motion is the
        ordinary amendment path's replace of one card with one registered
        predicate, frozen at its current version, appended to the card's
        holdouts. Only an evaluator or antagonist seat proposes one (charter audit
        M3). Like a metric challenge it buys a side-by-side trial with one novelty
        trial: for ``trial_windows`` closed windows the card is measured and the
        predicate resolved on each, and the motion then joins the next
        committee's agenda under its own id and its own predicted effect.
        """
        from factorylab.charter.charter import HOLDOUT_RE
        from factorylab.charter.holdout import behavioural_reads
        from factorylab.cortex.registration import (
            MAX_CHALLENGE_TRIAL_WINDOWS,
            MAX_EVIDENCE_CHARS,
            measured_role,
        )

        motion_id = str(item.get("id", ""))
        spec = item.get("holdout")
        others = sorted({"add", "replace", "remove", "lambda", "tick_interval"} & set(item))
        if others:
            self._refuse_amendment(item, "a holdout motion carries its holdout alone; this "
                                   f"one also carries {', '.join(others)}")
        if not isinstance(spec, dict) or set(spec) != {"card_id", "predicate", "evidence",
                                                       "trial_windows"}:
            self._refuse_amendment(item, "holdout needs exactly card_id, predicate, evidence "
                                   "and trial_windows")
        proposer = self._proposer_assembly(handle)
        emits = self.assemblies[proposer].spec.emits if proposer in self.assemblies else ()
        if measured_role(emits) not in ("evaluator", "antagonist"):
            self._refuse_amendment(item, "a holdout motion is proposed by an evaluator or "
                                   "antagonist seat")
        card = next((c for c in self.charter.cards if c.id == spec["card_id"]), None)
        if card is None:
            self._refuse_amendment(item, "holdout.card_id must name a current card")
        predicate = (self.predicates.get(spec["predicate"])
                     if isinstance(spec["predicate"], str) and spec["predicate"] else None)
        if predicate is None or predicate.code is None:
            self._refuse_amendment(item, "holdout.predicate must name a registered predicate; "
                                   "a seed predicate resolves over a forecast's horizon, not "
                                   "a closed window")
        try:
            # A holdout tests behaviour: a predicate on the window's index, the clock,
            # balances or market series would fail forever whatever the factory did.
            behavioural_reads(predicate.code)
        except ValueError as exc:
            self._refuse_amendment(item, str(exc))
        entry = f"{predicate.id}@{predicate.version}"
        if HOLDOUT_RE.fullmatch(entry) is None:
            self._refuse_amendment(item, "holdout.predicate is not a predicate id")
        if predicate.id in {h.split("@")[0] for h in card.holdout}:
            self._refuse_amendment(item, "the card already holds this predicate")
        windows = spec["trial_windows"]
        if type(windows) is not int or not 1 <= windows <= MAX_CHALLENGE_TRIAL_WINDOWS:
            self._refuse_amendment(item, "holdout.trial_windows must be an integer in "
                                   f"[1, {MAX_CHALLENGE_TRIAL_WINDOWS}]")
        evidence = spec["evidence"]
        if not isinstance(evidence, str) or not evidence.strip() or (
                len(evidence) > MAX_EVIDENCE_CHARS):
            self._refuse_amendment(item, "holdout.evidence must be a nonempty string of at "
                                   f"most {MAX_EVIDENCE_CHARS} chars")
        try:
            effect = PredictedEffect.parse(item.get("predicted_effect"))
        except ValueError as exc:
            self._refuse_amendment(item, str(exc))
        if effect.card_id != card.id:
            self._refuse_amendment(item, "a holdout motion's predicted_effect names its card")
        if any(ch["card_id"] == card.id for ch in self._live_challenges().values()):
            self._refuse_amendment(item, "card is already under challenge or holdout trial")
        closed = self.card_samples.windows[-1] if self.card_samples.windows else None
        if closed is None or self._resolve_holdout(entry, window_facts(closed)) is None:
            self._refuse_amendment(item, "holdout preflight: the predicate does not resolve on "
                                   "the last closed window")
        # The motion's id is checked like any amendment's before the trial is paid.
        Amendment(id=motion_id, proposer_handle=handle, edition_base=self.charter.edition,
                  add=(), replace=(card,), remove=(), predicted_effect=effect)
        if motion_id in self.challenges or any(
                am.id == motion_id for am in self.charter_book.pending()):
            self._refuse_amendment(item, "amendment id already proposed")
        replacement = MetricCard(card.id, card.norm, card.description, card.units, card.window,
                                 card.region, card.observation, card.answers_for,
                                 holdout=(*card.holdout, entry))
        contract = Contract(
            id=f"holdout:{motion_id}", version=1, kind="tool", description="holdout motion",
            input_schema={"type": "object"}, output_schema={"type": "object"},
            price=PriceSpec({}), permissions=frozenset(), resource_bounds=ResourceBounds())
        self._register_proposal(contract, handle, reason="trial:holdout")
        observation = self.observations.get(card.observation)
        definitions = ({observation.id: deepcopy(self.registered_observations[observation.id])}
                       if observation is not None and observation.registered else {})
        record = {
            "id": motion_id, "handle": handle, "card_id": card.id,
            "evidence": evidence, "incumbent": card, "replacement": replacement,
            "holdout": entry, "trial_windows": windows, "start_window": self.window.index,
            "observations": definitions, "series": [], "status": "trial",
            "amendment_id": None, "predicted_effect": effect,
        }
        self.ledger.append({"kind": "holdout.proposed",
                            **{k: v for k, v in record.items() if k != "series"},
                            "edition": self.charter.edition, "ts": self.clock.now_ns})
        self.challenges[motion_id] = record

    def _proposer_assembly(self, handle: str) -> str | None:
        """The assembly a proposing decision belongs to, when the queue knows it."""
        proposer = self.handle_to_assembly.get(handle)
        if proposer is None:
            try:
                proposer = self.queue.get(handle).propensity.chosen
            except KeyError:
                pass
        return proposer

    def _seat_learners(self, eligible: dict[str, str]) -> dict[str, frozenset[str]]:
        """Each eligible assembly's learner types: the strata sortition covers besides roles.

        Essay II.IV.a seats "no-regret learners, no-swap-regret learners". An
        assembly with its own registered learner is that learner's type; any other
        is the type of the routers that sample it (the routers of the kinds it
        accepts), both types when both kinds of router do.
        """
        from factorylab.learners.blum_mansour import BlumMansour

        def kind(learner) -> str:
            seen = set()
            while learner is not None and id(learner) not in seen:
                if isinstance(learner, BlumMansour):
                    return "blum_mansour"
                seen.add(id(learner))
                learner = getattr(learner, "inner", None)
            return "exp3"

        out = {}
        for assembly_id in eligible:
            own = self.assembly_learners.get(assembly_id)
            if own is not None:
                out[assembly_id] = frozenset({kind(own)})
                continue
            accepts = self.assemblies[assembly_id].spec.accepts
            out[assembly_id] = frozenset(
                kind(state.learner) for event_kind in sorted(accepts)
                for state in self.routers.get(event_kind, ())
                if assembly_id in state.universe) or frozenset({"exp3"})
        return out

    def _seat_internal(self, motion_id: str, eligible: dict[str, str], round: int = 1):
        """A per-motion committee for internal self-organization, never a rump.

        Retirements and connectors are the factory's own organization, not
        charter governance (charter audit P4): they keep sortition, stratified
        like the standing committee, and a population with fewer eligible seats
        than ``committee.quorum`` is refused rather than seated.
        """
        from factorylab.charter.committee import Committee, coverage, draw

        quorum = self.m.committee.quorum
        if len(eligible) < quorum:
            raise ValueError(f"{len(eligible)} eligible seats are fewer than "
                             f"committee.quorum {quorum}")
        learners = self._seat_learners(eligible)
        seats = draw(eligible, self.rng, self.m.committee.seats, learners=learners)
        return Committee(motion_id, round, seats), coverage(eligible, seats, learners)

    def _committee_eligible(self) -> dict[str, str]:
        """Distinct independently requested decisions need observed consequences to qualify.

        An outcome the world never let anyone observe (a censored payoff) is not a
        settled decision: it qualifies nobody, however many of them a seat has.
        Guarantees the answer ``_committee_eligible_scan`` gives over every decision
        the world ever opened, read from the running tally kept at settlement
        (``SettledMixin._tally_evidence``, wave 17b): a released decision was fully
        settled, so it can bring no new evidence, and its count stays in the tally.
        """
        from factorylab.charter.committee import experienced

        self._drain_finalized()
        return experienced({a.spec.id: a.spec.role for a in self.assemblies.values()
                            if a.spec.id not in self.retired_assemblies},
                           self.eligibility_tally, self.m.committee.min_settled)

    def _eligibility_scan_pairs(self) -> set[tuple[str, str | None]]:
        """The evidence pairs the eligibility scan reads, over every retained decision.

        Kept for the tally's own proof (``tests/runtime/test_settled_release.py``)
        and for a checkpoint older than the tally, which released nothing.
        """
        from factorylab.runtime.shared import DEF_EVALUATION

        evidence = {(r.handle, self.handle_to_assembly.get(r.handle))
                    for r in self.consequences.table.returns
                    if r.payoff is not None and r.payoff.censored is None
                    and self.queue.get(r.handle).channel in ("verdict", "exposure")}
        for decision in self.queue.state()["decisions"].values():
            if any(r.status is SettleStatus.SETTLED and r.definition_version == DEF_EVALUATION
                   for r in self.queue.history(decision.handle)):
                evidence.add((decision.handle, self.handle_to_assembly.get(decision.handle)))
            if decision.channel == "consequence" and decision.status is SettleStatus.SETTLED:
                if decision.parent_handle:
                    evidence.add((decision.parent_handle, self.handle_to_assembly.get(
                        decision.parent_handle, decision.actor)))
        return evidence

    def _committee_eligible_scan(self) -> dict[str, str]:
        """The eligibility the scan over every retained decision and account gives."""
        from collections import Counter

        from factorylab.charter.committee import experienced

        settled = Counter(assembly for handle, assembly in self._eligibility_scan_pairs()
                          if assembly is not None and self._independent_decision(handle, assembly))
        return experienced({a.spec.id: a.spec.role for a in self.assemblies.values()
                            if a.spec.id not in self.retired_assemblies},
                           settled, self.m.committee.min_settled)

    def _propose_retirement(self, handle: str, proposal: RetireProposal, *,
                            predicted_effect: PredictedEffect | None = None) -> None:
        """Any assembly may request a seed or population retirement through an internal draw.

        A retirement is the factory's internal self-organization, not charter
        governance (charter audit P4): it keeps its own sortition and its own
        queue, and never waits on or stalls a charter activation.
        """
        predicted_effect = self._policy_prediction(predicted_effect)
        target = proposal.assembly_id
        if target not in self.assemblies or target in self.retired_assemblies:
            raise ValueError("assembly is unavailable or already retired")
        version = self.assemblies[target].spec.version
        if any(row["proposal"].assembly_id == target and row["proposal"].version == version
               and row["status"] in ("voting", "passed")
               for row in self.retirement_proposals.values()):
            raise ValueError("retirement is already pending for this assembly version")
        eligible = self._committee_eligible()
        proposer = self.handle_to_assembly.get(handle, self.queue.get(handle).propensity.chosen)
        eligible.pop(proposer, None)
        eligible.pop(target, None)
        if len(eligible) < self.m.committee.quorum:
            raise ValueError(f"{len(eligible)} eligible seats are fewer than "
                             f"committee.quorum {self.m.committee.quorum}")
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
        committee, strata = self._seat_internal(motion.id, eligible,
                                                len(self.retirement_proposals) + 1)
        self.ledger.append({"kind": "retirement.proposed", **asdict(motion),
                            "committee": asdict(committee), "coverage": strata})
        self.retirement_proposals[motion.id] = {
            "proposal": motion, "committee": committee, "ballots": {}, "status": "voting"}
        self._hold_vote(motion, committee)

    def _prune_cadence_waiting(self) -> list[str]:
        """Only charter motions wait on the cadence; spent or stale heads leave it."""
        active = {am.id for am in self.charter_book.pending()}
        waiting = self.cadence.world_block(self.tick_clock)["waiting"]
        for proposal_id in waiting:
            if proposal_id not in active:
                self.cadence.refused(proposal_id, "proposal is no longer pending")
        return [pid for pid in waiting if pid in active]

    def _activate_retirements_if_due(self) -> None:
        """Retire every approved version at the first window boundary after its vote.

        Charter audit P4: retirements are the factory's internal self-organization.
        They have their own queue, in approval order, and never consult or advance
        the governance cadence, so a passed retirement cannot stall a charter
        activation and a charter motion cannot delay a retirement.
        """
        for row in self.retirement_proposals.values():
            if row["status"] != "passed":
                continue
            motion = row["proposal"]
            if (motion.assembly_id in self.retired_assemblies
                    or self.assemblies[motion.assembly_id].spec.version != motion.version):
                self.ledger.append({"kind": "retirement.stale", "proposal_id": motion.id})
                row["status"] = "stale"
                self._censor_ballots(motion.id)
                continue
            self._retire_assembly(motion.assembly_id, motion.id)
            row["status"] = "activated"
            self._activate_policy_ballots(motion.id)
            self.card_samples.revised(motion.proposer_handle)
            self.window.revision_returns += 1

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
        """Amendments and connectors share seats, metered ballot requests and majority counting.

        On a standing committee only the motion's voters (every seat but its
        proposer's) are asked, and each ballot also carries the committee's agenda.
        """
        from factorylab.charter.committee import StandingCommittee

        if am.id in self.voted_amendments:
            return
        retiring = isinstance(am, Retirement)
        prices = {} if retiring or connector is not None else dict(am.proposed_prices)
        standing = isinstance(committee, StandingCommittee)
        voters = set(self.charter_book.voters(committee, am.id)) if standing else None
        # A challenge-originated amendment shows its voters the trial (C7); an
        # ordinary amendment's ballot is unchanged.
        challenge_inputs = (None if retiring or connector is not None
                            else self._challenge_ballot_inputs(am.id))
        connector_yes = 0
        for seat in committee.seats:
            if self.wallet.dead:
                break
            alias, assembly_id = seat[0], seat[1]
            if voters is not None and alias not in voters:
                continue
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
            handle = self.queue.open(
                actor=lid, event_id=event_id,
                propensity=PropensityRecord((assembly_id,), (1.,), assembly_id, 0, lid,
                                            "direct-committee-seat"),
                channel="policy", horizon_ticks=self._policy_horizon(am.predicted_effect.window),
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
                                        "predicted_effect": am.predicted_effect.as_dict()},
                          # A ballot reads the motion like a machine (C7): its own
                          # operating access, not the whole world block.
                          "actor_context": self._operating_context(assembly_id,
                                                                   self._world_block()),
                          "charter": self._charter_text()}
            else:
                inputs = {
                    ("retirement" if retiring else "amendment"): ({
                        "assembly_id": am.assembly_id, "version": am.version,
                        "predicted_effect": am.predicted_effect.as_dict(),
                    } if retiring else {
                        "id": am.id,
                        **({"change": list(am.change_classes())}
                           if hasattr(am, "change_classes") else {}),
                        "add": [asdict(c) for c in am.add],
                        "replace": [asdict(c) for c in am.replace],
                        "remove": list(am.remove),
                        **({"holdout": {"card_id": am.holdout[0], "predicate": am.holdout[1]}}
                           if getattr(am, "holdout", None) else {}),
                        **({"lambda": prices} if prices else {}),
                        "predicted_effect": am.predicted_effect.as_dict(),
                        **({"tick_interval": am.tick_interval}
                           if am.tick_interval is not None else {}),
                    }),
                    **({"challenge": challenge_inputs} if challenge_inputs else {}),
                    **({"agenda": self._agenda_block(committee)} if standing else {}),
                    "charter": self._charter_text(),
                    "actor_context": self._operating_context(assembly_id,
                                                             self._world_block()),
                }
            # The seat's policy returns delivered since its last ballot: each is shown
            # once, and then released (wave 17b; essay II.IV.c, a verdict "is consumed
            # as a reward signal ... and then discarded"; II.I.b, the reward line is thin).
            fresh, delivered = self.queue.returns_since(lid, self.policy_seen.get(lid, 0))
            inputs["your_policy_returns"] = [asdict(lr) for lr in fresh
                                             if lr.channel == "policy"]
            self.policy_seen[lid] = delivered
            schema = {
                "type": "object",
                "properties": {"vote": {"type": "boolean"}, "reason": {"type": "string"}},
                "required": ["vote", "reason"],
            }
            req = self._request(
                handle,
                ("Vote on a connector registration." if connector is not None else
                 "Vote on retiring the named assembly version." if retiring else
                 "Vote on appending a holdout to a charter card: inputs.challenge carries "
                 "the proposer's evidence, the card's measured series and the holdout's "
                 "result per trial window."
                 if challenge_inputs and challenge_inputs.get("holdout") else
                 "Vote on an amendment to the charter's metric cards proposed by a metric "
                 "challenge: inputs.challenge carries the challenger's evidence and both "
                 "measured series, incumbent and replacement per trial window."
                 if challenge_inputs else
                 self._motion_description(am)),
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
                # A response, but no invocation: nothing was rendered or called, and
                # the window does not count it among its invocations either.
                self.card_samples.returned(handle=handle, assembly=assembly_id, role="other",
                                           window=self.window.index, ret=ret, invoked=False)
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
                    committee, am.id, alias, vote, str(ret.outputs.get("reason", ""))[:1000]
                )
                self.stats.votes_cast += 1
                self._record_policy_ballot(am, handle, assembly_id, vote)
            else:
                self.charter_book.abstain(committee, am.id, alias)
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
            outcome = self.charter_book.tally(committee, am.id)
        if outcome == "passed":
            if not retiring:
                self.cadence.approve(am.id)
                self.stats.amendments_passed += 1
                self.motion_tally["passed"] += 1
        elif outcome == "failed" and retiring:
            self._censor_ballots(am.id)
        elif outcome == "failed":
            # Charter audit P1: the rejected branch is observable. Its ballots and
            # its reject-branch forecasts are graded against the unchanged charter.
            self.motion_tally["failed"] += 1
            self.ledger.append({"kind": "policy.rejected", "amendment_id": am.id,
                                "window": self.window.index})
            self._activate_policy_ballots(am.id, branch="reject")

    @staticmethod
    def _motion_description(am: Any) -> str:
        """What a ballot is about, by the motion's change class; it states the motion only."""
        classes = am.change_classes() if hasattr(am, "change_classes") else ("cards",)
        if "lambda" in classes:
            return "Vote on a motion setting lambda on current charter cards."
        if "clock" in classes:
            return "Vote on a motion changing the world's tick interval."
        return "Vote on an amendment to the charter's metric cards."

    def _card_statistics(self) -> list[dict]:
        """Each priced card's lambda, its bound and saturation, and its violation's
        duration (M7; wave 16, R-E: saturation is published to governance as the card's
        price at its bound).

        Beside the controller's lambda stands the price the factory posted
        (charter audit M1), when any seat posted one, and beside its saturation the
        card's observation and its consecutive unmeasured windows (wave 16, R10-f).
        """
        observations = {card.id: card.observation for card in self.charter.cards}
        return [{"card_id": card_id, "lambda": self.controller.price(card_id),
                 **({"posted": posted} if (posted := self._posted_lambda(card_id)) else {}),
                 **({"observation": observations[card_id]} if card_id in observations
                    else {}),
                 **self.controller.saturation(card_id), **self._card_observed(card_id)}
                for card_id in sorted(self.priced)]

    def _posted_lambda(self, card_id: str) -> dict | None:
        """The factory's posted price for a card; the charter's markets supply it."""
        return None

    def _motion_market(self, motion_id: str) -> dict | None:
        """The conditional forecasts on one motion; the charter's markets supply them."""
        return None

    def _agenda_block(self, committee) -> dict:
        """The seated committee's agenda: its motions, the deferred ones and the card record.

        ``markets`` carries each agenda motion's conditional forecasts on both
        branches (charter audit M2), when there are any.
        """
        markets = {motion: market for motion in committee.agenda
                   if (market := self._motion_market(motion))}
        return {"boundary": committee.boundary, "round": committee.round,
                "motions": list(committee.agenda), "deferred": list(committee.deferred),
                **({"markets": markets} if markets else {}),
                "penalty_cap": self.m.prices.penalty_cap, "cards": self._card_statistics()}

    def _activate_passed(self) -> None:
        """Activate every passed motion at this boundary, each as its own edition, in order."""
        new = self.charter_book.activate_due(self.clock.now_ns)
        while new is not None:
            if isinstance(new, Refusal):
                self._close_refused_ballots(new)
            else:
                am = self.charter_book.activated_amendment(new.edition)
                self._activate_policy_ballots(am.id)
                self.cadence.activated(am.id, self.clock.now_ns, self.tick_clock)
                self._apply_edition(new, am)
            new = self.charter_book.activate_due(self.clock.now_ns)

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

    def _apply_edition(self, new: Charter, am: Amendment) -> None:
        """Put an activated motion's edition in force: cards, prices and clock."""
        self.charter = new
        self._drop_cards({c.id for c in new.cards}, am.id)
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

    def _drop_cards(self, kept: set[str], reason_id: str) -> None:
        """Unprice every priced card the edition in force no longer carries."""
        for card_id in sorted(self.priced - kept):
            self.controller.remove(card_id, amendment_id=reason_id)
            self.priced.remove(card_id)
            self.regions.pop(card_id, None)

    def _activate_charter_if_due(self) -> None:
        """Every window boundary: internal motions; a governance boundary: the committee.

        Challenges that finished their trial join the agenda, and passed
        retirements take effect on their own queue (charter audit P4). When the
        measured governance cadence opens a boundary (essay II.IV.c: the charter
        revises no faster than ``min_ratio`` times its slowest loop), a standing
        committee is seated and the boundary is held.
        """
        self._ballot_due_challenges()
        self._activate_retirements_if_due()
        if getattr(self, "dormancy", None) is not None:
            # Dormant (C2): no paid cognition; the boundary waits for the world to wake.
            return
        self._prune_cadence_waiting()
        if self.cadence.ready(now_ns=self.clock.now_ns, tick_interval_ns=self.tick_clock,
                              window=self.stats.reserve_windows):
            self._hold_governance_boundary()

    def _hold_governance_boundary(self) -> None:
        """Seat the boundary's committee, hear it on any norm edition, decide its agenda.

        Essay II.IV.a: "On the cadence of charter revision, a sample of the
        factory's population is seated … and that seat is consistently rotated."
        A new committee is drawn at every boundary, stratified over roles and
        learner types (C2); its agenda is every motion admitted since the last
        (C1). A signed norm edition from the norm house takes effect only here,
        after the committee's ledgered, non-binding testimony (M4). Passed
        motions then take effect, each as its own edition.
        """
        boundary = len(self.charter_book.sittings()) + self.charter_book.deferrals() + 1
        self.cadence.boundary(boundary, window=self.window.index, now_ns=self.clock.now_ns,
                              tick_interval_ns=self.tick_clock)
        agenda = self.charter_book.agenda()
        eligible = self._committee_eligible()
        recusals = {}
        for am in agenda:
            proposer = self._proposer_assembly(am.proposer_handle)
            recusals[am.id] = frozenset({proposer} if proposer is not None else ())
        committee = self.charter_book.seat(
            boundary, eligible, self.rng, size=self.m.committee.seats,
            quorum=self.m.committee.quorum, learners=self._seat_learners(eligible),
            recusals=recusals)
        edition = self._norm_edition_due()
        if edition is not None:
            self._testify(edition, committee)
            self._apply_norm_edition(edition)
        if committee is not None:
            pending = {am.id: am for am in self.charter_book.pending()}
            for motion in committee.agenda:
                if motion in pending and not self.wallet.dead:
                    self._hold_vote(pending[motion], committee)
        self._activate_passed()

    def _norm_edition_due(self) -> dict | None:
        """The next norm edition beside the ledger, verified, or None; a refusal is ledgered.

        The write permission is the manifest's ``[norm_house] signer``, a launch
        cast (essay II.IV.a: "the read/write permissions of the factory's input
        layer are part of the factory's hard kernel"). A file that is unsigned,
        signed by anyone else, for another world or out of sequence is refused
        with its reason and changes nothing.
        """
        from factorylab.charter.norm_edition import verify

        sequence = len(self.charter_book.norm_editions()) + 1
        found = self.norm_inbox.lookup(sequence)
        if found is None:
            return None
        try:
            if "unreadable" in found:
                raise ValueError(f"unreadable: {found['unreadable']}")
            norms, digest = verify(found["body"], signer=self.m.norm_house.signer,
                                   world=self.m.name, manifest_sha256=self.m.manifest_hash(),
                                   sequence=sequence)
        except ValueError as exc:
            self.ledger.append({"kind": "norm_edition.refused", "sequence": sequence,
                                "reason": str(exc)[:300], "ts": self.clock.now_ns})
            return None
        return {"sequence": sequence, "norms": norms, "digest": digest,
                "signer": self.m.norm_house.signer}

    def _testify(self, edition: dict, committee) -> None:
        """Each seat's assessment of a norm edition, ledgered and non-binding.

        Essay II.IV.a: the norm house is "read-only" from the factory's perspective,
        "though the factory is expected to testify within the assembly". Testimony
        is an ordinary metered return on the policy channel; it decides nothing, so
        nothing grades it and its decision closes unscored. With no committee
        seated (below quorum), the absence is ledgered and the edition still applies.
        """
        sequence = edition["sequence"]
        if committee is None:
            self.ledger.append({"kind": "norm_edition.testimony_absent", "sequence": sequence,
                                "reason": "no committee is seated at this boundary",
                                "ts": self.clock.now_ns})
            return
        current = [str(n) for n in self.charter.norms]
        proposed = [n.as_dict() for n in edition["norms"]]
        names = [str(n) for n in edition["norms"]]
        view = {"sequence": sequence, "norms": proposed,
                "removed": [n for n in current if n not in names],
                "added": [n for n in names if n not in current],
                "cards_refused": [c.id for c in self.charter.cards if c.norm not in names]}
        schema = {"type": "object", "properties": {"assessment": {"type": "string"}},
                  "required": ["assessment"]}
        for seat in committee.seats:
            if self.wallet.dead:
                break
            alias, assembly_id = seat.alias, seat.assembly_id
            event_id = f"testimony-{sequence}-{alias}"
            if event_id in self.vote_handles:
                continue
            lid = f"assembly:{assembly_id}"
            handle = self.queue.open(
                actor=lid, event_id=event_id,
                propensity=PropensityRecord((assembly_id,), (1.,), assembly_id, 0, lid,
                                            "direct-committee-seat"),
                channel="policy", horizon_ticks=self.clockwork.period(
                    "price", default=self.m.timing.min_ratio),
                parent_handle=None, cost_ceiling=max(0, self.wallet.available),
            )
            self.ledger.append({"kind": "committee.decision", "event_id": event_id,
                                "handle": handle})
            self.vote_handles[event_id] = handle
            self.handle_to_assembly[handle] = assembly_id
            self._start_return(handle)
            self.stats.decisions += 1
            req = self._request(
                handle,
                "Testify on a norm edition. inputs.norm_edition is the norm house's edition; "
                "it takes effect at this boundary. Your assessment is recorded in the diary "
                "and does not bind.",
                {"norm_edition": view, "charter": self._charter_text(),
                 "agenda": self._agenda_block(committee),
                 "actor_context": self._operating_context(assembly_id, self._world_block())},
                schema, self.clock.now_ns + self.tick_clock.interval_ns * 10, "policy")
            asm = self.assemblies.get(assembly_id)
            ret = (Return(handle, {"reason": "assembly unavailable"}, 0, "failed")
                   if asm is None else self._invoke(assembly_id, req, "voter", child=True))
            self.consequences.finish(handle, ret.cost)
            self._compute_routed = True
            assessment = ret.outputs.get("assessment") if ret.status == "ok" else None
            self.ledger.append({"kind": "norm_edition.testimony", "sequence": sequence,
                                "alias": alias, "handle": handle,
                                "assessment": (str(assessment)[:4000]
                                               if isinstance(assessment, str) else None),
                                "ts": self.clock.now_ns})
            # Non-binding: nothing is predicted, so nothing grades it.
            self.queue.settle(handle, channel="policy", score=0.0,
                              status=SettleStatus.CENSORED,
                              definition_version="norm-testimony-unscored-v1",
                              sampling_ref=None)

    def _apply_norm_edition(self, edition: dict) -> None:
        """Put the norm house's edition in force: new norms, the factory's cards carried over."""
        sequence = edition["sequence"]
        new, refused_cards, refused_motions = self.charter_book.apply_norm_edition(
            edition["norms"], sequence=sequence, digest=edition["digest"],
            signer=edition["signer"], now_ns=self.clock.now_ns)
        self.charter = new
        self._drop_cards({c.id for c in new.cards}, f"norm-edition:{sequence}")
        for motion in refused_motions:
            self._censor_ballots(motion)
            self.cadence.refused(motion, f"a card names a norm removed by norm edition "
                                         f"{sequence}")
        self._derive_regions()

    def _record_policy_ballot(self, proposal, handle: str, assembly: str,
                              vote: bool | None) -> None:
        """Freeze one ballot's card, observation version and acceptable region before activation."""
        if vote is None:
            self._settle_policy(handle, 0.0, SettleStatus.CENSORED)
            return
        ballot = {
            "handle": handle, "assembly": assembly, "amendment_id": proposal.id,
            "vote": vote, **self._promise_frame(proposal),
            "activation_window": None, "baseline": None,
        }
        self.ledger.append({"kind": "policy.promised", **ballot})
        self.pending_votes.append(ballot)

    def _promise_frame(self, proposal) -> dict:
        """The frozen measurement a motion's promise is graded on, shared by every bet on it.

        The card (or, for a clock motion, the burn observation's frame), the
        observation's version and definition, and the region, frozen before the
        branch is decided, so a later registration cannot rewrite what a ballot or
        a conditional forecast was a bet on.
        """
        effect = proposal.predicted_effect
        if effect.observation is not None:
            card = self._observation_card(effect.observation)
        else:
            cards = {c.id: c for c in (*self.charter.cards, *getattr(proposal, "replace", ()),
                                       *getattr(proposal, "add", ()))}
            cards.update(self._challenge_cards())  # a promise may name a challenge under trial
            card = cards[effect.card_id]
        observation = self.observations.get(card.observation)
        definitions = ({observation.id: deepcopy(self.registered_observations[observation.id])}
                       if observation.registered else {})
        return {"prediction": proposal.predicted_effect, "card": card,
                "observation_id": observation.id, "observation_version": observation.version,
                "observations": definitions,
                "region": region_for(card, rolling=self.rolling, observations=self.observations)}

    def _observation_card(self, observation_id: str) -> MetricCard:
        """The measurement a clock motion's promise is graded on: one whole window, unpriced.

        A clock motion predicts its effect on a burn observation, not on a card
        (charter audit M6). Its ballots are graded exactly as a card's are, on this
        frame: the observation over one closed window, its region the
        observation's declared range, so a move counts once it clears
        ``committee.promise_resolution`` of that range. It never enters the charter.
        """
        observation = self.observations.get(observation_id)
        return MetricCard(
            f"observation:{observation.id}", "clock", observation.description,
            observation.units, {"kind": "windows", "n": 1, "per": None},
            f"between {observation.unit_range[0]!r} and {observation.unit_range[1]!r}",
            observation.id, "all")

    def _policy_observations(self, vote: dict) -> ObservationBook:
        """A later observation registration cannot rewrite a frozen ballot's measurement."""
        return ObservationBook(vote["observations"], run=self.observation_runner.run,
                               reject=self._observation_out_of_range)

    def _activate_policy_ballots(self, proposal_id: str, branch: str = "enact") -> None:
        """Every bet on a motion starts its horizon when the motion's branch is decided.

        ``enact``: the change took effect, as it always was. ``reject``: a charter
        motion failed its vote, and the unchanged charter is the realized branch
        (charter audit P1; essay II.IV.a, "bet on beliefs"). Ballots follow the
        branch taken. A conditional forecast on the branch not taken is void: its
        decision closes censored and nothing grades it, as a conditional market
        refunds the branch that did not happen. Either way the baseline is the
        measurement at the decision and the horizon the motion's own window.
        """
        void = [v for v in self.pending_votes if v["amendment_id"] == proposal_id
                and v.get("forecast") and v["branch"] != branch]
        for vote in void:
            self.ledger.append({"kind": "policy.void", "handle": vote["handle"],
                                "amendment_id": proposal_id, "branch": vote["branch"],
                                "decided": branch})
            self._settle_policy(vote["handle"], 0.0, SettleStatus.CENSORED)
        self.pending_votes[:] = [v for v in self.pending_votes if v not in void]
        for vote in self.pending_votes:
            if vote["amendment_id"] != proposal_id:
                continue
            vote["branch"] = branch
            observations = self._policy_observations(vote)
            values = measure_card(vote["card"], self.card_samples, observations)
            activated = {**vote, "baseline": fmean(values.values()) if values else None,
                         "activation_window": self.window.index,
                         "activation_tick": self.ticks_consumed,
                         "region": vote["region"] or region_for(
                             vote["card"], rolling=self.rolling, observations=observations)}
            self.ledger.append({"kind": "policy.activated", **activated})
            vote.update(activated)

    def _settle_policy(self, handle, score, status, *, definition: str | None = None) -> None:
        """Policy feedback reaches the original assembly's durable, private return channel."""
        self.queue.settle(handle, channel="policy", score=score, status=status,
                          definition_version=definition or "policy-promise-brier-v2",
                          sampling_ref=None)

    @staticmethod
    def _branch_probability(vote: dict, branch: str) -> float:
        """The probability a bet gave that the motion's promise holds on the branch taken.

        A conditional forecast states it. A yes vote says the motion makes the
        promised difference: the promise holds if enacted (q = 1) and does not hold
        on the unchanged charter (q = 0); a no vote says the opposite. So a ballot
        is graded on whichever branch the committee took, and a no vote is liable
        exactly as a yes vote is (charter audit P1).
        """
        if vote.get("forecast"):
            return float(vote["q"])
        yes = float(bool(vote["vote"]))
        return yes if branch == "enact" else 1.0 - yes

    def _policy_horizon(self, windows: int) -> int:
        """The ticks a policy decision may wait: the rest of the run, then its grading.

        Covers the cadence it may await, the declared windows after activation at the
        price loop's current period, and the grading floor (``_policy_floor``), so a
        ballot is never cut off before the promise it bet on is graded.
        """
        window = self.clockwork.period("price", default=self.m.timing.min_ratio)
        return (self.events_budget + self.ev.consequence_backstop_ticks
                + windows * window + self._policy_floor())

    def _policy_floor(self) -> int:
        """Ticks after activation before a promise may be graded (time audit T2).

        Policy grading is an outer loop over the consequence loop the activated
        change acts through: grading it sooner than ``min_ratio`` measured
        consequence periods would grade governance on "the unfinished transients of
        the controlled loop" (essay II.IV.c).
        """
        return self.m.timing.min_ratio * self.cadence.consequence_period_events()

    def _close_policy_window(self, index: int) -> None:
        """Each vote is graded once at its declared post-activation boundary, or censored.

        Its boundary is the later of the declared window count and the grading floor
        (``_policy_floor``) in ticks since activation. A vote activated before the
        tick clock is graded by its window count alone.
        """
        self._close_challenge_window(index)  # both series of every trial, ledgered
        remaining = []
        floor = self._policy_floor()
        for vote in self.pending_votes:
            activation = vote["activation_window"]
            effect = vote["prediction"]
            tick = vote.get("activation_tick")
            if (activation is None or index < activation + effect.window - 1
                    or (tick is not None and self.ticks_consumed - tick < floor)):
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
            branch = vote.get("branch", "enact")
            q = self._branch_probability(vote, branch)
            score = brier(q, outcome) if outcome is not None else 0.0
            self.ledger.append({"kind": "policy.outcome", "handle": vote["handle"],
                                "amendment_id": vote["amendment_id"], "branch": branch,
                                "q": q, "forecast": bool(vote.get("forecast")),
                                "baseline": baseline,
                                "direction": effect.direction, "resolution": resolution,
                                "region": asdict(region) if region is not None else None,
                                "observation_id": vote["observation_id"],
                                "observation_version": vote["observation_version"],
                                "value": value, "window": index, "y": outcome,
                                "score": score, "status": str(status)})
            self._settle_policy(vote["handle"], score, status,
                                definition=MOTION_FORECAST_DEFINITION if vote.get("forecast")
                                else None)
        self.pending_votes[:] = remaining
        pending_handles = {self.queue.get(f.handle).parent_handle for f in self.book.pending()}
        self.card_samples.prune((*self.charter.cards, *(v["card"] for v in remaining),
                                 *self._challenge_cards().values()),
                                pending_handles=pending_handles)

    def _record_card_forecasts(self, pending, baseline) -> None:
        """Resolved samples retain distinct forecaster and judged-return identities."""
        from factorylab.charter.measurement import record_card_forecasts

        record_card_forecasts(self, pending, baseline)
