"""The wallet moves only when money moves (Wave 11; essay II.II.b, II.IV.a).

Every debit names a real counterparty: a provider's bill, a seller's price, a
rail's fee. A scarce resource that costs nothing at the margin (retained bytes on
the world's own disk, a public read, code in the world's own jail) is a
constraint: a hard limit, or a price the charter may put on reward through λ,
never a money debit. These tests attempt the fictitious debits the kernel used to
make and assert the wallet did not move for them, and check that each pass-through
of a real bill (re-attributed between seats, never created or destroyed) leaves the
world's total debits equal to the real outflows.
"""

from dataclasses import dataclass, field, replace

import pytest

from factorylab.kernel.budget import BudgetBook, SeatWallet, unlocked_balance
from factorylab.kernel.ledger import Ledger
from factorylab.kernel.wallet import Wallet
from factorylab.runtime.continuity import HARD_STATE_BYTES, STATE_TOO_LARGE
from factorylab.runtime.loop import Runtime
from factorylab.runtime.worlds import REMOVED_PRICE_KEYS, load_manifest, manifest_from_dict
from factorylab.world.metering import Meter
from factorylab.world.scripted import ScriptedProvider
from tests.conftest import make_runtime
from tests.runtime.test_connectors import decision, ledger_items

DAY_NS = 86_400 * 1_000_000_000
#: The reasons a nonzero debit may carry: each one names who was paid.
REAL_OUTFLOWS = ("model:", "tool:web.search", "tool:connector.x402", "treasury:fees")


def _next_window(rt, *, advance_ns):
    """Let wall time pass and the price loop fall due, then open the next window."""
    rt.clock.now_ns += advance_ns
    rt.clockwork.force("price", rt.ticks_consumed)
    rt._manage_reserve_window()


def test_a_large_working_state_held_across_many_windows_moves_no_money():
    """The fictitious debit storage rent used to make: nothing is paid to anyone for
    bytes on the world's own disk, so however large the state and however long it is
    held, the wallet does not move, no seat's entitlement moves, and the decision that
    wrote it carries no liability for it."""
    rt = make_runtime()
    rt._manage_reserve_window()
    handle = decision(rt)
    state = {"notes": "x" * (HARD_STATE_BYTES - 64)}
    head = rt.working_state.put("seed-decider", state, handle=handle)
    assert HARD_STATE_BYTES - 64 < head["bytes"] <= HARD_STATE_BYTES
    balance, entitlements = rt.wallet.balance, rt.budget.entitlements()
    for _ in range(12):
        _next_window(rt, advance_ns=30 * DAY_NS)
        assert rt.window.decisions.get(handle, {}).get("cost", 0) == 0
    assert rt.wallet.balance == balance
    assert rt.budget.entitlements() == entitlements
    assert not [i for i in ledger_items(rt) if i["kind"].startswith("wallet.")
                and i.get("reason") == "tool:state.storage"]
    assert not ledger_items(rt, "state.rent") and not ledger_items(rt, "state.rent_due")
    assert not [i for i in ledger_items(rt, "price.contribution") if i.get("storage")]
    # The size is observable where it was written, for the charter to price if it wants.
    assert ledger_items(rt, "state.put")[-1]["bytes"] == head["bytes"]


def test_the_working_state_hard_limit_still_refuses_and_leaves_the_head():
    """The constraint that replaces the rent is the 64 KiB hard cast (II.II.b)."""
    rt = make_runtime()
    rt._manage_reserve_window()
    handle = decision(rt)
    head = rt.working_state.put("seed-decider", {"fact": "kept"}, handle=handle)
    with pytest.raises(ValueError, match=STATE_TOO_LARGE):
        rt.working_state.put("seed-decider", {"notes": "x" * HARD_STATE_BYTES}, handle=handle)
    assert rt.working_state.head("seed-decider") == head


def test_the_storage_section_states_the_limit_and_no_price():
    world = make_runtime()._world_block()
    assert world["storage"]["working_state_max_bytes"] == HARD_STATE_BYTES
    assert "costs no money" in world["storage"]["pricing"]
    assert "micro_per_byte_day" not in world["storage"]


def test_every_seeded_tool_is_free_and_a_free_call_moves_no_money():
    """A kernel read, a venue read, a catalogue search and a jailed calc pay no one."""
    rt = make_runtime()
    rt._manage_reserve_window()
    rt._ensure_connector_tool()
    assert {tool_id: spec["price_micro_per_call"] for tool_id, spec in rt.tool_specs.items()
            if spec["price_micro_per_call"]} == {}
    handle = decision(rt)
    before = rt.wallet.balance
    for call in ({"tool": "venue.mids", "args": {}},
                 {"tool": "catalogue.search", "args": {"substring": "venue"}},
                 {"tool": "calc", "args": {"op": "notional", "size": "1", "price": "2"}},
                 {"tool": "world.read", "args": {"section": "prices"}}):
        result, cost = rt._run_tool("seed-decider", handle, call)
        assert cost == 0, (call, result)
    assert rt.wallet.balance == before


def _world(**tables):
    from tests.seed_charter import seed_charter_table

    return {"name": "x", "initial_balance_usd": "10", "charter": seed_charter_table(),
            "models": [{"id": "m", "input_usd_per_mtok": "1", "output_usd_per_mtok": "5"}],
            "assemblies": [{"id": "a", "model_id": "m"}],
            "novelty": {"share": 0.1}, "immune": {"price_step": 0.05},
            **tables}


@pytest.mark.parametrize("table,key", sorted(REMOVED_PRICE_KEYS))
def test_a_manifest_naming_a_removed_price_is_refused_by_name(table, key):
    """R8: a world file that still prices a call no one is paid for is refused, never
    loaded as though the price applied."""
    with pytest.raises(ValueError, match=f"{table}.{key} was removed"):
        manifest_from_dict(_world(**{table: {key: "0"}}))


@pytest.mark.parametrize("table", ["storage", "notes"])
def test_a_manifest_naming_storage_rent_is_refused(table):
    with pytest.raises(ValueError, match=rf"\[{table}\] was removed"):
        manifest_from_dict(_world(**{table: {"micro_per_byte_day": "0.04"}}))


# --- pass-throughs: a real bill re-attributed between seats -----------------------------


@pytest.fixture
def clock():
    return lambda: 100


@pytest.fixture
def ledger(clock):
    return Ledger(clock_ns=clock)


def _book(ledger, clock, amount=10_000):
    wallet = Wallet(amount, ledger, clock_ns=clock)
    book = BudgetBook(wallet, ledger, clock_ns=clock)
    book.genesis(["seat", "parent"])  # 4 000 each, 2 000 unallocated
    return wallet, book


def _conserved(book, wallet) -> bool:
    return (sum(book.entitlements().values()) + book.unallocated()
            == unlocked_balance(wallet) - book.holds()) and book.check_invariant()


def _commits(ledger):
    return [i for i in ledger._recovery_items() if i["kind"] == "budget" and i["op"] == "commit"]


def test_a_bill_the_commons_helps_pay_is_debited_once_at_its_real_amount(ledger, clock):
    """P: protected exploration or an overrun beyond the seat's entitlement is borne by
    the pool. The seat's share and the commons' share sum to the one bill, and the
    wallet moves by exactly that bill."""
    wallet, book = _book(ledger, clock)
    seat = SeatWallet(wallet, book, "seat", protected=lambda h, r: 5_000)
    bill = 6_500
    Meter(seat).run(handle="h", reason="model:m", ceiling=7_000, execute=lambda: "ok",
                    cost_of=lambda _: bill)
    [commit] = _commits(ledger)
    assert commit["own"] + commit["commons"] == commit["amount"] == bill
    assert commit["commons"] > 0
    assert wallet.balance == 10_000 - bill and _conserved(book, wallet)


def test_a_child_bill_charged_to_its_parent_is_debited_once_at_its_real_amount(ledger, clock):
    """P: a child request is its parent's subcontracting, so the parent's entitlement
    pays; the caller's does not move, and the wallet moves by exactly the bill."""
    wallet, book = _book(ledger, clock)
    child = SeatWallet(wallet, book, "seat", payer=lambda h: "parent")
    bill = 700
    Meter(child).run(handle="child", reason="model:m", ceiling=1_000, execute=lambda: "ok",
                     cost_of=lambda _: bill)
    assert book.entitlement("parent") == 4_000 - bill and book.entitlement("seat") == 4_000
    assert wallet.balance == 10_000 - bill and _conserved(book, wallet)


def test_a_bridge_moves_nothing_and_the_bill_it_backs_is_debited_once(ledger, clock):
    """P: the routing bridge backs a stale estimate from the pool for one call. The
    bridge itself moves no money; only the provider's bill does."""
    wallet, book = _book(ledger, clock)
    book.debit("seat", 3_900, "test")  # 100 left: the rendered request is dearer
    before = (wallet.balance, book.entitlements(), book.unallocated())
    backed = book.bridge("seat", "h", 400, "routing estimate")
    assert backed == 400 and (wallet.balance, book.entitlements(), book.unallocated()) == before
    seat = SeatWallet(wallet, book, "seat", protected=lambda h, r: backed)
    Meter(seat).run(handle="h", reason="model:m", ceiling=500, execute=lambda: "ok",
                    cost_of=lambda _: 450)
    [commit] = _commits(ledger)
    assert commit["own"] == 100 and commit["commons"] == 350
    assert wallet.balance == 10_000 - 450 and _conserved(book, wallet)


def test_a_bill_no_seat_authored_lands_on_the_pool_once(ledger, clock):
    """P: work on the shared meter is paid from the unallocated pool."""
    wallet, book = _book(ledger, clock)
    pool = book.unallocated()
    Meter(wallet).run(handle="kernel", reason="model:m", ceiling=300, execute=lambda: "ok",
                      cost_of=lambda _: 250)
    assert wallet.balance == 10_000 - 250 and book.unallocated() == pool - 250
    assert _conserved(book, wallet)


def test_moving_entitlement_between_seats_moves_no_money(ledger, clock):
    """An endowment, a trial paid to the pool and a grant reclassify money the wallet
    already holds: they net to zero across the world and are never a debit."""
    wallet, book = _book(ledger, clock)
    book.transfer("parent", "seat", 1_000, "trial:assembly")
    book.debit("seat", 500, "trial:tool")
    book.grant("parent", 500, "test")
    assert wallet.balance == 10_000
    assert sum(book.entitlements().values()) + book.unallocated() == 10_000
    assert not [i for i in ledger._recovery_items() if i["kind"] == "wallet.commit"]


@dataclass
class BillingProvider(ScriptedProvider):
    """The scripted world, billing every completion the way OpenRouter reports a cost.

    ``bills`` is the counterparty's own record: what it charged, call by call.
    """

    bills: list = field(default_factory=list)

    def complete(self, req):
        response = super().complete(req)
        bill = 7 * response.input_tokens + 11 * response.output_tokens + 3
        self.bills.append(bill)
        return replace(response, cost_micro=bill)


@pytest.mark.gate
def test_a_world_s_total_debits_equal_its_real_outflows():
    """Across a whole scripted world, every nonzero debit names a real counterparty,
    the model and search debits sum to exactly what the provider billed, every
    re-attribution to a seat or to the commons books the same amount the wallet
    debited, and the wallet fell by exactly the real outflows."""
    provider = BillingProvider()
    rt = Runtime(load_manifest("scripted"), events=60, seed=1, initial_balance_micro=None,
                 ledger_path=None, router_gamma=.1, provider=provider)
    items = []
    append = rt.ledger.append

    def capture(item):
        items.append(dict(item))
        return append(item)

    rt.ledger.append = capture
    start = rt.wallet.balance
    rt.run()
    commits = [i for i in items if i["kind"] == "wallet.commit"]
    assert commits and provider.bills
    fictitious = [c for c in commits if c["amount"] and not c["reason"].startswith(REAL_OUTFLOWS)]
    assert fictitious == []
    billed = sum(c["amount"] for c in commits
                 if c["reason"].startswith(("model:", "tool:web.search")))
    assert billed == sum(provider.bills)
    # Every seat-attributed commit re-attributes exactly what the wallet debited.
    debited = {c["reservation_id"]: c["amount"] for c in commits}
    for row in (i for i in items if i["kind"] == "budget" and i["op"] == "commit"):
        assert row["own"] + row["commons"] == row["amount"] == debited[row["reservation_id"]]
    # The wallet fell by the real outflows and nothing else; money that arrived
    # (settlements, drips) is added back so the identity holds in any world.
    arrived = sum(i["amount"] for i in items if i["kind"] in ("wallet.settle", "wallet.drip"))
    assert start + arrived - rt.wallet.balance == sum(c["amount"] for c in commits)
    assert rt.wallet.check_conservation() and rt.budget.check_invariant()
