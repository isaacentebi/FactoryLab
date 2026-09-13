"""A paid empty response has its own operator reason."""

from tests.audit.test_r3_f1_probe import _probe, seller  # noqa: F401


def test_paid_empty_completion_reason(seller, capsys):  # noqa: F811
    assert _probe(["--max-tokens", "32"]) == 1
    assert capsys.readouterr().err.splitlines()[0] == "factorylab probe: empty_completion"
