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

    for filename, var in (
        ("openrouter.key", "OPENROUTER_API_KEY"),
        ("hyperliquid.key", "HL_PRIVATE_KEY"),
        ("reserve.key", "RESERVE_PRIVATE_KEY"),
    ):
        keyfile = Path.cwd() / filename
        if keyfile.exists() and var not in os.environ:
            if filename == "reserve.key":
                import stat

                info = keyfile.lstat()
                if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
                    raise ValueError("reserve.key must be a regular file with mode 0600")
            value = keyfile.read_text().strip()
            if value:
                os.environ[var] = value
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
    if args.provider == "venice":
        from factorylab.world.models import ModelRequest
        from factorylab.world.venice import VeniceProvider
        from factorylab.world.x402 import VENICE_URL

        if not args.model or not args.model.startswith("venice:"):
            print("Venice probe requires --model venice:<id>", file=sys.stderr)
            return 2
        provider = VeniceProvider(
            base_url=args.base_url or VENICE_URL,
            reasoning_config={args.model: {"enabled": False}},
        )
        response = provider.complete(ModelRequest(
            args.model, "Reply briefly.", ({"role": "user", "content": "Reply with OK."},),
            max_tokens=32,
        ))
        print(json.dumps({
            "provider": "venice", "model": response.model_id,
            "text": response.text, "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens, "cost_micro": response.cost_micro,
            "cost_source": response.raw["cost_source"],
            "request_id": response.raw.get("request_id"),
        }))
        return 0
    if not args.world:
        print("probe requires --world or --provider venice --model venice:<id>", file=sys.stderr)
        return 2
    from factorylab.world.probe import probe_hyperliquid

    m = load_manifest(args.world)
    if m.exchange.kind != "hyperliquid":
        print(f"world {m.name!r} has no live venue to probe", file=sys.stderr)
        return 2
    out = probe_hyperliquid(mainnet=m.exchange.mainnet, coins=m.exchange.coins)
    print(json.dumps(out, indent=2))
    return 0


def _cmd_reserve(args: argparse.Namespace) -> int:
    """Only init writes a key; status is read-only and topup authorizes exactly $5."""
    import os
    from decimal import Decimal, InvalidOperation
    from pathlib import Path

    from eth_account import Account  # Already supplied by hyperliquid-python-sdk.

    from factorylab.world.x402 import BASE_RPC, TOP_UP_MICRO, VENICE_URL, X402Client

    if args.reserve_cmd == "init":
        try:
            # O_EXCL refuses existing files and symlinks without ever opening them for reading.
            fd = os.open(Path.cwd() / "reserve.key", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            print("reserve.key already exists; refusing to overwrite", file=sys.stderr)
            return 2
        with os.fdopen(fd, "w") as stream:
            account = Account.create(os.urandom(32))
            os.fchmod(stream.fileno(), 0o600)
            stream.write("0x" + account.key.hex() + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        print(account.address)
        return 0
    if args.reserve_cmd == "topup":
        try:
            amount = Decimal(args.usd)
            if not amount.is_finite() or amount != Decimal("5"):
                raise ValueError
        except (InvalidOperation, ValueError):
            print("Only --usd 5 is supported by the verified Venice quote", file=sys.stderr)
            return 2
    client = X402Client(base_url=args.base_url or VENICE_URL, rpc=args.rpc or BASE_RPC)
    if args.reserve_cmd == "status":
        usdc, eth, venice = client.usdc_balance(), client.eth_balance(), client.venice_balance()
        print(json.dumps({
            "address": client.address, "network": "eip155:8453",
            "usdc_micro": usdc, "usdc_usd": format(Decimal(usdc) / 1_000_000, ".6f"),
            "eth_wei": eth, "eth": format(Decimal(eth) / 10**18, ".18f"),
            "venice_balance_micro": venice,
            "venice_balance_usd": format(Decimal(venice) / 1_000_000, ".6f"),
            "topup_5_affordable": usdc >= TOP_UP_MICRO,
        }))
        return 0
    settlement = client.top_up(TOP_UP_MICRO)
    # Preserve the reference even if the subsequent balance read fails.
    print(json.dumps({"address": client.address, "settlement": settlement}, default=str))
    balance = client.venice_balance()
    print(json.dumps({
        "venice_balance_micro": balance,
        "venice_balance_usd": format(Decimal(balance) / 1_000_000, ".6f"),
    }))
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    try:
        from factorylab.runtime.loop import run_world
    except ImportError:
        print("factorylab run: the runtime loop is not built yet", file=sys.stderr)
        return 1
    m = load_manifest(args.world)
    if args.tick_interval:
        import dataclasses

        from factorylab.runtime.worlds import _ns

        m = dataclasses.replace(m, tick_interval_ns=_ns(args.tick_interval))
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
        kill_at_end=args.kill_at_end,
    )
    print(json.dumps(summary, indent=2, default=str))
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    with open(args.summary) as f:
        s = json.load(f)
    st = s["stats"]
    print(
        f"world {s['world']}  seed {s['seed']}  live {s.get('live')}  "
        f"terminated {s['terminated']} ({s['termination_reason']})"
    )
    spent = None
    spent_text = "n/a"
    agg = s.get("aggregates") or {}
    spend = (agg.get("spend_by_capability") or {}).get("spend") or {}
    if spend:
        spent = sum(spend.values()) / 1e6
        spent_text = f"{spent:.6f}"
    print(
        f"wallet {s['wallet_balance_micro'] / 1e6:.6f} USD  spent {spent_text} USD  "
        f"conservation {s['wallet_conservation']}  verify {s['ledger_verify']}  "
        f"seal_released {s['seal_key_released']}"
    )
    rows = [
        ("events", st["events"]),
        ("decisions", st["decisions"]),
        ("invocations", st["invocations"]),
        ("noops", st["noops"]),
        ("producer_returns", st["producer_returns"]),
        ("verdicts", st["verdicts"]),
        ("conformities", st["conformities"]),
        ("censored", st["censored"]),
        ("forecasts sealed/settled", f"{st['forecasts_sealed']}/{st['forecasts_settled']}"),
        (
            "registrations ok/rejected",
            f"{st['registrations_accepted']}/{st['registrations_rejected']}",
        ),
        ("epochs", st["epochs"]),
        ("routers replaced", st["routers_replaced"]),
        ("orders placed/rejected", f"{st['orders_placed']}/{st['orders_rejected']}"),
        ("fills", st["fills"]),
        ("reconciliations", st.get("reconciliations", 0)),
        ("price updates/skipped", f"{st.get('price_updates', 0)}/{st.get('price_skipped', 0)}"),
        ("penalized settlements", st.get("penalized_settlements", 0)),
    ]
    for k, v in rows:
        print(f"  {k:<28} {v}")
    print("  invocation status:", st["invocation_status"])
    print("  stop reasons:     ", st.get("stop_reasons", {}))
    print("  by role:          ", st["invocations_by_role"])
    if spend:
        print("  spend by capability (USD):", {k: round(v / 1e6, 6) for k, v in spend.items()})
    counts = (agg.get("action_frequencies") or {}).get("counts")
    if counts:
        print("  action frequencies:", counts)
    for k, v in s.get("routers", {}).items():
        print(f"  router {k:<16} epoch {v['epoch']}  {v['learner']}  {v['universe']}")
    standing = s.get("standing") or {}
    for e, v in standing.items():
        print(
            f"  standing {e:<10} n={v['n']} skill={v['skill']:.3f} "
            f"coverage={v['coverage']:.2f} weight={v['weight']:.3f}"
        )
    return 0


def _cmd_postmortem(args: argparse.Namespace) -> int:
    """Decrypt a dead world's diary with its released key file and print selected entries.

    Only meaningful after termination. Reading a live world's key file breaks the
    non-intervention covenant; this command does not check that for you.
    """
    from cryptography.fernet import Fernet

    key = open(args.key, "rb").read().strip()
    f = Fernet(key)
    kinds = set(args.kinds.split(",")) if args.kinds else None
    shown = 0
    with open(args.ledger, "rb") as stream:
        next(stream)  # header
        for line in stream:
            rec = json.loads(line)
            item = json.loads(f.decrypt(rec["item"].encode()))
            kind = item.get("kind")
            if kind == "event":
                kind = f"event:{item['event']['kind']}"
            if kinds and kind not in kinds:
                continue
            shown += 1
            if shown > args.limit:
                break
            if kind == "invocation":
                out = item.get("outputs", "")
                head = (
                    f"[{item['seq']}] {kind} {item['assembly_id']} ({item['role']}, "
                    f"{item['status']}, {item.get('stop_reason')}, {item['cost']} µUSD)"
                )
                print(head)
                print("    ", out[: args.width])
            elif kind.startswith("event:"):
                payload = json.dumps(item["event"].get("payload"), default=str)
                print(f"[{item['seq']}] {kind} {payload[: args.width]}")
            else:
                skip = ("kind", "seq", "prev_hash", "hash")
                body = json.dumps({k: v for k, v in item.items() if k not in skip}, default=str)
                print(f"[{item['seq']}] {kind} {body[: args.width]}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="factorylab")
    sub = p.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("manifest", help="validate a world manifest and print its hash")
    m.add_argument("--world", required=True)
    m.set_defaults(func=_cmd_manifest)

    pr = sub.add_parser("probe", help="read live venue data for a world (network)")
    probe_target = pr.add_mutually_exclusive_group(required=True)
    probe_target.add_argument("--world")
    probe_target.add_argument("--provider", choices=("venice",))
    pr.add_argument("--model")
    pr.add_argument("--base-url", help="Venice API root, including /api/v1")
    pr.add_argument("--rpc", help="Base RPC override (probe itself makes no RPC calls)")
    pr.set_defaults(func=_cmd_probe)

    reserve = sub.add_parser("reserve", help="initialize, inspect or fund the Venice reserve")
    reserve_sub = reserve.add_subparsers(dest="reserve_cmd", required=True)
    init = reserve_sub.add_parser("init", help="create reserve.key once; print only its address")
    init.set_defaults(func=_cmd_reserve)
    for name in ("status", "topup"):
        command = reserve_sub.add_parser(name)
        command.add_argument("--rpc", help="Base JSON-RPC URL")
        command.add_argument("--base-url", help="Venice API root, including /api/v1")
        if name == "topup":
            command.add_argument("--usd", required=True, help="exactly 5; never rounded")
        command.set_defaults(func=_cmd_reserve)

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
    r.add_argument("--tick-interval", default=None, help="override the manifest tick, e.g. 10s")
    r.add_argument(
        "--kill-at-end",
        action="store_true",
        help="end a budgeted rehearsal world by explicit kill so its seal key is released",
    )
    r.set_defaults(func=_cmd_run)

    rp = sub.add_parser("report", help="print a run summary readably")
    rp.add_argument("summary")
    rp.set_defaults(func=_cmd_report)

    pm = sub.add_parser("postmortem", help="decrypt a dead world's diary and print entries")
    pm.add_argument("ledger")
    pm.add_argument("key")
    pm.add_argument("--kinds", default=None, help="comma list, e.g. invocation,event:Registered")
    pm.add_argument("--limit", type=int, default=50)
    pm.add_argument("--width", type=int, default=400)
    pm.set_defaults(func=_cmd_postmortem)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    is_reserve = args.cmd == "reserve"
    is_venice_probe = args.cmd == "probe" and args.provider == "venice"
    if is_reserve or is_venice_probe:
        try:
            if not (is_reserve and args.reserve_cmd == "init"):
                _load_dotenv()
            return int(args.func(args))
        except Exception:
            # Loading/signing/transport exceptions can include secrets. No traceback or body.
            print("Reserve/Venice command failed; check key setup and endpoint status. "
                  "After a top-up submission, check balances before retrying.", file=sys.stderr)
            return 1
    _load_dotenv()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
