import json
from dataclasses import dataclass, field

import pytest

from factorylab.cortex.assembly import Assembly, AssemblySpec, _parse_json_object
from factorylab.cortex.request import Request, Return
from factorylab.cortex.sandbox import jail_available, run_python
from factorylab.world.metering import Meter, MeteredModel
from factorylab.world.models import FakeModel, ModelResponse, PriceTable, TokenPrice


@dataclass
class _Res:
    amount: int
    open: bool = True


@dataclass
class TinyWallet:
    balance: int
    reserved: int = 0
    log: list[tuple[str, int]] = field(default_factory=list)

    def reserve(self, amount, handle, reason):
        if amount > self.balance - self.reserved:
            raise RuntimeError("infeasible")
        self.reserved += amount
        self.log.append(("reserve", amount))
        return _Res(amount)

    def commit(self, r, actual):
        self.reserved -= r.amount
        self.balance -= actual
        r.open = False
        self.log.append(("commit", actual))

    def release(self, r):
        self.reserved -= r.amount
        r.open = False
        self.log.append(("release", r.amount))


def _req(handle="h1", ceiling=1_000_000, parent=None) -> Request:
    return Request(
        handle=handle,
        description="Decide whether to act on the latest mid.",
        inputs={"coin": "BTC", "mid": "60000"},
        capability_versions={"model:claude-opus-5": 1},
        outcome_schema={"type": "object", "properties": {"action": {"type": "string"}}},
        deadline_ns=10**12,
        cost_ceiling=ceiling,
        parent_handle=parent,
        completion_criterion="a JSON object with an action field",
        scoring_channel="fast",
        resource_liability="self",
    )


def _assembly(fake: FakeModel, wallet: TinyWallet, **spec_kw) -> Assembly:
    prices = PriceTable()
    prices.register("claude-opus-5", TokenPrice(5, 25))
    mm = MeteredModel(fake, prices, Meter(wallet))
    spec = AssemblySpec(id="seed-decider", version=1, model_id="claude-opus-5", **spec_kw)
    return Assembly(spec, mm)


def test_request_rejects_author_fields_and_bad_ceiling() -> None:
    with pytest.raises(ValueError):
        Request(
            "h", "d", {"author": "x"}, {}, {}, 1, 0, None, "c", "fast", "self"
        )
    with pytest.raises(ValueError):
        Request("h", "d", {}, {}, {}, 1, -1, None, "c", "fast", "self")
    text = _req().prompt_text()
    # Edition 3 C4: the decider sees its own handle, deadline, ceiling and liable
    # budget — an actor that cannot see its own ceiling cannot answer for it. The
    # scoring channel is still none of its business, and neither is its parent.
    assert "REQUEST" in text and "YOU" in text
    # R3-E renames the slot to §8's shape: the same four facts under ``request``.
    assert '"handle":"h1"' in text and "fast" not in text
    assert '"liable_budget":"self"' in text


def test_invoke_charges_wallet_exactly_and_parses_output() -> None:
    fake = FakeModel(
        script={"latest mid": json.dumps({"action": "hold"})},
        fixed_input_tokens=200,
        fixed_output_tokens=20,
    )
    w = TinyWallet(balance=10_000_000)
    a = _assembly(fake, w, max_tokens=100)
    ret = a.invoke(_req())
    assert isinstance(ret, Return) and ret.status == "ok"
    assert ret.outputs == {"action": "hold"}
    assert ret.cost == 200 * 5 + 20 * 25
    assert w.balance == 10_000_000 - ret.cost
    assert w.log[0][0] == "reserve" and w.log[1] == ("commit", ret.cost)
    assert ret.served_by == "claude-opus-5"


def test_invoke_fails_cleanly_when_request_ceiling_too_low() -> None:
    fake = FakeModel(fixed_input_tokens=1, fixed_output_tokens=1)
    w = TinyWallet(balance=10_000_000)
    a = _assembly(fake, w, max_tokens=1000)
    ret = a.invoke(_req(ceiling=10))
    assert ret.status == "failed" and ret.cost == 0 and w.log == []


def test_invoke_reports_infeasible_wallet_without_raising() -> None:
    fake = FakeModel(fixed_input_tokens=1, fixed_output_tokens=1)
    w = TinyWallet(balance=5)
    a = _assembly(fake, w, max_tokens=100)
    ret = a.invoke(_req())
    assert ret.status == "failed" and "infeasible" in ret.outputs["reason"] and w.balance == 5


def test_malformed_and_refused_still_return_with_cost() -> None:
    fake = FakeModel(default="not json at all", fixed_input_tokens=10, fixed_output_tokens=3)
    w = TinyWallet(balance=10_000_000)
    a = _assembly(fake, w, max_tokens=50)
    ret = a.invoke(_req())
    assert ret.status == "malformed" and ret.cost == 10 * 5 + 3 * 25

    class Refuser:
        name = "refuser"

        def complete(self, req):
            return ModelResponse(req.model_id, "", 7, 0, "refusal", refused=True)

    prices = PriceTable()
    prices.register("claude-opus-5", TokenPrice(5, 25))
    a2 = Assembly(
        AssemblySpec("s", 1, "claude-opus-5", max_tokens=10),
        MeteredModel(Refuser(), prices, Meter(w)),
    )
    ret2 = a2.invoke(_req(handle="h2"))
    assert ret2.status == "refused" and ret2.cost == 7 * 5


def test_children_are_built_through_factory_and_stripped_from_outputs() -> None:
    reply = {"action": "investigate", "requests": [{"description": "check funding", "inputs": {},
                                                    "target": "self", "outcome_schema": {}}]}
    fake = FakeModel(default=json.dumps(reply), fixed_input_tokens=1, fixed_output_tokens=1)
    w = TinyWallet(balance=10_000_000)
    a = _assembly(fake, w, max_tokens=10)

    def factory(parent: Request, item: dict, i: int) -> Request:
        return Request(
            handle=f"{parent.handle}.{i}",
            description=item["description"],
            inputs=item.get("inputs", {}),
            capability_versions=parent.capability_versions,
            outcome_schema=item.get("outcome_schema", {}),
            deadline_ns=parent.deadline_ns,
            cost_ceiling=parent.cost_ceiling // 2,
            parent_handle=parent.handle,
            completion_criterion=item.get("completion_criterion", "answer"),
            scoring_channel=parent.scoring_channel,
            resource_liability=parent.handle,
        )

    a.child_factory = factory
    ret = a.invoke(_req())
    assert ret.outputs == {"action": "investigate"}
    assert len(ret.children) == 1 and ret.children[0].parent_handle == "h1"
    assert ret.children[0].handle == "h1.0"


def test_handle_scoped_memory_only_flows_through_parent() -> None:
    fake = FakeModel(default='{"ok": true}', fixed_input_tokens=1, fixed_output_tokens=1)
    w = TinyWallet(balance=10_000_000)
    a = _assembly(fake, w, max_tokens=10, memory_policy="handle-scoped")
    a.invoke(_req(handle="p"))
    child = _req(handle="p.0", parent="p")
    mreq = a.build_model_request(child)
    assert len(mreq.messages) == 3  # prior user+assistant, then this request
    stranger = _req(handle="q")
    assert len(a.build_model_request(stranger).messages) == 1


def test_parse_json_tolerates_fences_and_prose() -> None:
    assert _parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert _parse_json_object('Sure: {"a": {"b": [1,2]}} done') == {"a": {"b": [1, 2]}}
    assert _parse_json_object("[1,2]") is None
    assert _parse_json_object("{broken") is None


def test_sandbox_runs_isolated_and_times_out() -> None:
    if not jail_available():
        pytest.skip("host cannot launch an OS jail; refusal is tested separately")
    r = run_python("import os,sys; print(sorted(os.environ)); print(sys.flags.isolated)")
    assert r.returncode == 0 and not r.timed_out
    env_line, isolated = r.stdout.strip().splitlines()
    assert isolated == "1"
    for secret in ("HOME", "ANTHROPIC_API_KEY", "HL_PRIVATE_KEY", "PYTHONPATH"):
        assert secret not in env_line
    r2 = run_python("import time; time.sleep(5)", timeout_s=0.5)
    assert r2.timed_out and r2.returncode == -1
    r3 = run_python("print(input())", stdin="hello")
    assert r3.stdout.strip() == "hello"


@pytest.mark.parametrize("prefix", ["", "Prose {broken, then ", "```json\n"])
def test_json_extraction_preserves_braces_and_escapes_in_strings(prefix):
    body = {"verdict": 0.5, "rationale": 'literal } and { and \"quoted\"',
            "nested": {"values": ["}", "{"]}}
    assert _parse_json_object(prefix + json.dumps(body) + " trailing") == body
