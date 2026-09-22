"""Keyless, read-only checks around the hybrid capital-loop rehearsal's real money.

Guarantees nothing here signs, holds or reads a signing key. Every chain read is a
public ``eth_call``, ``eth_getBlockByNumber`` or ``eth_getLogs`` against Base mainnet
through an ``EVM`` built with no account; a run's diary is read the way
``factorylab postmortem`` reads it, with the ledger's own sealing key file beside it
(``ledger.jsonl.key``), never the reserve's key.

Two questions are answered, both from the chain rather than a clock or a typed value
(essay II.IV: a reciprocal flow of capital is only bounded if its bound is observed):

* ``outstanding(run_dir)``: which Venice top-up authorizations a run ever journaled,
  current and superseded, and whether each can still settle; and which shadow sends
  it left unconfirmed. ``scripts/capital_loop_outstanding.py`` prints this.
* ``launch_check(manifest)``: before a capital-loop rehearsal starts, the real reserve
  may lose at most ``max_venice_total_usd`` before reaching its floor, and no earlier
  run left an authorization that could still settle.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from factorylab.world.evm import BASE, EVM
from factorylab.world.x402 import BASE_RPC, Transport, http_request, usdc_balance


class CapitalLoopRefused(RuntimeError):
    """A launch or read refused with a stable reason code and the numbers behind it."""

    def __init__(self, reason: str, detail: dict | None = None):
        super().__init__(reason)
        self.reason, self.detail = reason, detail or {}


def read_items(run_dir: str | Path) -> list[dict]:
    """Every readable diary item of a run, decrypted with the run's own ledger key file.

    An unterminated or unreadable last line (a crash mid-append) is skipped: an item
    that never finished writing never reached the rail either.
    """
    from cryptography.fernet import Fernet, InvalidToken

    path = Path(run_dir) / "ledger.jsonl"
    key_path = Path(str(path) + ".key")
    if not path.exists():
        raise CapitalLoopRefused("run_ledger_missing", {"run_dir": str(run_dir)})
    if not key_path.exists():
        raise CapitalLoopRefused("run_ledger_key_missing", {"run_dir": str(run_dir)})
    cipher = Fernet(key_path.read_bytes().strip())
    items = []
    with path.open("rb") as stream:
        next(stream, None)  # the header names the genesis, nothing else
        for line in stream:
            try:
                record = json.loads(line)
                item = record["item"]
                items.append(json.loads(cipher.decrypt(item.encode())) if isinstance(
                    item, str) else item)
            except (ValueError, KeyError, TypeError, InvalidToken):
                continue
    return items


def _state_step(state: dict) -> str | None:
    steps, index = state.get("steps") or (), state.get("index")
    return steps[index] if type(index) is int and 0 <= index < len(steps) else None


def journaled_references(items: list[dict]) -> tuple[list[dict], list[dict]]:
    """The run's top-up authorizations (one per nonce) and its unconfirmed shadow sends.

    A top-up reference counts once it was journaled toward a signature
    (``treasury.submitted`` or ``treasury.step_submitted``) or kept as superseded; one
    only prepared was never signed. A shadow send is pending when the last journaled
    state of its transfer is still submitted at the shadow step.
    """
    top_ups: dict[str, dict] = {}
    last: dict[str, dict] = {}
    for item in items:
        state = item.get("state")
        if not isinstance(state, dict) or not str(item.get("kind", "")).startswith("treasury."):
            continue
        last[state.get("id")] = state
        references = list((state.get("route_data") or {}).get("superseded_references") or ())
        if (item["kind"] in ("treasury.submitted", "treasury.step_submitted")
                and _state_step(state) == "venice_top_up" and state.get("reference")):
            references.append(state["reference"])
        for reference in references:
            nonce = (reference.get("authorization") or {}).get("nonce")
            if nonce:
                top_ups.setdefault(nonce, {"transfer_id": state.get("id"), **reference})
    shadows = [{"transfer_id": tid, "nonce": (s.get("reference") or {}).get("nonce"),
                "sink": (s.get("reference") or {}).get("destination")
                or (s.get("reference") or {}).get("sink"),
                "amount_micro": s.get("amount_micro")}
               for tid, s in last.items()
               if s.get("status") == "submitted" and _state_step(s) == "shadow_send"]
    return list(top_ups.values()), shadows


def keyless_base(*, transport: Transport = http_request, rpc: str | None = None) -> EVM:
    """Base mainnet with no account: it can read and can never sign."""
    return EVM(BASE, None, transport=transport, rpc=rpc)


def outstanding(run_dir: str | Path, *, reserve_address: str, base: EVM) -> dict[str, Any]:
    """Each journaled top-up's on-chain standing, and the run's pending shadow sends."""
    from factorylab.world.treasury_rails import authorization_status

    top_ups, shadows = journaled_references(read_items(run_dir))
    rows = []
    for reference in top_ups:
        row = {"transfer_id": reference.get("transfer_id"),
               "nonce": reference["authorization"]["nonce"],
               "valid_before": int(reference["authorization"]["validBefore"])}
        try:
            row.update(authorization_status(base, reserve_address, reference))
        except Exception as exc:  # noqa: BLE001 - an unread chain is reported, not guessed
            row["unreadable"] = type(exc).__name__
        rows.append(row)
    return {"run_dir": str(run_dir), "top_ups": rows, "shadow_sends": shadows}


def _unsettled(row: dict) -> bool:
    """An authorization that might still settle: live, or not readable at all."""
    return "unreadable" in row or bool(row.get("live"))


def launch_check(manifest: Any, *, previous_runs: tuple = (),
                 transport: Transport = http_request, rpc: str = BASE_RPC) -> dict:
    """Refuse a capital-loop launch the chain says could overspend; return the numbers.

    The floor is only a bound across runs if it is close to the balance: the reserve
    may lose at most ``max_venice_total_usd`` before reaching it, so
    ``balance - floor <= max_venice_total_usd`` is required, read keylessly now. And
    no earlier run's authorization may still be able to settle, since a crashed
    world's last authorization stays valid on chain for up to its quote's timeout.
    """
    treasury = manifest.treasury
    reserve = treasury.reserve_address
    balance = usdc_balance(reserve, rpc=rpc, transport=transport)
    floor, total = treasury.venice_reserve_floor_micro, treasury.max_venice_total_micro
    numbers = {"reserve_address": reserve, "reserve_usdc_micro": balance,
               "venice_reserve_floor_micro": floor, "max_venice_total_micro": total,
               "spendable_above_floor_micro": balance - floor}
    if balance - floor > total:
        raise CapitalLoopRefused("reserve_floor_leaves_more_than_the_total_cap", numbers)
    base = keyless_base(transport=transport)
    previous = []
    for run_dir in previous_runs:
        report = outstanding(run_dir, reserve_address=reserve, base=base)
        previous.append(report)
        live = [row for row in report["top_ups"] if _unsettled(row)]
        if live:
            raise CapitalLoopRefused("previous_run_authorization_may_still_settle", {
                **numbers, "run_dir": str(run_dir), "authorizations": live})
    return {**numbers, "previous_runs": previous}
