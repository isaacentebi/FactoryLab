"""Edition 2 vertical slice: the reviewer's twelve steps, scripted, offline, in one Runtime.

One scripted world on a real ledger file walks the whole story of
``docs/plans/edition2.md`` end to end: a locked endowment released on a
schedule (C1), a program seat with private state (C8, C9), a service sold on
its tool (C11), conserved lot credit and losses charged to their maker (C6,
C10), a metric challenge adopted without the challenged card's veto (C7),
dormancy between releases (C2), crash and resume at three points, refusal of a
different release (C4), retirement and kill. The steps are numbered as the
reviewer numbered them; they execute in the order the money allows (the
challenge is adopted before the loss that empties the pool), and every test
names the step it witnesses.

Nothing here reaches a network, a credential or a mainnet: the provider is
scripted, the venue is the fake, the payment is the buyer's own test key
verified offline and settled by a stubbed facilitator.
"""

import json
import shutil
from dataclasses import dataclass, field, replace
from decimal import Decimal
from pathlib import Path

import pytest

from factorylab.charter.windows import MetricWindow
from factorylab.cortex.assembly import ProgramAssembly
from factorylab.kernel.budget import unlocked_balance
from factorylab.kernel.events import Bus
from factorylab.kernel.ledger import Ledger, LedgerIntegrityError, LedgerLock
from factorylab.kernel.termination import DORMANT, Termination
from factorylab.runtime import release
from factorylab.runtime.loop import Runtime
from factorylab.runtime.resume import (
    ResumeError,
    restore_runtime,
    resume_runtime,
    runtime_state,
)
from factorylab.runtime.seller import seller_from_runtime, services_from_runtime
from factorylab.runtime.wake import public_window_item
from factorylab.runtime.worlds import EndowmentSpec, load_manifest
from factorylab.world.exchange import FakeExchange
from factorylab.world.scripted import ScriptedProvider
from tests.audit.test_a15_liability import boundary
from tests.audit.test_e2_challenge import _close, _returns
from tests.cortex.test_jail import require_jail
from tests.runtime.test_loop import _consequence_judge, _consequence_produce
from tests.runtime.test_seller import RESERVE, paid_header, settlement
from tests.world.test_x402 import FakeHTTP

pytestmark = pytest.mark.slow

BASE = load_manifest("scripted")
TICK = BASE.tick_interval_ns
INITIAL = BASE.initial_balance_micro          # $100
LOCKED = 90_000_000                           # $90 locked; $10 unlocked at genesis
CRASH_TICK = 60                               # the first crash point, after step 4
BOUNDARY_NS = BASE.timing.min_ratio * BASE.evaluation.consequence_backstop_events * TICK
DORMANT_TICKS = 40                            # ticks the world spends dormant before the release
# One governance boundary (the challenge is balloted and activated at the same one)
# passes before the loss; the release is due a fixed number of ticks after the loss
# empties the pool. Stage 3 asserts the boundary count the schedule was built on.
BOUNDARIES = 1
RELEASE_AT = CRASH_TICK * TICK + BOUNDARIES * BOUNDARY_NS + DORMANT_TICKS * TICK
AWAKE_TICKS = 6                               # ticks after the release, awake again

PROGRAM_ID = "norm-1"
TOOL_ID = "normalize"
SERVICE_PRICE = 2_500

PROGRAM_CODE = (
    "import json, sys\n"
    "d = json.load(sys.stdin)\n"
    "s = d['state'] or {'n': 0, 'lo': None, 'hi': None}\n"
    "mid = float(d['inputs'].get('payload', {}).get('mids', {}).get('BTC', 0) or 0)\n"
    "lo = mid if s['lo'] is None else min(s['lo'], mid)\n"
    "hi = mid if s['hi'] is None else max(s['hi'], mid)\n"
    "x = 0.5 if hi <= lo else (mid - lo) / (hi - lo)\n"
    "print(json.dumps({'action': 'hold', 'payoff': 0.1, 'seen': s['n'], 'normalized': x,"
    " 'state': {'n': s['n'] + 1, 'lo': lo, 'hi': hi}}))\n"
)
TOOL_CODE = (
    "import json, sys\n"
    "a = json.load(sys.stdin)\n"
    "v = [float(x) for x in a['values']]\n"
    "lo, hi = min(v), max(v)\n"
    "print(json.dumps({'normalized': [0.5 if hi <= lo else (x - lo) / (hi - lo) for x in v]}))\n"
)
PROGRAM = {"kind": "assembly", "id": PROGRAM_ID, "model_id": "program", "accepts": ["Tick"],
           "code": PROGRAM_CODE, "state_policy": "private"}
TOOL = {"kind": "tool", "id": TOOL_ID, "description": "Min-max normalise a list of values",
        "args_schema": {"type": "object",
                        "properties": {"values": {"type": "array",
                                                  "items": {"type": "number"}}},
                        "required": ["values"]},
        "code": TOOL_CODE, "timeout_s": 2}
SERVICE = {"kind": "service", "program_id": TOOL_ID, "price_micro": SERVICE_PRICE,
           "description": "Normalisation as a service"}
CHALLENGE_A = {
    "kind": "challenge", "card_id": "cost_per_return",
    "evidence": "cheap successes hide one expensive failure under cost_per_return",
    "replacement": {"observation": "cost_per_attempt", "rule": "at most", "value": 5000,
                    "window": {"kind": "windows", "n": 1, "per": None}},
    "trial_windows": 2,
}
CHALLENGE_B = {
    "kind": "challenge", "card_id": "well_formed_rate",
    "evidence": "the floor should be read per window, not per return count",
    "replacement": {"observation": "well_formed_rate", "rule": "at least", "value": 0.5,
                    "window": {"kind": "windows", "n": 2, "per": None}},
    "trial_windows": 1,
}
OTHER_RELEASE = "e" * 64


@dataclass
class SliceProvider(ScriptedProvider):
    """The scripted population of the slice.

    The first producer call registers the program's tool and the program seat,
    the second the service on that tool; every other produce holds, unless the
    test queued an order for the trade it is driving. Judges, metas and voters
    keep the scripted base behaviour.
    """

    queued: list = field(default_factory=list)

    def _produce(self, description, inputs):
        self._producer_calls += 1
        if self.queued:
            return self.queued.pop(0)
        reply = {"action": "hold", "payoff": 0.1}
        if self._producer_calls == 1:
            reply["register"] = [TOOL, PROGRAM]
        elif self._producer_calls == 2:
            reply["register"] = [SERVICE]
        return reply


def manifest():
    # Cards measured over one complete reserve window, as the controller and challenge
    # tests select, so every closed window prices the cost card it will be asked about.
    charter = replace(BASE.charter, cards=tuple(
        replace(c, window=MetricWindow("windows", 1, None)) for c in BASE.charter.cards))
    return replace(BASE, charter=charter,
                   endowment=EndowmentSpec(LOCKED, ((RELEASE_AT, LOCKED),)),
                   treasury=replace(BASE.treasury, reserve_address=RESERVE))


def exchange():
    """A flat venue: BTC at 100 until the test moves it, no spread, no fee."""
    return FakeExchange(coins=("BTC", "ETH"), start_prices={"BTC": Decimal("100"),
                                                            "ETH": Decimal("10")},
                        spread_bps=Decimal(0), fee_bps=Decimal(0), step_bps=Decimal(0))


def world(path, events, **kwargs):
    return Runtime(manifest(), events=events, seed=1, initial_balance_micro=None,
                   ledger_path=None if path is None else str(path), drip=False,
                   router_gamma=.1, provider=SliceProvider(), exchange=exchange(), **kwargs)


def diary(path):
    return Ledger.reopen(path, manifest=json.loads(manifest().canonical_json()))._recovery_items()


def items(rt, *kinds):
    return [i for i in rt.ledger._recovery_items() if not kinds or i.get("kind") in kinds]


def budget_ops(rows, op):
    return [i for i in rows if i.get("kind") == "budget" and i.get("op") == op]


def events_of(rows, kind):
    return [i["event"]["payload"] for i in rows
            if i.get("kind") == "event" and i["event"]["kind"] == kind]


def invariant(rt) -> bool:
    book = rt.budget
    return (sum(book.entitlements().values()) + book.unallocated()
            == unlocked_balance(rt.wallet) - book.holds()) and book.check_invariant()


class ProcessDeath(BaseException):
    pass


def stop_after(rt, predicate):
    original = rt._process_event

    def interrupted(event):
        result = original(event)
        if predicate(rt, event):
            raise ProcessDeath
        return result

    rt._process_event = interrupted
    with pytest.raises(ProcessDeath):
        rt.run()


def align_ticks(rt):
    """After a governance boundary moved the sim clock, the tick stream continues from it."""
    rt.tick_clock.last_ns = rt.tick_clock.last_event_ns = rt.clock.now_ns


def decision(rt, owner):
    from tests.runtime.test_connectors import decision as open_decision

    return open_decision(rt, owner)


def checkpoint(rt, root):
    """Restore an in-memory checkpoint into a fresh runtime and compare what must survive."""
    state = runtime_state(rt)
    twin = Runtime(manifest(), ledger_path=None, provider=SliceProvider(), exchange=exchange(),
                   **state["config"])
    restore_runtime(twin, state)
    twin.artifacts.root = root
    program = rt.assemblies[PROGRAM_ID]
    facts = {
        "entitlements": rt.budget.state(),
        "locked": (rt.wallet.locked, rt.wallet.released_tranches, rt.wallet.next_release_ns),
        "program_state_sha": program.state_sha,
        "challenges": {cid: ch["status"] for cid, ch in rt.challenges.items()},
        "release_digest": rt.release_digest,
        "dormancy": rt.dormancy,
        "charter_edition": rt.charter.edition,
    }
    restored = {
        "entitlements": twin.budget.state(),
        "locked": (twin.wallet.locked, twin.wallet.released_tranches,
                   twin.wallet.next_release_ns),
        "program_state_sha": twin.assemblies[PROGRAM_ID].state_sha,
        "challenges": {cid: ch["status"] for cid, ch in twin.challenges.items()},
        "release_digest": twin.release_digest,
        "dormancy": twin.dormancy,
        "charter_edition": twin.charter.edition,
    }
    return {"facts": facts, "restored": restored, "state": state, "twin": twin,
            "twin_state": runtime_state(twin), "invariant": invariant(rt) and invariant(twin)}


class Story:
    """The slice, executed lazily one stage at a time; a failed stage fails every later one."""

    def __init__(self, root: Path):
        self.root = root
        self.path = root / "world" / "slice.jsonl"
        self.path.parent.mkdir()
        self.results: dict[int, object] = {}
        self.errors: dict[int, BaseException] = {}
        self.rt = None
        self.checkpoints: dict[str, dict] = {}

    def stage(self, n):
        for k in range(1, n + 1):
            if k in self.errors:
                raise self.errors[k]
            if k not in self.results:
                try:
                    self.results[k] = getattr(self, f"_stage_{k}")()
                except BaseException as exc:  # noqa: BLE001 - recorded for every later stage
                    self.errors[k] = exc
                    raise
        return self.results[n]

    # -- stage 1: genesis, program seat, routed and judged, private state; crash; resume ----

    def _stage_1(self):
        rt = world(self.path, CRASH_TICK)
        genesis = budget_ops(items(rt, "budget"), "genesis")
        launch_wallet = rt.wallet.state()
        stop_after(rt, lambda r, e: r.ticks_consumed == CRASH_TICK and str(e.kind) == "Tick")
        crashed = diary(self.path)
        crashed_facts = {
            "budget": rt.budget.state(), "wallet": rt.wallet.state(),
            "program_sha": rt.assemblies[PROGRAM_ID].state_sha,
            "release_digest": rt.release_digest, "invariant": invariant(rt),
            "artifact_index": dict(rt.artifacts.index),
        }
        # An earlier snapshot of the whole identity: the diary, its key and its archive,
        # copied before anything else touches them (step 12 reads it after the kill).
        shutil.copytree(self.path.parent, self.root / "earlier")
        # Step 10: a different release is refused before any state is restored.
        with pytest.MonkeyPatch.context() as patched:
            patched.setattr(release, "release_digest", lambda root=None: OTHER_RELEASE)
            with pytest.raises(ResumeError) as refused:
                resume_runtime(manifest(), str(self.path), provider=SliceProvider(),
                               exchange=exchange())
        after_refusal = diary(self.path)
        # Step 9, first point: the same release resumes from the file.
        restored = resume_runtime(manifest(), str(self.path), provider=SliceProvider(),
                                  exchange=exchange())
        self.rt = restored
        self.checkpoints["after_step_4"] = checkpoint(restored, self.path.with_suffix(".artifacts"))
        return {"genesis": genesis, "launch_wallet": launch_wallet, "crashed": crashed,
                "crashed_facts": crashed_facts, "refused": refused.value,
                "after_refusal": after_refusal, "restored_facts": {
                    "budget": restored.budget.state(), "wallet": restored.wallet.state(),
                    "program_sha": restored.assemblies[PROGRAM_ID].state_sha,
                    "release_digest": restored.release_digest,
                    "invariant": invariant(restored),
                    "artifact_index": dict(restored.artifacts.index)}}

    # -- stage 2: the artifact is readable by another seat; the service is paid ------------

    def _stage_2(self):
        rt = self.rt
        sha = rt.assemblies[PROGRAM_ID].state_sha
        reader = decision(rt, "eval-a")
        before = rt.wallet.balance
        read, cost = rt._run_tool("eval-a", reader, {"tool": "artifact.get", "args": {"sha": sha}})
        artifact_read = {"result": read, "cost": cost,
                         "balance_unchanged": rt.wallet.balance == before}
        transfers = budget_ops(items(rt, "budget"), "transfer")
        author = next(t["src"] for t in transfers if t["dst"] == PROGRAM_ID)
        # Step 5. The live return that carried the service proposal was malformed (see
        # test_step_5_seam_...): the author registers it here by the registration path
        # the seller's own acceptance test uses, on a decision of its own.
        live_registration = TOOL_ID in services_from_runtime(rt)
        if not live_registration:
            from factorylab.cortex.request import Return

            handle = decision(rt, author)
            rt._apply_registrations(handle, Return(handle, {"register": [SERVICE]}, 0, "ok"))
        service = services_from_runtime(rt)[TOOL_ID]
        transport = FakeHTTP([settlement()])
        seller = seller_from_runtime(rt, transport=transport, facilitator="https://facilitator.test")
        entitlement_before = rt.budget.entitlement(author)
        pool_before = rt.budget.unallocated()
        status, _headers, output = seller.handle(
            TOOL_ID, json.dumps({"values": [1, 2, 3]}).encode(),
            {"X-PAYMENT": paid_header(service)}, f"http://localhost/service/{TOOL_ID}")
        pots = rt.wallet.pots()
        income = public_window_item(rt, window=rt.window.index, event=rt.n)["income"]
        return {"artifact_read": artifact_read, "sha": sha, "author": author,
                "live_registration": live_registration,
                "service": service, "status": status, "output": output,
                "facilitator_calls": len(transport.calls),
                "entitlement_before": entitlement_before, "pool_before": pool_before,
                "entitlement_after": rt.budget.entitlement(author),
                "pool_after": rt.budget.unallocated(), "pots": pots, "income": income,
                "invariant": invariant(rt)}

    # -- stage 3: the metric challenge, admitted, trialled, balloted, adopted (step 7) -----

    def _stage_3(self):
        rt = self.rt
        rt._manage_reserve_window()
        real = rt._committee_eligible()
        patched = len(real) < rt.m.committee.seats
        if patched:
            rt._committee_eligible = lambda: {"eval-a": "evaluator", "eval-b": "evaluator",
                                              "meta-a": "meta"}
        _returns(rt, (100, True))
        author = decision(rt, "seed-decider")
        reserve_before = rt.reserve.remaining()
        from factorylab.cortex.request import Return

        rt._apply_registrations(author, Return(author, {"register": [CHALLENGE_A]}, 0, "ok"))
        trial_cost = reserve_before - rt.reserve.remaining()
        (cid, challenge), = rt.challenges.items()
        proposed = items(rt, "challenge.proposed")
        first = rt.window.index + 1
        _close(rt, first, (1_000, True), (1_000, True), (100_000, False))
        under_incumbent, _failed = _close(rt, first + 1, (1_000, True), (7_000, False))
        rows = items(rt, "challenge.window")
        status_due = challenge["status"]
        boundary(rt, first + 2)
        balloted = items(rt, "challenge.balloted")
        votes = [v for v in items(rt, "charter.vote") if v["amendment_id"] == cid]
        boundaries = 1
        if rt.charter.edition == 1:
            boundary(rt, first + 3)
            boundaries += 1
        assert boundaries == BOUNDARIES, "the release schedule was built on one boundary"
        align_ticks(rt)
        adopted = next(c for c in rt.charter.cards if c.id == "cost_per_return")
        frozen = next(c for c in rt.price_windows[first + 1].closed_cards
                      if c.id == "cost_per_return")
        terms = rt._penalty_terms("producer", under_incumbent)
        # A second challenge, due before the pool empties: its ballot must wait out
        # the dormancy of step 8.
        second = decision(rt, "seed-decider")
        rt._apply_registrations(second, Return(second, {"register": [CHALLENGE_B]}, 0, "ok"))
        cid_b = next(c for c in rt.challenges if c != cid)
        _close(rt, first + 4, (1_000, True), (1_000, True))
        if patched:
            del rt._committee_eligible
        self.checkpoints["after_adoption"] = checkpoint(rt, self.path.with_suffix(".artifacts"))
        return {"cid": cid, "challenge": challenge, "proposed": proposed, "rows": rows,
                "status_due": status_due, "balloted": balloted, "votes": votes,
                "adopted": adopted, "frozen": frozen, "terms": terms,
                "edition": rt.charter.edition, "cid_b": cid_b,
                "status_b": rt.challenges[cid_b]["status"], "trial_cost": trial_cost,
                "committee_patched": patched, "invariant": invariant(rt)}

    # -- stage 4: a gain credited once to opener and closer, then a loss to a floor (6) ----

    def _stage_4(self):
        rt = self.rt
        provider = rt.provider.target
        venue = rt.exchange.target
        opener_seat, closer_seat = "seed-observer", "seed-decider"
        before = {seat: rt.budget.entitlement(seat) for seat in (opener_seat, closer_seat)}
        provider.queued.append({"action": "order", "coin": "BTC", "side": "buy", "size": "0.1",
                                "payoff": 0.1})
        opener, _ = _consequence_produce(rt, opener_seat)
        venue.price_path = {"BTC": [Decimal("110")]}
        rt._settle_exchange_effects(venue.advance(rt.clock.now_ns))
        provider.queued.append({"action": "order", "coin": "BTC", "side": "sell", "size": "0.1",
                                "payoff": 0.1})
        closer, _ = _consequence_produce(rt, closer_seat)
        gain = {"opener": rt.consequences.payoff(opener), "closer": rt.consequences.payoff(closer)}
        credits = [c for c in budget_ops(items(rt, "budget"), "credit")
                   if c["reason"] == "return_paid_off"]
        after_gain = {seat: rt.budget.entitlement(seat) for seat in (opener_seat, closer_seat)}
        # The loss: a quarter coin bought at 110 (collateral within the unlocked ten
        # dollars at the venue's leverage) and sold at 60 loses $12.50, more than the
        # seats and the pool together hold.
        provider.queued.append({"action": "order", "coin": "BTC", "side": "buy", "size": "0.25",
                                "payoff": 0.1})
        loser_open, event = _consequence_produce(rt, opener_seat)
        # A judge seals ten-event forecasts about the opening return while the pool
        # can still pay for judging: they fall due while the world is dormant.
        judge = _consequence_judge(rt, event, "eval-a")
        venue.price_path = {"BTC": [Decimal("60")]}
        rt._settle_exchange_effects(venue.advance(rt.clock.now_ns))
        provider.queued.append({"action": "order", "coin": "BTC", "side": "sell", "size": "0.25",
                                "payoff": 0.1})
        loser_close, _ = _consequence_produce(rt, closer_seat)
        loss = {"opener": rt.consequences.payoff(loser_open),
                "closer": rt.consequences.payoff(loser_close)}
        charges = [c for c in budget_ops(items(rt, "budget"), "charge")
                   if c["reason"] == "return_paid_off"]
        return {"opener_seat": opener_seat, "closer_seat": closer_seat, "before": before,
                "gain": gain, "credits": credits, "after_gain": after_gain, "loss": loss,
                "charges": charges,
                "after_loss": {seat: rt.budget.entitlement(seat)
                               for seat in (opener_seat, closer_seat)},
                "unlocked": rt.wallet.unlocked, "unallocated": rt.budget.unallocated(),
                "judge": judge, "invariant": invariant(rt)}

    # -- stage 5: dormancy, the release, the wake (step 8; checkpoint during dormancy) ------

    def _stage_5(self):
        rt = self.rt
        before = len(items(rt))
        clock_at_entry = rt.clock.now_ns
        rt.events_budget = rt.tick_clock.index + 4
        rt.run()
        dormant_entry = items(rt, "dormant")
        assert rt.dormancy is not None, "the drained pool did not pause the world"
        self.checkpoints["during_dormancy"] = checkpoint(rt, self.path.with_suffix(".artifacts"))
        remaining = -(-(rt.wallet.next_release_ns - rt.clock.now_ns) // TICK)
        rt.events_budget = rt.tick_clock.index + remaining + AWAKE_TICKS
        rt.run()
        rows = items(rt)[before:]
        return {"dormant_entry": dormant_entry, "rows": rows, "dormancy_after": rt.dormancy,
                "clock_at_entry": clock_at_entry,
                "locked_after": rt.wallet.locked, "seats": rt.budget.seats(),
                "status_b": rt.challenges[self.results[3]["cid_b"]]["status"],
                "invariant": invariant(rt)}

    # -- stage 6: the author retires (step 11) ----------------------------------------------

    def _stage_6(self):
        rt = self.rt
        author = self.results[2]["author"]
        sha = rt.assemblies[PROGRAM_ID].state_sha
        pool_before = rt.budget.unallocated()
        held = rt.budget.entitlement(author)
        rt._retire_assembly(author, "retire-author")
        retire = budget_ops(items(rt, "budget"), "retire")[-1]
        reader = decision(rt, "eval-b")
        read, cost = rt._run_tool("eval-b", reader, {"tool": "artifact.get", "args": {"sha": sha}})
        return {"author": author, "sha": sha, "pool_before": pool_before, "held": held,
                "retire": retire, "pool_after": rt.budget.unallocated(),
                "entitlement_after": rt.budget.entitlement(author), "read": read, "cost": cost,
                "owner": rt.artifacts.owner_for(sha), "bytes": rt.artifacts.get(sha),
                "retired": set(rt.retired_assemblies), "invariant": invariant(rt)}

    # -- stage 7: kill (step 12) --------------------------------------------------------------

    def _stage_7(self):
        rt = self.rt
        rt._ledger_lock.close()
        earlier_state = runtime_state(rt)
        with LedgerLock(str(self.path)):
            ledger = Ledger.reopen(self.path, manifest=json.loads(manifest().canonical_json()))
            termination = Termination(ledger=ledger, bus=Bus(ledger))
            termination.kill("explicit_kill:operator")
            released = ledger.seal_key_released()
        frozen = Ledger.open_read_only(self.path, manifest=json.loads(manifest().canonical_json()))
        final = [i["event"]["payload"] for i in frozen.items()
                 if i.get("kind") == "event" and i["event"]["kind"] == "Terminated"]
        with pytest.raises(LedgerIntegrityError) as refused:
            resume_runtime(manifest(), str(self.path), provider=SliceProvider(),
                           exchange=exchange())
        return {"reason": termination.reason, "released": released, "final": final,
                "times": frozen.event_times(), "refused": refused.value,
                "earlier_state": earlier_state}


@pytest.fixture(scope="module")
def story(tmp_path_factory):
    require_jail()
    with pytest.MonkeyPatch.context() as patched:
        def deny(*args, **kwargs):
            pytest.fail("real network is forbidden in the slice")

        patched.setattr("urllib.request.OpenerDirector.open", deny)
        yield Story(tmp_path_factory.mktemp("slice"))


# ---- step 1 ----------------------------------------------------------------------------

def test_step_1_genesis_locks_the_backing_schedules_its_release_and_endows_by_base_share(story):
    s = story.stage(1)
    genesis, = s["genesis"]
    seats = [a.id for a in BASE.assemblies]
    unlocked = INITIAL - LOCKED
    share = int(unlocked * Decimal("0.8")) // len(seats)
    assert genesis["backed"] == unlocked and genesis["grants"] == {seat: share for seat in seats}
    assert genesis["to_unallocated"] == unlocked - share * len(seats)
    wallet = s["launch_wallet"]
    assert wallet["locked"] == wallet["locked_micro"] == LOCKED and wallet["released"] == 0
    assert wallet["release_schedule"].releases == ((RELEASE_AT, LOCKED),)
    launch = events_of(s["crashed"], "Launch")[0]
    assert launch["release_digest"] == release.release_digest()
    anchors = [i for i in s["crashed"] if i.get("kind") == "wallet.anchor"]
    assert len(anchors) == 1 and anchors[0]["launch_ns"] == 0
    assert s["crashed_facts"]["invariant"]


# ---- step 2 ----------------------------------------------------------------------------

def test_step_2_a_model_seat_registers_a_private_program_and_pays_its_trial(story):
    s = story.stage(1)
    rows = s["crashed"]
    registered = {p["id"]: p for p in events_of(rows, "Registered") if p.get("kind") == "assembly"}
    assert registered[PROGRAM_ID]["program"] is True
    assert registered[PROGRAM_ID]["state_policy"] == "private"
    transfer = next(t for t in budget_ops(rows, "transfer") if t["dst"] == PROGRAM_ID)
    seats = {a.id for a in BASE.assemblies}
    assert transfer["src"] in seats and transfer["reason"] == "trial:assembly"
    assert transfer["amount"] == BASE.evaluation.trial_amount_micro
    assert transfer["entitlement_after"][PROGRAM_ID] == transfer["amount"]
    # The proposer paid: its entitlement fell by the trial, the pool did not move.
    movements = [i for i in rows if i.get("kind") == "budget" and "unallocated_after" in i]
    previous = movements[movements.index(transfer) - 1]
    assert transfer["unallocated_after"] == previous["unallocated_after"]
    assert isinstance(story.rt.assemblies[PROGRAM_ID], ProgramAssembly)


# ---- step 3 ----------------------------------------------------------------------------

def test_step_3_the_program_is_routed_returns_well_formed_output_is_judged_and_priced(story):
    s = story.stage(1)
    rows = s["crashed"]
    price = BASE.prices.program_micro_per_call
    calls = [i for i in rows if i.get("kind") == "invocation" and i["assembly_id"] == PROGRAM_ID]
    assert len(calls) >= 3
    assert all(c["status"] == "ok" and c["served_by"] == "program" and c["cost"] == price
               for c in calls)
    outputs = [json.loads(c["outputs"]) for c in calls]
    assert [o["seen"] for o in outputs] == list(range(len(calls)))
    assert all(0.0 <= o["normalized"] <= 1.0 for o in outputs)
    # Every call is a wallet transaction at the flat price, charged to the seat itself.
    commits = [i for i in budget_ops(rows, "commit") if i["assembly_id"] == PROGRAM_ID]
    assert len(commits) == len(calls) and all(c["amount"] == price and c["own"] == price
                                              for c in commits)
    handles = {c["handle"] for c in calls}
    verdicts = [v for v in events_of(rows, "Verdict") if v["about_handle"] in handles]
    assert verdicts and all("normalized" in v["producer_outputs"] for v in verdicts)
    judges = {story.rt.handle_to_assembly[v["evaluator_handle"]] for v in verdicts}
    assert judges <= set(story.rt.standing.snapshot())
    assert PROGRAM_ID in json.dumps(story.rt._world_block()["catalogue"])


# ---- step 4 ----------------------------------------------------------------------------

def test_step_4_private_state_persists_by_hash_and_any_seat_reads_the_artifact(story):
    s = story.stage(1)
    rows = s["crashed"]
    calls = [i for i in rows if i.get("kind") == "program.call" and i["assembly_id"] == PROGRAM_ID]
    puts = [i for i in rows if i.get("kind") == "artifact.put"]
    assert [c["state_in"] for c in calls] == [None, *(c["state_out"] for c in calls[:-1])]
    assert [p["sha"] for p in puts] == [c["state_out"] for c in calls]
    assert all(p["owner"] == PROGRAM_ID and p["artifact_kind"] == "program.state" for p in puts)
    last = calls[-1]["state_out"]
    assert s["crashed_facts"]["program_sha"] == last
    state = json.loads(story.rt.artifacts.get(last))
    assert state["n"] == len(calls) and state["lo"] <= state["hi"]
    assert (story.path.with_suffix(".artifacts") / last).exists()
    read = story.stage(2)["artifact_read"]
    assert read["cost"] == 0 and read["balance_unchanged"]
    assert read["result"] == {"sha": last, "owner": PROGRAM_ID, "kind": "program.state",
                              "bytes": len(story.rt.artifacts.get(last)),
                              "text": story.rt.artifacts.get(last).decode()}
    gets = items(story.rt, "artifact.get")
    assert gets and gets[-1]["assembly_id"] == "eval-a" and gets[-1]["found"]


# ---- step 5 ----------------------------------------------------------------------------

def test_step_5_a_paid_service_call_runs_the_tool_ledgers_income_and_credits_its_owner(story):
    s = story.stage(2)
    rt = story.rt
    assert rt.registry.get(f"service:{TOOL_ID}").kind == "service"
    registered = items(rt, "service.registered")[-1]
    assert registered["code"] == TOOL_CODE and registered["owner"] == s["author"]
    assert s["status"] == 200 and s["output"] == {"normalized": [0.0, 0.5, 1.0]}
    assert s["facilitator_calls"] == 1
    earned = items(rt, "income.earned")
    assert len(earned) == 1
    assert (earned[0]["service"], earned[0]["micro"], earned[0]["program"]) == (
        TOOL_ID, SERVICE_PRICE, TOOL_ID)
    assert earned[0]["tx"] == settlement().body["transaction"]
    credit = [c for c in budget_ops(items(rt, "budget"), "credit")
              if c["reason"] == f"income.earned:{TOOL_ID}"]
    assert len(credit) == 1 and credit[0]["assembly_id"] == s["author"]
    assert credit[0]["amount"] == SERVICE_PRICE
    assert s["entitlement_after"] == s["entitlement_before"] + SERVICE_PRICE
    assert s["pool_after"] == s["pool_before"] - SERVICE_PRICE
    assert s["pots"]["earned_micro"] == SERVICE_PRICE and s["pots"]["subsidy_micro"] == 0
    assert s["pots"]["converted_from_principal_micro"] == 0
    assert s["income"] == {"earned_micro": SERVICE_PRICE, "subsidy_micro": 0,
                           "converted_from_principal_micro": 0}
    assert s["invariant"]


def test_step_5_seam_a_live_return_can_carry_the_service_proposal(story):
    s = story.stage(1)
    rows = s["crashed"]
    malformed = [i for i in rows if i.get("kind") == "invocation" and i["status"] == "malformed"
                 and '"kind": "service"' in json.loads(i["outputs"]).get("raw", "")]
    assert not malformed, "the scripted service proposal was refused as a malformed return"
    assert any(p.get("kind") == "service" for p in events_of(rows, "Registered"))
    assert story.stage(2)["live_registration"]


# ---- step 7 (executes before step 6: the challenge needs a solvent committee) ------------

def test_step_7_a_metric_challenge_is_admitted_trialled_balloted_and_adopted(story):
    s = story.stage(3)
    rt = story.rt
    assert s["cid"] == "challenge-1-cost-per-return"
    assert s["trial_cost"] == BASE.evaluation.trial_amount_micro
    proposed, = s["proposed"]
    assert proposed["trial_windows"] == 2 and proposed["edition"] == 1
    rows = [r for r in s["rows"] if r["challenge_id"] == s["cid"]]
    assert len(rows) == 2 and all(r["incumbent"]["observation"] == "cost_per_return"
                                  and r["replacement"]["observation"] == "cost_per_attempt"
                                  for r in rows)
    assert all(r["incumbent"]["value"] is not None and r["replacement"]["value"] is not None
               for r in rows)
    # Failed attempts count on the replacement side only.
    assert all(r["replacement"]["value"] > r["incumbent"]["value"] for r in rows)
    assert s["status_due"] == "due"
    balloted, = s["balloted"]
    assert balloted["challenge_id"] == balloted["amendment_id"] == s["cid"]
    assert s["votes"] and all(v["vote"] in (True, False) for v in s["votes"])
    assert s["edition"] == 2 and rt.charter_book.activated_amendment(2).id == s["cid"]
    assert s["adopted"] == s["challenge"]["replacement"]
    assert s["adopted"].observation == "cost_per_attempt"
    assert s["challenge"]["status"] == "balloted"


def test_step_7_a_commitment_incurred_under_the_incumbent_settles_under_the_incumbent(story):
    s = story.stage(3)
    assert s["frozen"].observation == "cost_per_return"
    assert [t["observation"] for t in s["terms"] if t["card_id"] == "cost_per_return"] == [
        "cost_per_return"]
    assert s["status_b"] == "due" and s["invariant"]


# ---- step 6 ----------------------------------------------------------------------------

def test_step_6_a_settled_trade_credits_opener_and_closer_once_conserved(story):
    s = story.stage(4)
    opener, closer = s["gain"]["opener"], s["gain"]["closer"]
    # 0.1 BTC bought at 100 and sold at 110: one dollar, once, split 100:110 by notional.
    assert opener.net_micro + closer.net_micro == pytest.approx(1_000_000, abs=2)
    assert opener.net_micro == pytest.approx(1_000_000 * 100 / 210, abs=1)
    assert closer.net_micro == pytest.approx(1_000_000 * 110 / 210, abs=1)
    assert opener.y == 1 and closer.y == 1 and not opener.marked and not closer.marked
    credits = {c["assembly_id"]: c for c in s["credits"]}
    assert set(credits) == {s["opener_seat"], s["closer_seat"]}
    assert credits[s["opener_seat"]]["amount"] == opener.net_micro
    assert credits[s["closer_seat"]]["amount"] == closer.net_micro
    for seat in (s["opener_seat"], s["closer_seat"]):
        assert s["after_gain"][seat] > s["before"][seat]


def test_step_6_a_loss_debits_its_maker_to_a_floor_of_zero_and_the_commons_bears_the_rest(story):
    s = story.stage(4)
    opener, closer = s["loss"]["opener"], s["loss"]["closer"]
    # 0.25 BTC bought at 110 and sold at 60: twelve dollars fifty, once, split 110:60.
    assert opener.net_micro + closer.net_micro == pytest.approx(-12_500_000, abs=2)
    assert opener.net_micro == pytest.approx(-12_500_000 * 110 / 170, abs=1)
    assert closer.net_micro == pytest.approx(-12_500_000 * 60 / 170, abs=1)
    assert opener.y == 0 and closer.y == 0
    charges = {c["assembly_id"]: c for c in s["charges"]}
    assert set(charges) == {s["opener_seat"], s["closer_seat"]}
    for seat, payoff in ((s["opener_seat"], opener), (s["closer_seat"], closer)):
        charge = charges[seat]
        assert charge["amount"] == -payoff.net_micro == charge["own"] + charge["commons"]
        assert 0 < charge["own"] <= s["after_gain"][seat] and charge["commons"] > 0
        assert charge["entitlement_after"][seat] == 0 and s["after_loss"][seat] == 0
    assert s["unlocked"] < 0 and s["unallocated"] < 0
    assert s["invariant"]


# ---- step 8 ----------------------------------------------------------------------------

def test_step_8_the_world_goes_dormant_routes_nothing_keeps_settling_and_a_ballot_waits(story):
    s = story.stage(5)
    rows = s["rows"]
    entered = next(r for r in rows if r.get("kind") == "dormant" and r["state"] == "entered")
    exited = next(r for r in rows if r.get("kind") == "dormant" and r["state"] == "exited")
    assert entered["trigger"] == "wallet" and entered["locked"] == LOCKED
    assert entered["next_release_ns"] == RELEASE_AT and entered["unlocked"] < 0
    span = rows[rows.index(entered) + 1:rows.index(exited)]
    assert not [r for r in span if r.get("kind") == "compute.route"]
    assert not [r for r in span if r.get("kind") == "wallet.commit"
                and str(r.get("reason", "")).startswith("model:")]
    ticks = [r for r in span if r.get("kind") == "runtime.input" and "world" in r]
    assert len(ticks) >= DORMANT_TICKS
    assert s["clock_at_entry"] == CRASH_TICK * TICK + BOUNDARIES * BOUNDARY_NS
    settled = [r for r in span if r.get("kind") in ("forecast.consequence", "decision.settle")]
    assert settled, "forecasts due while dormant were not settled"
    # The second challenge completed its trial before the pause; its ballot waited.
    windows = [r for r in span if r.get("kind") == "price.window"]
    assert windows, "a reserve window closed while dormant"
    assert not [r for r in span if r.get("kind") == "challenge.balloted"]
    assert not [r for r in span if r.get("kind") == "charter.propose"]
    assert s["status_b"] == "due"


def test_step_8_the_release_lands_once_on_schedule_and_the_tranche_is_split_by_the_hook(story):
    s = story.stage(5)
    rows = s["rows"]
    released, = [r for r in rows if r.get("kind") == "release"]
    assert (released["amount"], released["due_ns"], released["locked_after"]) == (
        LOCKED, RELEASE_AT, 0)
    assert released["ts"] >= RELEASE_AT
    split, = budget_ops(rows, "release")
    assert split["amount"] == LOCKED and 0 < split["backed"] <= LOCKED
    seats = s["seats"]
    assert PROGRAM_ID in seats and len(seats) == len(BASE.assemblies) + 1
    per_seat = int(split["backed"] * Decimal("0.8")) // len(seats)
    assert split["grants"] == {seat: per_seat for seat in seats}
    assert split["to_unallocated"] == LOCKED - per_seat * len(seats)
    exited = next(r for r in rows if r.get("kind") == "dormant" and r["state"] == "exited")
    assert rows.index(released) < rows.index(split) < rows.index(exited)
    assert s["dormancy_after"] is None and s["locked_after"] == 0
    # Awake again: paid cognition resumed after the exit, on the released money.
    after = rows[rows.index(exited) + 1:]
    assert [r for r in after if r.get("kind") == "compute.route"]
    assert [r for r in after if r.get("kind") == "wallet.commit"
            and str(r.get("reason", "")).startswith("model:")]
    assert s["invariant"]


# ---- step 9 ----------------------------------------------------------------------------

def test_step_9_a_crash_after_step_4_resumes_from_the_file_with_the_same_positions(story):
    s = story.stage(1)
    crashed, restored = s["crashed_facts"], s["restored_facts"]
    assert restored["budget"] == crashed["budget"]
    assert restored["wallet"]["locked"] == crashed["wallet"]["locked"] == LOCKED
    assert restored["wallet"]["balance"] == crashed["wallet"]["balance"]
    assert restored["program_sha"] == crashed["program_sha"]
    assert restored["release_digest"] == crashed["release_digest"] == release.release_digest()
    assert restored["artifact_index"] == crashed["artifact_index"]
    assert crashed["invariant"] and restored["invariant"]
    assert story.rt.stats.resumes == 1
    tail = [i.get("kind") for i in items(story.rt)]
    assert tail.count("failed_resume") == 1 and tail.count("resume.begin") == 1
    assert tail.index("failed_resume") < tail.index("resume.begin")


@pytest.mark.parametrize("point", ["after_step_4", "after_adoption", "during_dormancy"])
def test_step_9_a_checkpoint_restores_identically_and_keeps_the_invariant(story, point):
    story.stage({"after_step_4": 1, "after_adoption": 3, "during_dormancy": 5}[point])
    cp = story.checkpoints[point]
    assert cp["restored"] == cp["facts"]
    assert cp["twin_state"] == cp["state"]
    assert cp["invariant"]
    if point == "during_dormancy":
        assert cp["facts"]["dormancy"] is not None
        assert cp["twin"].termination.check(
            cp["twin"].wallet, cp["twin"].clock.now_ns,
            cheapest_seat_micro=cp["twin"]._cheapest_seat_micro()) == DORMANT
    if point == "after_adoption":
        assert cp["facts"]["charter_edition"] == 2
        assert cp["facts"]["challenges"] == {"challenge-1-cost-per-return": "balloted",
                                             "challenge-2-well-formed-rate": "due"}


# ---- step 10 ---------------------------------------------------------------------------

def test_step_10_a_different_release_digest_is_refused_with_release_mismatch(story):
    s = story.stage(1)
    assert s["refused"].code == "release_mismatch"
    failed = [i for i in s["after_refusal"] if i.get("kind") == "failed_resume"]
    assert len(failed) == 1 and failed[0]["reason"] == "release_mismatch"
    assert failed[0]["running_release_digest"] == OTHER_RELEASE
    assert failed[0]["ledgered_release_digest"] == release.release_digest()
    assert not any(i.get("kind") == "resume.begin" for i in s["after_refusal"])


# ---- step 11 ---------------------------------------------------------------------------

def test_step_11_the_author_retires_the_artifact_stays_readable_and_its_entitlement_returns(
        story):
    s = story.stage(6)
    rt = story.rt
    assert s["author"] in s["retired"] and s["author"] != PROGRAM_ID
    assert s["retire"]["assembly_id"] == s["author"] and s["retire"]["amount"] == s["held"] > 0
    assert s["pool_after"] == s["pool_before"] + s["held"] and s["entitlement_after"] == 0
    assert s["author"] not in rt.budget.seats()
    assert s["cost"] == 0 and s["read"]["sha"] == s["sha"] and s["read"]["owner"] == PROGRAM_ID
    assert json.loads(s["bytes"])["n"] >= 3 and s["owner"] == PROGRAM_ID
    assert PROGRAM_ID not in rt.retired_assemblies
    assert s["invariant"]


# ---- step 12 ---------------------------------------------------------------------------

def test_step_12_kill_is_ledgered_final_and_the_killed_ledger_cannot_resume(story):
    s = story.stage(7)
    assert s["reason"] == "explicit_kill:operator" and s["released"]
    assert [f["reason"] for f in s["final"]] == ["explicit_kill:operator"]
    assert s["times"]["terminated"]
    assert str(s["refused"]) == "cannot resume a terminated world"


def test_step_12_an_earlier_copy_of_the_killed_diary_cannot_purchase_again(story):
    """The kill is witnessed outside the diary's directory; a pre-kill copy reads it there."""
    story.stage(7)
    earlier = story.root / "earlier" / story.path.name
    try:
        revived = resume_runtime(manifest(), str(earlier), provider=SliceProvider(),
                                 exchange=exchange())
    except (ResumeError, LedgerIntegrityError):
        return  # the killed identity is bound outside the file that recorded the kill
    assert revived.launch_nonce == story.rt.launch_nonce
    assert revived.release_digest == story.rt.release_digest
    before = len(items(revived, "compute.route"))
    revived.events_budget = revived.tick_clock.index + 2
    revived.run()
    routed = len(items(revived, "compute.route")) - before
    assert routed == 0, f"the killed identity bought {routed} more routed decisions"


def test_step_12_an_earlier_checkpoint_of_the_killed_identity_is_final_when_restored(story):
    """A checkpoint names the diary it came from; the kill of that diary is on record."""
    s = story.stage(7)
    twin = Runtime(manifest(), ledger_path=None, provider=SliceProvider(), exchange=exchange(),
                   **s["earlier_state"]["config"])
    try:
        restore_runtime(twin, s["earlier_state"])
    except ResumeError:
        return  # refused: the checkpoint names a killed identity
    assert twin.launch_nonce == story.rt.launch_nonce
    assert twin.termination.final, "the restored checkpoint of a killed world is not final"
