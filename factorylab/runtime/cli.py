"""Command-line entry point.

Commands:
  factorylab manifest --world <name>   validate a manifest and print its hash
  factorylab probe --world <name>      read live mids and funding (network)
  factorylab run --world <name> ...    run the event loop (runtime workstream)
"""

from __future__ import annotations

import argparse
import json
import sys

from factorylab.runtime.worlds import load_manifest


def _load_dotenv() -> None:
    """Load KEY=VALUE lines from a .env file in the working directory into the environment.

    Existing variables win. Values are never printed. This is the only place
    the runtime reads a file for secrets; the file is gitignored.
    """
    import os
    from pathlib import Path

    path = Path.cwd() / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _cmd_manifest(args: argparse.Namespace) -> int:
    m = load_manifest(args.world)
    out = {"name": m.name, "hash": m.manifest_hash(), "models": [t.id for t in m.models]}
    print(json.dumps(out))
    return 0


def _cmd_probe(args: argparse.Namespace) -> int:
    from factorylab.world.probe import probe_hyperliquid

    m = load_manifest(args.world)
    if m.exchange.kind != "hyperliquid":
        print(f"world {m.name!r} has no live venue to probe", file=sys.stderr)
        return 2
    out = probe_hyperliquid(mainnet=m.exchange.mainnet, coins=m.exchange.coins)
    print(json.dumps(out, indent=2))
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    try:
        from factorylab.runtime.loop import run_world
    except ImportError:
        print("factorylab run: the runtime loop is not built yet", file=sys.stderr)
        return 1
    m = load_manifest(args.world)
    events = args.events
    if args.duration:
        from factorylab.runtime.worlds import _ns

        events = max(1, _ns(args.duration) // m.tick_interval_ns)
    summary = run_world(
        m,
        events=events,
        seed=args.seed,
        initial_balance_micro=args.initial_balance,
        ledger_path=args.ledger,
        drip=not args.no_drip,
    )
    print(json.dumps(summary, indent=2, default=str))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="factorylab")
    sub = p.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("manifest", help="validate a world manifest and print its hash")
    m.add_argument("--world", required=True)
    m.set_defaults(func=_cmd_manifest)

    pr = sub.add_parser("probe", help="read live venue data for a world (network)")
    pr.add_argument("--world", required=True)
    pr.set_defaults(func=_cmd_probe)

    r = sub.add_parser("run", help="run a world's event loop")
    r.add_argument("--world", required=True)
    r.add_argument("--events", type=int, default=200)
    r.add_argument("--seed", type=int, default=None)
    r.add_argument("--initial-balance", type=int, default=None, help="micro-USD override")
    r.add_argument("--ledger", default=None, help="ledger file path; in-memory if omitted")
    r.add_argument("--no-drip", action="store_true", help="launch without the manifest's drip")
    r.add_argument(
        "--duration", default=None, help="wall-clock length like 30m; overrides --events"
    )
    r.set_defaults(func=_cmd_run)
    return p


def main(argv: list[str] | None = None) -> int:
    _load_dotenv()
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
