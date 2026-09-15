"""Edition 3, C5: the first world's manifest, the kill contract, and the calibration gate.

Three things are pinned here. The manifest (`worlds/edition3-testnet.toml`) is the roster,
the money and the kill contract of GPT-6 Pro's architect reading, and its hash is pinned so
a silent edit to the physics is a test failure rather than a surprise at ratification. The
kill contract is the promise that a dead factory carries no exposure: with
`[kill] wind_down = true` a kill empties the venue first and says so in the diary and in the
witness line, with it false nothing is touched, and in neither case may a venue delay
finality. The calibration gate is the screen every candidate route passes before the
population is seated: at least forty bounded cases, each with one determinate outcome.
"""

from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from factorylab.kernel.ledger import Ledger
from factorylab.runtime import witness
from factorylab.runtime.loop import Runtime, run_world
from factorylab.runtime.venue import wind_down
from factorylab.runtime.worlds import NS_PER_DAY, KillSpec, load_manifest
from factorylab.world.exchange import Order

EDITION3_HASH = "781584ce44b746ea660d24944d057140e771882f955550a8312f807986260426"


@pytest.fixture(autouse=True)
def _fresh_witness(monkeypatch):
    """No receiver, no inherited kill note: each test witnesses only its own kill."""
    monkeypatch.delenv(witness.URL_ENV, raising=False)
    monkeypatch.setattr(witness, "_killed_here", set())
    witness.note_wind_down(wind_down=False, orders=0)


# --- the manifest -------------------------------------------------------------------------


def test_edition3_manifest_loads_with_the_roster_money_and_kill_contract_of_c5():
    """The nine seats of GPT-6 §10.3, §12's money, §13's kill contract, and a pinned hash."""
    m = load_manifest("edition3-testnet")
    assert m.name == "edition3-testnet"
    assert m.exchange.kind == "hyperliquid" and m.exchange.mainnet is False
    assert m.exchange.client_namespace is None  # drawn by `scripts/rehearsal.py prepare`
    assert m.tick_interval_ns == 600_000_000_000

    # The roster: nine seats, the exact model ids the plan names.
    assert [(a.id, a.model_id) for a in m.assemblies] == [
        ("mechanism", "deepseek/deepseek-v4.1-flash"),
        ("empirical", "venice:z-ai-glm-5-3-flash"),
        ("constructor", "openai/gpt-5.6-sol"),
        ("opportunity", "deepseek/deepseek-v4.1-flash"),
        ("judge-consequence", "deepseek/deepseek-v4.1-flash"),
        ("judge-fidelity", "venice:z-ai-glm-5-3-flash"),
        ("meta-calibration", "deepseek/deepseek-v4.1-flash"),
        ("meta-countercase", "venice:qwen-3-8-flash"),
        ("antagonist", "venice:qwen-3-8-flash"),
    ]
    seats = {a.id: a for a in m.assemblies}
    producers = ("mechanism", "empirical", "constructor", "opportunity")
    for name in producers:
        assert seats[name].role == "producer"
        assert seats[name].accepts == ("WorldUpdate", "Fill")  # C2's coalesced update
    # Judges and meta keep their edition 2 accepts; the antagonist keeps its own.
    assert seats["judge-consequence"].accepts == seats["judge-fidelity"].accepts \
        == ("ProducerReturn",)
    assert seats["meta-calibration"].accepts == seats["meta-countercase"].accepts \
        == ("Verdict",)
    assert seats["antagonist"].accepts == ("Tick", "MarketMid")
    # The one expensive seat, with the availability policy §10.3 sized for it.
    assert seats["constructor"].max_tokens == 4096
    assert seats["constructor"].cadence_floor == 9
    assert {a.cadence_floor for a in m.assemblies if a.id != "constructor"} == {1}
    # Every OpenRouter route is pinned; Venice is a single-provider route with no fallback.
    routes = {m_.id: m_ for m_ in m.models}
    for model_id in ("deepseek/deepseek-v4.1-flash", "openai/gpt-5.6-sol"):
        assert dict(routes[model_id].extra_body) == {
            "provider": {"require_parameters": True}}

    # §12: the whole $300 of backing, $120 at genesis and $60 on days 7, 14 and 21.
    assert m.initial_balance_micro == 300_000_000
    assert m.endowment.locked_micro == 180_000_000
    assert m.initial_balance_micro - m.endowment.locked_micro == 120_000_000
    assert m.endowment.releases == tuple(
        (day * NS_PER_DAY, 60_000_000) for day in (7, 14, 21))
    assert sum(amount for _, amount in m.endowment.releases) == m.endowment.locked_micro
    assert m.endowment.base_share == 0.8
    # C10: 0.8 of the genesis pool across nine lineages, GPT-6 §12.2's ~$10.67 each.
    assert int(0.8 * 120_000_000) // 9 == 10_666_666
    assert m.exchange.start_cash_usd == "120"  # risk-bearing trading principal
    # Provider inventories are separate facts, never one spendable figure.
    assert m.providers.openrouter_micro == 220_000_000
    assert m.providers.venice_micro == 80_000_000

    # §13: the kill contract, precommitted where it cannot be decided in the moment.
    assert m.kill.wind_down is True
    assert m.kill.dust_micro == 1_000_000

    assert m.manifest_hash() == EDITION3_HASH


def test_every_seat_carries_its_seed_lens_verbatim_as_state_and_as_prompt():
    """C5: the common paragraph of §11 then the seat's own lens, character for character."""
    import re

    source = Path("docs/audits/v6/gpt6/seed-prompts.md").read_text()
    quoted = re.findall(r"^> (.+)$", source, re.MULTILINE)
    names = re.findall(r"^\*\*([a-z-]+)\*\*$", source, re.MULTILINE)
    common, lenses = quoted[0], dict(zip(names, quoted[1:], strict=True))
    assert len(lenses) == 9

    m = load_manifest("edition3-testnet")
    for seat in m.assemblies:
        expected = common + "\n\n" + lenses[seat.id]
        # The lens is the first head of C1's working state, and, until C1 delivers state
        # in the request, the prompt the seat actually reads. Both, verbatim, from §11.
        assert seat.initial_state == {"lens": expected}, seat.id
        assert seat.system_prompt == expected, seat.id


def test_edition3_keys_leave_every_earlier_world_identical():
    """A key added for edition 3 may not rename a world that predates it.

    The kill contract, the provider inventories and the three per-seat fields are dropped
    from the canonical JSON at their defaults, so the edition 2 manifest hash pinned in
    `tests/runtime/test_manifests.py` and the roster hash its charter was ratified against
    are both exactly what they were.
    """
    from factorylab.charter.provenance import roster_hash

    m = load_manifest("edition2-testnet")
    assert m.kill == KillSpec() and m.kill.wind_down is False
    assert m.manifest_hash() == (
        "b184b1d8dc55daf56978ea51181be0d06e59493bef2727d97b2f67717efdbf8b")
    assert roster_hash(m) == (
        "e0d1d9fbb937952845a967b8fa5d1978e6882b940f685ad4b0016085cbb90f2a")
    payload = json.loads(m.canonical_json())
    assert "kill" not in payload and "providers" not in payload
    assert all("cadence_floor" not in a and "initial_state" not in a
               and "system_prompt" not in a for a in payload["assemblies"])


def test_edition3_preflight_passes_every_gate_up_to_the_namespace():
    """The ratified edition 3 charter binds to this roster; only a fresh namespace is missing.

    `scripts/rehearsal.py preflight` binds a rehearsal to the exact charter a roster voted.
    The edition 3 roster ratified `docs/charter/edition3-ratified.toml` on 15 September
    (docs/charter/edition3-ratification.json): its roster digest is this roster's and its
    cards are what the manifest loads verbatim. The base manifest deliberately carries no
    exchange client namespace (a rehearsal copy supplies one), so preflight on the base
    stops exactly there and nowhere earlier.
    """
    import tomllib

    from scripts.draft_edition1 import charter_digest, roster_hash
    from scripts.rehearsal import preflight, voted_charter

    world = Path("worlds/edition3-testnet.toml")
    charter_path = Path("docs/charter/edition3-ratified.toml")
    m = load_manifest(str(world))

    voted = voted_charter(charter_path, m)
    raw = tomllib.loads(world.read_text())
    loaded = {k: v for k, v in raw["charter"].items()
              if k not in ("ratified_sha256", "roster_sha256")}
    assert loaded == voted
    assert raw["charter"]["ratified_sha256"] == charter_digest(voted)
    assert raw["charter"]["roster_sha256"] == roster_hash(m)
    assert [c["id"] for c in voted["cards"]] == ["censorship-bound"]
    assert all(isinstance(n, dict) and n["definition"] for n in voted["norms"])
    with pytest.raises(ValueError, match="fresh exchange client namespace"):
        preflight(world, charter_path)

def _exposed_runtime(*, wind: bool, ledger_path=None) -> Runtime:
    """A scripted world holding one resting order, one perp position and one spot balance."""
    manifest = load_manifest("scripted")
    manifest = replace(manifest, kill=KillSpec(wind_down=wind))
    rt = Runtime(manifest, events=40, seed=1, initial_balance_micro=None,
                 ledger_path=ledger_path, drip=False, router_gamma=0.1)
    exchange = rt.exchange
    mid = exchange.mids()["BTC"]
    # One resting limit order, far from the mid so it cannot cross into a fill.
    resting = exchange.place(Order("BTC", True, Decimal("0.001"), kind=_limit(),
                                   limit_px=(mid / 2).quantize(Decimal("0.01")),
                                   client_id="resting-1"))
    assert resting.status == "resting"
    # One open perp position.
    assert exchange.place(Order("BTC", True, Decimal("0.002"),
                                client_id="perp-1")).status == "filled"
    # One spot balance, bought with spot cash.
    rt.treasury.transfer("perps_to_spot", "20", handle="transfer", now_ns=1)
    rt.treasury.tick(2)
    exchange.sync_cash(rt.treasury.venue_balance_usd)
    assert exchange.place(Order("BTC/USDC", True, Decimal("0.0002"), market="spot",
                                client_id="spot-1")).status == "filled"
    exchange.drain_events()

    account = exchange.account()
    assert len(exchange.open_orders()) == 1
    assert [p.coin for p in account.positions if p.size] == ["BTC"]
    assert [b.coin for b in account.spot_balances if b.coin != "USDC" and b.total > 0]
    return rt


def _limit():
    from factorylab.world.exchange import OrderKind

    return OrderKind.LIMIT


def test_wind_down_empties_the_venue_and_ledgers_every_order_before_terminated():
    """C5: cancel, close, sell, each ledgered with its result, all of it before the end."""
    rt = _exposed_runtime(wind=True)
    report = rt.kill("explicit_kill:operator")

    assert report["attempted"] and report["failed"] == 0
    assert report["cancelled"] == 1 and report["closed"] == 1 and report["sold"] == 1
    assert report["orders"] == 3

    account = rt.exchange.account()
    assert rt.exchange.open_orders() == []
    assert [p for p in account.positions if p.size] == []
    assert [b for b in account.spot_balances if b.coin != "USDC" and b.total > 0] == []

    items = list(rt.ledger.items())
    steps = [i for i in items if i["kind"] == "kill.wind_down"]
    assert [s["step"] for s in steps] == ["cancelled", "closed", "sold", "summary"]
    assert all(s["result"]["status"] in ("cancelled", "filled")
               for s in steps if s["step"] != "summary")
    assert steps[-1]["orders"] == 3
    # The whole wind-down is in the diary before the event that seals it.
    terminated = [i for i in items
                  if (i.get("event") or {}).get("kind") == "Terminated"]
    assert len(terminated) == 1
    last_wind_down = max(i["seq"] for i in items if i["kind"] == "kill.wind_down")
    assert terminated[0]["seq"] > last_wind_down
    assert terminated[0]["seq"] == max(i["seq"] for i in items)
    assert rt.termination.final and rt.termination.reason == "explicit_kill:operator"


def test_wind_down_false_leaves_the_venue_exactly_as_it_was():
    """The old behaviour, still available and still documented: exposure survives the world."""
    rt = _exposed_runtime(wind=False)
    before = rt.exchange.account()
    report = rt.kill("explicit_kill:operator")

    assert report == {"attempted": False, "orders": 0}
    assert len(rt.exchange.open_orders()) == 1
    after = rt.exchange.account()
    assert [(p.coin, p.size) for p in after.positions] == [
        (p.coin, p.size) for p in before.positions]
    assert [(b.coin, b.total) for b in after.spot_balances] == [
        (b.coin, b.total) for b in before.spot_balances]
    assert not [i for i in rt.ledger.items() if i["kind"] == "kill.wind_down"]
    assert rt.termination.final


def test_an_unreachable_venue_is_ledgered_and_the_world_still_dies():
    """A kill a venue could block is not a kill. Every read and every order is guarded."""

    class Unreachable:
        """Answers nothing: reads raise, and so would any write that got that far."""

        name = "unreachable"

        def open_orders(self):
            raise OSError("venue unreachable")

        def account(self):
            raise OSError("venue unreachable")

        def mids(self):
            raise OSError("venue unreachable")

    rt = _exposed_runtime(wind=True)
    rt.exchange = Unreachable()
    report = rt.kill("explicit_kill:operator")

    assert rt.termination.final and rt.termination.reason == "explicit_kill:operator"
    assert report["failed"] == 2 and report["orders"] == 0
    assert {e["step"] for e in report["errors"]} == {"open_orders", "account"}
    assert {e["error"] for e in report["errors"]} == {"OSError"}
    failures = [i for i in rt.ledger.items()
                if i["kind"] == "kill.wind_down" and i["step"] == "read_failed"]
    assert {f["read"] for f in failures} == {"open_orders", "account"}
    assert all(f["error"] == "OSError" for f in failures)


def test_a_refusing_venue_is_recorded_order_by_order_and_never_raises():
    """A venue that rejects every write leaves a legible partial wind-down, not an exception."""

    class Refusing:
        name = "refusing"

        def open_orders(self):
            return [{"order_id": "1", "coin": "BTC", "side": "buy", "size": Decimal("1"),
                     "price": Decimal("1")}]

        def account(self):
            from factorylab.world.exchange import AccountState, Position, SpotBalance

            return AccountState(
                equity_usd=Decimal(0), cash_usd=Decimal(0),
                positions=(Position("BTC", Decimal("0.5"), Decimal("60000")),),
                margin_used_usd=Decimal(0),
                spot_balances=(SpotBalance("PURR", Decimal("100"), Decimal("100")),))

        def mids(self):
            return {"BTC": Decimal("60000"), "PURR/USDC": Decimal("4.6")}

        def cancel(self, order_id, *, coin=None, client_id=None):
            raise RuntimeError("venue refused the cancel")

        def close(self, coin, size=None, *, client_id=None, market="perp"):
            return {"status": "rejected", "error": "venue refused the close"}

    ledger = _MemoryLedger()
    report = wind_down(Refusing(), ledger)
    assert report["orders"] == 3 and report["failed"] == 3
    assert report["cancelled"] == report["closed"] == report["sold"] == 0
    assert [i["step"] for i in ledger.rows] == ["cancelled", "closed", "sold", "summary"]
    assert ledger.rows[0]["result"] == {"status": "failed", "error": "RuntimeError"}


def test_dust_is_left_where_it_is():
    """Selling a dollar of PURR costs more than the dollar, so the contract does not."""

    class Dusty:
        name = "dusty"

        def open_orders(self):
            return []

        def account(self):
            from factorylab.world.exchange import AccountState, SpotBalance

            return AccountState(
                equity_usd=Decimal(0), cash_usd=Decimal(0), positions=(),
                margin_used_usd=Decimal(0),
                spot_balances=(SpotBalance("USDC", Decimal("5"), Decimal("5")),
                               SpotBalance("PURR", Decimal("0.1"), Decimal("0.1"))))

        def mids(self):
            return {"PURR/USDC": Decimal("4.6")}

    ledger = _MemoryLedger()
    report = wind_down(Dusty(), ledger, dust_micro=1_000_000)
    assert report["orders"] == 0 and report["sold"] == 0 and report["failed"] == 0
    dust = [i for i in ledger.rows if i["step"] == "dust"]
    assert len(dust) == 1 and dust[0]["coin"] == "PURR/USDC"
    assert dust[0]["value_micro"] == 460_000


def test_the_witness_line_carries_the_contract_and_the_count(tmp_path):
    """The record a restored copy of the diary cannot carry says what the kill owed a venue."""
    manifest = replace(load_manifest("scripted"), kill=KillSpec(wind_down=True))
    path = tmp_path / "runs" / "w.jsonl"
    path.parent.mkdir()
    run_world(manifest, events=1, seed=1, ledger_path=str(path), kill_at_end=True)

    lines = [json.loads(raw)
             for raw in witness.witness_path(path).read_text().splitlines()]
    assert len(lines) == 1
    assert lines[0]["event"] == "kill" and lines[0]["reason"] == "explicit_kill:budget"
    assert lines[0]["wind_down"] is True
    assert lines[0]["wind_down_orders"] == _wind_down_orders(path, manifest)


def test_a_world_that_precommitted_nothing_is_witnessed_as_owing_nothing(tmp_path):
    path = tmp_path / "runs" / "w.jsonl"
    path.parent.mkdir()
    manifest = load_manifest("scripted")
    assert manifest.kill.wind_down is False
    run_world(manifest, events=1, seed=1, ledger_path=str(path), kill_at_end=True)

    line = json.loads(witness.witness_path(path).read_text().splitlines()[0])
    assert line["wind_down"] is False and line["wind_down_orders"] == 0


def _wind_down_orders(path, manifest) -> int:
    summary = [i for i in Ledger.open_read_only(
        path, manifest=json.loads(manifest.canonical_json())).items()
        if i["kind"] == "kill.wind_down" and i["step"] == "summary"]
    assert len(summary) == 1
    return summary[0]["orders"]


class _MemoryLedger:
    """Just enough ledger for the wind-down: it appends, and it keeps what it appended."""

    def __init__(self):
        self.rows: list[dict] = []

    def append(self, item: dict) -> int:
        self.rows.append(item)
        return len(self.rows)


# --- the calibration gate ------------------------------------------------------------------


def test_the_case_set_has_at_least_forty_cases_with_one_determinate_outcome_each():
    """C5's gate: forty bounded cases per route, and no case without an expected answer."""
    from scripts.calibrate_seats import CATEGORIES, GATE, case_set

    cases = case_set()
    assert len(cases) >= GATE["min_cases_per_route"] >= 40
    assert len({c.id for c in cases}) == len(cases)
    for case in cases:
        assert case.category in CATEGORIES
        assert case.expected["status"] in ("ok", "refused")
        if case.expected["status"] == "ok":
            # An expected answer is exact values, or a program whose output is fixed.
            assert case.expected.get("outputs") or case.program_stdout is not None
        else:
            assert not case.expected.get("outputs")  # a refusal has nothing to report

    # Every kind GPT-6 §10.3 names is present, and the critical categories are marked.
    counts = {name: sum(1 for c in cases if c.category == name) for name in CATEGORIES}
    assert all(counts[name] > 0 for name in CATEGORIES), counts
    for case in cases:
        assert case.critical is (case.category in GATE["critical_categories"])
    # The restart cases come after the others: state is retrieved after other turns.
    order = [c.category for c in cases]
    assert order.index("restart") > max(order.index(name) for name in
                                        ("fee", "funding", "refusal", "safety",
                                         "construction"))
    # The arithmetic is checkable without a model: the carry cases split into known
    # opportunities and known no-ops by the same computation.
    for case in cases:
        if case.category in ("opportunity", "no_op"):
            net = Decimal(case.expected["outputs"]["net_usd"])
            assert case.expected["outputs"]["worth_doing"] is (net > 0)
            assert (case.category == "opportunity") is (net > 0)


def test_the_gate_fails_on_too_few_cases_a_critical_miss_or_malformed_returns():
    """Three necessary conditions, each of which alone fails the route."""
    from scripts.calibrate_seats import GATE, score_cases

    def outcome(name, category, met, valid=True, critical=False, **extra):
        return {"case": name, "category": category, "critical": critical, "met": met,
                "valid": valid, "status": "ok", "expected_status": "ok", **extra}

    full = [outcome(f"c{i}", "fee" if i < 10 else "opportunity", True,
                    critical=i < 10) for i in range(45)]
    assert score_cases({"route": full})["routes"]["route"]["passed"]
    assert score_cases({"route": full})["passed"]

    thin = score_cases({"route": full[:39]})["routes"]["route"]
    assert thin["enough_cases"] is False and thin["passed"] is False

    missed = score_cases({"route": [outcome("c0", "safety", False, critical=True), *full]})
    assert missed["routes"]["route"]["failed_critical"] == ["c0"]
    assert missed["routes"]["route"]["passed"] is False and missed["passed"] is False

    malformed = [*full, *[outcome(f"bad{i}", "opportunity", False, valid=False)
                          for i in range(4)]]
    row = score_cases({"route": malformed})["routes"]["route"]
    assert row["valid_share"] < GATE["min_valid_share"]
    assert row["valid_share_met"] is False and row["passed"] is False

    # A case the host could not run is named and counted as not met, never as a pass.
    unavailable = [*full, outcome("construction-sum-0", "construction", False,
                                  unavailable=True, critical=True)]
    row = score_cases({"route": unavailable})["routes"]["route"]
    assert row["unavailable"] == ["construction-sum-0"] and row["passed"] is False

    assert score_cases({})["passed"] is False  # no route ran: nothing was screened


def test_a_case_is_met_only_by_the_exact_outcome():
    """No partial credit and no rubric: the wrong value and the wrong outcome both fail."""
    from scripts.calibrate_seats import case_outcome, case_set

    class Ret:
        def __init__(self, status, outputs):
            self.status, self.outputs = status, outputs

    cases = {c.id: c for c in case_set()}
    fee = next(c for c in cases.values() if c.category == "fee")
    assert case_outcome(fee, Ret("ok", dict(fee.expected["outputs"])))["met"]
    assert not case_outcome(fee, Ret("ok", {**fee.expected["outputs"],
                                            "fee_usd": "0.000000"}))["met"]
    assert not case_outcome(fee, Ret("refused", {}))["met"]

    refusal = next(c for c in cases.values() if c.category == "refusal")
    assert case_outcome(refusal, Ret("refused", {}))["met"]
    # Answering an unanswerable question is exactly the failure the case exists to find.
    answered = case_outcome(refusal, Ret("ok", {"fill_id": "made up", "settled_at_ns": 1}))
    assert not answered["met"] and answered["valid"] is True

    build = next(c for c in cases.values() if c.category == "construction")
    assert case_outcome(build, Ret("ok", {"program": "x"}),
                        {"stdout": build.program_stdout})["met"]
    assert not case_outcome(build, Ret("ok", {"program": "x"}), {"stdout": "wrong"})["met"]
    missing = case_outcome(build, Ret("ok", {"program": "x"}),
                           {"error": "no jail on this host"})
    assert not missing["met"] and missing["unavailable"] is True
