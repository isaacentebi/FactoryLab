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
    volunteered a counterfactual: this 60-tick run scored 0.279 of its judge decisions
    against the world (0.009 in a live 2.5 h run). With it, every compliant bare return
    is priced, and the share is most of them."""
    card = fastloop.run("scripted", 60, WORLD, tmp_path, cap_usd="2", seed=1)
    assert card["status"] == "completed", card.get("error")
    assert not any("counterfactual" in reason for reason in card["malformed_reasons"]), (
        card["malformed_reasons"])
    chain = card["reward_chain"]
    assert chain["judge_decisions"] >= 30
    assert chain["judge_consequence_share"] >= 0.5, chain
