"""Edition 2, W10: uncertain bills settle from the provider's own balance.

Two live rehearsals (docs/audits/v5/rehearsal.md) billed every provider call that returned
no HTTP status at its reserved ceiling, 34 to 62 percent of all spend. Model calls are
serial, so the true cost of such a call is the provider's balance before it minus the
balance after. These tests run the scripted world with a provider whose balance drops by
what each call really cost, whether or not the call returned a bill, and no network.
"""

from dataclasses import replace

import pytest

from factorylab.runtime.loop import Runtime
from factorylab.runtime.resume import resume_runtime
from factorylab.runtime.worlds import load_manifest
from factorylab.world.exchange import FakeExchange
from factorylab.world.scripted import ScriptedProvider
from tests.runtime.test_connectors import ledger_items

MANIFEST = load_manifest("scripted")


class Died(BaseException):
    pass


class BalanceProvider:
    """The scripted provider with a balance: every call costs it the table price of its
    tokens; every ``drop_every``-th call is billed by the provider but the reply is lost.
    Not a ``ScriptedProvider`` subclass, so the runtime journals it as a live adapter."""

    name = "balance"

    def __init__(self, *, balance=50_000_000, drop_every=3, readable=True):
        self.balance, self.drop_every, self.readable = balance, drop_every, readable
        self.calls, self.reads, self.prices = 0, 0, None
        self.script = ScriptedProvider()
        self.input_tokens, self.output_tokens = self.script.input_tokens, self.script.output_tokens

    def complete(self, req):
        self.calls += 1
        response = self.script.complete(req)
        self.balance -= self.prices.price(req.model_id).cost(
            response.input_tokens, response.output_tokens)
        if self.calls % self.drop_every == 0:
            raise ConnectionError("the reply never arrived")
        return response

    def balance_of(self, model_id):
        self.reads += 1
        if not self.readable:
            raise OSError("balance endpoint unavailable")
        return self.balance


def runtime(provider, *, events, ledger_path=None, manifest=MANIFEST):
    rt = Runtime(manifest, events=events, seed=1, initial_balance_micro=None,
                 ledger_path=ledger_path, drip=False, router_gamma=.1,
                 exchange=FakeExchange(), provider=provider)
    # The real adapter shape: a non-deterministic provider whose reads are journaled.
    provider.prices = rt.prices
    assert not rt.provider.deterministic
    return rt


def by_kind(rt, kind):
    return ledger_items(rt, kind)


def test_uncertain_bills_are_charged_at_the_ceiling_then_settled_to_the_true_cost():
    provider = BalanceProvider()
    rt = runtime(provider, events=14)
    rt.run()
    uncertain = by_kind(rt, "metering.uncertain")
    settlements = by_kind(rt, "metering.settlement")
    settled = by_kind(rt, "wallet.settle_uncertain")
    assert len(uncertain) >= 3 and len(settlements) == len(uncertain)
    # No launch reference in a scripted world (its models are fake): the first uncertain
    # bill takes the reference and keeps its ceiling; every later one settles exactly.
    assert [s["status"] for s in settlements] == ["reference_taken"] + [
        "settled"] * (len(uncertain) - 1)
    assert len(settled) == len(uncertain) - 1 and provider.reads == len(uncertain)
    assert list(rt.wallet.uncertain_bills) == [uncertain[0]["reservation_id"]]
    for bill, item in zip(uncertain[1:], settled, strict=True):
        assert item["reservation_id"] == bill["reservation_id"]
        assert item["provisional_micro"] == bill["provisional_micro"]
        drop = rt.prices.price(bill["reason"].removeprefix("model:")).cost(
            provider.input_tokens, provider.output_tokens)
        assert item["actual_micro"] == drop > 0
        assert item["amount"] == bill["provisional_micro"] - drop > 0
        assert (item["provider_balance_before"] - item["spent_since_before"]
                - item["provider_balance_after"]) == drop
        assert item["handle"] == bill["handle"] and item["reason"] == bill["reason"]
    # The balance reads are journaled like every other provider read.
    reads = [i for i in by_kind(rt, "io.call") if i["name"] == "provider.balance_of"]
    results = {i["call"] for i in by_kind(rt, "io.result")}
    assert len(reads) == len(uncertain) and all(r["seq"] in results for r in reads)
    # The failed invocations report the settled cost, not the ceiling.
    invocations = {i["handle"]: i for i in by_kind(rt, "invocation")}
    for bill in uncertain[1:]:
        assert invocations[bill["handle"]]["cost"] < bill["provisional_micro"]
    assert invocations[uncertain[0]["handle"]]["cost"] == uncertain[0]["provisional_micro"]
    assert rt.wallet.check_conservation() and rt.ledger.verify()
    assert rt.bill_settlement.reference["openrouter"]["balance"] == settled[-1][
        "provider_balance_after"]


def test_the_seat_that_paid_the_ceiling_is_refunded_its_entitlement():
    rt = runtime(BalanceProvider(), events=14)
    rt.run()
    commits = {i["reservation_id"]: i for i in by_kind(rt, "budget") if i["op"] == "commit"}
    refunds = [i for i in by_kind(rt, "budget") if i["op"] == "settle_uncertain"]
    settled = by_kind(rt, "wallet.settle_uncertain")
    assert refunds and len(refunds) == len(settled)
    for refund, item in zip(refunds, settled, strict=True):
        paid = commits[item["reservation_id"]]
        assert refund["assembly_id"] == paid["assembly_id"]
        assert refund["own"] == paid["own"] and refund["released"] == item["amount"]
        assert refund["amount"] == min(item["amount"], paid["own"]) > 0
        assert refund["entitlement_after"][paid["assembly_id"]] >= refund["amount"]
    assert rt.budget.check_invariant() and rt.budget.state()["uncertain"].keys() == set(
        rt.wallet.uncertain_bills)


def test_a_balance_read_that_fails_keeps_the_ceiling_and_the_bill_uncertain():
    provider = BalanceProvider(readable=False)
    rt = runtime(provider, events=14)
    rt.run()
    uncertain = by_kind(rt, "metering.uncertain")
    settlements = by_kind(rt, "metering.settlement")
    assert len(uncertain) >= 3 and provider.reads == len(uncertain)
    assert {s["status"] for s in settlements} == {"balance_unavailable"}
    assert all(s["error"] == "OSError" for s in settlements)
    assert not by_kind(rt, "wallet.settle_uncertain")
    assert list(rt.wallet.uncertain_bills) == [b["reservation_id"] for b in uncertain]
    invocations = {i["handle"]: i for i in by_kind(rt, "invocation")}
    # An invocation's cost is its model bill plus whatever tools that return called;
    # the uncertain bill is the model call alone, so it is the floor, not the total.
    # (Restated in R3-D, where the scripted schedule moved and one of these returns
    # began making priced tool calls.)
    assert all(invocations[b["handle"]]["cost"] >= b["provisional_micro"] for b in uncertain)
    assert rt.bill_settlement.reference == {} and rt.wallet.check_conservation()
    # The failed reads are journaled as errors, so replay reproduces the refusal.
    errors = [i for i in by_kind(rt, "io.result") if i.get("error") == "OSError"]
    assert len(errors) == len(uncertain)


def test_a_launched_world_reads_its_reference_once_at_launch():
    manifest = replace(MANIFEST, models=tuple(
        replace(t, provider="openrouter") for t in MANIFEST.models))
    provider = BalanceProvider()
    rt = runtime(provider, events=14, manifest=manifest)
    rt.run()
    assert provider.reads >= 1
    reads = [i for i in by_kind(rt, "io.call") if i["name"] == "provider.balance_of"]
    launch = next(i for i in ledger_items(rt) if i.get("kind") == "event"
                  and i["event"]["kind"] == "Launch")
    assert reads[0]["seq"] > launch["seq"]
    # With a launch reference, even the first uncertain bill settles.
    settlements = by_kind(rt, "metering.settlement")
    assert settlements and {s["status"] for s in settlements} == {"settled"}
    assert rt.wallet.uncertain_bills == {} and rt.wallet.check_conservation()


def test_a_settlement_interrupted_between_its_read_and_its_result_survives_resume(tmp_path):
    """Death after the balance read was journaled but before its result: the resumed
    world completes the read (a read is replayable), settles the bill and continues to
    the same money as the world that never died."""
    path = tmp_path / "bills.jsonl"
    provider = BalanceProvider()
    rt = runtime(provider, events=14, ledger_path=str(path))
    append = rt.ledger.append
    reads = []

    def interrupt(entry):
        seq = append(entry)
        if entry["kind"] == "io.call" and entry["name"] == "provider.balance_of":
            reads.append(seq)
            if len(reads) == 2:  # the first settling read, after the reference was taken
                raise Died
        return seq

    rt.ledger.append = interrupt
    with pytest.raises(Died):
        rt.run()
    rt.ledger.append = append
    rt._ledger_lock.close()
    balance_at_death = provider.balance
    resumed = resume_runtime(MANIFEST, str(path), provider=provider, exchange=FakeExchange())
    provider.prices = resumed.prices
    summary = resumed.run()
    assert summary["stats"]["resumes"] == 1 and summary["ledger_verify"]
    settled = by_kind(resumed, "wallet.settle_uncertain")
    uncertain = by_kind(resumed, "metering.uncertain")
    assert settled and len(settled) == len(uncertain) - 1
    # The interrupted read completed against the provider's balance at death, not a guess.
    assert settled[0]["provider_balance_after"] == balance_at_death
    assert resumed.wallet.check_conservation() and resumed.budget.check_invariant()
    assert list(resumed.wallet.uncertain_bills) == [uncertain[0]["reservation_id"]]
    assert resumed.bill_settlement.reference["openrouter"]["balance"] == settled[-1][
        "provider_balance_after"]
    # Same world, never interrupted: the same bills, the same settlements, the same money.
    twin = BalanceProvider()
    reference = runtime(twin, events=14)
    reference.run()
    assert reference.wallet.balance == resumed.wallet.balance
    assert [s["actual_micro"] for s in by_kind(reference, "wallet.settle_uncertain")] == [
        s["actual_micro"] for s in settled]
    assert twin.balance == provider.balance


def test_a_checkpoint_carries_the_reference_so_a_resumed_bill_settles_exactly(tmp_path):
    """Death between events, after a settlement: the checkpoint restores the reference and
    what was booked since, so the next uncertain bill after resume settles to its cost."""
    from factorylab.runtime.resume import decode, restore_runtime, runtime_state

    provider = BalanceProvider()
    rt = runtime(provider, events=14)
    rt.run()
    assert by_kind(rt, "wallet.settle_uncertain")
    state = runtime_state(rt)
    saved = decode(state["components"])["bill_settlement"]["reference"]
    assert saved == rt.bill_settlement.reference and saved["openrouter"]["balance"] > 0
    twin = runtime(BalanceProvider(), events=14)
    restore_runtime(twin, state)
    assert twin.bill_settlement.reference == rt.bill_settlement.reference
    assert twin.wallet.uncertain_bills == rt.wallet.uncertain_bills
    assert twin.budget.state()["uncertain"] == rt.budget.state()["uncertain"]
