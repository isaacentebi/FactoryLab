"""The polymarket pot against Polygon's own word (issue #180), through the running world.

Chapter II §II.b (physics is enforced): the pot's reconciliation held the books to
Polymarket's APIs, so an answer they gave wrong the same way everywhere was adopted.
Each test here makes the chain and the APIs disagree, or the chain go unread, and
asserts that buying waits or halts and nothing is paid that the chain does not state.
The venue is ``LivePolymarket`` on ``FakeClob``, whose fake chain answers from the
simulated venue's own state; nothing touches a network or a funded key.
"""

from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest
from eth_utils import keccak

from factorylab.runtime import polymarket
from factorylab.runtime.resume import JournalProxy, RecoveryJournal
from factorylab.runtime.worlds import PolymarketSpec, load_manifest
from factorylab.world import polygon_ctf
from factorylab.world import polymarket_clob as clob
from tests.helpers import collateral_decision
from tests.runtime.test_polymarket_live import buy, items, live_world, maker_fill, token
from tests.runtime.test_polymarket_surface import still_fake
from tests.world.fake_clob import live_venue


def drifts(rt):
    return [row for row in items(rt, "polymarket.drift") if "chain" in row]


def test_an_agreeing_chain_confirms_the_pot_and_buying_goes_on():
    rt, server = live_world()
    maker_fill(rt, server, collateral_decision(rt), price="0.40")
    polymarket.tick(rt)
    methods = {params[0]["data"][:10] for method, params in server.chain_calls
               if method == "eth_call"}
    assert "0x" + keccak(text="balanceOf(address)")[:4].hex() in methods
    assert "0x" + keccak(text="balanceOfBatch(address[],uint256[])")[:4].hex() in methods
    assert not items(rt, "polymarket.drift") and not rt.polymarket.drifting
    assert buy(rt, server, collateral_decision(rt), price="0.20")["status"] == "resting"


def test_pusd_the_chain_does_not_hold_is_drift_and_buying_waits_until_it_agrees():
    rt, server = live_world()
    server.chain_usdc_delta = Decimal("-1")  # the API states 1 pUSD the chain does not hold
    polymarket.tick(rt)
    (row,) = drifts(rt)
    assert row["chain"]["usdc"] == str(server.fake._cash - 1)
    refused = buy(rt, server, collateral_decision(rt))
    assert refused["status"] == "rejected" and refused["error"] == polymarket.DRIFT_REFUSAL
    server.chain_usdc_delta = Decimal(0)
    polymarket.tick(rt)
    assert not rt.polymarket.drifting
    assert buy(rt, server, collateral_decision(rt), slot="tool:1")["status"] == "resting"


def test_tokens_the_api_lists_but_the_chain_does_not_hold_are_drift():
    """The consistent wrong answer #180 names: the books and the API agree on 10 tokens,
    and only the chain says the wallet holds fewer."""
    rt, server = live_world()
    maker_fill(rt, server, collateral_decision(rt), price="0.40")
    polymarket.tick(rt)
    assert not rt.polymarket.drifting
    server.chain_tokens[token(server)] = Decimal(7)
    polymarket.tick(rt)
    (row,) = drifts(rt)
    assert row["chain"]["tokens"] == [token(server)]
    refused = buy(rt, server, collateral_decision(rt), price="0.20")
    assert refused["error"] == polymarket.DRIFT_REFUSAL


def test_tokens_the_chain_holds_that_the_api_omits_are_drift():
    rt, server = live_world()
    maker_fill(rt, server, collateral_decision(rt), price="0.40")
    polymarket.tick(rt)
    other = token(server, "fake-2")
    server.chain_tokens[other] = Decimal(3)
    # A token the chain is asked about: one the books hold. The API lists none of fake-2.
    rt.polymarket.cursor["book"][other] = ["3", "0.1"]
    polymarket.tick(rt)
    assert any(other in row["chain"]["tokens"] for row in drifts(rt))
    assert rt.polymarket.drifting


def test_an_unread_chain_fails_closed_from_the_opening_on():
    rt, server = live_world(opened=False)
    server.chain_fail = 10**6
    polymarket.tick(rt)
    assert rt.polymarket.opening is not None  # the API's opening is read ...
    assert rt.polymarket.drifting  # ... but nothing is bought on it unconfirmed
    rows = items(rt, "polymarket.chain_unavailable")
    assert rows and {row["reason"] for row in rows} == {"the pot was not read on Polygon"}
    refused = buy(rt, server, collateral_decision(rt))
    assert refused["error"] == polymarket.DRIFT_REFUSAL
    assert ("POST", "/order") not in server.calls
    server.chain_fail = 0
    polymarket.tick(rt)
    assert not rt.polymarket.drifting


@pytest.mark.parametrize("answer", [
    lambda method, a: {**a, "result": "0x1"} if method == "eth_chainId" else a,
    lambda method, a: {"jsonrpc": "2.0", "id": a["id"], "error": {"code": 429}},
    lambda method, a: ({**a, "result": a["result"][:-2]} if method == "eth_call" else a),
])
def test_an_answer_that_is_not_polygon_s_is_unread_and_holds_buying(answer):
    rt, server = live_world()
    server.chain_answer = answer
    polymarket.tick(rt)
    assert items(rt, "polymarket.chain_unavailable") and rt.polymarket.drifting


def test_the_unread_row_is_the_same_whatever_failed_so_a_replay_writes_it_again():
    """A replay reconstructs a recorded failure as another type (``_recorded_error``):
    the row the run wrote must not depend on which."""
    rows = []
    for failure in (polygon_ctf.ChainUnread("x"), RuntimeError("external call failed")):
        rt, _server = live_world()

        def fail(*, tokens, failure=failure):
            raise failure

        rt.polymarket.venue.target.chain_account = fail
        polymarket.tick(rt)
        rows.append([{k: v for k, v in row.items() if k != "ts"}
                     for row in items(rt, "polymarket.chain_unavailable")])
    assert rows[0] == rows[1] != []


def test_a_replay_answers_the_chain_from_the_journal_and_sends_nothing():
    venue, server = live_venue()
    recorded = []

    def append(entry):
        recorded.append(entry)
        return len(recorded) - 1

    journal = RecoveryJournal(SimpleNamespace(append=append), lambda: 0)
    journal.active = True
    tokens = sorted(server.fake._tokens)[:2]
    first = JournalProxy(venue, journal, "polymarket").chain_account(tokens=tokens)
    server.chain_fail = 1
    with pytest.raises(Exception):  # noqa: B017 - recorded as it is replayed
        JournalProxy(venue, journal, "polymarket").chain_account(tokens=[])
    sent = len(server.chain_calls)
    replay = RecoveryJournal(SimpleNamespace(append=append), lambda: 0)
    replay.active = replay.recovering = True
    replay.tail = [{**entry, "ts": 0, "seq": seq} for seq, entry in enumerate(list(recorded))]
    server.chain_usdc_delta = Decimal(5)  # the chain moved since: the replay reads the run
    again = JournalProxy(venue, replay, "polymarket")
    assert again.chain_account(tokens=tokens) == first
    with pytest.raises(Exception):  # noqa: B017 - the recorded failure, as recorded
        again.chain_account(tokens=[])
    assert len(server.chain_calls) == sent


def test_a_resumed_journal_completes_an_interrupted_chain_read_by_reading_again():
    from tests.runtime.test_polymarket_live import _journaled_names

    rt, server = live_world()
    assert "polymarket.chain_account" in _journaled_names(rt, server)


# --- resolutions: Gamma's payout is paid only once Polygon reports the same one ----------


def resolved_world(**kwargs):
    fake = still_fake(resolutions={"fake-1": (10**15, 0)})  # YES wins
    rt, server = live_world(fake=fake, **kwargs)
    maker_fill(rt, server, collateral_decision(rt), price="0.40")  # 10 YES tokens held
    polymarket.tick(rt)
    rt.clock.now_ns = 10**15
    server.advance(10**15)
    return rt, server


def condition_of(server, market="fake-1"):
    return server.fake._markets[market]["condition_id"]


def test_a_resolution_polygon_confirms_is_paid():
    rt, server = resolved_world()
    polymarket.tick(rt)
    (row,) = items(rt, "polymarket.resolution")
    assert row["payout"] == "1" and not rt.polymarket.contradicted
    bound = rt.polymarket.cursor["bound"]
    assert bound["position"][token(server)] == [condition_of(server), 0, False]
    assert bound["payout"][condition_of(server)] == ["1", "1", "0"]


def test_a_resolution_polygon_has_not_reported_waits_and_is_paid_once_it_does():
    rt, server = resolved_world()
    server.chain_lag.add("fake-1")  # Gamma: resolved; the chain: not yet reported
    polymarket.tick(rt)
    polymarket.tick(rt)
    assert not items(rt, "polymarket.resolution") and not rt.polymarket.contradicted
    assert token(server) not in rt.polymarket.cursor.get("resolved", {})
    server.chain_lag.clear()
    polymarket.tick(rt)
    (row,) = items(rt, "polymarket.resolution")
    assert row["payout"] == "1"


@pytest.mark.parametrize("report", [(1, [0, 1]), (2, [1, 1])])
def test_a_payout_polygon_contradicts_halts_buying_and_pays_nothing(report):
    """Gamma pays YES 1; the chain reports NO (or 50-50): a payout Gamma states
    consistently is still not adopted."""
    rt, server = resolved_world()
    server.chain_payouts[condition_of(server)] = report
    polymarket.tick(rt)
    assert not items(rt, "polymarket.resolution")
    assert rt.polymarket.contradicted
    assert any("Polygon" in row.get("reason", "") for row in items(rt, "polymarket.drift"))
    refused = buy(rt, server, collateral_decision(rt), market="fake-2", price="0.20")
    assert refused["error"] == polymarket.MAKER_ONLY_REFUSAL
    polymarket.tick(rt)
    assert not items(rt, "polymarket.resolution")


def test_a_token_its_market_s_condition_does_not_issue_halts_and_pays_nothing():
    """Gamma names a condition whose position at the token's index, on chain, is another
    token: the payout of that condition is not this token's."""
    rt, server = resolved_world()
    real = server._eth_call
    position = keccak(text="getPositionId(address,bytes32)")[:4]
    server._eth_call = lambda to, data: ((1).to_bytes(32, "big") if data[:4] == position
                                         else real(to, data))
    polymarket.tick(rt)
    assert not items(rt, "polymarket.resolution") and rt.polymarket.contradicted
    assert any("does not issue this token" in row.get("reason", "")
               for row in items(rt, "polymarket.drift"))


def test_a_market_of_the_other_kind_is_not_proven_and_pays_nothing():
    """The collateral follows the market's kind: Gamma calling a standard market
    neg-risk names another position, so the token is not proven."""
    rt, server = resolved_world()
    server.market_row = lambda row: {**row, "negRisk": True}
    polymarket.tick(rt)
    assert not items(rt, "polymarket.resolution") and rt.polymarket.contradicted


def test_a_resolution_read_the_chain_did_not_answer_is_read_again_never_paid_unread():
    rt, server = resolved_world()
    server.chain_fail = 10**6
    polymarket.tick(rt)
    assert not items(rt, "polymarket.resolution") and not rt.polymarket.contradicted
    server.chain_fail = 0
    polymarket.tick(rt)
    assert [row["payout"] for row in items(rt, "polymarket.resolution")] == ["1"]


def test_a_condition_gamma_later_changes_contradicts_the_proof_bound_at_first_sight():
    rt, server = resolved_world()
    server.chain_lag.add("fake-1")
    polymarket.tick(rt)  # proven and bound to its condition; the payout not yet reported
    assert token(server) in rt.polymarket.cursor["bound"]["position"]
    server.market_row = lambda row: {**row, "conditionId": "0x" + "77" * 32}
    server.chain_lag.clear()
    polymarket.tick(rt)
    assert not items(rt, "polymarket.resolution") and rt.polymarket.contradicted


# --- construction ------------------------------------------------------------------------


def test_a_live_pot_cannot_be_built_without_its_chain():
    with pytest.raises(TypeError):
        clob.LivePolymarket(funder="0x" + "ab" * 20, signature_type=0, budget=10)
    with pytest.raises(clob.PolymarketRefused, match="Polygon"):
        clob.LivePolymarket(funder="0x" + "ab" * 20, signature_type=0, budget=10, chain=None)


def test_an_unread_payout_check_holds_buying_while_balances_still_answer():
    """Sol P0, round 1: an endpoint answering balance reads but failing payout reads left
    buying on while the payout check it owed went unread."""
    rt, server = resolved_world()
    real = server._eth_call
    payout = keccak(text="payoutDenominator(bytes32)")[:4]

    def payouts_down(to, data):
        if data[:4] == payout:
            raise clob.PolymarketUnavailable("transport: TimeoutError")
        return real(to, data)

    server._eth_call = payouts_down
    polymarket.tick(rt)
    assert not items(rt, "polymarket.resolution") and rt.polymarket.drifting
    assert not drifts(rt) and items(rt, "polymarket.chain_unavailable")
    refused = buy(rt, server, collateral_decision(rt), market="fake-2", price="0.20")
    assert refused["error"] == polymarket.DRIFT_REFUSAL
    server._eth_call = real
    polymarket.tick(rt)
    assert [row["payout"] for row in items(rt, "polymarket.resolution")] == ["1"]


def test_a_resolution_pays_only_what_polygon_holds():
    """Sol P0, round 1: the payout was proven on chain but paid on the books' quantity,
    which only the APIs stated; the APIs and books say 10 tokens, the chain holds 7."""
    rt, server = resolved_world()
    server.chain_tokens[token(server)] = Decimal(7)
    polymarket.tick(rt)
    polymarket.tick(rt)
    assert not items(rt, "polymarket.resolution")
    assert token(server) in rt.polymarket.cursor["resolved"]  # proven, not yet paid
    assert rt.polymarket.drifting
    del server.chain_tokens[token(server)]
    polymarket.tick(rt)
    assert [(row["payout"], row["size"]) for row in items(rt, "polymarket.resolution")] == [
        ("1", "10")]


def test_the_installed_live_venue_reads_polygon(monkeypatch):
    monkeypatch.delenv(polygon_ctf.RPC_ENV, raising=False)
    spec = replace(load_manifest("scripted").polymarket, **vars(PolymarketSpec(
        enabled=True, venue="live", orders=True, funder="0x" + "ab" * 20,
        principal_micro=1_000_000)))
    venue = clob.live_venue(spec)
    assert isinstance(venue.chain, polygon_ctf.PolygonCtf)
    assert venue.chain._rpc == polygon_ctf.POLYGON_RPC
