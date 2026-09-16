"""A16: verdict and payoff are two numbers; closers are credited (seat 1, finding 5)."""

import json
from fractions import Fraction
from types import SimpleNamespace

import pytest

from factorylab.kernel.events import Event, EventKind
from factorylab.kernel.queue import SettleStatus
from factorylab.settlement.lots import LotTable
from factorylab.world.models import ModelResponse
from factorylab.world.scripted import ScriptedProvider
from tests.runtime.test_loop import (
    _consequence_decision,
    _consequence_judge,
    _consequence_produce,
    _consequence_runtime,
)


def _fill(table, owner, oid, *, size="1", px="100", buy=True, fee="0"):
    return table.order(oid, owner, size).fill(
        order_id=oid, coin="BTC", is_buy=buy, size=size, px=px, fee_usd=fee,
    )


def test_seat_1_abc_reproduction_credits_the_closer_and_zeroes_research():
    # A opens one BTC at 100, B closes it at 110, C supplies research without fills;
    # every handle costs 100 micro-USD and there are no fees.
    table = LotTable()
    for handle in ("A", "B", "C"):
        table = table.start(handle, 0).finish(handle, 100)
    table = _fill(table, "A", "open", px="100")
    table = _fill(table, "B", "close", px="110", buy=False)
    table = table.resolve(1, 200, {})
    a, b, c = (table.account(h).payoff for h in ("A", "B", "C"))
    # C6/F7: the lot's 10 is credited once, split 100:110 by entry and exit notional and
    # floored on each side; both parts clear the 100 micro-USD cost.
    assert a.y == 1 and a.net_micro == int(Fraction(10) * 100 / 210 * 1_000_000)  # 4_761_904
    assert b.y == 1 and b.net_micro == int(Fraction(10) * 110 / 210 * 1_000_000)  # 5_238_095
    assert a.net_micro + b.net_micro == 10_000_000 - 1  # once, less the two floors
    assert c.y == 0 and c.net_micro == 0
    assert table.account("B").closes == 1 and table.account("A").opened_lots == 1
    assert table.lots == ()


def test_each_side_is_net_of_its_own_costs_only():
    table = LotTable().start("opener", 0).finish("opener", 0)
    table = table.start("closer", 0).finish("closer", 0)
    table = _fill(table, "opener", "open", size="2", px="100", fee="2")
    table = table.funding("BTC", "4")
    table = _fill(table, "closer", "close", size="1", px="120", buy=False, fee="1")
    # P&L on the closed unit is 20, split 100:120 by entry and exit notional (C6/F7); the
    # opener's part is net of half its opening fee (1) and half the funding (2), the
    # closer's of its whole closing fee (1), and neither of the other's.
    opener_part, closer_part = Fraction(20) * 100 / 220, Fraction(20) * 120 / 220
    assert table.account("opener").realized_micro == (opener_part - 3) * 1_000_000
    assert table.account("closer").realized_micro == (closer_part - 1) * 1_000_000
    assert opener_part + closer_part == 20


class _Judge(ScriptedProvider):
    def __init__(self, verdict, payoff):
        super().__init__()
        self.reply = {"verdict": verdict, "payoff": payoff, "rationale": "t", "forecasts": []}

    def _evaluate(self, req, inputs):
        return dict(self.reply)


def test_research_judged_useful_earns_a_good_payoff_brier_without_verdict_penalty():
    runtime = _consequence_runtime(provider=_Judge(0.9, 0.1))
    about, event = _consequence_produce(runtime)  # opens and closes nothing: y = 0
    judge = _consequence_judge(runtime, event, "eval-a")
    assert runtime.queue.history(about)[0].score == 0.9  # quality is the verdict
    standing = runtime.standing.snapshot()["eval-a"]
    # payoff 0.1 against y = 0; the mean is now over the charter's weight on the claim
    assert standing["n"] == 1 and standing["mean_brier"] == pytest.approx(0.99)
    assert runtime.standing.skill("eval-a") > 0
    seal = next(i for i in runtime.ledger._recovery_items()
                if i["kind"] == "forecast.seal" and i["predicate_id"] == "return_paid_off")
    assert seal["q"] == 0.1 and seal["evaluator_id"] == "eval-a"
    assert runtime.queue.get(seal["handle"]).parent_handle == judge
    # The judge is told, in its own outcome inbox, addressed to the verdict it made.
    item = runtime.outcomes.get("eval-a", judge)
    assert item["outcome"]["judged_return_paid_off"] == 0
    assert item["outcome"]["your_payoff_brier"] == 0.99


def test_payoff_is_optional_and_the_old_single_number_path_is_gone():
    """Restated by R3-D: payoff is no longer mandatory.

    A16 pinned the privilege GPT-6's third reading tells us to remove (§7: "Remove the
    remaining mandatory payoff privilege"), by asserting that a verdict without a payoff
    number is malformed and settles zero. A judge with nothing to say about the kernel's
    consequence predicate is no longer forced to invent a number for it: the verdict
    stands, it settles the return it judged, it is committed as a normative claim under
    its own key, and no kernel forecast is sealed on the judge's behalf. What A16 was
    really pinning — the two numbers are two different things, and neither substitutes
    for the other — is asserted here and in the tests around it.
    """
    runtime = _consequence_runtime(provider=_Judge(0.9, None))
    del runtime.provider.target.reply["payoff"]
    about, event = _consequence_produce(runtime)
    judge = _consequence_judge(runtime, event, "eval-a")
    assert runtime.queue.get(judge).status is SettleStatus.PENDING  # awaiting its meta
    assert runtime.queue.get(about).status is SettleStatus.SETTLED
    assert runtime.queue.history(about)[0].score == 0.9  # the verdict, as always
    assert runtime.book.outstanding() == 0  # nothing sealed about return_paid_off
    committed = [i for i in runtime.ledger._recovery_items()
                 if i["kind"] == "verdict.committed_without_payoff"]
    assert committed and committed[0]["handle"] == judge and committed[0]["q"] == 0.9
    schema_required = None

    class Capture(ScriptedProvider):
        def complete(self, req):
            nonlocal schema_required
            text = req.messages[-1]["content"]
            schema_required = json.loads(text.split("OUTCOME SCHEMA\n")[1]
                                         .split("\n\nCOMPLETION")[0])["required"]
            return ModelResponse(req.model_id, json.dumps(
                {"verdict": 0.5, "payoff": 0.5, "rationale": "t", "forecasts": []}), 1, 1, "s")

    runtime = _consequence_runtime(provider=Capture())
    _, event = _consequence_produce(runtime)
    _consequence_judge(runtime, event, "eval-a")
    # Both fields are offered; neither is required, because an evaluation may also
    # conclude that there is nothing here to measure.
    assert schema_required == ["rationale"]


def test_scoring_block_and_return_contract_document_both_numbers():
    runtime = _consequence_runtime()
    world = runtime._world_block()
    assert "verdict" in world["a_return_may_include"]
    assert "payoff" in world["a_return_may_include"]
    assert "payoff" in world["reserved_return_fields"]
    scoring = world["scoring"]
    assert "verdict_and_payoff" in scoring and "payoff_standing" in scoring
    assert "return_paid_off" in scoring and "return_paid_off" in scoring["verdict_and_payoff"]
    assert "consequence_standing" not in scoring  # the old single-number formula is gone


RULE_WORDS = ("cost", "closer", "opener", "P&L", "fee", "funding", "marked", "exceeds",
              "opens and closes nothing", "pays off", "paid off")


def test_the_payoff_predicate_is_named_to_the_population_but_never_computed_for_it():
    """AGENTS.md: kernel physics is enforced in code, never stated to the population. The
    predicate id is launch-declared vocabulary; its rule is not."""
    texts = []

    class Capture(ScriptedProvider):
        def complete(self, req):
            # The request description alone: the stable world block leads the
            # prompt, and what it discloses is asserted below from the world block.
            texts.append(
                req.messages[-1]["content"].split("\n\nINPUTS\n")[0].split("REQUEST\n")[-1])
            return ModelResponse(req.model_id, json.dumps(
                {"action": "hold", "verdict": 0.5, "payoff": 0.5, "rationale": "t",
                 "forecasts": []}), 1, 1, "s")

    runtime = _consequence_runtime(provider=Capture())
    _consequence_produce(runtime, "antagonist-a", "exposure")  # the self-forecast offer
    _, event = _consequence_produce(runtime, "seed-decider")
    _consequence_judge(runtime, event, "eval-a")
    world = runtime._world_block()
    antagonist, producer, judge = texts
    named = (antagonist, judge, world["scoring"]["verdict_and_payoff"])
    assert all("return_paid_off" in text for text in named)
    for text in (*named, producer, world["scoring"]["return_paid_off"],
                 world["a_return_may_include"]["payoff"]):
        assert not any(word in text for word in RULE_WORDS), text


def test_standing_is_trained_by_every_settled_predicate_not_only_the_payoff():
    """Edition 3 (C3) removes A16's privilege: public predicates train standing too."""
    runtime = _consequence_runtime(provider=_Judge(0.5, 0.1))
    parent = _consequence_decision(runtime, "eval-a", "conformity")
    runtime._open_forecasts(parent, "eval-a", "unused", [
        {"predicate": "wallet_up", "params": {"horizon_events": 1}, "q": 1.0},
    ])
    runtime.n += 2
    runtime.balance_at.extend([runtime.wallet.balance] * 2)
    runtime._settle_due_forecasts()
    assert runtime.book.outstanding() == 0  # the optional forecast settled to its handle
    assert runtime.standing.snapshot()["eval-a"]["n"] == 1  # and trained standing with it
    about, event = _consequence_produce(runtime)
    _consequence_judge(runtime, event, "eval-a")
    assert runtime.standing.snapshot()["eval-a"]["n"] == 2
    assert runtime.standing.coverage("eval-a") == 1.0  # every forecast asked for, settled


def test_verdict_event_carries_payoff_and_meta_sees_it():
    runtime = _consequence_runtime(provider=_Judge(0.9, 0.1))
    _, event = _consequence_produce(runtime)
    _consequence_judge(runtime, event, "eval-a")
    verdict = next(e for e in runtime.internal if e.kind is EventKind.VERDICT)
    assert verdict.payload["verdict"] == 0.9 and verdict.payload["payoff"] == 0.1
    seen = {}

    class Meta(ScriptedProvider):
        def _meta(self, inputs):
            seen.update(inputs["verdict"])
            return {"conformity": 0.7, "rationale": "m"}

    runtime.provider.target = Meta()
    handle = _consequence_decision(runtime, "meta-a", "fast")
    runtime._meta_step(Event("v", EventKind.VERDICT, 0, dict(verdict.payload), "runtime"), handle,
                       SimpleNamespace(chosen="meta-a"), runtime.queue.get(handle).deadline_ns)
    assert seen["verdict"] == 0.9 and seen["payoff"] == 0.1
