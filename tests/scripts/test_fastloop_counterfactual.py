"""The priced road not taken reaches the judges on the free tier (essay II.III.b).

AGENTS.md rule 6: evaluators are graded by realized consequence, which includes the
priced road not taken. Once a producing return that executes nothing must name the
trade it declined, the verdicts about such returns are scored against a world fact,
not only by the tier above. A mechanism check, not a behaviour target: the scripted
population complies with the contract, and the diary shows the judges' consequence
signal arriving.
"""

from pathlib import Path

import pytest

from scripts import fastloop

WORLD = Path(__file__).parents[2] / "worlds" / "edition6-capital-loop.toml"


@pytest.mark.gate
def test_the_scripted_edition6_world_grades_most_judges_by_the_world(tmp_path):
    """Before the contract, a return that executed nothing had a world outcome only if it
    volunteered a counterfactual: a 60-tick run scored 0.279 of its judge decisions
    against the world (0.009 in a live 2.5 h run). With it, every compliant bare return
    is priced, and most of the judges whose return reached its horizon in the run are
    graded by the world. The horizon is world_repricing / min_ratio of venue time
    (wave 16, D2): twenty minutes, 120 of this world's 10 s ticks, so a run must be
    longer than it for any judge to be graded, and only the part of the run past it
    can be."""
    from factorylab.runtime.worlds import load_manifest

    ticks = 200
    card = fastloop.run("scripted", ticks, WORLD, tmp_path, cap_usd="2", seed=1)
    assert card["status"] == "completed", card.get("error")
    assert not any("counterfactual" in reason for reason in card["malformed_reasons"]), (
        card["malformed_reasons"])
    chain = card["reward_chain"]
    assert chain["judge_decisions"] >= 100
    world = load_manifest(str(WORLD))
    past_horizon = 1 - world.consequence_horizon_ns / (ticks * world.tick_interval_ns)
    assert chain["judge_consequence_share"] >= 0.5 * past_horizon > 0, chain
