"""Round three, group F1: a probe that pays for an empty answer has failed.

T26 (seat 5 finding 7): ``probe --provider x402`` hard-coded ``max_tokens=32``.
A reasoning seller spends that on reasoning, ``content`` comes back empty, and
the probe printed a successful-looking result and charged the wallet. Nothing
here reaches a network or a key: the seller is a stub.
"""

import json
from types import SimpleNamespace

import pytest

from factorylab.runtime.cli import build_parser, main
from factorylab.world.models import ModelResponse

SELLER = "https://seller.example/v1/chat/completions"
SETTLEMENT = {"success": True, "transaction": "0x9b8efe0c"}


class Seller:
    """Answers with reasoning until the budget is large enough for content."""

    def __init__(self, needs_tokens: int) -> None:
        self.needs, self.requests = needs_tokens, []

    def quote(self, req):
        self.requests.append(req)
        return SimpleNamespace(amount_micro=1000, accepted={}, resource=None, extensions=None)

    def register(self, model_id, ceiling_micro):
        pass

    def complete(self, req, *, quoted=None):
        answered = req.max_tokens >= self.needs
        return ModelResponse(
            model_id=req.model_id,
            text="OK" if answered else "",
            input_tokens=12,
            output_tokens=req.max_tokens,
            stop_reason="stop" if answered else "length",
            raw={"cost_source": "settlement", "quote": {"amount_micro": 1000},
                 "settlement": SETTLEMENT},
            cost_micro=1000,
        )


@pytest.fixture
def seller(monkeypatch, tmp_path):
    # `probe` loads every key file in the working directory into the environment.
    # A test must not leave the architect's credentials in this process.
    monkeypatch.chdir(tmp_path)
    for name in ("HL_PRIVATE_KEY", "RESERVE_PRIVATE_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    made = Seller(needs_tokens=64)
    monkeypatch.setattr("factorylab.world.market.X402Provider", lambda **kw: made)
    return made


def _probe(argv):
    return main(["probe", "--provider", "x402", "--seller", SELLER, "--model", "minimax-m3",
                 *argv])


def test_the_probe_budget_is_large_enough_for_a_reasoning_model_to_answer(seller, capsys):
    code = _probe([])
    out = json.loads(capsys.readouterr().out)
    assert seller.requests[0].max_tokens >= 64
    assert out["answered"] and out["text"] == "OK" and code == 0


def test_an_empty_completion_that_stopped_on_length_is_a_failed_probe(seller, capsys):
    """The payment settled and is reported; the probe still failed."""
    code = _probe(["--max-tokens", "32"])
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    assert out["text"] == "" and out["stop_reason"] == "length" and out["answered"] is False
    assert out["settlement"] == SETTLEMENT and out["cost_micro"] == 1000
    assert code == 1
    assert captured.err.splitlines()[0] == "factorylab probe: adapter_unavailable"


def test_the_probe_budget_is_an_argument():
    parser = build_parser()
    probe = parser._subparsers._group_actions[0].choices["probe"]
    budget = next(a for a in probe._actions if "--max-tokens" in a.option_strings)
    assert budget.default >= 64 and budget.type is int
