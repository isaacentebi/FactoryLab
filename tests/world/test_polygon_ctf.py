"""Polygon's own word on the polymarket pot (``world/polygon_ctf.py``; issue #180).

No network: every answer is either recorded from Polygon (chain 137, read-only
``eth_call`` at finalized block 0x5a7f759, 2026-10-03) or written here. The reader holds
no key and can move nothing; these tests attempt each way an answer could be adopted
when it should not be, and assert it is unread instead.
"""

import json

import pytest
from eth_abi import encode
from eth_utils import keccak

from factorylab.world import polygon_ctf as ctf

BLOCK = 0x5A7F759
OWNER = "0x" + "ab" * 20
#: A standard market and a neg-risk market, both resolved NO ([0, 1]), as Gamma lists
#: them (``conditionId``, ``clobTokenIds``), with the collection ids Polygon answered.
STANDARD = {
    "condition": "0xd6476150d718dd5c9c135d1ef67459f4639aaec71e1a80cd062cedfe946e6c1a",
    "tokens": ["41349797193840422983673651947593858858232532296686892536959529815131975740512",
               "28871177830017877675575784724750218794394250039434831759474413270552777065257"],
    "collections": ["0x4234caf4a88033fe260b2538da6c8b887c2a458616bfdc738b19a29fe4f2a764",
                    "0x02a4878f8b8d91db06af654450262ce0078cb69f858dfe674953f8e67ecab21b"]}
NEG_RISK = {
    "condition": "0x719d6a3b3dc68f874e34b6d3072df4a22bd616db5edbf547f54dadc7a6538d9b",
    "tokens": ["2338841808780425363725236417983937448335794372612611751805861911255570108051",
               "17741031805950526293735577922027056851024422977976297312169990535195658229358"],
    "collections": ["0x456fd49084500298b19ce228660a67d9f0c7e736eba34a82da50e416406816b3",
                    "0x1b274f2ccdb25ad2d90e5c214e951d7f7b5e2f7102b8624a7100bf41aff9e21c"]}


def word(value: int) -> str:
    return "0x" + value.to_bytes(32, "big").hex()


def selector(signature: str) -> bytes:
    return keccak(text=signature)[:4]


@pytest.mark.parametrize("market,collateral", [
    (STANDARD, "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"),
    (NEG_RISK, "0x3A3BD7bb9528E159577F7C2e685CC81A765002E2")])
def test_a_token_id_is_its_condition_s_position_for_the_market_s_collateral(market, collateral):
    """The recorded collection ids, with CTHelpers' ``getPositionId`` (keccak of the packed
    collateral and collection), give back Gamma's token ids: USDC.e for a standard market,
    the wrapped collateral for a neg-risk one, never pUSD."""
    assert ctf.collateral_for(market is NEG_RISK) == collateral
    for token, collection in zip(market["tokens"], market["collections"], strict=True):
        packed = bytes.fromhex(collateral[2:]) + bytes.fromhex(collection[2:])
        assert int.from_bytes(keccak(packed), "big") == int(token)
        pusd = bytes.fromhex(ctf.PUSD[2:]) + bytes.fromhex(collection[2:])
        assert int.from_bytes(keccak(pusd), "big") != int(token)


class Chain:
    """A JSON-RPC endpoint answering from a table of eth_call results, recording every
    request. ``rewrite`` may replace an answer."""

    def __init__(self, calls: dict[bytes, str] | None = None, *, chain_id: str = "0x89"):
        self.calls = calls or {}
        self.chain_id = chain_id
        self.sent: list[dict] = []
        self.rewrite = None

    def __call__(self, method, url, headers, body):
        assert method == "POST" and url == "https://polygon.test/rpc"
        request = json.loads(body)
        self.sent.append(request)
        name, params = request["method"], request["params"]
        if name == "eth_chainId":
            result = self.chain_id
        elif name == "eth_getBlockByNumber":
            assert params == ["finalized", False]
            result = {"number": hex(BLOCK)}
        else:
            assert name == "eth_call" and params[1] == hex(BLOCK)
            data = bytes.fromhex(params[0]["data"][2:])
            result = self.calls[data[:4]] if data[:4] in self.calls else self.calls[data]
        answer = {"jsonrpc": "2.0", "id": request["id"], "result": result}
        return answer if self.rewrite is None else self.rewrite(name, answer)


def reader(chain, **kwargs):
    return ctf.PolygonCtf(rpc="https://polygon.test/rpc", send=chain, **kwargs)


def resolution_answers(market):
    condition = bytes.fromhex(market["condition"][2:])
    answers = {selector("payoutDenominator(bytes32)"): word(1)}
    for index in (0, 1):
        answers[selector("payoutNumerators(bytes32,uint256)") + encode(
            ["bytes32", "uint256"], [condition, index])] = word(index)
        answers[selector("getCollectionId(bytes32,bytes32,uint256)") + encode(
            ["bytes32", "bytes32", "uint256"], [bytes(32), condition, 1 << index])] = (
            market["collections"][index])
    return answers


@pytest.mark.parametrize("market", [STANDARD, NEG_RISK])
def test_a_resolution_states_the_recorded_payout_and_proves_the_token(market):
    answers = resolution_answers(market)
    neg_risk = market is NEG_RISK
    for index in range(len(market["tokens"])):
        collateral = ctf.collateral_for(neg_risk)
        position = bytes.fromhex(collateral[2:]) + bytes.fromhex(
            market["collections"][index][2:])
        answers[selector("getPositionId(address,bytes32)") + encode(
            ["address", "bytes32"], [collateral, bytes.fromhex(
                market["collections"][index][2:])])] = (
            "0x" + keccak(position).hex())
    chain = Chain(answers)
    found = reader(chain).resolution(market["condition"], token=market["tokens"][1], index=1,
                                     neg_risk=neg_risk)
    assert found == {"block": BLOCK, "issues": True, "denominator": "1",
                     "numerators": ["0", "1"]}
    # The other kind's collateral makes another position: the token is not proven.
    other = reader(Chain(answers | {selector("getPositionId(address,bytes32)"): word(7)}))
    assert other.resolution(market["condition"], token=market["tokens"][1], index=1,
                            neg_risk=not neg_risk)["issues"] is False
    assert {r["method"] for r in chain.sent} == {"eth_chainId", "eth_getBlockByNumber",
                                                 "eth_call"}
    assert {r["params"][0]["to"] for r in chain.sent if r["method"] == "eth_call"} == {
        ctf.CONDITIONAL_TOKENS}


def account_chain(usdc=5_000_000, balances=(10_000_000, 0)):
    return Chain({selector("balanceOf(address)"): word(usdc),
                  selector("balanceOfBatch(address[],uint256[])"):
                      "0x" + encode(["uint256[]"], [list(balances)]).hex()})


def test_an_account_reads_pusd_and_every_token_at_one_finalized_block():
    chain = account_chain()
    tokens = STANDARD["tokens"]
    assert reader(chain).account(OWNER, tokens) == {
        "block": BLOCK, "usdc": "5000000", "tokens": {tokens[0]: "10000000", tokens[1]: "0"}}
    calls = [r["params"][0] for r in chain.sent if r["method"] == "eth_call"]
    assert [c["to"] for c in calls] == [ctf.PUSD, ctf.CONDITIONAL_TOKENS]
    batch = bytes.fromhex(calls[1]["data"][2:])
    assert batch == selector("balanceOfBatch(address[],uint256[])") + encode(
        ["address[]", "uint256[]"], [[OWNER, OWNER], [int(t) for t in tokens]])


@pytest.mark.parametrize("rewrite,why", [
    (lambda name, a: {**a, "id": a["id"] + 1}, "another request's answer"),
    (lambda name, a: {"jsonrpc": "2.0", "id": a["id"], "error": {"code": -32000}}, "an error"),
    (lambda name, a: {**a, "result": "0x" + "00" * 31} if name == "eth_call" else a,
     "a short word"),
    (lambda name, a: {**a, "result": "12"} if name == "eth_call" else a, "not hex"),
    (lambda name, a: {**a, "result": "0x"} if name == "eth_call" else a,
     "no code at the address"),
    (lambda name, a: ({**a, "result": {"number": "0x00ff"}}
                      if name == "eth_getBlockByNumber" else a), "a non-canonical quantity"),
    (lambda name, a: {k: v for k, v in a.items() if k != "jsonrpc"}, "not JSON-RPC 2.0"),
])
def test_an_answer_that_is_not_the_chain_s_is_unread_never_a_value(rewrite, why):
    chain = account_chain()
    chain.rewrite = rewrite
    with pytest.raises(ctf.ChainUnread):
        reader(chain).account(OWNER, STANDARD["tokens"])


@pytest.mark.parametrize("balances", [(10_000_000,), (1, 2, 3)])
def test_a_batch_that_does_not_answer_each_token_once_is_unread(balances):
    with pytest.raises(ctf.ChainUnread, match="tokens asked"):
        reader(account_chain(balances=balances)).account(OWNER, STANDARD["tokens"])


def test_a_batch_with_trailing_bytes_is_unread():
    chain = account_chain()
    chain.rewrite = lambda name, a: (
        {**a, "result": a["result"] + "00" * 32}
        if name == "eth_call" and len(a["result"]) > 66 else a)
    with pytest.raises(ctf.ChainUnread):
        reader(chain).account(OWNER, STANDARD["tokens"])


def test_another_chain_is_refused_before_any_read():
    chain = account_chain()
    chain.chain_id = "0x1"
    with pytest.raises(ctf.ChainUnread, match="not Polygon"):
        reader(chain).account(OWNER, STANDARD["tokens"])
    assert [r["method"] for r in chain.sent] == ["eth_chainId"]


def test_a_transport_failure_is_unread_and_names_no_url_or_body():
    def down(method, url, headers, body):
        raise OSError("https://polygon.test/rpc?key=SECRET said no")

    with pytest.raises(ctf.ChainUnread) as raised:
        reader(down).account(OWNER, [])
    assert "SECRET" not in str(raised.value) and "polygon.test" not in str(raised.value)


def test_a_request_past_the_budget_is_never_sent():
    chain = account_chain()
    limited = reader(chain, wall=lambda: 1_790_000_000_000_000_000, limit=3)
    with pytest.raises(ctf.ChainUnread, match="budget"):
        limited.account(OWNER, STANDARD["tokens"])  # 4 requests: the 4th is not sent
    assert len(chain.sent) == 3


@pytest.mark.parametrize("url", ["http://polygon.test/rpc", "https://user:pw@polygon.test/",
                                 "https://polygon.test/#frag", "polygon.test", ""])
def test_an_endpoint_that_is_not_plain_https_is_refused(url):
    with pytest.raises(ValueError) as raised:
        ctf.PolygonCtf(rpc=url)
    assert "pw" not in str(raised.value)


def test_the_operator_s_endpoint_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv(ctf.RPC_ENV, "https://polygon.test/rpc")
    assert ctf.PolygonCtf.from_environment()._rpc == "https://polygon.test/rpc"
    monkeypatch.delenv(ctf.RPC_ENV)
    assert ctf.PolygonCtf.from_environment()._rpc == ctf.POLYGON_RPC
    assert "polygon" not in repr(ctf.PolygonCtf.from_environment())


@pytest.mark.parametrize("call", [
    lambda r: r.account("0xnot-an-address", []),
    lambda r: r.account(OWNER, ["0123"]),
    lambda r: r.resolution("0x1234"),
    lambda r: r.resolution(STANDARD["condition"], token=STANDARD["tokens"][0], index=2),
])
def test_a_malformed_question_is_refused_before_anything_is_sent(call):
    chain = account_chain()
    with pytest.raises(ctf.ChainUnread):
        call(reader(chain))
    assert chain.sent == []
