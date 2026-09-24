"""One guarded compute proof; only --dry-run is safe to execute during implementation.

Result files and durable .attempt markers are never automatically reset. On any
rerun, existing evidence refuses the command before HTTP or credential loading.
Dry-run evidence lives separately in runs/proof/dry-run and cannot block the paid
proof. Reconcile an interrupted payment on-chain before any manual recovery.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from factorylab.runtime.cli import _load_dotenv
from factorylab.world.market import X402Provider
from factorylab.world.models import ModelRequest, ModelResponse
from factorylab.world.venice import VeniceError, VeniceProvider
from factorylab.world.x402 import (
    BASE_NETWORK,
    TOP_UP_MICRO,
    HTTPResponse,
    Transport,
    X402Client,
    X402Error,
    _decode,
    _header,
    http_request,
    redact,
)

STEPS = ("status", "topup", "venice", "farouter", "aispace", "report")
VENICE_MODEL = "venice:z-ai-glm-5-3-flash"
FAROUTER_MODEL = "x402:https://farouter.tech#glm-5.3-flash"
AISPACE_MODEL = "x402:https://x402.aispace.bot/api#z-ai-glm-5-3-flash"
CEILING_MICRO = 100_000


class ProofRefused(Exception):
    """Existing evidence or a balance guard prevents a new attempt."""


def model_request(model: str) -> ModelRequest:
    """Every proof requests the same short answer with at most 64 output tokens."""
    return ModelRequest(
        model,
        "",
        (
            {
                "role": "user",
                "content": "Reply with the single word OK.",
            },
        ),
        max_tokens=64,
    )


def settlement_reference(value: Any) -> str | None:
    """Only a decoded receipt's named reference is treated as a settlement reference."""
    if isinstance(value, dict):
        for key in ("transaction", "transactionHash", "transaction_hash", "settlement_id"):
            if isinstance(value.get(key), str) and value[key]:
                return value[key]
        return settlement_reference(value.get("data"))
    return None


def sync_directory(path: Path) -> None:
    """The directory entry is durable before a payment can be signed."""
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_file(path: Path, content: str, *, exclusive: bool = False) -> None:
    """Exclusive markers cannot be replaced; subsequent evidence updates are atomic."""
    target = path if exclusive else path.with_name(path.name + ".tmp")
    with target.open("x" if exclusive else "w", encoding="utf-8") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    if not exclusive:
        os.replace(target, path)
    sync_directory(path.parent)


class Proof:
    """No step can pay again while its result or durable attempt marker exists."""

    def __init__(
        self,
        root: Path,
        *,
        dry_run: bool = False,
        transport: Transport | None = None,
        load_credentials: Callable[[], None] = _load_dotenv,
        continue_run: bool = False,
    ) -> None:
        self.root = root
        self.dry_run = dry_run
        self.continue_run = continue_run
        self.directory = root / "runs" / "proof"
        if dry_run:
            self.directory /= "dry-run"
        self.transport = transport or http_request
        self.load_credentials = load_credentials
        self.secrets: list[str] = []
        self.current: dict[str, Any] = {}
        self.charge_submitted = False
        self.client: X402Client | None = None

    def clean(self, value: Any) -> Any:
        """Keys, captured authentication headers and signed payload fields never reach disk."""
        if isinstance(value, dict):
            value = {
                k: "[REDACTED]"
                if str(k).lower().replace("_", "-")
                in {
                    "authorization",
                    "signature",
                    "payload",
                    "private-key",
                    "payment-signature",
                    "x-402-payment",
                    "x-sign-in-with-x",
                    "payment-response",
                    "x-payment-response",
                    "payment-required",
                }
                else self.clean(v)
                for k, v in value.items()
            }
        elif isinstance(value, (list, tuple)):
            value = [self.clean(v) for v in value]
        return redact(value, tuple(self.secrets))

    def json(self, value: Any) -> str:
        """Serialized evidence uses integer money and redacted metadata."""
        return json.dumps(self.clean(value), default=str, sort_keys=True)

    def checkpoint(self) -> None:
        """Receipts survive a later balance-refresh failure or process interruption."""
        write_file(self.directory / (self.current["step"] + ".attempt"), self.json(self.current))

    def wire(self, method: str, url: str, payload: dict | None, headers: dict) -> HTTPResponse:
        """Every potentially charged POST has durable evidence before it reaches HTTP."""
        for value in headers.values():
            self.secrets.append(value)
            try:
                envelope = _decode(value)
                signature = envelope.get("signature") or envelope.get("payload", {}).get(
                    "signature"
                )
                if signature:
                    self.secrets.append(signature)
                authorization = envelope.get("payload", {}).get("authorization")
                if authorization:
                    self.secrets.append(json.dumps(authorization))
            except (X402Error, AttributeError):
                pass
        payment = any(k.lower() in {"payment-signature", "x-402-payment"} for k in headers)
        credit = method == "POST" and self.current.get("step") == "venice"
        if payment or credit:
            if self.dry_run:
                raise ProofRefused("Dry run cannot submit a charge")
            self.charge_submitted = True
            self.current["charge_submitted"] = True
            self.checkpoint()
        response = self.transport(method, url, payload, headers)
        if payment or credit:
            self.current["http_status"] = response.status
            encoded = _header(response.headers, "payment-response", "x-payment-response")
            if encoded:
                self.secrets.append(encoded)
                try:
                    self.current["settlement"] = self.clean(_decode(encoded))
                except X402Error:
                    self.current["receipt_error"] = "Invalid settlement receipt"
            elif payment and self.current["step"] == "topup":
                self.current["settlement"] = self.clean(response.body)
            self.current["settlement_reference"] = settlement_reference(
                self.current.get("settlement"),
            )
            self.checkpoint()
        return response

    def step(self, name: str, action: Callable[[], dict]) -> bool:
        """A step is claimed exclusively before signing and never retried automatically."""
        result = self.directory / (name + ".json")
        marker = self.directory / (name + ".attempt")
        if result.exists() or marker.exists() or result.is_symlink() or marker.is_symlink():
            raise ProofRefused(f"Refusing {name}: result or attempt marker already exists")
        self.current = {
            "step": name,
            "dry_run": self.dry_run,
            "started_at": datetime.now(UTC).isoformat(),
            "status": "attempting",
        }
        self.charge_submitted = False
        try:
            write_file(marker, self.json(self.current), exclusive=True)
        except FileExistsError:
            raise ProofRefused(f"Refusing {name}: attempt marker already exists") from None
        started = time.monotonic_ns()
        try:
            self.current.update(action())
            self.current["status"] = "ok"
        except Exception as exc:
            # Exception messages can contain transport credentials; retain only their type.
            self.current.update(
                status="failed",
                error_type=type(exc).__name__,
                payment_outcome="unknown" if self.charge_submitted else "not_sent",
            )
        self.current["latency_ms"] = (time.monotonic_ns() - started) // 1_000_000
        self.current["finished_at"] = datetime.now(UTC).isoformat()
        write_file(result, self.json(self.current))
        if self.current["status"] == "failed" and not self.charge_submitted:
            marker.unlink()  # Definitive local/pre-submission non-payment only.
            sync_directory(self.directory)
        else:
            self.checkpoint()
        print(self.json(self.current), flush=True)
        return self.current["status"] == "ok"

    def status(self) -> dict:
        """Status authenticates the reserve through the existing loader without paying."""
        self.load_credentials()
        self.secrets.extend(
            os.environ.get(k, "")
            for k in (
                "RESERVE_PRIVATE_KEY",
                "VENICE_API_KEY",
                "OPENROUTER_API_KEY",
                "HL_PRIVATE_KEY",
            )
        )
        from factorylab.runtime.capital_loop import ReserveGuard

        # Recorded ahead under the reserve lock, or never signed.
        self.client = X402Client(transport=self.wire, guard=ReserveGuard("compute_proof"))
        return {
            "address": self.client.address,
            "network": BASE_NETWORK,
            "usdc_micro": self.client.usdc_balance(),
            "venice_balance_micro": self.client.venice_balance(),
        }

    def topup(self) -> dict:
        """Only the existing exact-$5 client may top up a wallet below $4.50 credit."""
        before = self.client.venice_balance()
        self.current["balance_before_micro"] = before
        if before >= 4_500_000:
            raise ProofRefused("Venice credit is already at least $4.50")
        self.current["authorized_amount_micro"] = TOP_UP_MICRO
        settlement = self.client.top_up(TOP_UP_MICRO)
        self.current.update(
            settlement=settlement,
            settlement_reference=settlement_reference(settlement),
            cost_micro=TOP_UP_MICRO,
        )
        self.checkpoint()
        after = self.client.venice_balance()
        return {"balance_after_micro": after, "credited_difference_micro": after - before}

    @staticmethod
    def completion(response: ModelResponse) -> dict:
        """A real answer requires nonempty text and the provider's stop finish reason."""
        return {
            "model": response.model_id,
            "text": response.text,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "finish_reason": response.stop_reason,
            "real_answer": isinstance(response.text, str)
            and bool(response.text.strip())
            and response.stop_reason == "stop",
            "cost_micro": response.cost_micro,
            "cost_source": response.raw.get("cost_source"),
            "request_id": response.raw.get("request_id"),
        }

    def venice(self) -> dict:
        """Venice uses reserve SIWE, never API-key billing, for one thinking-disabled call."""
        before = self.client.venice_balance()
        self.current["balance_before_micro"] = before

        def transport(method, path, payload):
            response = self.wire(
                method, self.client.base_url + path, payload, self.client.auth_headers(path)
            )
            if not 200 <= response.status < 300:
                raise VeniceError(response.status, "HTTP request failed")
            return response.body

        provider = VeniceProvider(
            transport=transport, reasoning_config={VENICE_MODEL: {"enabled": False}}
        )
        self.current.update(self.completion(provider.complete(model_request(VENICE_MODEL))))
        self.checkpoint()
        after = self.client.venice_balance()
        return {"balance_after_micro": after, "balance_debit_micro": before - after}

    def seller(self, model: str) -> dict:
        """The exact unpaid request body is reused once under a $0.10 payment ceiling."""
        extra = {"venice_parameters": {"disable_thinking": True}} if model == AISPACE_MODEL else {}
        provider = X402Provider(transport=self.wire, extra_body=extra,
                                guard=self.client.guard)
        req = model_request(model)
        quote = provider.quote(req)
        self.current.update(
            model=model,
            quote=asdict(quote),
            ceiling_micro=CEILING_MICRO,
            within_ceiling=quote.amount_micro <= CEILING_MICRO,
        )
        self.checkpoint()
        if self.dry_run:
            return {"payment_outcome": "not_sent"}
        provider.register(model, CEILING_MICRO)
        # Quote again inside complete(): re-wrapping the saved quote broke on sellers whose
        # extension blobs carry decimals (FarOuter, 12 September). The unpaid 402 is free.
        response = provider.complete(req)
        return {
            **self.completion(response),
            "settlement": response.raw.get("settlement"),
            "settlement_reference": settlement_reference(response.raw.get("settlement")),
        }

    def report(self) -> dict:
        """The report derives evidence from saved results, without another provider request."""
        results = {}
        for name in STEPS[:-1]:
            path = self.directory / (name + ".json")
            results[name] = json.loads(path.read_text()) if path.exists() else {"status": "not_run"}
        topup = results["topup"]
        venice = results["venice"]
        verdicts = {}
        venice_proven = (
            topup.get("status") == "ok"
            and topup.get("settlement_reference")
            and topup.get("credited_difference_micro", 0) > 0
            and venice.get("status") == "ok"
            and venice.get("real_answer")
            and venice.get("cost_source") == "reported"
            and venice.get("cost_micro", 0) > 0
            and venice.get("balance_debit_micro", 0) > 0
            and abs(venice["balance_debit_micro"] - venice["cost_micro"]) <= 1
        )
        verdicts["venice"] = (
            "PASS: wallet credit funded and consumed"
            if venice_proven
            else ("UNPROVEN: requires settled funding, credited balance and a billed real answer")
        )
        for name in ("farouter", "aispace"):
            entry = results[name]
            receipt = entry.get("settlement") or {}
            proven = (
                entry.get("status") == "ok"
                and entry.get("real_answer")
                and receipt.get("success") is True
                and entry.get("settlement_reference")
                and entry.get("cost_micro", 0) > 0
            )
            verdicts[name] = (
                "PASS: settled per-request real answer"
                if proven
                else ("UNPROVEN: requires a successful settlement reference and paid real answer")
            )
        cash_steps = ("topup", "farouter", "aispace")
        known_spent = sum(results[n].get("cost_micro", 0) or 0 for n in cash_steps)
        uncertain = any(
            r.get("charge_submitted")
            and (r.get("status") != "ok" or (n in cash_steps and not r.get("settlement_reference")))
            for n, r in results.items()
        )
        summary = {
            "known_total_spent_micro": known_spent,
            "total_spent_uncertain": uncertain,
            "inference_cost_micro": sum(
                results[n].get("cost_micro", 0) or 0 for n in ("venice", "farouter", "aispace")
            ),
            "verdicts": verdicts,
            "steps": results,
        }
        summary["proof_complete"] = all(v.startswith("PASS:") for v in verdicts.values())
        lines = [
            "# Compute proof",
            "",
            "All money is integer micro-USD; latencies are milliseconds.",
            "Cash spent counts the Venice top-up once; credit consumption is separate.",
            "Missing receipts or ambiguous submissions leave the live proof unproven.",
            "",
            f"Known total spent: {known_spent} micro-USD.",
            f"Total spent uncertain: {str(uncertain).lower()}.",
            f"Inference cost: {summary['inference_cost_micro']} micro-USD.",
            "",
        ]
        for name, entry in results.items():
            lines.extend([f"## {name}", "", "```json", self.json(entry), "```", ""])
        lines.extend(["## report", "", "Generated from the five step result files.", ""])
        lines.extend(f"- {name}: {verdict}" for name, verdict in verdicts.items())
        destination = self.root / "docs" / "runs" / "compute-proof.md"
        destination.parent.mkdir(parents=True, exist_ok=True)
        write_file(destination, "\n".join(lines) + "\n", exclusive=True)
        return {**summary, "path": "docs/runs/compute-proof.md"}

    def _done(self, name: str) -> bool:
        """A step counts as done with a result file whose status is ok (its attempt marker
        stays as evidence); a failed step's result is removed so it can run again."""
        result = self.directory / (name + ".json")
        if not result.exists():
            return False
        try:
            data = json.loads(result.read_text())
        except (OSError, ValueError):
            return False
        if data.get("status") == "ok":
            self.current = data
            return True
        result.unlink()
        return False

    def run(self) -> int:
        """Existing evidence refuses the whole run before HTTP, including concurrent runs,
        unless ``continue_run`` is set: then completed steps are kept, failed or unrun steps
        run, and the report is rewritten. An attempt marker without a result still refuses,
        because a payment may have settled."""
        names = ("status", "farouter", "aispace") if self.dry_run else STEPS
        report = self.root / "docs" / "runs" / "compute-proof.md"
        if self.continue_run and not self.dry_run:
            for name in names:
                marker = self.directory / (name + ".attempt")
                result = self.directory / (name + ".json")
                if marker.exists() and not result.exists():
                    raise ProofRefused(f"Refusing {name}: attempt marker without a result")
            if report.exists():
                report.unlink()
            (self.directory / "report.json").unlink(missing_ok=True)
            (self.directory / "report.attempt").unlink(missing_ok=True)
        else:
            for name in names:
                for suffix in (".json", ".attempt"):
                    path = self.directory / (name + suffix)
                    if path.exists() or path.is_symlink():
                        raise ProofRefused(
                            f"Refusing {name}: result or attempt marker already exists"
                        )
            if not self.dry_run and (report.exists() or report.is_symlink()):
                raise ProofRefused("Refusing report: docs/runs/compute-proof.md already exists")
        self.directory.mkdir(parents=True, exist_ok=True)
        sync_directory(self.directory.parent)
        sync_directory(self.root)
        # Credentials load once here, not inside the status step: a --continue run that
        # skips status must still sign (12 September: the seller steps ran keyless).
        self.load_credentials()

        def run_step(name: str, fn) -> bool:
            if self.continue_run and self._done(name):
                return True
            if self.continue_run:
                (self.directory / (name + ".attempt")).unlink(missing_ok=True)
            return self.step(name, fn)

        ok = run_step("status", self.status)
        if self.dry_run:
            # Public quotes remain useful even when reserve status cannot authenticate.
            for name, model in (("farouter", FAROUTER_MODEL), ("aispace", AISPACE_MODEL)):
                ok = self.step(name, lambda model=model: self.seller(model)) and ok
            return 0 if ok else 1
        if ok:
            ok = run_step("topup", self.topup)
        if ok:
            ok = run_step("venice", self.venice)
        if ok:
            for name, model in (("farouter", FAROUTER_MODEL), ("aispace", AISPACE_MODEL)):
                if not run_step(name, lambda model=model: self.seller(model)):
                    ok = False
                    break
        reported = self.step("report", self.report)
        return 0 if ok and reported and self.current.get("proof_complete") else 1


def main(argv: list[str] | None = None) -> int:
    """The default command is paid; --dry-run permits only status and unsigned quotes."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--continue",
        dest="continue_run",
        action="store_true",
        help="keep completed steps, rerun failed or unrun ones, rewrite the report",
    )
    args = parser.parse_args(argv)
    try:
        return Proof(Path.cwd(), dry_run=args.dry_run, continue_run=args.continue_run).run()
    except ProofRefused as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        print(
            f"Compute proof failed ({type(exc).__name__}); retain all attempt markers",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
