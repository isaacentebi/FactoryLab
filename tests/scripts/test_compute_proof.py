import base64
import json
from pathlib import Path
from urllib import request

import pytest

from factorylab.world import x402
from factorylab.world.market import X402Provider
from factorylab.world.x402 import BASE_NETWORK, BASE_USDC, HTTPResponse, X402Error
from scripts.compute_proof import Proof, ProofRefused

# Every signature here goes through the production chokepoint, with a real
# ReserveGuard in this test's temporary lock directory (tests/conftest.py).
pytestmark = pytest.mark.usefixtures("write_ahead")


TEST_KEY = "0x" + "11" * 32  # Public synthetic key; tests never open any key file.


def encoded(value):
    return base64.b64encode(json.dumps(value).encode()).decode()


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setenv("RESERVE_PRIVATE_KEY", TEST_KEY)
    monkeypatch.setenv("VENICE_API_KEY", "test-api-key-must-not-be-used")
    monkeypatch.setattr(request.OpenerDirector, "open", lambda *a, **k: pytest.fail("network"))
    monkeypatch.setattr(Path, "read_text", guarded_read_text)


ORIGINAL_READ_TEXT = Path.read_text


def guarded_read_text(path, *args, **kwargs):
    assert path.suffix != ".key" and path.name != ".env", "must not read credentials"
    return ORIGINAL_READ_TEXT(path, *args, **kwargs)


class FakeHTTP:
    def __init__(self, root, *, fail=None, initial_credit=0, quote_amount=1734):
        self.root = root
        self.calls = []
        self.usdc = 10_000_000
        self.credit = initial_credit
        self.fail = fail
        self.quote_amount = quote_amount
        self.echo = False
        self.finish_reason = "stop"
        self.text = "OK"

    @property
    def payments(self):
        return [call for call in self.calls if
                {"PAYMENT-SIGNATURE", "X-402-Payment"} & call[3].keys()]

    def __call__(self, method, url, payload, headers):
        self.calls.append((method, url, payload, headers))
        assert "Authorization" not in headers
        if payload and payload.get("method") == "eth_call":
            return HTTPResponse(200, {"id": 1, "result": hex(self.usdc)})
        if payload and payload.get("method") == "eth_chainId":
            return HTTPResponse(200, {"id": 1, "result": hex(8453)})
        if payload and payload.get("method") == "eth_blockNumber":
            return HTTPResponse(200, {"id": 1, "result": hex(1_000)})
        if "/x402/balance/" in url:
            assert "X-Sign-In-With-X" in headers
            if self.fail == "balance_after" and self.credit:
                raise TimeoutError(TEST_KEY)
            return HTTPResponse(200, {"data": {"balanceUsd": f"{self.credit // 1000000}."
                                             f"{self.credit % 1000000:06d}"}})
        topup = url.endswith("/x402/top-up")
        venice = "api.venice.ai" in url and not topup
        step = ("topup" if topup else "venice" if venice else
                "farouter" if "farouter.tech" in url else "aispace")
        if not topup:
            assert payload["max_tokens"] == 64
            assert payload["messages"][-1]["content"] == "Reply with the single word OK."
            if step in {"venice", "aispace"}:
                assert payload["venice_parameters"]["disable_thinking"] is True
        paid = bool({"PAYMENT-SIGNATURE", "X-402-Payment"} & headers.keys())
        if not paid and not venice:
            amount = 5_000_000 if topup else self.quote_amount
            if self.fail == "bad_quote" and topup:
                amount += 1
            return HTTPResponse(402, {"x402Version": 2, "accepts": [{
                "scheme": "exact", "network": BASE_NETWORK, "asset": BASE_USDC,
                "amount": str(amount), "payTo": "0x" + "22" * 20,
                "maxTimeoutSeconds": 60,
            }]})
        marker = self.root / "runs" / "proof" / (step + ".attempt")
        assert marker.exists()
        assert json.loads(marker.read_text())["charge_submitted"] is True
        if self.fail == step:
            raise TimeoutError(TEST_KEY + str(headers))
        if self.fail == "rejected" and topup:
            return HTTPResponse(402)
        receipt = {"success": True, "network": BASE_NETWORK, "transaction": "0x" + "ab" * 32}
        if paid:
            self.usdc -= 5_000_000 if topup else self.quote_amount
        if topup:
            self.credit += 5_000_000
            return HTTPResponse(200, {}, {"PAYMENT-RESPONSE": encoded(receipt)})
        if venice:
            self.credit -= 13
        text = self.text
        if self.echo:
            envelope = json.loads(base64.b64decode(next(iter(headers.values()))))
            signature = envelope.get("signature") or envelope["payload"]["signature"]
            text = TEST_KEY + " " + next(iter(headers.values())) + " " + signature
            receipt["signature"] = signature
        body = {"id": "completion-1", "model": payload["model"],
                "choices": [{"message": {"content": text}, "finish_reason": self.finish_reason}],
                "usage": {"prompt_tokens": 15, "completion_tokens": 1},
                "cost": {"usd": "0.000013"}}
        return HTTPResponse(200, body, {"PAYMENT-RESPONSE": encoded(receipt)} if paid else {})


def proof(tmp_path, fake, **kwargs):
    return Proof(tmp_path, transport=fake, load_credentials=lambda: None, **kwargs)


def result(tmp_path, step, *, dry=False):
    directory = tmp_path / "runs" / "proof"
    if dry:
        directory /= "dry-run"
    return json.loads((directory / (step + ".json")).read_text())


def test_full_proof_guards_before_signing_and_report_shape(tmp_path, monkeypatch, capsys):
    fake = FakeHTTP(tmp_path)
    original = x402.payment_header

    def guarded_sign(account, quote, *, guard=None, head=None):
        step = "topup" if quote.amount_micro == 5_000_000 else (
            "farouter" if len(fake.payments) == 1 else "aispace"
        )
        assert (tmp_path / "runs" / "proof" / (step + ".attempt")).exists()
        # Every proof payment is written ahead of its signature, under its own name.
        assert guard is not None and guard.origin == "compute_proof"
        return original(account, quote, guard=guard, head=head)

    monkeypatch.setattr(x402, "payment_header", guarded_sign)
    assert proof(tmp_path, fake).run() == 0
    assert len(fake.payments) == 3
    topup = result(tmp_path, "topup")
    assert topup["credited_difference_micro"] == topup["cost_micro"] == 5_000_000
    venice = result(tmp_path, "venice")
    assert venice["balance_debit_micro"] == venice["cost_micro"] == 13
    for name in ("venice", "farouter", "aispace"):
        entry = result(tmp_path, name)
        assert entry["real_answer"] and entry["finish_reason"] == "stop"
        assert entry["text"] == "OK" and entry["input_tokens"] == 15
        assert entry["latency_ms"] >= 0 and entry["started_at"] and entry["finished_at"]
    report = result(tmp_path, "report")
    assert report["known_total_spent_micro"] == 5_003_468
    assert report["inference_cost_micro"] == 3481
    assert not report["total_spent_uncertain"]
    assert all(v.startswith("PASS:") for v in report["verdicts"].values())
    markdown = (tmp_path / "docs/runs/compute-proof.md").read_text()
    assert all(f"## {name}" in markdown for name in (*report["steps"], "report"))
    assert topup["settlement_reference"] in markdown
    before = len(fake.calls)
    with pytest.raises(ProofRefused, match="status"):
        proof(tmp_path, fake).run()
    assert len(fake.calls) == before
    assert TEST_KEY[2:] not in capsys.readouterr().out + markdown


@pytest.mark.parametrize("name", ["status", "topup", "venice", "farouter", "aispace", "report"])
@pytest.mark.parametrize("suffix", [".attempt", ".json"])
def test_any_existing_evidence_refuses_before_loading_or_http(tmp_path, name, suffix):
    directory = tmp_path / "runs/proof"
    directory.mkdir(parents=True)
    (directory / (name + suffix)).touch()
    runner = Proof(tmp_path, transport=lambda *a: pytest.fail("HTTP"),
                   load_credentials=lambda: pytest.fail("credential load"))
    with pytest.raises(ProofRefused):
        runner.run()


def test_concurrent_status_claim_cannot_replace_marker(tmp_path, monkeypatch):
    fake = FakeHTTP(tmp_path)
    runner = proof(tmp_path, fake)
    original = runner.step

    def race(name, action):
        (tmp_path / "runs/proof/status.attempt").write_text("other process")
        return original(name, action)

    monkeypatch.setattr(runner, "step", race)
    with pytest.raises(ProofRefused):
        runner.run()
    assert not fake.calls
    assert (tmp_path / "runs/proof/status.attempt").read_text() == "other process"


def test_ceiling_stops_before_signing_seller_payment(tmp_path):
    fake = FakeHTTP(tmp_path, quote_amount=100_001)
    assert proof(tmp_path, fake).run() == 1
    assert len(fake.payments) == 1  # Venice top-up only.
    assert not (tmp_path / "runs/proof/farouter.attempt").exists()
    assert result(tmp_path, "farouter")["within_ceiling"] is False


def test_header_and_key_echoes_are_redacted_everywhere(tmp_path, capsys):
    fake = FakeHTTP(tmp_path)
    fake.echo = True
    assert proof(tmp_path, fake).run() == 0
    contents = capsys.readouterr().out
    contents += "".join(p.read_text() for p in tmp_path.rglob("*") if p.is_file())
    assert TEST_KEY[2:] not in contents
    assert "[REDACTED]" in contents
    for _, _, _, headers in fake.calls:
        for header in headers.values():
            assert header not in contents
            envelope = json.loads(base64.b64decode(header))
            signature = envelope.get("signature") or envelope["payload"]["signature"]
            assert signature not in contents


@pytest.mark.parametrize("key", ["model", "messages", "max_tokens", "stream"])
def test_extra_body_cannot_override_bounded_request(key):
    with pytest.raises(X402Error, match="override"):
        X402Provider(extra_body={key: "unexpected"})
