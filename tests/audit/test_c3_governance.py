"""Cold audit round three, seat 3: retirement, the cadence and the architect's one control.

Each test reproduces one finding in docs/audits/v3/defects-fable.md and fails on the
audited commit. Nothing here touches a network.
"""

from dataclasses import asdict

import pytest

from factorylab.cortex.request import Return
from tests.conftest import make_runtime
from tests.runtime.test_fidelity import decision


def _experienced(rt, assemblies=None):
    rt._manage_reserve_window()
    for aid in assemblies or list(rt.assemblies):
        for _ in range(rt.m.committee.min_settled):
            decision(rt, aid, settled=True)


def _boundary(rt):
    rt.n += rt.m.timing.min_ratio * rt.ev.consequence_backstop_events + 1
    rt.cadence.advance(rt.n)
    rt.clock.now_ns = rt.n * rt.tick_clock.interval_ns
    rt.stats.reserve_windows += 1
    rt._manage_reserve_window()
    rt._activate_charter_if_due()


@pytest.mark.xfail(strict=False, reason="round three, open: docs/audits/v3/triage.md")
def test_finding_3_a_refused_amendment_blocks_every_later_retirement():
    """``activate_due`` refuses a passed amendment that an earlier activation made
    redundant. The refusal closes its ballots but never removes it from the cadence's
    waiting list, and ``_activate_retirements_if_due`` only activates the retirement at
    the head of that list. From then on no retirement can ever take effect."""
    rt = make_runtime()
    _experienced(rt)
    proposer = decision(rt, "seed-decider")
    card = {**asdict(rt.charter.cards[1]), "description": "restated once"}
    for amendment_id in ("same-one", "same-two"):
        rt._propose_amendment(proposer, {"id": amendment_id, "replace": [card],
                                         "predicted_effect": {"card_id": "cost_per_return",
                                                              "direction": "decrease",
                                                              "window": 1}})
    _boundary(rt)
    _boundary(rt)
    assert rt.charter.edition == 2
    refused = [i for i in rt.ledger._recovery_items() if i["kind"] == "charter.refused"]
    assert [i["amendment_id"] for i in refused] == ["same-two"]
    rt._apply_registrations(proposer, Return(proposer, {"register": [
        {"kind": "retire", "assembly_id": "eval-a"}]}, 0, "ok"))
    row = next(iter(rt.retirement_proposals.values()))
    assert row["status"] == "passed"
    for _ in range(3):
        _boundary(rt)
    waiting = rt.cadence.world_block(rt.tick_clock.interval_ns)["waiting"]
    assert "eval-a" in rt.retired_assemblies, f"retirement never activates; waiting={waiting}"


@pytest.mark.xfail(strict=False, reason="round three, open: docs/audits/v3/triage.md")
def test_finding_10_an_assembly_votes_on_its_own_retirement():
    """The proposer is excluded from the draw; the assembly being retired is not."""
    rt = make_runtime()
    _experienced(rt, ["eval-a", "eval-b", "meta-a", "seed-observer", "seed-decider"])
    proposer = decision(rt, "seed-decider")
    rt._apply_registrations(proposer, Return(proposer, {"register": [
        {"kind": "retire", "assembly_id": "eval-a"}]}, 0, "ok"))
    row = next(iter(rt.retirement_proposals.values()))
    seated = [seat.assembly_id for seat in row["committee"].seats]
    assert "seed-decider" not in seated
    assert "eval-a" not in seated, seated


def test_finding_6_the_operator_has_a_kill_control():
    """README: after launch there is one live view and one control: kill. Termination has
    an ``explicit_kill`` condition, but no command reaches it on a running world; the
    only path is ``--kill-at-end`` chosen at launch, and the funded budget is 2**63."""
    from factorylab.runtime.cli import build_parser

    parser = build_parser()
    commands = set(parser._subparsers._group_actions[0].choices)
    assert "kill" in commands, sorted(commands)
