"""Prepare and run a ten-minute testnet rehearsal bound to its population-voted charter.

The mechanism command runs the deterministic scripted world with simulated time.
Neither path changes a living world's manifest or resumes an older experiment.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import tomllib
from pathlib import Path
from uuid import uuid4

from factorylab.runtime.worlds import load_manifest, manifest_from_dict
from scripts.draft_edition1 import charter_digest, roster_hash


def voted_charter(path: Path, manifest) -> dict:
    """Reject missing, altered, empty or differently surveyed charter artifacts."""
    text = path.read_text()
    metadata = dict(re.findall(r"^# (roster_sha256|charter_sha256) = ([0-9a-f]{64})$",
                               text, re.MULTILINE))
    raw = tomllib.loads(text)
    charter = raw.get("charter")
    if not isinstance(charter, dict) or not charter.get("cards"):
        raise ValueError("rehearsal requires a nonempty explicit voted charter")
    if metadata.get("roster_sha256") != roster_hash(manifest):
        raise ValueError("charter survey roster differs from rehearsal roster")
    if metadata.get("charter_sha256") != charter_digest(charter):
        raise ValueError("charter differs from the survey export")
    return charter


def preflight(world: Path, charter_path: Path) -> dict:
    """Require exact charter provenance and testnet-only execution before loading credentials."""
    manifest = load_manifest(str(world))
    charter = voted_charter(charter_path, manifest)
    raw = tomllib.loads(world.read_text())
    # The manifest may carry the ratification's provenance digests beside the cards
    # (charter.ratified_sha256, charter.roster_sha256); the vote is on the cards.
    loaded = {k: v for k, v in (raw.get("charter") or {}).items()
              if k not in ("ratified_sha256", "roster_sha256")}
    if loaded != charter:
        raise ValueError("rehearsal did not load the exact voted charter")
    if manifest.exchange.kind != "hyperliquid" or manifest.exchange.mainnet:
        raise ValueError("live rehearsal requires Hyperliquid testnet")
    if manifest.exchange.client_namespace is None:
        raise ValueError("rehearsal requires a fresh exchange client namespace")
    manifest.validate()
    return {
        "world": manifest.name, "roster_sha256": roster_hash(manifest),
        "client_namespace": manifest.exchange.client_namespace,
        "charter_sha256": charter_digest(charter),
        "cards": [{"id": c.id, "answers_for": c.answers_for,
                   "window": {"kind": c.window.kind, "n": c.window.n, "per": c.window.per},
                   "region": c.acceptable_region,
                   "bootstrap": "previous window required" if "previous window" in
                   c.acceptable_region else "absolute region; sample support still required"}
                  for c in manifest.charter.cards],
        "tick_interval_ns": manifest.tick_interval_ns,
        "price_window_ns": manifest.novelty.window_ns,
        "governance_floor_ns_at_declared_tick": manifest.timing.min_ratio
        * manifest.evaluation.consequence_backstop_events * manifest.tick_interval_ns,
        "duration": "10m", "deadline_semantics": "finish an in-progress cascade",
        "claims": "execution and feedback check; no profitability or convergence claim",
    }


def prepare(base: Path, charter_path: Path, out: Path) -> dict:
    """Embed precisely the exported charter without changing its thresholds or sample windows."""
    manifest = load_manifest(str(base))
    charter = voted_charter(charter_path, manifest)
    raw = tomllib.loads(base.read_text())
    if "charter" in raw:
        raise ValueError("base must be a drafting roster without an existing charter")
    raw.update(name=out.stem, charter=charter)
    namespace = uuid4().hex
    raw["exchange"]["client_namespace"] = namespace
    manifest_from_dict(raw).validate()
    base_text = re.sub(r'^name = "[^"]+"$', f'name = "{out.stem}"',
                       base.read_text(), count=1, flags=re.MULTILINE)
    base_text = base_text.replace("[exchange]", '[exchange]\nclient_namespace = "'
                                  + namespace + '"', 1)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("x") as stream:
        stream.write(base_text.rstrip() + "\n\n" + charter_path.read_text())
    return preflight(out, charter_path)


def run_command(command: list[str], out: Path) -> int:
    """Each experiment has a fresh evidence directory and separately retained stdout/stderr."""
    out.mkdir(parents=True, exist_ok=False)
    with (out / "summary.json").open("w") as stdout, (out / "stderr.log").open("w") as stderr:
        result = subprocess.run(command + ["--ledger", str(out / "ledger.jsonl")],
                                stdout=stdout, stderr=stderr, check=False)
    return result.returncode


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    prep = subs.add_parser("prepare")
    prep.add_argument("--base", type=Path, required=True)
    prep.add_argument("--charter", type=Path, required=True)
    prep.add_argument("--out", type=Path, required=True)
    for name in ("preflight", "live"):
        p = subs.add_parser(name)
        p.add_argument("--world", type=Path, required=True)
        p.add_argument("--charter", type=Path, required=True)
        if name == "live":
            p.add_argument("--out", type=Path, required=True)
    mechanism = subs.add_parser("mechanism")
    mechanism.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "prepare":
        print(json.dumps(prepare(args.base, args.charter, args.out), indent=2))
        return 0
    if args.command == "mechanism":
        return run_command(["uv", "run", "factorylab", "run", "--world", "scripted",
                            "--events", "500", "--seed", "1", "--kill-at-end"], args.out)
    evidence = preflight(args.world, args.charter)
    print(json.dumps(evidence, indent=2), flush=True)
    if args.command == "preflight":
        return 0
    # Preflight completes before the CLI loads any keys or constructs live clients.
    # Reusing a prepared manifest would also reuse its venue client identities, so the
    # marker is written only once the evidence directory is known to be creatable:
    # a mistaken --out must not consume the manifest.
    if args.out.exists():
        parser.error(f"evidence directory already exists: {args.out}")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    marker = args.world.with_suffix(".used")
    with marker.open("x") as stream:
        stream.write(str(args.out.resolve()) + "\n")
    code = run_command(["uv", "run", "factorylab", "run", "--world", str(args.world),
                        "--duration", "10m", "--kill-at-end"], args.out)
    (args.out / "preflight.json").write_text(json.dumps(evidence, indent=2) + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
