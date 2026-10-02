"""The runtime's own routers are no-regret on a fixed menu (audit s06, finding #2).

Every router the runtime builds kept a constant exploration floor, so on a fixed
menu its expected regret grew as T/30: linear. Here the routers ``_build_router``
makes, the frontier's EXP3 and the core's Blum-Mansour, are driven through the
runtime's own draw (``Router.route`` on the keyed adapter) and its own update path
(``_apply_router_round``) on the constant-gap game, and their expected regret, read
from the policies they drew from, grows sublinearly and within the published bound
(docs/architecture/learners-noregret.md §2.1, §2.2).
"""

import math
from random import Random

import pytest

from factorylab.learners.base import BanditFeedback
from factorylab.runtime.shared import NOOP
from tests.conftest import make_runtime

KIND = "Tick"


def _regret(kind: str, horizon: int) -> tuple[float, float, int]:
    """(external, swap) expected regret of a runtime router over ``horizon`` rounds."""
    rt = make_runtime()
    state = rt._build_router(KIND, kind)
    menu = tuple(state.universe)
    best = next(a for a in menu if a != NOOP)
    rewards = {a: 1.0 if a in (best, NOOP) else 0.0 for a in menu}
    rng = Random(0)
    own, swap = 0.0, {i: dict.fromkeys(menu, 0.0) for i in menu}
    for t in range(horizon):
        key = f"r{t}"
        state.learner.current_key, state.learner.current_ordinal = key, t
        sample = state.router.route(state.kind, lambda _a: (True, ""), rng)
        p = dict(zip(sample.action_ids, sample.probs, strict=True))
        own += math.fsum(p[a] * rewards[a] for a in menu)
        for i in menu:
            for j in menu:
                swap[i][j] += p[i] * rewards[j]
        k = sample.chosen
        assert rt._apply_router_round(state, key, key, BanditFeedback(k, rewards[k], p[k]))
    external = horizon - own
    swapped = math.fsum(max(swap[i][j] - swap[i][i] for j in menu) for i in menu)
    return external, swapped, len(menu)


@pytest.mark.gate
def test_runtime_learners_have_sublinear_fixed_menu_regret():
    """EXP3's external regret and Blum-Mansour's swap regret on the runtime's own Tick
    router: each within its proposition's bound, and each growing slower than the
    horizon (a constant exploration floor grew in proportion). The core's first epoch
    is the runtime's own, at least the delivery bound (design §2.2), so its doubling
    shows only past it: it is read from 8,000 draws to 32,000."""
    small, large = 1_000, 8_000
    ext_small, _, n = _regret("exp3", small)
    ext_large, _, _ = _regret("exp3", large)
    for regret, horizon in ((ext_small, small), (ext_large, large)):
        assert regret <= (n * math.log(n) + 4) * math.sqrt(horizon) + 3
    assert ext_large / ext_small <= 8 ** 0.65
    small, large = 8_000, 32_000
    _, swap_small, _ = _regret("blum_mansour", small)
    _, swap_large, _ = _regret("blum_mansour", large)
    for regret, horizon in ((swap_small, small), (swap_large, large)):
        assert regret <= 8.98 * n * math.sqrt(horizon * n * math.log(n))
    assert swap_large / swap_small <= 4 ** 0.85
