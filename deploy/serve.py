#!/usr/bin/env python3
"""Serve the world's registered services over x402 beside the wake.

Run as the ``factory`` user with the repository's own interpreter:

    /srv/factorylab/repo/.venv/bin/python deploy/serve.py \\
        --ledger /srv/factorylab/runs/funded.jsonl \\
        --spool /srv/factorylab/runs/funded.income.jsonl --port 8402

The server reads the sealed ledger the way the wake does (read-only, at a fixed
boundary, re-read every ``--refresh`` seconds) to learn which programs are for
sale and at what price, and reads the reserve address from the manifest the
ledger's genesis names. ``POST /service/<id>`` without a payment header returns
the 402 quote; with one, the payment is verified and settled through the
facilitator, a receipt is appended to the spool, the program runs in the jail
and its output is returned. The runtime, the ledger's only writer, books each
receipt as ``income.earned`` on its next tick (``FACTORYLAB_INCOME_SPOOL``).
No key is read here and nothing is signed: the buyer signs, the facilitator
submits, the reserve receives.
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from factorylab.world.seller import (  # noqa: E402
    IncomeSpool,
    Seller,
    default_runner,
    serve,
    services_from_items,
    spool_earn,
)


def load_catalogue(ledger_path: Path):
    """The current services and the manifest's reserve address, from one frozen read."""
    from factorylab.runtime.wake import _open_snapshot

    ledger, manifest = _open_snapshot(ledger_path)
    return services_from_items(ledger.items()), manifest.treasury.reserve_address


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ledger", required=True, help="the living world's ledger")
    parser.add_argument("--spool", required=True,
                        help="receipt spool the runtime reads (FACTORYLAB_INCOME_SPOOL)")
    parser.add_argument("--bind", default="127.0.0.1", help="listen address")
    parser.add_argument("--port", type=int, default=8402, help="listen port")
    parser.add_argument("--facilitator", default=None,
                        help="x402 facilitator base URL (default: FACTORYLAB_FACILITATOR_URL)")
    parser.add_argument("--refresh", type=float, default=60.0,
                        help="seconds between catalogue re-reads of the ledger")
    args = parser.parse_args(argv)
    ledger_path = Path(args.ledger)
    try:
        services, pay_to = load_catalogue(ledger_path)
    except Exception:
        print("factorylab serve: ledger_unavailable", file=sys.stderr)
        return 1
    if pay_to is None:
        print("factorylab serve: reserve_unconfigured", file=sys.stderr)
        return 2
    seller = Seller(services, pay_to=pay_to, runner=default_runner(),
                    earn=spool_earn(IncomeSpool(args.spool)), facilitator=args.facilitator)
    server = serve(seller, host=args.bind, port=args.port)

    def refresh() -> None:
        while True:
            time.sleep(max(1.0, args.refresh))
            try:
                seller.refresh(load_catalogue(ledger_path)[0])
            except Exception:
                pass  # the previous catalogue stands until the ledger reads again

    threading.Thread(target=refresh, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
