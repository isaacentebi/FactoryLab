"""Edition 2, W10: uncertain bills settle from the provider's own balance.

Two live rehearsals (docs/audits/v5/rehearsal.md) billed every provider call that returned
no HTTP status at its reserved ceiling, 34 to 62 percent of all spend. Model calls are
serial, so the true cost of such a call is the provider's balance before it minus the
balance after. These tests run the scripted world with a provider whose balance drops by
what each call really cost, whether or not the call returned a bill, and no network.
"""


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
