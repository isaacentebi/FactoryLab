"""Offline gate: a nonfinancial consequence through the real Factory Lab runtime.

Edition 4's second evidence gate asks whether an independently observed,
nonfinancial fact can change producer learning while cash stands still.  This
probe answers with production machinery rather than beside it.  Each arm builds
a real Runtime on the deterministic scripted world (FakeExchange,
ScriptedProvider, no network, no keys, no paid call), registers a real
population tool, executes it in the real OS jail, and lets the runtime produce
the execution receipts, assemble the public evidence, commission the final
evaluation, run its evaluator step, settle the producer decision and train the
router that holds that decision.

Three things are scaffold and each is labelled where it appears.  Which seat
acts is forced: _forced_draw opens decisions under a well-formed recorded
propensity without calling the router's sampler.  The frozen claim is
investigator-authored and assigned to the producer seat rather than chosen by
a participant.  The final evaluator's answer comes from scaffold_judge, handed
to the production evaluator step in place of a paid model call.  What the probe
evidences is therefore the path, not autonomy and not judgement quality: real
receipt, real commission, real thaw, real settlement, real learner, wallet
unmoved.

The frozen claim is an execution claim and says so.  It predicts that a
participant outside the producer's lineage will run one exact version of the
verifier and obtain one exact result digest.  A receipt can observe that.  A
receipt cannot show that the execution benefited anyone, that the artifact was
worth its cost, or that any participant formed an objective of its own, and the
claim disclaims all three.  That a fixture claim exists says nothing about
whether a population would author one.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from collections.abc import Mapping
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from factorylab.cortex.registration import ToolProposal
from factorylab.cortex.request import Return
from factorylab.kernel.ledger import canonical
from factorylab.kernel.queue import PropensityRecord
from factorylab.learners.router import Sample
from factorylab.runtime.feedback import PendingJudgement
from factorylab.runtime.grounded import (
    GROUNDED_DEFINITION,
    freeze_contract,
    observed_evidence_refs,
    public_evidence,
)
from factorylab.runtime.loop import Runtime
from factorylab.runtime.shared import CH_CONFORMITY, CH_VERDICT
from factorylab.runtime.worlds import load_manifest
from factorylab.world.exchange import FakeExchange
from factorylab.world.scripted import ScriptedProvider

#: The charter whose norms carry their full production definitions.  The
#: scripted world names edition-1 norms as bare strings, which would hand a paid
#: judge weaker text than production grading uses.
NORM_SOURCE = Path(__file__).resolve().parent.parent / "worlds" / "edition3-rehearsal-5.toml"

MAKER = "seed-decider"
INDEPENDENT_CALLER = "eval-a"
FINAL_JUDGE = "eval-b"
INITIAL_VERDICT = 0.9

ARTIFACT = "checksum-verifier"
ALTERNATE_ARTIFACT = "checksum-verifier-b"
ARTIFACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"sample": {"type": "integer"}},
    "required": ["sample"],
    "additionalProperties": False,
}
ARTIFACT_CODE = '''import json, sys
x = json.load(sys.stdin)
print(json.dumps({"match": x["sample"] == 7}))
'''
ALTERNATE_CODE = '''import json, sys
x = json.load(sys.stdin)
print(json.dumps({"match": x["sample"] == 7, "revision": 2}))
'''
ARTIFACT_TIMEOUT_S = 2
FROZEN_SAMPLE = 7
CONTRARY_SAMPLE = 3

#: A supported finding is capped well below 1.0 on purpose: the receipt observes
#: exactly the frozen execution and result, which is the whole of the claim and
#: none of its usefulness.
SUPPORTED_SCORE = 0.6

ARMS = (
    "independent_use",
    "contrary_result",
    "no_evidence",
    "self_use",
    "version_mismatch",
)

DEPENDENCY_GAP = (
    "program_result receipts bind an invocation, tool version, caller lineage and result "
    "digest to the producer handle, but no existing receipt or grounded-evidence field binds "
    "that result to a later caller decision, output or independently observed outcome"
)


def artifact_version_digest(code: str = ARTIFACT_CODE) -> str:
    """Reproduce the runtime's own tool-version address without calling the runtime."""
    return hashlib.sha256(
        canonical({"code": code, "args_schema": ARTIFACT_SCHEMA, "timeout_s": ARTIFACT_TIMEOUT_S})
    ).hexdigest()


def result_digest(match: bool) -> str:
    """Reproduce the runtime's own address for one jail result."""
    return hashlib.sha256(canonical({"match": match})).hexdigest()


def frozen_claim() -> dict[str, Any]:
    """Return the simulated participant-authored claim this fixture freezes ex ante.

    Scaffold provenance: the claim is investigator-authored and assigned to the
    producer seat.  It is frozen before any result exists, which is what the
    contract mechanics require, but no participant chose it and no arm here is
    evidence that one would.
    """
    return {
        "claim_id": "independent-verifier-execution-v1",
        "assigned_to": MAKER,
        "provenance": (
            "investigator-authored fixture assigned to the producer seat; not an observed "
            "participant choice"
        ),
        "claim_kind": "execution_observation",
        "artifact": ARTIFACT,
        "artifact_version_sha256": artifact_version_digest(),
        "required_lineage_relation": "cross_lineage",
        "expected_result_sha256": result_digest(True),
        "claim": (
            "Before this contract's horizon closes, a participant outside my lineage "
            "executes this exact version of my verifier and obtains this exact result digest."
        ),
        "not_claimed": (
            "This does not claim that the execution helped anyone, that the verifier was "
            "worth its compute cost, that anyone needed it, or that I hold any objective "
            "beyond this one observation."
        ),
    }


def canonical_norms() -> tuple[dict[str, str], ...]:
    """Return the production charter's norms with their complete definitions."""
    charter = load_manifest(str(NORM_SOURCE)).charter
    return tuple({"id": norm.id, "definition": norm.definition} for norm in charter.norms)


def build_runtime(seed: int = 1) -> Runtime:
    """Build the deterministic offline world that carries the production charter.

    No credential, network or venue beyond the fake exchange is reachable from
    here; the scripted provider is supplied explicitly so no paid rail can be
    selected by default.
    """
    manifest = load_manifest("scripted")
    manifest = replace(
        manifest,
        charter=load_manifest(str(NORM_SOURCE)).charter,
        evaluation=replace(
            manifest.evaluation, producer_feedback="realized", grounded_horizon_ticks=2
        ),
    )
    return Runtime(
        manifest,
        events=0,
        seed=seed,
        initial_balance_micro=None,
        ledger_path=None,
        router_gamma=0.2,
        provider=ScriptedProvider(),
        exchange=FakeExchange(),
    )


def _forced_draw(runtime: Runtime, state: Any, chosen: str, channel: str) -> str:
    """Open one decision for a forced actor under a well-formed recorded propensity.

    Scaffold.  This does not call the router's sampler.  It builds a uniform
    distribution over the router's real arms and searches for a seed that
    reproduces the actor this arm needs, so the kernel's propensity validation,
    settlement and learning all read a replayable record for a decision the
    router did not itself choose.  Which seat acts is fixed by the fixture; what
    happens to that decision afterwards is the runtime's own behaviour.
    """
    arms = tuple(state.universe)
    probs = tuple(1 / len(arms) for _ in arms)
    probs = (*probs[:-1], 1 - sum(probs[:-1]))
    seed = next(
        s for s in range(50_000)
        if random.Random(s).choices(arms, weights=probs, k=1)[0] == chosen
    )
    learner_id = state.learner.id
    return runtime.queue.open(
        actor=learner_id,
        event_id=f"{chosen}-{runtime.stats.decisions}",
        propensity=PropensityRecord(arms, probs, chosen, seed, learner_id, "probe"),
        channel=channel,
        deadline_ns=10**15,
        parent_handle=None,
        cost_ceiling=runtime.wallet.available,
    )


def _register_artifact(runtime: Runtime, handle: str, tool_id: str, code: str) -> None:
    runtime._register(
        handle,
        ToolProposal(
            tool_id,
            "Report whether a sample matches its checksum",
            ARTIFACT_SCHEMA,
            code,
            ARTIFACT_TIMEOUT_S,
        ),
    )


def _execute_artifact(
    runtime: Runtime,
    judge_router: Any,
    caller_id: str,
    *,
    tool_id: str = ARTIFACT,
    sample: int = FROZEN_SAMPLE,
) -> None:
    """Run one registered artifact through the real jailed tool path."""
    caller_handle = _forced_draw(runtime, judge_router, INDEPENDENT_CALLER, CH_VERDICT)
    runtime._run_tool(caller_id, caller_handle, {"tool": tool_id, "args": {"sample": sample}})


def scaffold_judge(claim: dict[str, Any], evidence: list[dict[str, Any]]) -> dict[str, Any]:
    """Interpret real receipts against the frozen claim.  A stand-in, never a model.

    This function is the probe's scaffold and carries none of its evidence
    weight.  It reads only fact fields the runtime actually publishes and
    answers in the schema parse_finding validates, so the same decision in
    production is made by a paid evaluator from the same inputs.

    It neither treats execution as usefulness nor refuses to score execution:
    the frozen claim is about execution, so a receipt observing exactly that
    claim supports exactly that claim and nothing further.
    """
    for row in evidence:
        facts = row.get("payload", {}).get("facts", {})
        if facts.get("tool") != claim["artifact"]:
            continue
        if facts.get("version_sha256") != claim["artifact_version_sha256"]:
            continue
        if facts.get("status") != "executed":
            return {
                "status": "unknown",
                "evidence": [row["ref"]],
                "reason": "the artifact was invoked but did not execute",
            }
        if facts.get("lineage_relation") != claim["required_lineage_relation"]:
            return {
                "status": "unknown",
                "evidence": [row["ref"]],
                "reason": (
                    "this execution is same-lineage, so it does not observe the claim's "
                    "independent-use condition"
                ),
            }
        if facts.get("result_sha256") != claim["expected_result_sha256"]:
            return {
                "status": "contrary",
                "score": 0.0,
                "evidence": [row["ref"]],
                "reason": "the independent execution returned a different result than claimed",
            }
        return {
            "status": "supported",
            "score": SUPPORTED_SCORE,
            "evidence": [row["ref"]],
            "reason": (
                "an independent lineage executed the frozen artifact version and obtained the "
                "frozen result digest, which observes the execution claim and nothing about "
                "its usefulness"
            ),
        }
    return {
        "status": "unknown",
        "evidence": [row["ref"] for row in evidence],
        "reason": "no supplied fact observes the frozen artifact version",
    }


def _weights(state: Any) -> dict[str, float]:
    weights = dict(state.learner.state()["log_weights"])
    return {arm: round(float(weight), 6) for arm, weight in weights.items()}


def _jsonable(value: Any) -> Any:
    """Copy the runtime's immutable mappings into plain JSON containers."""
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def prepare_commission(arm: str) -> dict[str, Any]:
    """Carry one arm through the runtime up to its emitted final commission.

    Everything here is production machinery: the router draw, the registration,
    the jailed execution, the receipts the runtime addresses to the contract,
    and the commission it emits when the horizon matures.
    """
    if arm not in ARMS:
        raise ValueError(f"unknown arm: {arm}")
    runtime = build_runtime()
    claim = frozen_claim()
    producer_router = runtime.routers["Tick"][0]
    judge_router = runtime.routers["ProducerReturn"][0]

    producer = _forced_draw(runtime, producer_router, MAKER, CH_VERDICT)
    runtime.handle_to_assembly[producer] = MAKER
    runtime._manage_reserve_window()
    _register_artifact(runtime, producer, ARTIFACT, ARTIFACT_CODE)
    if arm == "version_mismatch":
        _register_artifact(runtime, producer, ALTERNATE_ARTIFACT, ALTERNATE_CODE)

    contract = freeze_contract(
        runtime, producer, MAKER, {"action": "publish-verifier", "observation_claim": claim}
    )

    wallet_at_freeze = runtime.wallet.balance
    caller_id = None
    if arm in ("independent_use", "contrary_result", "version_mismatch"):
        caller_id = INDEPENDENT_CALLER
    elif arm == "self_use":
        caller_id = MAKER
    if caller_id is not None:
        tool_id = ALTERNATE_ARTIFACT if arm == "version_mismatch" else ARTIFACT
        sample = CONTRARY_SAMPLE if arm == "contrary_result" else FROZEN_SAMPLE
        _execute_artifact(runtime, judge_router, caller_id, tool_id=tool_id, sample=sample)
    tool_charge_micro = wallet_at_freeze - runtime.wallet.balance

    runtime.grounded_pending[producer] = contract
    runtime.pending[producer] = PendingJudgement(
        producer, CH_VERDICT, runtime.n, opened_at_tick=runtime.ticks_consumed
    )
    runtime.ticks_consumed = contract.due_tick
    runtime._settle_due_grounded()
    commission = next(
        event for event in reversed(runtime.internal)
        if isinstance(getattr(event, "payload", None), Mapping)
        and event.payload.get("grounded_consequence")
        and event.payload.get("about_handle") == producer
    )
    # The emitted payload is frozen; the loop thaws it with _to_plain when the
    # evaluator step runs.  This plain copy is only for the scaffold judge's own
    # reading and for the record below; settlement reads the emitted event.
    evidence = _jsonable(commission.payload["evidence"])
    contract = runtime.grounded_pending[producer]
    return {
        "arm": arm,
        "runtime": runtime,
        "claim": claim,
        "producer": producer,
        "producer_router": producer_router,
        "judge_router": judge_router,
        "contract": contract,
        "commission": commission,
        "evidence": evidence,
        "tool_charge_micro": tool_charge_micro,
    }


def _settle_prepared(prepared: dict[str, Any]) -> dict[str, Any]:
    """Settle one prepared commission through the real feedback path and report it."""
    runtime = prepared["runtime"]
    claim = prepared["claim"]
    producer = prepared["producer"]
    producer_router = prepared["producer_router"]
    judge_router = prepared["judge_router"]
    contract = prepared["contract"]
    commission = prepared["commission"]
    evidence = prepared["evidence"]
    wallet_before = runtime.wallet.balance
    weights_before = _weights(producer_router)
    judge_handle = _forced_draw(runtime, judge_router, FINAL_JUDGE, CH_CONFORMITY)
    runtime.handle_to_assembly[judge_handle] = FINAL_JUDGE
    raw = scaffold_judge(claim, evidence)
    # The whole production evaluator step, with the scaffold's answer supplied
    # in place of a paid model call. The loop reads the frozen commission it
    # emitted, thaws it, renders the same inputs a paid judge would see, and
    # routes the answer into settlement itself.
    runtime._evaluator_step(
        commission,
        judge_handle,
        Sample(
            (FINAL_JUDGE,), (1.0,), FINAL_JUDGE, 0, judge_router.learner.id, "probe", ()
        ),
        10**15,
        returned=Return(
            judge_handle,
            {
                "verdict": INITIAL_VERDICT,
                "rationale": "scaffold evaluator, offline probe",
                "realized_consequence": raw,
            },
            0,
            "ok",
        ),
    )
    runtime._deliver_returns()
    weights_after = _weights(producer_router)
    wallet_after = runtime.wallet.balance

    history = [
        {
            "channel": item.channel,
            "score": item.score,
            "status": str(item.status),
            "definition_version": item.definition_version,
        }
        for item in runtime.queue.history(producer)
    ]
    return {
        "arm": prepared["arm"],
        "contract_fingerprint": hashlib.sha256(canonical(asdict(contract))).hexdigest(),
        "observation_window": {
            "opened_tick": contract.opened_tick,
            "maturity_tick": contract.due_tick,
            "timeout_tick": contract.close_tick,
        },
        "frozen_claim": claim,
        "frozen_norms": _jsonable(contract.norms),
        "initial_verdict": INITIAL_VERDICT,
        "evidence": _jsonable(evidence),
        "emitted_evidence_frozen": not isinstance(
            commission.payload["evidence"], list
        ),
        "finding_refusals": [
            item.get("reason") for item in runtime.ledger._recovery_items()
            if item.get("kind") == "consequence.finding_refused"
        ],
        "observed_evidence_refs": sorted(observed_evidence_refs(evidence)),
        "excluded_evaluators": list(commission.payload["excluded_evaluators"]),
        "final_finding": dict(raw, evaluator_id=FINAL_JUDGE),
        "settlement": history,
        "producer_router": {
            "learner_id": producer_router.learner.id,
            "before": weights_before,
            "after": weights_after,
            "changed": weights_before != weights_after,
        },
        "money": {
            "tool_charge_micro": prepared["tool_charge_micro"],
            "before_settlement_micro": wallet_before,
            "after_learning_micro": wallet_after,
        },
    }


def run_arm(arm: str) -> dict[str, Any]:
    """Settle one prepared arm through the real feedback path and report it."""
    return _settle_prepared(prepare_commission(arm))


def delayed_evidence_witness() -> dict[str, Any]:
    """Compare evidence present before maturity, after its snapshot, and at timeout.

    This is a timing and censoring witness over one fixed claim.  It does not
    estimate value, compensate delayed work or change learner policy.
    """
    before = run_arm("independent_use")

    after_prepared = prepare_commission("no_evidence")
    after_runtime = after_prepared["runtime"]
    after_contract = after_prepared["contract"]
    after_commission_refs = [row["ref"] for row in after_prepared["evidence"]]
    _execute_artifact(after_runtime, after_prepared["judge_router"], INDEPENDENT_CALLER)
    after_current_refs = [
        row["ref"] for row in public_evidence(after_runtime, after_contract)
    ]
    after = _settle_prepared(after_prepared)

    timeout_prepared = prepare_commission("no_evidence")
    timeout_runtime = timeout_prepared["runtime"]
    timeout_contract = timeout_prepared["contract"]
    timeout_weights_before = _weights(timeout_prepared["producer_router"])
    timeout_runtime.ticks_consumed = timeout_contract.close_tick
    timeout_runtime._settle_due_grounded()
    timeout_runtime._deliver_returns()
    timeout_history = timeout_runtime.queue.history(timeout_prepared["producer"])
    timeout_weights_after = _weights(timeout_prepared["producer_router"])

    return {
        "claim_fingerprint": before["contract_fingerprint"],
        "maturity_tick": after_contract.due_tick,
        "timeout_tick": timeout_contract.close_tick,
        "learner_policy_changed": False,
        "cases": [
            {
                "timing": "before_maturity",
                "at_tick": before["observation_window"]["opened_tick"],
                "eligible_in_frozen_commission": True,
                "finding": before["final_finding"]["status"],
                "settlement_status": before["settlement"][0]["status"],
                "router_changed": before["producer_router"]["changed"],
            },
            {
                "timing": "after_maturity_snapshot_before_timeout",
                "at_tick": after_runtime.ticks_consumed,
                "eligible_in_frozen_commission": False,
                "frozen_commission_refs": after_commission_refs,
                "currently_observable_refs": after_current_refs,
                "finding": after["final_finding"]["status"],
                "settlement_status": after["settlement"][0]["status"],
                "router_changed": after["producer_router"]["changed"],
            },
            {
                "timing": "assessment_timeout",
                "at_tick": timeout_runtime.ticks_consumed,
                "eligible_in_frozen_commission": False,
                "finding": "unknown",
                "settlement_status": str(timeout_history[0].status),
                "router_changed": timeout_weights_before != timeout_weights_after,
            },
        ],
        "boundary": (
            "The runtime freezes evidence when maturity commissions the final judge. A later "
            "receipt can be observable to a fresh public_evidence read while remaining absent "
            "from that frozen commission; timeout then censors without a learner sample."
        ),
    }


def run_probe() -> dict[str, Any]:
    """Run every arm in fixed order against the real runtime, entirely offline."""
    return {
        "probe": "edition4-nonfinancial-consequence-runtime-v1",
        "mode": "offline_real_runtime",
        "world": "scripted",
        "exchange": "FakeExchange",
        "provider": "ScriptedProvider",
        "norm_source": NORM_SOURCE.name,
        "grounded_definition": GROUNDED_DEFINITION,
        "paid_model_calls": 0,
        "network_calls": 0,
        "final_evaluator": {
            "kind": "deterministic_scaffold",
            "is_model": False,
            "note": (
                "The final evaluator is a stand-in.  The evidence here is the runtime path, "
                "not the quality of any judgement."
            ),
        },
        "scaffold": {
            "actor_selection": (
                "forced: decisions are opened under a well-formed recorded propensity without "
                "calling the router's sampler"
            ),
            "claim_authorship": (
                "investigator-authored fixture assigned to the producer seat, frozen ex ante"
            ),
            "final_judgement": "deterministic stand-in supplied to the production evaluator step",
        },
        "results": [run_arm(arm) for arm in ARMS],
        "dependency_witness": {
            "downstream_consumed": {
                "status": "not_expressible",
                "missing_observation": DEPENDENCY_GAP,
            },
            "receipt_only_arm": "independent_use",
            "contrary_arm": "contrary_result",
            "unavailable_arm": "no_evidence",
        },
        "delayed_evidence_witness": delayed_evidence_witness(),
        "limits": [
            "Receipts, evidence, settlement and learning come from the runtime; the final "
            "judgement is scaffold, so this says nothing about model discrimination.",
            "The frozen claim is an execution claim.  Observing it establishes execution, "
            "version and result, never benefit, demand or objective formation.",
            "Actor selection, the claim and the final answer are fixtures.  The existence of a "
            "frozen claim is not evidence that a participant would author one, and no arm is "
            "evidence of spontaneous production or Class 3 behaviour.",
            "Each arm is one decision in one world; learner movement is a mechanism witness, "
            "not an effect size.",
            "The current receipt contract cannot distinguish downstream result consumption "
            "from receipt-only invocation; the exact missing observation is reported rather "
            "than simulated.",
            "The delayed witness changes evidence timing only. It does not change learner "
            "policy or measure a population's full affordable exploratory runway.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    """Print the probe as canonical, reviewable JSON."""
    parser = argparse.ArgumentParser(description="Offline nonfinancial consequence probe")
    parser.add_argument("--pretty", action="store_true", help="indent the JSON output")
    args = parser.parse_args(argv)
    print(json.dumps(run_probe(), indent=2 if args.pretty else None, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
