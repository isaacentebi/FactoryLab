"""The checkpoint plateaus (wave 17b).

Essay II.I.b: the queue holds "outstanding decisions awaiting their reward"; II.IV.c:
a verdict "is consumed ... and then discarded", and what persists is aggregates. With
settled decisions released, the world's checkpoint holds what is still owed plus
aggregates, so on a scripted world it stops growing with the world's age: between
1,500 and 5,000 world events no level of its checkpoints (each 500-event block's
median) is more than 10% above the median of those levels or above the first one.

The simulated venue's own books (``fake_exchange``: every fill, mid and funding row the
simulator ever produced, which it answers history queries from) are the world, not the
factory, and on a live world they are the venue's; they are reported beside the
factory's own bytes and are not part of the bound.
"""

from statistics import median

import pytest

from factorylab.kernel.ledger import canonical
from factorylab.runtime.loop import Runtime
from factorylab.runtime.resume import runtime_state
from factorylab.runtime.worlds import load_manifest

pytestmark = pytest.mark.slow

EVENTS = 5_000
FROM = 1_500
BLOCK = 500


def test_the_checkpoint_plateaus_between_1500_and_5000_events():
    rt = Runtime(load_manifest("scripted"), events=EVENTS, seed=1, initial_balance_micro=None,
                 ledger_path=None, router_gamma=.1)
    sizes = []
    snapshot = rt._snapshot

    def measured(boundary):
        written = snapshot(boundary)
        if written and rt.ticks_consumed >= FROM:
            # The state the checkpoint just wrote: nothing changed since but the held
            # venue reads a checkpoint never carries.
            state = runtime_state(rt)
            world = len(canonical(state["fake_exchange"])) if state["fake_exchange"] else 0
            sizes.append((rt.ticks_consumed, len(canonical(state)) - world, world))
        return written

    rt._snapshot = measured
    rt.run()
    assert rt.ticks_consumed == EVENTS
    assert sizes and sizes[0][0] < FROM + BLOCK and sizes[-1][0] > EVENTS - BLOCK
    # One checkpoint follows the window it closes: a window whose decisions are still
    # measured (the charter's margin windows hold ten) holds them, so single
    # checkpoints swing with the activity of the windows behind them. The plateau is
    # the level they swing around: the median of each 500-event block.
    blocks = {}
    for tick, size, _world in sizes:
        blocks.setdefault((tick - FROM) // BLOCK, []).append(size)
    medians = [median(block) for _key, block in sorted(blocks.items())]
    level = median(medians)
    assert len(medians) == (EVENTS - FROM) // BLOCK
    # It does not grow: no block's level is more than 10% above the level of the
    # checkpoints or above the first block's. A quieter stretch of the world holds
    # fewer open decisions and checkpoints smaller; that is activity, not growth.
    assert all(m <= 1.1 * level and m <= 1.1 * medians[0] for m in medians), (level, medians)
