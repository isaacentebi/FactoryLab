#!/usr/bin/env python3
"""Measure prompt rendering from one explicitly selected FactoryLab source tree."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path

SCENARIOS = {"modest": 2_048, "large": 32_768}
OUTCOME_COUNT = 30
INLINE_COUNT = 8


def digest(value: object) -> str:
    """Return a stable SHA-256 for JSON-shaped benchmark evidence."""
    wire = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(wire).hexdigest()


def structural_schema(value: object) -> object:
    """Remove explanatory prose so both sources render one identical contract shape."""
    if isinstance(value, dict):
        return {key: structural_schema(item) for key, item in value.items()
                if key != "description"}
    if isinstance(value, list):
        return [structural_schema(item) for item in value]
    return value


def fixture_outcome(index: int) -> tuple[dict, dict, dict, str, str]:
    """Return deterministic said/outcome/evidence records and their exact hashes."""
    claim = f"claim-{index:02d}: venue consequence required before acceptance"
    evidence = f"evidence-{index:02d}: deterministic offline observation"
    claim_hash, evidence_hash = digest(claim), digest(evidence)
    said = {
        "rationale": (f"Decision {index:02d} rationale. " +
                      "Retain the premise, alternative, and rejection condition. " * 18),
        "payoff": round((index % 10) / 10, 1),
        "forecasts": [{"claim": claim, "probability": 0.5}],
    }
    outcome = {"status": "settled", "needed_claim_hash": claim_hash,
               "decision": "accepted" if index % 2 else "rejected"}
    proof = {"evidence_hash": evidence_hash, "source": "offline-fixture"}
    return said, outcome, proof, claim_hash, evidence_hash


def build(source: Path) -> dict:
    """Build both scenarios without running an event or calling a paid rail."""
    source = source.resolve()
    sys.path.insert(0, str(source))
    from factorylab.cortex import request as request_module
    from factorylab.runtime.continuity import canonical
    from factorylab.runtime.loop import Runtime
    from factorylab.runtime.worlds import PromptSpec, load_manifest
    from factorylab.world.exchange import FakeExchange
    from factorylab.world.scripted import ScriptedProvider

    loaded = Path(request_module.__file__).resolve()
    if source not in loaded.parents:
        raise RuntimeError(f"source shadowed: imported {loaded}, expected under {source}")
    manifest_path = source / "worlds" / "edition3-rehearsal-5.toml"
    source_manifest = load_manifest(str(manifest_path))
    manifest = replace(
        source_manifest,
        prompt=PromptSpec(mode="compact"),
        exchange=replace(source_manifest.exchange, kind="fake", mainnet=False,
                         client_namespace=None),
        treasury=replace(source_manifest.treasury, reserve_address=None,
                         hyperevm_gas_budget_wei=0, base_gas_budget_wei=0),
    )
    runtime = Runtime(
        manifest, events=0, seed=1, initial_balance_micro=None, ledger_path=None,
        drip=False, router_gamma=0.1, provider=ScriptedProvider(),
        exchange=FakeExchange(coins=manifest.exchange.coins),
    )
    seat = "mechanism"
    expected = []
    for index in range(1, OUTCOME_COUNT + 1):
        handle = f"decision-{index:02d}"
        said, outcome, evidence, claim_hash, evidence_hash = fixture_outcome(index)
        runtime.outcomes.record_said(seat, handle, said)
        record = runtime.outcomes.append(seat, handle=handle, outcome=outcome,
                                         evidence=evidence, observed_at_ns=index)
        body = runtime.outcomes.body(record["sha"])
        expected.append({"outcome_id": f"outcome:{index}",
                         "needed_claim_hash": claim_hash,
                         "evidence_hash": evidence_hash, "body_sha256": digest(body)})
    unread = runtime.outcomes.unread(seat)
    if unread["count"] != OUTCOME_COUNT or len(unread["items"]) != INLINE_COUNT:
        raise RuntimeError("fixture no longer exercises a 30-item queue with eight inline")
    world = runtime._world_block()
    schema = structural_schema(runtime._contract_schema(seat))
    cases = {}
    for name, state_bytes in SCENARIOS.items():
        empty_bytes = len(canonical({"notes": ""}))
        state_obj = {"notes": "S" * (state_bytes - empty_bytes)}
        head = runtime.working_state.put(seat, state_obj, handle=f"state-{name}")
        state = runtime.working_state.render(seat)
        stored = runtime.artifacts.get(head["sha"])
        if len(stored) != state_bytes or stored != canonical(state_obj):
            raise RuntimeError("working-state fixture did not round-trip exactly")
        inputs = {"kind": "WorldUpdate", "payload": {"index": 1}, "world": world,
                  "you": seat, "your_state": state, "unread_outcomes": unread,
                  "your_action_policy": {}}
        def rendered(your_state: object, outcomes: dict, *, _name=name, _inputs=inputs):
            return runtime._request(
                f"benchmark-{_name}", "Respond to the supplied world event.",
                {**_inputs, "your_state": your_state, "unread_outcomes": outcomes},
                schema, 10**15, "verdict",
            )

        request = rendered(state, unread)
        prompt = request.prompt_text()
        visible = expected[:INLINE_COUNT]
        checks = {
            "queue_count_visible": f'"unread_count": {OUTCOME_COUNT}' in prompt,
            "inline_outcome_ids": [row["outcome_id"] for row in visible
                                   if row["outcome_id"] in prompt],
            "needed_claim_hashes": [row["needed_claim_hash"] for row in visible
                                    if row["needed_claim_hash"] in prompt],
            "evidence_hashes": [row["evidence_hash"] for row in visible
                                if row["evidence_hash"] in prompt],
            "state_sha_visible": head["sha"] in prompt,
            "state_bytes_visible": f'"bytes": {state_bytes}' in prompt,
        }
        if (not checks["queue_count_visible"] or
                len(checks["inline_outcome_ids"]) != INLINE_COUNT or
                not checks["state_sha_visible"] or not checks["state_bytes_visible"]):
            raise RuntimeError(f"fixture index became undiscoverable in {name}")
        exact_bodies = [digest(runtime.outcomes.body(row["sha"]))
                        for row in runtime.outcomes.items[seat]]
        expected_bodies = [row["body_sha256"] for row in expected]
        fetched = [runtime.outcomes.get(seat, row["outcome_id"]) for row in expected]
        claims_exact = all(item["outcome"]["needed_claim_hash"] == row["needed_claim_hash"]
                           for item, row in zip(fetched, expected, strict=True))
        evidence_exact = all(item["evidence"]["evidence_hash"] == row["evidence_hash"]
                             for item, row in zip(fetched, expected, strict=True))
        empty = rendered(None, {"count": 0, "more": 0, "items": []}).section_bytes()
        state_only = rendered(state, {"count": 0, "more": 0, "items": []}).section_bytes()
        inbox_only = rendered(None, unread).section_bytes()
        sections = request.section_bytes()
        cases[name] = {"section_bytes": sections, "prefix_bytes": sections["stable_prefix"],
                       "prompt_bytes": sections["total"], "prompt_sha256": digest(prompt),
                       "discoverability": checks,
                       "contributions": {
                           "empty_you_bytes": empty["you"],
                           "working_state_you_bytes": state_only["you"] - empty["you"],
                           "inbox_you_bytes": inbox_only["you"] - empty["you"],
                       },
                       "exact_recoverability": {
                           "working_state_artifact": (
                               digest(json.loads(stored)) == digest(state_obj)),
                           "all_outcome_bodies": exact_bodies == expected_bodies,
                           "all_needed_claim_hashes": claims_exact,
                           "all_evidence_hashes": evidence_exact,
                           "outcome_body_digest": digest(exact_bodies),
                       }}
    fixture = {"manifest": "worlds/edition3-rehearsal-5.toml",
               "source_manifest_hash": source_manifest.manifest_hash(),
               "offline_manifest_hash": manifest.manifest_hash(), "seat": seat, "events": 0,
               "provider": "ScriptedProvider", "exchange": "FakeExchange",
               "outcome_count": OUTCOME_COUNT, "inline_count": INLINE_COUNT,
               "state_bytes": SCENARIOS, "prompt_mode": "compact",
               "schema_sha256": digest(schema),
               "expected_inline": expected[:INLINE_COUNT]}
    return {"schema_version": 1, "source": str(source), "fixture_sha256": digest(fixture),
            "fixture": fixture, "cases": cases}


def comparison(baseline: dict, candidate: dict) -> dict:
    """Compare byte counts only when both runs used the exact same fixture."""
    if baseline["fixture_sha256"] != candidate["fixture_sha256"]:
        raise ValueError("fixture mismatch: refusing an invalid comparison")
    rows = {}
    for name in SCENARIOS:
        before, after = baseline["cases"][name], candidate["cases"][name]
        rows[name] = {"baseline_bytes": before["prompt_bytes"],
                      "candidate_bytes": after["prompt_bytes"],
                      "saved_bytes": before["prompt_bytes"] - after["prompt_bytes"],
                      "saved_percent": round(100 * (before["prompt_bytes"] -
                                                    after["prompt_bytes"]) /
                                             before["prompt_bytes"], 2),
                      "baseline_prefix_bytes": before["prefix_bytes"],
                      "candidate_prefix_bytes": after["prefix_bytes"],
                      "section_bytes": {
                          section: {"baseline": before["section_bytes"].get(section, 0),
                                    "candidate": after["section_bytes"].get(section, 0),
                                    "saved": (before["section_bytes"].get(section, 0) -
                                              after["section_bytes"].get(section, 0))}
                          for section in sorted(set(before["section_bytes"]) |
                                                set(after["section_bytes"]))
                      },
                      "contributions": {"baseline": before["contributions"],
                                        "candidate": after["contributions"]},
                      "inline_ids_equal": (before["discoverability"]["inline_outcome_ids"] ==
                                           after["discoverability"]["inline_outcome_ids"]),
                      "inline_hash_counts": {
                          "baseline": {
                              "needed_claim": len(before["discoverability"][
                                  "needed_claim_hashes"]),
                              "evidence": len(before["discoverability"]["evidence_hashes"]),
                          },
                          "candidate": {
                              "needed_claim": len(after["discoverability"][
                                  "needed_claim_hashes"]),
                              "evidence": len(after["discoverability"]["evidence_hashes"]),
                          },
                      },
                      "exact_recoverability": after["exact_recoverability"]}
    return {"schema_version": 1, "fixture_sha256": baseline["fixture_sha256"],
            "baseline_source": baseline["source"], "candidate_source": candidate["source"],
            "cases": rows,
            "interpretation": "Byte savings are rendering evidence, not behavioral evidence."}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compare", nargs=2, type=Path, metavar=("BASELINE", "CANDIDATE"))
    args = parser.parse_args()
    if bool(args.source) == bool(args.compare):
        parser.error("choose exactly one of --source or --compare")
    result = (build(args.source) if args.source else
              comparison(*(json.loads(path.read_text()) for path in args.compare)))
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
