"""The public wake contains only authenticated aggregates and account statements."""

from __future__ import annotations

import hashlib
import html
import json
import os
import stat
import tempfile
import time
from decimal import Decimal
from pathlib import Path

from cryptography.fernet import InvalidToken

from factorylab.kernel.ledger import KeyStore, Ledger, LedgerIntegrityError, _canonical
from factorylab.runtime.worlds import WORLDS_DIR, load_manifest

VIEWS = (
    "wallet_series", "spend_by_capability", "invocations_by_assembly",
    "action_frequencies", "settlement_latency",
)
UNAVAILABLE = "unavailable"


class _Snapshot(Ledger):
    """A frozen, sealed ledger supports kernel aggregates even after termination.

    Ledger.reopen is a writer recovery API: it rejects final worlds and concurrent
    appends. This compatibility adapter hydrates its read state only. It never
    releases the public key or calls the recovery/item-export API. Keep the private
    field coupling here until the kernel offers a read-only snapshot constructor.
    """

    def __init__(self, path: Path, manifest) -> None:
        super().__init__(manifest=json.loads(manifest.canonical_json()))
        key_path = Path(str(path) + ".key")
        mode = key_path.lstat().st_mode
        if not stat.S_ISREG(mode) or stat.S_IMODE(mode) != 0o600:
            raise LedgerIntegrityError("ledger key unavailable")
        self._Ledger__keys = KeyStore(key_path.read_bytes().strip())
        self._Ledger__path = path
        tokens = self._tokens()  # One read; an incomplete final line is rejected.
        self._Ledger__path = None  # Every view verifies the same immutable bytes.
        self._Ledger__tokens = tokens
        if tokens:
            self._Ledger__head = json.loads(self._Ledger__keys._decrypt(tokens[-1]))["hash"]
        if not self.verify():
            raise LedgerIntegrityError("ledger verification failed")

    def append(self, entry: dict) -> int:
        """A wake reader cannot append evidence."""
        raise PermissionError("read-only wake")

    def timing(self, *, live: bool, now_ns: int) -> dict:
        """Only event timestamps escape; payloads, identities and kinds remain private."""
        if not self.verify():
            raise LedgerIntegrityError("ledger verification failed")
        first_tick, last, final = None, None, False
        for token in self._tokens():
            item = json.loads(self._Ledger__keys._decrypt(token))
            if item.get("kind") != "event":
                continue
            event = item["event"]
            last = event["ts_ns"]
            if event["kind"] == "Tick" and first_tick is None:
                first_tick = last
            final |= event["kind"] == "Terminated"
        start = first_tick if live else 0
        end = now_ns if live and not final else last
        uptime = max(0, end - start) if start is not None and end is not None else 0
        return {"uptime_ns": uptime, "last_event_time_ns": last}


def _open_snapshot(path: Path):
    # Match the public genesis to an installed manifest, never a summary or diary.
    with path.open("rb") as stream:
        header = json.loads(stream.readline())
    for manifest_path in sorted(WORLDS_DIR.glob("*.toml")):
        manifest = load_manifest(str(manifest_path))
        genesis = hashlib.sha256(_canonical(
            {"manifest": json.loads(manifest.canonical_json())},
        )).hexdigest()
        if header == {"format": 1, "genesis_hash": genesis}:
            return _Snapshot(path, manifest), manifest
    raise LedgerIntegrityError("manifest unavailable")


def _micro(value: Decimal) -> int:
    return int(value * 1_000_000)


def _realized(exchange) -> int | str:
    # The venue retains at most 10,000 fills. Never label a truncated sum all-time.
    start, count, total = 0, 0, Decimal(0)
    while True:
        rows = exchange._guarded(
            "wake fills", lambda start=start: exchange._info.user_fills_by_time(
                exchange._address, start,
            ),
        )
        count += len(rows)
        if count >= 10_000:
            return UNAVAILABLE
        if len(rows) < 2000:
            total += sum((Decimal(str(row["closedPnl"])) for row in rows), Decimal(0))
            return _micro(total)
        # Repeat the boundary millisecond so simultaneous fills cannot be lost.
        boundary = max(int(row["time"]) for row in rows)
        if boundary <= start:
            return UNAVAILABLE
        total += sum((Decimal(str(row["closedPnl"])) for row in rows
                      if int(row["time"]) < boundary), Decimal(0))
        start = boundary


def _venue(manifest) -> dict:
    from factorylab.world.exchange import HyperliquidExchange

    result = {"equity_micro": UNAVAILABLE, "positions": UNAVAILABLE,
              "realized_to_date_micro": UNAVAILABLE}
    try:
        exchange = HyperliquidExchange(
            mainnet=manifest.exchange.mainnet, coins=manifest.exchange.coins,
        )
        account = exchange.account()
        result.update(equity_micro=_micro(account.equity_usd), positions=[
            {"coin": p.coin, "size": str(p.size), "entry_px": str(p.entry_px)}
            for p in account.positions
        ])
        result["realized_to_date_micro"] = _realized(exchange)
    except Exception:
        pass  # Provider exceptions can contain credentials or response bodies.
    return result


def _reserve() -> dict:
    from factorylab.world.x402 import X402Client

    result = {"usdc_micro": UNAVAILABLE, "venice_micro": UNAVAILABLE}
    try:
        client = X402Client()
    except Exception:
        return result
    readers = (("usdc_micro", client.usdc_balance), ("venice_micro", client.venice_balance))
    for field, read in readers:
        try:
            result[field] = read()
        except Exception:
            pass
    return result


def collect_wake(path: str | Path, *, now_ns: int | None = None, sleep=time.sleep) -> dict:
    """Exactly the allowlist escapes; a failed chain is retried once, never partially shown."""
    result = dict.fromkeys((*VIEWS, "world", "manifest_hash", "uptime_ns", "last_event_time_ns"),
                           UNAVAILABLE)
    manifest = None
    for attempt in range(2):
        try:
            ledger, candidate = _open_snapshot(Path(path))
            aggregates = {view: ledger.aggregate(view) for view in VIEWS}
            timing = ledger.timing(live=candidate.exchange.kind != "fake",
                                   now_ns=time.time_ns() if now_ns is None else now_ns)
            result.update(aggregates, **timing, world=candidate.name,
                          manifest_hash=candidate.manifest_hash())
            manifest = candidate
            break
        except (LedgerIntegrityError, InvalidToken, OSError, ValueError, KeyError, TypeError):
            if attempt == 0:
                sleep(0.1)
    if os.environ.get("HL_PRIVATE_KEY"):
        result["venue"] = _venue(manifest) if manifest else {
            "equity_micro": UNAVAILABLE, "positions": UNAVAILABLE,
            "realized_to_date_micro": UNAVAILABLE,
        }
    if os.environ.get("RESERVE_PRIVATE_KEY"):
        result["reserve"] = _reserve()
    return result


def _chart(wallet) -> str:
    if not isinstance(wallet, dict) or not wallet["series"]:
        return "<p>unavailable</p>"
    series = wallet["series"]
    low, high = min(p["balance"] for p in series), max(p["balance"] for p in series)
    first, last = series[0]["ts"], series[-1]["ts"]
    points = " ".join(
        f'{20 + (p["ts"] - first) * 760 // max(1, last - first)},'
        f'{180 - (p["balance"] - low) * 150 // max(1, high - low)}' for p in series
    )
    return (
        '<svg viewBox="0 0 800 200" role="img" aria-label="Wallet balance in micro-USD">'
        f'<title>Wallet balance: {low} to {high} micro-USD</title>'
        f'<polyline points="{points}" fill="none" stroke="currentColor" stroke-width="3"/>'
        f'</svg><p>{low} – {high} micro-USD</p>'
    )


def render_wake(data: dict) -> str:
    """The page is self-contained, script-free and escapes every dynamic text value."""
    sections = []
    order = (
        "world", "manifest_hash", "uptime_ns", "last_event_time_ns", "venue", "reserve", *VIEWS,
    )
    for field in order:
        if field not in data:
            continue
        value = data[field]
        chart = _chart(value) if field == "wallet_series" else ""
        body = '<pre>' + html.escape(json.dumps(value, indent=2, ensure_ascii=False)) + '</pre>'
        if field == "wallet_series":
            body = '<details><summary>Balance series</summary>' + body + '</details>'
        sections.append(f'<section><h2>{html.escape(field)}</h2>{chart}{body}</section>')
    return '''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>Factory wake</title><style>
:root{color-scheme:dark;font:16px/1.5 system-ui,sans-serif;background:#10151b;color:#e7edf4}
body{max-width:960px;margin:auto;padding:24px}h1{font-size:2rem}h2{font-size:1rem;color:#aedacb}
main{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,350px),1fr));gap:16px}
section{min-width:0;padding:16px;border:1px solid #34404d;border-radius:12px}
pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:.85rem}svg{width:100%;color:#91e0bf}
text{fill:currentColor;font-size:16px}@media(max-width:480px){body{padding:12px}}
</style></head><body><h1>Factory wake</h1><main>''' + "".join(sections) + '</main></body></html>'


def write_wake(path: str | Path, out: str | Path) -> dict:
    """Each public artifact replaces its predecessor atomically; no private bytes are written."""
    data = collect_wake(path)
    directory = Path(out)
    directory.mkdir(parents=True, exist_ok=True)
    for name, body in (("wake.json", json.dumps(data, indent=2) + "\n"),
                       ("wake.html", render_wake(data))):
        fd, temporary = tempfile.mkstemp(prefix=".wake-", dir=directory)
        try:
            with os.fdopen(fd, "w") as stream:
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
                os.fchmod(stream.fileno(), 0o644)
            os.replace(temporary, directory / name)
        finally:
            Path(temporary).unlink(missing_ok=True)
    return data
