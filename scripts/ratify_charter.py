"""The original committee resolves incompatible majority-approved cards without rewriting them."""

from __future__ import annotations

import argparse
import json
import random
import tomllib
from pathlib import Path

from factorylab.charter.book import validate_observation_bindings
from factorylab.charter.charter import MetricCard
from factorylab.charter.committee import draw
from factorylab.cortex.assembly import _parse_json_object
from factorylab.runtime.cli import _load_dotenv
from factorylab.runtime.live import build_provider
from factorylab.runtime.worlds import load_manifest
from factorylab.world.models import ModelRequest
from scripts.draft_edition1 import charter_digest, render_toml, roster_hash
from scripts.rehearsal import voted_charter


def select_cards(raw: dict, selected: list[str]) -> list[tuple[MetricCard, float | None]]:
    """Only distinct supplied cards passing composition validation can enter a ballot."""
    if (not isinstance(selected, list) or any(not isinstance(cid, str) for cid in selected)
            or len(selected) != len(set(selected))):
        raise ValueError("selected must contain distinct card identifiers")
    by_id = {c["id"]: c for c in raw["cards"]}
    if set(selected) - by_id.keys():
        raise ValueError("selected names an absent card")
    cards = []
    for cid in selected:
        c = dict(by_id[cid])
        price = c.pop("lambda", None)
        # TOML omits null; use the same normalization as the manifest loader.
        c["window"] = {"per": None, **c["window"]}
        cards.append((MetricCard(**c), price))
    validate_observation_bindings([c for c, _ in cards])
    return cards


def majority_subset(raw: dict, ballots: list[list[str] | None], seats: int) -> list:
    """A strict majority of individually compatible subsets cannot approve an overlapping pair."""
    for ballot in ballots:
        if ballot is not None:
            select_cards(raw, ballot)
    valid = [set(b) for b in ballots if b is not None]
    selected = [c["id"] for c in raw["cards"]
                if sum(c["id"] in b for b in valid) > seats // 2]
    return select_cards(raw, selected)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--world", required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.out.exists() or args.report.exists():
        raise FileExistsError("ratification outputs already exist")
    manifest = load_manifest(args.world)
    raw = voted_charter(args.candidate, manifest)
    conflicts = []
    for i, left in enumerate(raw["cards"]):
        for right in raw["cards"][i + 1:]:
            try:
                select_cards(raw, [left["id"], right["id"]])
            except ValueError as exc:
                conflicts.append({"cards": [left["id"], right["id"]],
                                  "preflight_refusal": str(exc)})
    seats = draw({a.id: a.role for a in manifest.assemblies}, random.Random(manifest.seed),
                 size=manifest.committee.seats)
    _load_dotenv()
    provider = build_provider(manifest)
    ballots, calls = [], []
    for seat in seats:
        spec = next(a for a in manifest.assemblies if a.id == seat.assembly_id)
        inputs = {"charter": raw, "preflight_feedback": conflicts,
                  "outcome_schema": {"type": "object", "properties": {
                      "selected": {"type": "array", "items": {"type": "string",
                          "enum": [c["id"] for c in raw["cards"]]}},
                      "reason": {"type": "string"}}, "required": ["selected", "reason"]}}
        req = ModelRequest(spec.model_id,
                           "Reply with one JSON object satisfying the outcome schema.",
                           ({"role": "user", "content":
                             "These cards received a majority in the first survey. Select the "
                             "subset you approve together for edition 1, considering the actual "
                             "preflight feedback. Keep every selected definition unchanged. "
                             "Give one short reason for your complete selection.\n"
                             + json.dumps(inputs)},), max_tokens=2000,
                           effort=spec.effort, json_object=True)
        row = {"seat": {"alias": seat.alias, "assembly_id": seat.assembly_id,
                        "role": seat.role}, "model": spec.model_id}
        try:
            response = provider.complete(req)
            row.update(text=response.text, stop=response.stop_reason,
                       cost_micro=response.cost_micro, input_tokens=response.input_tokens,
                       output_tokens=response.output_tokens, served_by=response.model_id)
            parsed = _parse_json_object(response.text)
            if parsed is None or not isinstance(parsed.get("reason"), str):
                raise ValueError("ballot needs a selection and reason")
            select_cards(raw, parsed.get("selected"))
            ballots.append(parsed["selected"])
            row.update(valid=True, selected=parsed["selected"])
        except Exception as exc:
            ballots.append(None)
            row.update(valid=False, failure=type(exc).__name__,
                       http_status=getattr(exc, "status", None))
        calls.append(row)
        print(json.dumps({k: v for k, v in row.items() if k != "text"}), flush=True)
    cards = majority_subset(raw, ballots, len(seats))
    body = render_toml(cards, tuple(raw["norms"]))
    artifact = tomllib.loads(body)["charter"]
    evidence = {"roster_sha256": roster_hash(manifest),
                "parent_charter_sha256": charter_digest(raw), "conflicts": conflicts,
                "calls": calls, "accepted": [c.id for c, _ in cards],
                "charter_sha256": charter_digest(artifact)}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(evidence, indent=2) + "\n")
    if not cards:
        raise ValueError("no compatible subset won a majority; no charter exported")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x") as stream:
        for key in ("roster_sha256", "parent_charter_sha256", "charter_sha256"):
            stream.write(f"# {key} = {evidence[key]}\n")
        stream.write(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
