"""C5: a Venice purchase is confirmed on the canonical debit; the credit balance is advisory.

Cold audit F2: the balance is a stock, not a flow. A $5 purchase on a $5 balance
whose acknowledgment is lost, followed by one micro-dollar of usage, reads
9,999,999 rather than 10,000,000 and was never confirmed, although the chain
holds the exact debit. These witnesses hold the reviewer's names: the first
scenario now confirms, and the control without canonical evidence still refuses.
"""

from types import SimpleNamespace

import pytest

from factorylab.world.evm import BASE, event_topic, word_address
from tests.world.test_treasury_rails import setup as rail_setup

TRANCHE = 5_000_000
NONCE = "0x" + "ab" * 32


def debited_rail():
    """A mainnet-shaped rail whose Base fake holds the exact, finalized debit of the tranche."""
    rail = rail_setup()
    rail.testnet = False
    rail.base.chain = BASE
    topics = [event_topic("AuthorizationUsed(address,bytes32)"),
              "0x" + word_address(rail.reserve_address).hex(), NONCE]
    event = {"address": BASE.usdc, "topics": topics, "transactionHash": "0xtransaction"}
    rail.base.log_rows = [event]
    rail.base.proved = {"status": "0x1", "blockHash": "0xblock", "logs": [event]}
    rail.base.credit_transfer = True
    return rail


def purchase(*, submission=None, credit_before=TRANCHE, started_ns=1_000):
    state = {
        "amount_micro": TRANCHE,
        "started_ns": started_ns,
        "reference": {"authorization": {"nonce": NONCE, "to": "0xrecipient"},
                      "start_block": 99, "network": "eip155:8453",
                      "credit_before_micro": credit_before},
        "route_data": {},
    }
    if submission is not None:
        state["route_data"]["submission"] = submission
    return state


def test_lost_ack_and_one_micro_of_usage_prevent_confirmation():
    """The reviewer's false negative: the debit is on chain, the balance is one micro short."""
    rail = debited_rail()
    rail._venice_client = lambda: SimpleNamespace(venice_balance=lambda: 2 * TRANCHE - 1)
    rail.metered_usage_since = lambda since_ns: 1 if since_ns == 1_000 else None
    result = rail._venice_receipt(purchase())
    assert result is not None and result["confirmed"] and result["principal_moved"]
    assert result["received_micro"] == TRANCHE and result["fee_micro"] == 0
    evidence = result["evidence"]
    assert evidence["tx_hash"] == "0xtransaction" and evidence["nonce"] == NONCE
    assert evidence["credit_before_micro"] == TRANCHE
    assert evidence["amount_micro"] == TRANCHE
    assert evidence["observed_micro"] == 2 * TRANCHE - 1
    assert evidence["balance_source"] == "balance_read"
    assert evidence["balance_shortfall_micro"] == 1
    assert evidence["metered_usage_since_micro"] == 1


@pytest.mark.parametrize("control", ["no_receipt", "reverted", "no_authorization_log",
                                     "no_transfer", "other_nonce"])
def test_missing_canonical_debit_control_does_not_confirm(control):
    """A generous balance is never enough: every missing piece of the debit refuses."""
    rail = debited_rail()
    if control == "no_receipt":
        rail.base.proved = None
    elif control == "reverted":
        rail.base.proved["status"] = "0x0"
    elif control == "no_authorization_log":
        rail.base.proved["logs"] = []
    elif control == "no_transfer":
        rail.base.credit_transfer = False
    else:
        rail.base.log_rows[0]["topics"][2] = "0x" + "cd" * 32
    rail._venice_client = lambda: SimpleNamespace(venice_balance=lambda: 10 * TRANCHE)
    generous = {"credit_after_micro": 10 * TRANCHE}
    assert rail._venice_receipt(purchase(submission=generous)) is None
    assert rail._venice_receipt(purchase()) is None


def test_a_retained_acknowledgment_confirms_with_its_recorded_balance():
    rail = debited_rail()

    def forbidden():
        raise AssertionError("an acknowledged purchase reads no balance")

    rail._venice_client = forbidden
    result = rail._venice_receipt(purchase(submission={"credit_after_micro": 2 * TRANCHE}))
    evidence = result["evidence"]
    assert evidence["balance_source"] == "acknowledgment"
    assert evidence["observed_micro"] == evidence["credit_after_micro"] == 2 * TRANCHE
    assert evidence["balance_shortfall_micro"] == 0
    assert evidence["metered_usage_since_micro"] is None  # no diary bound to this rail


def test_an_unreadable_balance_or_meter_is_recorded_as_such_and_never_blocks():
    rail = debited_rail()

    def unreachable():
        raise ConnectionError("venice is down")

    rail._venice_client = unreachable
    rail.metered_usage_since = lambda since_ns: (_ for _ in ()).throw(RuntimeError("no diary"))
    result = rail._venice_receipt(purchase())
    assert result is not None and result["confirmed"]
    evidence = result["evidence"]
    assert evidence["observed_micro"] is None and evidence["balance_source"] == "unavailable"
    assert evidence["balance_shortfall_micro"] is None
    assert evidence["metered_usage_since_micro"] is None
