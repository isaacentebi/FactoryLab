"""Check each seat on two synthetic requests without executing its returns."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from factorylab.cortex.assembly import _parse_json_object, _validate_return
from factorylab.runtime.cli import _load_dotenv
from factorylab.runtime.live import build_provider
from factorylab.runtime.loop import Runtime
from factorylab.runtime.propensity import action_label, declared_record
from factorylab.runtime.worlds import load_manifest
from factorylab.world.exchange import FakeExchange
from factorylab.world.scripted import ScriptedProvider
from scripts.draft_edition1 import roster_hash


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--world", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--assembly", action="append", help="check only named seats")
    args = parser.parse_args(argv)
    if args.out.exists():
        raise FileExistsError(args.out)
    manifest = load_manifest(args.world)
    rt = Runtime(manifest, events=1, seed=None, initial_balance_micro=None, ledger_path=None,
                 drip=False, router_gamma=0.1, provider=ScriptedProvider(),
                 exchange=FakeExchange(coins=manifest.exchange.coins))
    _load_dotenv()
    provider = build_provider(manifest)
    rows = []
    selected = set(args.assembly or [a.id for a in manifest.assemblies])
    if selected - {a.id for a in manifest.assemblies}:
        raise ValueError("unknown calibration seat")
    for spec in manifest.assemblies:
        if spec.id not in selected:
            continue
        for example in range(2):
            schema = rt._contract_schema(spec.id)
            kind = "Verdict" if spec.role == "meta" else (
                "ProducerReturn" if spec.role == "evaluator" else "Tick")
            subject = {"handle": "decision-1", "outputs": {"action": "hold",
                       "rationale": "No new evidence in this sample."}, "status": "ok",
                       "cost": 1800, "verdict": 0.6, "payoff": 0.1,
                       "rationale": "A low-cost hold with no claimed trading payoff."}
            inputs = {"kind": kind, "payload": {"index": example + 1},
                      "world": rt._world_block(), "your_recent_returns": [],
                      "your_action_policy": {}, "subject_handle": "decision-1"}
            if spec.role in ("evaluator", "meta"):
                inputs["return" if spec.role == "evaluator" else "verdict"] = subject
            description = ("Assess the supplied return against the charter." if spec.role in
                           ("evaluator", "meta") else "Respond to the supplied world event.")
            req = rt._request("calibration", description, inputs, schema, 10**15, "verdict")
            model_req = rt.assemblies[spec.id].build_model_request(req)
            row = {"assembly": spec.id, "model": spec.model_id, "sample": example + 1,
                   "max_tokens": spec.max_tokens}
            try:
                resp = provider.complete(model_req)
                row.update(stop=resp.stop_reason, output_tokens=resp.output_tokens,
                           input_tokens=resp.input_tokens, cost_micro=resp.cost_micro,
                           text=resp.text, served_by=resp.model_id)
                parsed = _parse_json_object(resp.text)
                if parsed is None:
                    raise ValueError("not a JSON object")
                _validate_return(parsed, schema)
                label = action_label(spec.role, parsed, "ok")
                _, reason = declared_record(label, parsed.get("propensity"),
                                            learner_id=spec.id, state_hash="calibration")
                row.update(valid=True, action_label=label, propensity_problem=reason)
            except Exception as exc:
                row.update(valid=False, failure=type(exc).__name__,
                           http_status=getattr(exc, "status", None))
            rows.append(row)
            print(json.dumps({k: v for k, v in row.items() if k != "text"}), flush=True)
    report = {"roster_sha256": roster_hash(manifest), "synthetic_requests": True,
              "executed_returns": False, "selected_seats": sorted(selected), "rows": rows}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    return 0 if all(r["valid"] for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
