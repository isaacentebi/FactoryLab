import base64
import json
import stat
from dataclasses import asdict
from io import BytesIO
from pathlib import Path
from urllib import error, request

import pytest
from eth_account import Account

from factorylab.kernel.events import Bus
from factorylab.kernel.ledger import Ledger
from factorylab.kernel.termination import Termination
from factorylab.kernel.wallet import Wallet
from factorylab.runtime.cli import _load_dotenv, main
from factorylab.world.metering import Meter, MeteredModel
from factorylab.world.models import ModelRequest, PriceTable, TokenPrice
from factorylab.world.venice import VeniceProvider
from factorylab.world.x402 import BASE_NETWORK, BASE_USDC

TEST_KEY = "0x" + "11" * 32  # Deliberately public test fixture, never a real wallet.


class Response(BytesIO):
    def __init__(self, body, headers=None):
        super().__init__(json.dumps(body).encode())
        self.status, self.headers = 200, headers or {}


@pytest.fixture(autouse=True)
def isolated_process(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for name in ("RESERVE_PRIVATE_KEY", "VENICE_API_KEY", "OPENROUTER_API_KEY", "HL_PRIVATE_KEY"):
        monkeypatch.delenv(name, raising=False)

    def deny(*args, **kwargs):
        pytest.fail("unexpected real HTTP request")

    monkeypatch.setattr(request.OpenerDirector, "open", deny)


@pytest.fixture
def wire(monkeypatch):
    pending, calls = [], []

    def fake_open(self, req, timeout):
        calls.append(req)
        assert timeout == 180  # world.x402.MODEL_HTTP_TIMEOUT_S
        response = pending.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(request.OpenerDirector, "open", fake_open)
    return pending, calls


@pytest.fixture
def keyfile(tmp_path):
    # Only newly created synthetic fixtures are read by CLI tests.
    path = tmp_path / "reserve.key"
    path.write_text(TEST_KEY + "\n")
    path.chmod(0o600)
    return path


def test_init_prints_only_address_creates_0600_and_never_overwrites(monkeypatch, capsys, tmp_path):
    account = Account.from_key(TEST_KEY)
    monkeypatch.setattr(Account, "create", lambda entropy: account)
    monkeypatch.setattr(
        "factorylab.runtime.cli._load_dotenv", lambda: pytest.fail("init must not read secrets")
    )
    assert main(["reserve", "init"]) == 0
    first = capsys.readouterr()
    assert first.out == account.address + "\n" and first.err == ""
    path = tmp_path / "reserve.key"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    before = path.stat()
    assert path.read_text().strip() == TEST_KEY
    assert main(["reserve", "init"]) == 2
    second = capsys.readouterr()
    assert second.err == "factorylab reserve init: reserve_key_exists\n" and not second.out
    assert path.stat().st_mtime_ns == before.st_mtime_ns
    assert TEST_KEY[2:] not in first.out + first.err + second.out + second.err


def test_init_refuses_symlink_without_reading_or_overwriting(tmp_path, capsys):
    target = tmp_path / "synthetic-target"
    target.write_text("untouched")
    (tmp_path / "reserve.key").symlink_to(target)
    assert main(["reserve", "init"]) == 2
    assert target.read_text() == "untouched"
    assert not capsys.readouterr().out


def test_key_files_take_precedence_over_the_environment_and_never_print(
    monkeypatch, keyfile, capsys
):
    """fix/venue-money #14: the key file wins; an exported key is in the process's
    initial environment block, which other processes can read."""
    import os

    from factorylab.cortex import sandbox

    _load_dotenv()
    assert os.environ["RESERVE_PRIVATE_KEY"] == TEST_KEY
    _load_dotenv()  # loading again is not a conflict
    assert capsys.readouterr() == ("", "")
    monkeypatch.setattr(sandbox, "jail_installed", lambda: False)
    monkeypatch.setenv("RESERVE_PRIVATE_KEY", "environment-loses")
    _load_dotenv()
    assert os.environ["RESERVE_PRIVATE_KEY"] == TEST_KEY
    captured = capsys.readouterr()
    assert captured.out == "" and "environment-loses" not in captured.err
    assert TEST_KEY[2:] not in captured.err


def test_insecure_key_permissions_fail_before_read(keyfile, monkeypatch, capsys):
    keyfile.chmod(0o644)
    monkeypatch.setattr(
        Path, "read_text", lambda *a, **kw: pytest.fail("must not read insecure key")
    )
    assert main(["reserve", "status"]) == 1
    assert TEST_KEY[2:] not in str(capsys.readouterr())


def topup_responses():
    quote = {
        "x402Version": 2,
        "accepts": [
            {
                "scheme": "exact",
                "network": BASE_NETWORK,
                "asset": BASE_USDC,
                "amount": "5000000",
                "payTo": "0x2670b922ef37c7df47158725c0cc407b5382293f",
                "maxTimeoutSeconds": 300,
                "extra": {"name": "USD Coin", "version": "2"},
            }
        ],
    }
    settlement = {
        "success": True,
        "network": BASE_NETWORK,
        "transaction": "0x" + "ab" * 32,
        "payer": Account.from_key(TEST_KEY).address,
        "note": TEST_KEY,
    }
    return [
        Response({"id": 1, "result": hex(5_500_000)}),
        error.HTTPError(
            "http://venice.fake/api/v1/x402/top-up",
            402,
            "Payment Required",
            {},
            BytesIO(json.dumps(quote).encode()),
        ),
        # The chain head the write-ahead record takes as the authorization's start_block.
        Response({"jsonrpc": "2.0", "id": 1, "result": hex(8453)}),
        Response({"jsonrpc": "2.0", "id": 1, "result": hex(1_000)}),
        Response(settlement),
        Response({"balanceUsd": "5"}),
    ]


def test_topup_cli_prints_settlement_then_rereads_balance(keyfile, wire, capsys):
    responses, calls = wire
    responses.extend(topup_responses())
    assert (
        main(
            [
                "reserve",
                "topup",
                "--usd",
                "5",
                "--rpc",
                "http://rpc.fake",
                "--base-url",
                "http://venice.fake/api/v1",
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    first, second = map(json.loads, captured.out.splitlines())
    assert first["settlement"]["transaction"] == "0x" + "ab" * 32
    assert second["venice_balance_micro"] == 5_000_000
    assert calls[0].full_url == "http://rpc.fake"
    assert len(calls) == 6 and calls[-1].full_url.endswith("/" + first["address"])
    assert [c.full_url for c in calls[2:4]] == ["http://rpc.fake", "http://rpc.fake"]
    headers = [{k.lower(): v for k, v in r.header_items()} for r in calls[1:2] + calls[4:]]
    assert len({h["x-sign-in-with-x"] for h in headers}) == 3
    assert "x-402-payment" not in headers[0] and "x-402-payment" in headers[1]
    payment = json.loads(base64.b64decode(headers[1]["x-402-payment"]))
    assert payment["payload"]["authorization"]["value"] == "5000000"
    assert TEST_KEY[2:] not in captured.out + captured.err


def test_topup_keeps_transaction_reference_if_balance_refresh_fails(keyfile, wire, capsys):
    responses, calls = wire
    responses.extend(topup_responses()[:-1] + [error.URLError(TEST_KEY)])
    assert main(["reserve", "topup", "--usd", "5"]) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out)["settlement"]["transaction"] == "0x" + "ab" * 32
    assert len(calls) == 6 and TEST_KEY[2:] not in captured.out + captured.err


@pytest.mark.parametrize("amount", ["4.999999", "5.000001", "10", "-5", "nan", "inf", "bad"])
def test_unapproved_cli_amount_never_contacts_http(keyfile, wire, capsys, amount):
    assert main(["reserve", "topup", "--usd", amount]) == 2
    assert capsys.readouterr().err == "factorylab reserve topup: topup_amount_refused\n"
    assert not wire[1]


def completion():
    return {
        "id": "test-request",
        "model": "test-flash",
        "cost": {"usd": "0.0000011"},
        "usage": {"prompt_tokens": 10, "completion_tokens": 1},
        "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
    }


def test_cli_failure_never_prints_transport_secret(keyfile, wire, capsys):
    wire[0].append(RuntimeError(TEST_KEY))
    assert main(["probe", "--provider", "venice", "--model", "venice:test-flash"]) == 1
    captured = capsys.readouterr()
    assert captured.err.startswith("factorylab probe: reserve_unavailable\n")
    assert TEST_KEY[2:] not in captured.out + captured.err


def test_cli_output_and_decrypted_metering_ledger_never_contain_private_key(
    monkeypatch,
    keyfile,
    wire,
    capsys,
    tmp_path,
):
    wire[0].extend(topup_responses())
    assert main(["reserve", "topup", "--usd", "5"]) == 0
    captured = capsys.readouterr()
    ledger = Ledger(tmp_path / "proof.jsonl")
    term = Termination(ledger=ledger, bus=Bus(ledger))
    wallet = Wallet(10_000, ledger=ledger)
    response = completion()
    response["id"] = TEST_KEY
    response["choices"][0]["message"]["content"] = TEST_KEY[2:]
    provider = VeniceProvider(transport=lambda *args: response)
    model = MeteredModel(
        provider, PriceTable({"venice:test-flash": TokenPrice(1, 5)}), Meter(wallet)
    )
    req = ModelRequest("venice:test-flash", "", (), 32)
    result = model.complete(req, handle="proof")
    assert result.cost == 2 and wallet.balance == 9998
    seq = ledger.append(
        {
            "kind": "venice-proof",
            "response": asdict(result.result),
            "stdout": captured.out,
            "stderr": captured.err,
        }
    )

    # Provider exceptions are safe even when an assembly records their string in a diary.
    def fail(*args):
        raise RuntimeError(TEST_KEY)

    provider._transport = fail
    try:
        model.complete(req, handle="failure")
    except Exception as exc:
        failure_seq = ledger.append({"kind": "provider-failure", "reason": str(exc)})
    term.kill("proof complete")
    assert ledger.verify()
    for index in range(failure_seq + 1):
        assert TEST_KEY[2:] not in json.dumps(ledger.decrypt_item(index))
    assert "[REDACTED]" in json.dumps(ledger.decrypt_item(seq))
    assert TEST_KEY[2:].encode() not in (tmp_path / "proof.jsonl").read_bytes()


def test_owner_read_only_key_does_not_trap_loading(keyfile, monkeypatch):
    import os

    keyfile.chmod(0o400)
    # Synthetic content only; production credentials are never inspected by tests.
    monkeypatch.setattr(Path, "read_text", lambda *_a, **_kw: TEST_KEY)
    _load_dotenv()
    assert os.environ["RESERVE_PRIVATE_KEY"] == TEST_KEY


def test_resume_reports_insecure_key_metadata_without_reading_it(
    tmp_path, monkeypatch, capsys, scripted_run,
):
    path = scripted_run("scripted", 0, None).copy_to(tmp_path / "synthetic")
    (tmp_path / "hyperliquid.key").touch(mode=0o644)
    monkeypatch.delenv("HL_PRIVATE_KEY", raising=False)
    monkeypatch.setattr(Path, "read_text", lambda *_a, **_kw: pytest.fail("must not read key"))
    assert main(["resume", "--world", "scripted", "--ledger", str(path)]) == 2
    captured = capsys.readouterr()
    assert captured.err == "factorylab resume: credential_unsafe\n"
    assert captured.out == ""


@pytest.mark.parametrize("mode", [0o644, 0o666, 0o604])
def test_world_readable_openrouter_key_is_refused_like_the_other_credentials(mode, tmp_path):
    from factorylab.runtime.cli import KeyFileModeError

    # Synthetic content only; production credentials are never inspected by tests.
    path = tmp_path / "openrouter.key"
    path.write_text("sk-fixture\n")
    path.chmod(mode)
    with pytest.raises(KeyFileModeError, match="openrouter.key"):
        _load_dotenv()


def test_owner_only_openrouter_key_still_loads(tmp_path, monkeypatch):
    import os

    path = tmp_path / "openrouter.key"
    path.write_text("sk-fixture\n")
    path.chmod(0o600)
    _load_dotenv()
    assert os.environ["OPENROUTER_API_KEY"] == "sk-fixture"
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)


def test_topup_is_written_ahead_under_the_reserve_lock_or_refused(keyfile, wire, capsys):
    # Every EIP-3009 signature passes x402.sign_transfer_authorization: the CLI's top-up
    # too. While a capital-loop run holds the reserve it signs and sends nothing; free,
    # its nonce is in the reserve's write-ahead record before the payment leaves.
    from factorylab.runtime import capital_loop

    reserve = Account.from_key(TEST_KEY).address
    record = capital_loop.default_lock_dir() / f"{reserve.lower()}.authorizations.jsonl"
    responses, calls = wire
    responses.extend(topup_responses())
    with capital_loop.ReserveLock(reserve):
        assert main(["reserve", "topup", "--usd", "5"]) != 0
    sent = [{k.lower(): v for k, v in r.header_items()} for r in calls]
    assert not any("x-402-payment" in h for h in sent)
    assert capital_loop.read_authorizations(record) == []
    capsys.readouterr()
    responses.clear()
    calls.clear()
    responses.extend(topup_responses())
    seen = []
    original = request.OpenerDirector.open

    def watching(self, req, timeout):
        headers = {k.lower(): v for k, v in req.header_items()}
        if "x-402-payment" in headers:
            seen.append([e["nonce"] for e in capital_loop.read_authorizations(record)])
        return original(self, req, timeout)

    request.OpenerDirector.open = watching
    try:
        assert main(["reserve", "topup", "--usd", "5"]) == 0
    finally:
        request.OpenerDirector.open = original
    payment = json.loads(base64.b64decode(
        {k.lower(): v for k, v in calls[4].header_items()}["x-402-payment"]))
    nonce = payment["payload"]["authorization"]["nonce"]
    assert seen == [[nonce]]  # recorded before it left
    assert [(e["nonce"], e["origin"]) for e in capital_loop.read_authorizations(record)] == [
        (nonce, "reserve_topup")]
