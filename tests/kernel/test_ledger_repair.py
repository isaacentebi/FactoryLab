import pytest

from factorylab.kernel.ledger import Ledger, LedgerIntegrityError


@pytest.mark.parametrize("cut", [1, 10, 100])
def test_reopen_discards_only_unacknowledged_tail_and_records_repair(tmp_path, cut):
    path = tmp_path / "synthetic.jsonl"
    ledger = Ledger(path, manifest={}, key_path=str(path) + ".key")
    ledger.append({"kind": "acknowledged"})
    prefix = path.read_bytes()
    ledger.append({"kind": "unacknowledged", "payload": "x" * 100})
    raw = path.read_bytes()[:-cut]
    path.write_bytes(raw)
    reopened = Ledger.reopen(path, manifest={})
    assert reopened.verify() and not reopened.seal_key_released()
    assert path.read_bytes().startswith(prefix)
    items = reopened._recovery_items()
    assert [i["kind"] for i in items] == ["acknowledged", "ledger.repaired"]
    assert items[-1]["discarded_bytes"] == len(raw) - len(prefix)
    reopened.append({"kind": "continued"})
    again = Ledger.reopen(path, manifest={})
    assert [i["kind"] for i in again._recovery_items()].count("ledger.repaired") == 1


def test_torn_tail_never_repairs_a_corrupt_acknowledged_prefix(tmp_path):
    path = tmp_path / "synthetic.jsonl"
    ledger = Ledger(path, manifest={}, key_path=str(path) + ".key")
    ledger.append({"kind": "acknowledged"})
    raw = path.read_bytes().replace(b'"item"', b'"oops"') + b'{"item": "torn'
    path.write_bytes(raw)
    with pytest.raises(LedgerIntegrityError):
        Ledger.reopen(path, manifest={})
    assert path.read_bytes() == raw
