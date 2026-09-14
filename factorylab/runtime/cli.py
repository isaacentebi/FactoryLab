"""The operator's whole interface to a world: create one, watch it, read it after it dies.

Every failure here prints one code from ``runtime.reasons.Reason`` on its first
line, so a supervisor can classify it and no provider message, address or key
can reach a log. The two commands that create or end a world add a second line
naming the class and the module that refused — words this repository wrote,
never a provider's — because a launch that cannot construct is otherwise one
undiagnosable word.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from types import MappingProxyType

from factorylab.runtime.reasons import (
    PAYMENT_MAY_HAVE_SETTLED,
    CredentialMissing,
    Reason,
    record,
)
from factorylab.runtime.worlds import load_manifest

ARGUMENT_EXIT = 2  # A refusal that protects a world, a key or the committed first move.
TERMINATED_EXIT = 3  # deploy/factorylab.service: SuccessExitStatus + RestartPreventExitStatus
LEDGER_BUSY_EXIT = 4
NO_LAUNCH_EXIT = 5  # deploy/start.sh: authenticated evidence shows no world exists yet.

EPILOG = """exit codes:
  0  the command succeeded
  1  the command failed; the reason code says how
  2  a refusal: an argument, a key, or the world's own committed first move
  3  the world is terminated; this is final and the supervisor must not restart
  4  another process holds the ledger's writer lock
  5  no launch exists in the ledger yet (start.sh then runs the world)

never do this:
  never read, print or commit a *.key file while its world is alive
  never write worlds/funded.toml or touch a running world: the first move is
  made once, and after it the experimenter does not intervene
"""


#: What a command that only reads says when it cannot finish. Money and worlds
#: have their own handlers below; these five neither spend nor write.
READ_ONLY_FAILURE: Mapping[str, Reason] = MappingProxyType({
    "manifest": Reason.MANIFEST_UNAVAILABLE,
    "probe": Reason.VENUE_UNREACHABLE,
    "report": Reason.EVIDENCE_UNREADABLE,
    "postmortem": Reason.EVIDENCE_UNREADABLE,
    "versions": Reason.EVIDENCE_UNREADABLE,
})


def _origin(exc: BaseException) -> str | None:
    """Name the exception's class and the factory module that raised it, and nothing else.

    A class name and a module path are written by this repository, never by a
    provider, a venue or a key; an exception *message* can carry all three, so
    no message is read here. Returns ``None`` when the raise never passed
    through ``factorylab``.
    """
    module, traceback = None, exc.__traceback__
    while traceback is not None:
        name = traceback.tb_frame.f_globals.get("__name__")
        if isinstance(name, str) and name.split(".")[0] == "factorylab":
            module = name
        traceback = traceback.tb_next
    return f"{type(exc).__name__} in {module}" if module else None


def refuse(command: str, reason: Reason, cause: BaseException | None = None) -> None:
    """Print exactly one reason code for one command, and leave it where a supervisor reads it.

    The commands that create or end a world add a second line naming the failing
    subsystem — an exception class and the module it was raised in — because
    ``adapter_unavailable`` alone cost two audit seats a source read each before
    they could tell which of two faults had refused the launch. Every other
    command still prints exactly one line. The first line and the recorded
    reason never change, so a supervisor classifies on one code either way.
    """
    print(f"factorylab {command}: {reason.value}", file=sys.stderr)
    if cause is not None and (origin := _origin(cause)) is not None:
        print(f"factorylab {command}: raised {origin}", file=sys.stderr)
    record(reason)


class KeyFileModeError(ValueError):
    """A credential's metadata is unsafe; its contents have not been read."""


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
        if (keyfile.exists() or keyfile.is_symlink()) and var not in os.environ:
            if filename in {"reserve.key", "hyperliquid.key"}:
                import stat

                info = keyfile.lstat()
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                        or stat.S_IMODE(info.st_mode) not in (0o400, 0o600)):
                    raise KeyFileModeError(
                        f"{filename} must be an owned regular file with mode 0400 or 0600"
                    )
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
    charter = json.loads(m.canonical_json())["charter"]
    prices = dict(m.charter_prices)
    for card in charter["cards"]:
        if card["id"] in prices:
            card["lambda"] = prices[card["id"]]
    out["charter"] = charter
    out["venue_validation"] = m.validate_venue_metadata()
    print(json.dumps(out))
    return 1 if out["venue_validation"]["status"] == "invalid" else 0


def _cmd_probe(args: argparse.Namespace) -> int:
    if args.provider == "x402":
        from factorylab.kernel.money import nonnegative_usd_micro
        from factorylab.world.market import X402Provider, seller_root
        from factorylab.world.models import ModelRequest
        from factorylab.world.x402 import BASE_RPC

        if not args.seller or not args.model:
            refuse("probe", Reason.ARGUMENTS_INCOMPLETE)
            return ARGUMENT_EXIT
        model_id = f"x402:{seller_root(args.seller)}#{args.model}"
        provider = X402Provider(rpc=args.rpc or BASE_RPC)
        req = ModelRequest(
            model_id,
            "Reply briefly.",
            ({"role": "user", "content": "Reply with OK."},),
            args.max_tokens,
        )
        quote = provider.quote(req)
        if quote.amount_micro > nonnegative_usd_micro(args.max_cost_usd, rounding="floor"):
            refuse("probe", Reason.QUOTE_ABOVE_CAP)
            return ARGUMENT_EXIT
        provider.register(model_id, quote.amount_micro)
        response = provider.complete(req, quoted=quote)
        settlement = response.raw.get("settlement") or {}
        # A reasoning seller spends the budget on reasoning and returns no content:
        # the payment settled, so the probe reports it, but it answered nothing.
        answered = bool(response.text.strip())
        print(
            json.dumps(
                {
                    "provider": "x402",
                    "model": model_id,
                    "text": response.text,
                    "max_tokens": args.max_tokens,
                    "stop_reason": response.stop_reason,
                    "answered": answered,
                    "cost_micro": response.cost_micro,
                    "cost_source": response.raw["cost_source"],
                    "quote": response.raw["quote"],
                    "settlement": settlement,
                    "settlement_reference": settlement.get("transaction"),
                },
                default=str,
            )
        )
        if not answered:
            # The seller answered nothing and was paid anyway.
            refuse("probe", Reason.EMPTY_COMPLETION)
            return 1
        return 0
    if args.provider == "venice":
        from factorylab.world.models import ModelRequest
        from factorylab.world.venice import VeniceProvider
        from factorylab.world.x402 import VENICE_URL

        if not args.model or not args.model.startswith("venice:"):
            refuse("probe", Reason.ARGUMENTS_INCOMPLETE)
            return ARGUMENT_EXIT
        provider = VeniceProvider(
            base_url=args.base_url or VENICE_URL,
            reasoning_config={args.model: {"enabled": False}},
        )
        response = provider.complete(
            ModelRequest(
                args.model,
                "Reply briefly.",
                ({"role": "user", "content": "Reply with OK."},),
                max_tokens=args.max_tokens,
            )
        )
        print(
            json.dumps(
                {
                    "provider": "venice",
                    "model": response.model_id,
                    "text": response.text,
                    "input_tokens": response.input_tokens,
                    "output_tokens": response.output_tokens,
                    "cost_micro": response.cost_micro,
                    "cost_source": response.raw["cost_source"],
                    "request_id": response.raw.get("request_id"),
                }
            )
        )
        return 0
    if not args.world:
        refuse("probe", Reason.ARGUMENTS_INCOMPLETE)
        return ARGUMENT_EXIT
    from factorylab.world.probe import probe_hyperliquid

    m = load_manifest(args.world)
    if m.exchange.kind != "hyperliquid":
        refuse("probe", Reason.NO_LIVE_VENUE)
        return ARGUMENT_EXIT
    out = probe_hyperliquid(mainnet=m.exchange.mainnet, coins=m.exchange.coins)
    print(json.dumps(out, indent=2))
    return 0


def _cmd_market(args: argparse.Namespace) -> int:
    """Public discovery prints compact sellers without loading credentials or paying."""
    from factorylab.world.market import DISCOVERY_URL, discover

    print(
        json.dumps(
            discover(
                url_substring=args.url_substring,
                query=args.query,
                limit=args.limit,
                discovery_url=args.discovery_url or DISCOVERY_URL,
            ),
            indent=2,
            default=str,
        )
    )
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
            refuse("reserve init", Reason.RESERVE_KEY_EXISTS)
            return ARGUMENT_EXIT
        with os.fdopen(fd, "w") as stream:
            account = Account.create(os.urandom(32))
            os.fchmod(stream.fileno(), 0o600)
            stream.write("0x" + account.key.hex() + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        print(account.address)
        return 0
    if args.reserve_cmd == "topup":
        from factorylab.runtime.treasury_cli import world_ledger_exists

        if world_ledger_exists(Path.cwd(), getattr(args, "ledger", None)):
            refuse("reserve topup", Reason.WORLD_EXISTS)
            return ARGUMENT_EXIT
        try:
            amount = Decimal(args.usd)
            if not amount.is_finite() or amount != Decimal("5"):
                raise ValueError
        except (InvalidOperation, ValueError):
            refuse("reserve topup", Reason.TOPUP_AMOUNT_REFUSED)
            return ARGUMENT_EXIT
    client = X402Client(base_url=args.base_url or VENICE_URL, rpc=args.rpc or BASE_RPC)
    if args.reserve_cmd == "status":
        usdc, eth, venice = client.usdc_balance(), client.eth_balance(), client.venice_balance()
        print(
            json.dumps(
                {
                    "address": client.address,
                    "network": "eip155:8453",
                    "usdc_micro": usdc,
                    "usdc_usd": format(Decimal(usdc) / 1_000_000, ".6f"),
                    "eth_wei": eth,
                    "eth": format(Decimal(eth) / 10**18, ".18f"),
                    "venice_balance_micro": venice,
                    "venice_balance_usd": format(Decimal(venice) / 1_000_000, ".6f"),
                    "topup_5_affordable": usdc >= TOP_UP_MICRO,
                }
            )
        )
        return 0
    settlement = client.top_up(TOP_UP_MICRO)
    # Preserve the reference even if the subsequent balance read fails.
    print(json.dumps({"address": client.address, "settlement": settlement}, default=str))
    balance = client.venice_balance()
    print(
        json.dumps(
            {
                "venice_balance_micro": balance,
                "venice_balance_usd": format(Decimal(balance) / 1_000_000, ".6f"),
            }
        )
    )
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    from factorylab.runtime.loop import run_world

    m = load_manifest(args.world)
    if args.tick_interval:
        from factorylab.runtime.worlds import duration_ns

        if duration_ns(args.tick_interval) != m.tick_interval_ns:
            refuse("run", Reason.TICK_OVERRIDE_REFUSED)
            return ARGUMENT_EXIT
    events, clock_source = args.events, None
    if args.duration:
        import time

        from factorylab.runtime.worlds import duration_ns

        span_ns = duration_ns(args.duration)
        events = max(1, span_ns // m.tick_interval_ns)
        if m.exchange.kind != "fake":
            # A live tick lasts as long as its work, not as long as the declared
            # interval, so an event count is not a wall-clock length. The clock
            # keeps the budget as a ceiling and stops at the first tick after the
            # deadline. A simulated world advances by the interval, so its count
            # already is its duration.
            from factorylab.runtime.live import LiveClock

            clock_source = LiveClock(m.tick_interval_ns, events,
                                     deadline_ns=time.time_ns() + span_ns)
    summary = run_world(
        m,
        events=events,
        seed=args.seed,
        initial_balance_micro=args.initial_balance,
        ledger_path=args.ledger,
        drip=not args.no_drip,
        kill_at_end=args.kill_at_end,
        clock_source=clock_source,
    )
    print(json.dumps(summary, indent=2, default=str))
    return 0


def _cmd_order_status(args: argparse.Namespace) -> int:
    """Query original client identities without starting a world or submitting any order."""
    from dataclasses import asdict

    from factorylab.world.exchange import live_exchange

    manifest = load_manifest(args.world)
    if manifest.exchange.kind != "hyperliquid":
        raise ValueError("order-status requires a live venue manifest")
    exchange = live_exchange(manifest.exchange)
    orders = {identity: asdict(exchange.lookup(identity)) for identity in args.client_id}
    print(json.dumps({"world": manifest.name, "read_only": True,
                      "orders": orders}, indent=2, default=str))
    return 0


def _cmd_kill(args: argparse.Namespace) -> int:
    """End a living world now, finally, and release its seal. The operator's one control.

    Takes the writer lock first, so a running process cannot be killed underneath
    itself: stop the unit, then kill. Records ``explicit_kill:operator`` through
    ``Termination``, which is the only authority that may publish ``Terminated``
    and the only path that releases the ledger key. Takes no argument that could
    steer a world: a world is created once and ended once, and nothing in
    between is the experimenter's to say.
    """
    from factorylab.kernel.events import Bus
    from factorylab.kernel.ledger import Ledger, LedgerBusyError, LedgerIntegrityError, LedgerLock
    from factorylab.kernel.termination import Termination

    try:
        with LedgerLock(args.ledger):
            manifest = load_manifest(args.world)
            ledger = Ledger.reopen(args.ledger, manifest=json.loads(manifest.canonical_json()))
            termination = Termination(ledger=ledger, bus=Bus(ledger))
            termination.kill("explicit_kill:operator")
            print(json.dumps({
                "world": manifest.name,
                "terminated": True,
                "termination_reason": termination.reason,
                "seal_key_released": ledger.seal_key_released(),
            }))
        return TERMINATED_EXIT
    except LedgerBusyError:
        refuse("kill", Reason.LEDGER_BUSY)
        return LEDGER_BUSY_EXIT
    except LedgerIntegrityError as exc:
        if str(exc) == "cannot resume a terminated world":
            refuse("kill", Reason.TERMINATED)  # already final; killing again changes nothing
            return TERMINATED_EXIT
        refuse("kill", Reason.LEDGER_INTEGRITY, exc)
        return 1
    except Exception as exc:
        refuse("kill", Reason.ADAPTER_UNAVAILABLE, exc)
        return 1


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


def _cmd_resume(args: argparse.Namespace, *, load_keys: bool = False) -> int:
    """Acquire the writer lock before preflight, repair, credential loading or recovery."""
    from factorylab.kernel.ledger import LedgerBusyError, LedgerLock

    try:
        with LedgerLock(args.ledger) as lock:
            return _cmd_resume_locked(args, load_keys=load_keys, lock=lock)
    except LedgerBusyError:
        refuse("resume", Reason.LEDGER_BUSY)
        return LEDGER_BUSY_EXIT
    except KeyFileModeError:
        refuse("resume", Reason.CREDENTIAL_UNSAFE)
        return ARGUMENT_EXIT
    except Exception as exc:
        from factorylab.runtime.resume import resume_reason

        refuse("resume", resume_reason(exc))
        return 1


def _cmd_resume_locked(args: argparse.Namespace, *, load_keys: bool, lock) -> int:
    """Continue only the authenticated original manifest and saved event budget."""
    from factorylab.kernel.ledger import Ledger, LedgerIntegrityError
    from factorylab.runtime.resume import resume_reason, resume_world

    try:
        manifest = load_manifest(args.world)
        if load_keys:
            # Finality must win even if an unrelated provider credential has become invalid.
            Ledger.reopen(args.ledger, manifest=json.loads(manifest.canonical_json()))
            _load_dotenv()
        summary = resume_world(manifest, args.ledger, _lock=lock)
    except Exception as exc:
        if isinstance(exc, KeyFileModeError):
            refuse("resume", Reason.CREDENTIAL_UNSAFE)
            return ARGUMENT_EXIT
        if isinstance(exc, LedgerIntegrityError) and str(exc) == "cannot resume a terminated world":
            refuse("resume", Reason.TERMINATED)
            return TERMINATED_EXIT
        reason = resume_reason(exc)
        refuse("resume", reason)
        return NO_LAUNCH_EXIT if reason is Reason.NO_LAUNCH else 1
    print(json.dumps(summary, indent=2, default=str))
    return TERMINATED_EXIT if summary["terminated"] else 0


def _cmd_wake(args: argparse.Namespace) -> int:
    """Publish only the sealed wake; failed verification replaces stale data with unavailable."""
    from factorylab.runtime.wake import UNAVAILABLE, write_wake

    data = write_wake(args.ledger, args.out)
    if data["wallet_series"] == UNAVAILABLE:
        refuse("wake", Reason.WAKE_UNAVAILABLE)
        return 1
    return 0


def _cmd_versions(args: argparse.Namespace) -> int:
    """Behaviour-based versions, pathologies and early warnings over a dead world's diary.

    Thresholds come from the genesis manifest carried by Launch. Read-only;
    needs the released key like postmortem.
    """
    from factorylab.versioning.reader import read_diary
    from factorylab.versioning.report import render, summary

    report = summary(read_diary(args.ledger, args.key))
    print(json.dumps(report, default=str) if args.json else render(report))
    return 0


def _cmd_postmortem(args: argparse.Namespace) -> int:
    """Decrypt a dead world's diary with its released key file and print selected entries.

    Only meaningful after termination. Reading a live world's key file breaks the
    non-intervention covenant; this command does not check that for you.
    """
    from cryptography.fernet import Fernet

    with open(args.key, "rb") as stream:
        key = stream.read().strip()
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


def _cmd_treasury(args: argparse.Namespace) -> int:
    from factorylab.runtime.treasury_cli import command

    return command(args)


def build_parser() -> argparse.ArgumentParser:
    """Build the whole command surface, with help on every command and argument."""
    p = argparse.ArgumentParser(
        prog="factorylab",
        description="Run, watch and read back one bounded world. A world is created once "
                    "from a manifest in worlds/, runs unattended until it dies, and is only "
                    "read afterwards from its own sealed diary.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="cmd", required=True, metavar="command")

    m = sub.add_parser("manifest", help="validate a world manifest and print its hash",
                       description="Parse a manifest, print its canonical hash and charter. "
                                   "Writes nothing.")
    m.add_argument("--world", required=True,
                   help="a manifest name in worlds/ (without .toml), or a path to one")
    m.set_defaults(func=_cmd_manifest)

    pr = sub.add_parser("probe", help="read live venue data for a world (network)",
                        description="Read one live price, model completion or seller quote. "
                                    "Touches the network and, for x402, the reserve's money.")
    probe_target = pr.add_mutually_exclusive_group(required=True)
    probe_target.add_argument("--world", help="probe this world's venue for mids and funding")
    probe_target.add_argument("--provider", choices=("venice", "x402"),
                              help="probe a compute rail instead of a venue")
    pr.add_argument("--model", help="venice:<id>, or the seller's model name for x402")
    pr.add_argument("--seller", help="x402 seller root or chat-completions URL")
    pr.add_argument("--max-cost-usd", default="0.10", help="x402 probe payment cap (default $0.10)")
    pr.add_argument("--max-tokens", type=int, default=256,
                    help="completion budget for a model probe (default 256); a reasoning "
                         "model spends a small budget on reasoning and answers nothing")
    pr.add_argument("--base-url", help="Venice API root, including /api/v1")
    pr.add_argument("--rpc", help="Base RPC override for the x402 reserve balance check")
    pr.set_defaults(func=_cmd_probe)

    market = sub.add_parser("market", help="discover public x402 sellers",
                            description="Read the public x402 discovery index. "
                                        "Loads no credentials and pays nothing.")
    market_sub = market.add_subparsers(dest="market_cmd", required=True, metavar="subcommand")
    discovery = market_sub.add_parser("discover", help="list sellers from the public index")
    discovery.add_argument("--url-substring", help="keep only resources whose URL contains this")
    discovery.add_argument("--query", help="free-text query passed to the index")
    discovery.add_argument("--limit", type=int, default=20, help="maximum sellers to print")
    discovery.add_argument("--discovery-url", help="override the public discovery index URL")
    discovery.set_defaults(func=_cmd_market)

    reserve = sub.add_parser("reserve", help="initialize, inspect or fund the Venice reserve",
                             description="The reserve is the wallet that buys Venice compute. "
                                         "Only init writes a key; only topup spends money.")
    reserve_sub = reserve.add_subparsers(dest="reserve_cmd", required=True, metavar="subcommand")
    init = reserve_sub.add_parser("init", help="create reserve.key once; print only its address")
    init.set_defaults(func=_cmd_reserve)
    for name, summary in (("status", "print reserve balances without spending"),
                          ("topup", "buy exactly $5 of Venice credit, before launch only")):
        command = reserve_sub.add_parser(name, help=summary)
        command.add_argument("--rpc", help="Base JSON-RPC URL")
        command.add_argument("--base-url", help="Venice API root, including /api/v1")
        if name == "topup":
            command.add_argument("--usd", required=True,
                                 help="exactly 5; the quote is verified and never rounded")
            command.add_argument("--ledger", help="world ledger path to check before seed funding")
        command.set_defaults(func=_cmd_reserve)

    r = sub.add_parser("run", help="run a world's event loop",
                       description="Create a world from its manifest and run it. A world is "
                                   "created once: an existing ledger is never reopened here.")
    r.add_argument("--world", required=True,
                   help="a manifest name in worlds/ (without .toml), or a path to one")
    r.add_argument("--events", type=int, default=200,
                   help="world events to run (default 200); internal events they cause are extra")
    r.add_argument("--seed", type=int, default=None,
                   help="seed for every sampled decision; omit for the manifest's own")
    r.add_argument("--initial-balance", type=int, default=None,
                   help="starting wallet balance in micro-USD, overriding the manifest")
    r.add_argument("--ledger", default=None,
                   help="ledger file path; its .key is written beside it. In-memory if omitted")
    r.add_argument("--no-drip", action="store_true", help="launch without the manifest's drip")
    r.add_argument("--duration", default=None,
                   help="wall-clock length like 30m; overrides --events. A live world stops "
                        "at the first tick after the deadline, never later than the ticks "
                        "the duration buys at the manifest interval")
    r.add_argument("--tick-interval", default=None,
                   help="must equal the manifest tick, e.g. 10s; any other value is refused")
    r.add_argument(
        "--kill-at-end",
        action="store_true",
        help="end a budgeted rehearsal world by explicit kill so its seal key is released",
    )
    r.set_defaults(func=_cmd_run)

    treasury = sub.add_parser(
        "treasury", help="journaled native CCTP treasury acceptance",
        description="Move USDC between the venue and the reserve over the real rails, "
                    "journaled and recoverable. Testnet only.")
    treasury_sub = treasury.add_subparsers(dest="treasury_command", required=True,
                                           metavar="subcommand")
    for name, summary in (("status", "print the journal's state and both rails' balances"),
                          ("transfer", "submit one transfer and journal every step"),
                          ("advance", "continue an interrupted transfer; never start another")):
        command = treasury_sub.add_parser(name, help=summary)
        command.add_argument("--ledger", default="runs/treasury-testnet.jsonl",
                             help="acceptance journal path")
        command.add_argument("--network", default="testnet", choices=("testnet",),
                             help="the only network these rails accept")
        if name == "transfer":
            command.add_argument("--direction", required=True, choices=("to_reserve", "to_venue"),
                                 help="which way the USDC moves")
            command.add_argument("--usd", required=True, help="amount in USD, as text")
        command.set_defaults(func=_cmd_treasury)

    kill = sub.add_parser("kill", help="end a living world now and release its seal",
                          description="The one control the experimenter keeps after launch. "
                                      "Records an explicit kill in the world's own diary, "
                                      "which is final and releases the ledger seal. Stop the "
                                      "unit first: this takes the writer lock. It steers "
                                      "nothing and takes no other argument.")
    kill.add_argument("--world", required=True, help="the world's original manifest name")
    kill.add_argument("--ledger", required=True, help="the living world's ledger")
    kill.set_defaults(func=_cmd_kill)

    resume = sub.add_parser("resume", help="continue a process-interrupted world",
                            description="Reopen an existing world after its process died, "
                                        "replay its authenticated tail and carry on. Refuses "
                                        "anything it cannot authenticate.")
    resume.add_argument("--world", required=True, help="the world's original manifest name")
    resume.add_argument("--ledger", required=True, help="ledger with an adjacent .key file")
    resume.set_defaults(func=_cmd_resume)

    wake = sub.add_parser("wake", help="publish sealed aggregates and account balances",
                          description="Write the one public page a living world has. "
                                      "Reads the diary; publishes only role totals and the "
                                      "world block the population already sees.")
    wake.add_argument("--ledger", required=True, help="the living world's ledger")
    wake.add_argument("--out", required=True, help="directory for wake.json and wake.html")
    wake.set_defaults(func=_cmd_wake)

    order_status = sub.add_parser("order-status", help="read venue status for original client ids")
    order_status.add_argument("--world", required=True,
                              help="original manifest, including namespace")
    order_status.add_argument("--client-id", action="append", required=True,
                              help="original intent id, repeatable; never resubmitted")
    order_status.set_defaults(func=_cmd_order_status)

    rp = sub.add_parser("report", help="print a run summary readably",
                        description="Format a summary JSON file that run or resume printed.")
    rp.add_argument("summary", help="path to a summary JSON file")
    rp.set_defaults(func=_cmd_report)

    pm = sub.add_parser("postmortem", help="decrypt a dead world's diary and print entries",
                        description="Read the interior of a world that has died. Its key is "
                                    "released only by termination; reading a living world's "
                                    "key breaks the non-intervention covenant.")
    pm.add_argument("ledger", help="the dead world's ledger")
    pm.add_argument("key", help="the released .key file beside it")
    pm.add_argument("--kinds", default=None, help="comma list, e.g. invocation,event:Registered")
    pm.add_argument("--limit", type=int, default=50, help="maximum entries to print")
    pm.add_argument("--width", type=int, default=400, help="characters of each entry to print")
    pm.set_defaults(func=_cmd_postmortem)

    vs = sub.add_parser("versions", help="version a dead world's diary by behaviour",
                        description="Segment a dead world into behavioural versions and name "
                                    "the pathologies it died of. Thresholds come from the "
                                    "genesis manifest, not from this command.")
    vs.add_argument("ledger", help="the dead world's ledger")
    vs.add_argument("key", help="the released .key file beside it")
    vs.add_argument("--json", action="store_true", help="print the full summary as JSON")
    vs.set_defaults(func=_cmd_versions)
    return p


def main(argv: list[str] | None = None) -> int:
    """Dispatch one command, translating every failure into one reason code."""
    args = build_parser().parse_args(argv)
    if args.cmd == "kill":
        # Finality must not depend on a credential: kill loads none and reaches
        # no network. It handles its own failures and names its own exit code.
        return int(args.func(args))
    if args.cmd == "run":
        from factorylab.cortex.sandbox import NoJail
        from factorylab.kernel.ledger import LedgerBusyError

        try:
            _load_dotenv()
            return int(args.func(args))
        except LedgerBusyError:
            refuse("run", Reason.LEDGER_BUSY)
            return LEDGER_BUSY_EXIT
        except KeyFileModeError:
            refuse("run", Reason.CREDENTIAL_UNSAFE)
            return ARGUMENT_EXIT
        except CredentialMissing:
            refuse("run", Reason.CREDENTIAL_MISSING)
            return ARGUMENT_EXIT
        except NoJail:
            refuse("run", Reason.JAIL_UNAVAILABLE)
            return ARGUMENT_EXIT
        except Exception as exc:
            # A world's interior — provider bodies, addresses, keys — is never
            # printed, not even while it is failing to be created. The class and
            # the module that raised are this repository's own words, so they do
            # name which subsystem refused.
            refuse("run", Reason.ADAPTER_UNAVAILABLE, exc)
            return 1
    if args.cmd == "treasury":
        try:
            _load_dotenv()
            return int(args.func(args))
        except Exception:
            refuse("treasury", Reason.TREASURY_UNAVAILABLE)
            print("Preserve the journal and reconcile its references. No new transfer should"
                  " be submitted to replace an uncertain one.", file=sys.stderr)
        return 1
    if args.cmd in {"wake", "resume"}:
        try:
            if args.cmd == "resume":
                return _cmd_resume(args, load_keys=True)
            _load_dotenv()
            return int(args.func(args))
        except Exception:
            refuse(args.cmd, Reason.ADAPTER_UNAVAILABLE)
            return 1
    is_reserve = args.cmd == "reserve"
    is_venice_probe = args.cmd == "probe" and args.provider == "venice"
    is_x402 = args.cmd == "market" or (args.cmd == "probe" and args.provider == "x402")
    if is_x402:
        try:
            if args.cmd == "probe":
                _load_dotenv()
            return int(args.func(args))
        except Exception:
            refuse(args.cmd, Reason.MARKET_UNAVAILABLE)
            print(PAYMENT_MAY_HAVE_SETTLED, file=sys.stderr)
            return 1
    if is_reserve or is_venice_probe:
        try:
            if is_reserve and args.reserve_cmd == "topup":
                from pathlib import Path

                from factorylab.runtime.treasury_cli import world_ledger_exists

                if world_ledger_exists(Path.cwd(), args.ledger):
                    refuse("reserve topup", Reason.WORLD_EXISTS)
                    return ARGUMENT_EXIT
            if not (is_reserve and args.reserve_cmd == "init"):
                _load_dotenv()
            return int(args.func(args))
        except Exception:
            # Loading, signing and transport exceptions can include secrets. No text escapes.
            refuse(args.cmd, Reason.RESERVE_UNAVAILABLE)
            print(PAYMENT_MAY_HAVE_SETTLED, file=sys.stderr)
            return 1
    try:
        _load_dotenv()
        return int(args.func(args))
    except KeyFileModeError:
        refuse(args.cmd, Reason.CREDENTIAL_UNSAFE)
        return ARGUMENT_EXIT
    except Exception:
        refuse(args.cmd, READ_ONLY_FAILURE.get(args.cmd, Reason.ADAPTER_UNAVAILABLE))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
