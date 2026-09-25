"""The Class 2 static audit, gate tier: every request a seat is sent, rendered and linted.

Phase-2 design B1: what a seat sees is assembled at runtime, so each launchable world
runs its launch path (the venue swapped for the seeded fake, ``fastloop``) on the
scripted population for 60 ticks, and every request is split into its sections and
leaves. Population-authored text is dropped by provenance (what the population itself
emitted), the charter's cards and norms by their section (§IV.a).
"""

import pytest

from tests.audit import class2_audit as audit
from tests.audit import class2_corpus as corpus
from tests.audit import class2_lexicon as lexicon

pytestmark = pytest.mark.gate

WORLDS = corpus.launchable_worlds()


@pytest.fixture(scope="module")
def allowlist():
    return lexicon.load_allowlist()


@pytest.fixture(scope="module")
def rendered():
    return {}


def _render(rendered, world):
    if world not in rendered:
        rendered[world] = corpus.render_dynamic(world)
    return rendered[world]


@pytest.mark.parametrize("world", WORLDS)
def test_every_rendered_request_has_no_untriaged_finding_and_no_stale_one(
        world, rendered, allowlist):
    run = _render(rendered, world)
    assert run.status == "completed" and run.requests > 0, run.status
    result = audit.triage(run.leaves, allowlist)
    assert not result.review, [f.key for f in result.review]
    drift = lexicon.compare(result.findings, audit.baseline_rows(world, "rendered"))
    assert drift == {"new": [], "stale": []}, drift


@pytest.mark.parametrize("world", WORLDS)
def test_no_request_leaks_what_the_population_wrote_into_the_lint(world, rendered):
    """Provenance: a leaf carrying a string the scripted population emitted is dropped, so
    the lint never reads the population's own text as the architect's."""
    run = _render(rendered, world)
    long = [s for s in run.emitted if len(s) >= 12]
    assert not [p for p, t in run.leaves if any(s in t for s in long)]


def test_rendered_surfaces_match_the_coverage_registry(rendered, allowlist):
    """Every rendered request surface is registered and every registered one is rendered
    by some world, and the builders rendered, statically and by the runs together, are
    exactly the registered ones."""
    seen = set()
    used = set()
    for world in WORLDS:
        run = _render(rendered, world)
        seen |= audit.surfaces_of(run.leaves)
        used |= audit.triage(run.leaves, allowlist).used
    registered = set(audit.load_surfaces()["rendered"])
    assert seen - registered == set(), "unregistered surfaces"
    assert registered - seen == set(), "registered but not rendered"
    forms = {"/".join(s.split("/")[:2]) for s in seen}
    builders = audit.static_builders(WORLDS).union(
        *(_render(rendered, world).builders for world in WORLDS))
    assert builders == set(audit.load_surfaces()["builders"])
    assert {"request/vote", "request/testify"} <= forms | {
        "/".join(s.split("/")[:2]) for s in audit.load_surfaces()["static"]}
    rendered_entries = {i for i, e in enumerate(allowlist["allow"])
                        if "/request/" in e["path"]}
    assert rendered_entries <= used
