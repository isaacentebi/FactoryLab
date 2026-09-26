"""The corpus the Class 2 audit reads: every seat-visible string, one leaf per stable path.

Phase-2 design B1. Source literals are the wrong input (they are dominated by
docstrings and error messages); what a seat sees is assembled at runtime. So the
audit reads rendered leaves, ``(path, text)``, with paths of the form
``<world>/<surface>/<section>/<json.path>`` and list indices collapsed to ``[]`` so a
path is stable across calls.

* ``render_static`` (check tier) reads a world without running it: the system prompts,
  the fixed texts of the stable prefix, every institution section, every published
  tool, the kernel's refusal texts, and each seat's genesis state.
  It also builds, through the real builders, the committee's ballots and testimony,
  which no short run reaches (``render_governance``).
* ``render_dynamic`` (gate tier) runs the launch path on the scripted population
  (``scripts/fastloop.py``) and records every request the seats are sent.

Provenance: population-authored text (what the scripted population itself emitted,
its tools, its registrations) and the charter's cards and norms are excluded from the
lint — the metrics layer is the factory's and the norms sit behind the read-only wall
(§IV.a); both still reach the LLM auditor as context (B2).
"""

from __future__ import annotations

import json
import re
import tempfile
import tomllib
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
WORLDS = ROOT / "worlds"

Leaf = tuple[str, str]

#: Sections excluded as a whole, each with its reason (design B1 provenance). Matched
#: against the whole path; the charter and norm subtrees, wherever a request carries
#: them, and the rendered WORLD CONTRACT, whose fixed wrapper is linted in the static
#: corpus and whose body is the norms.
EXCLUDED = {
    r"(^|[/.])charter(\.|\[|#|$)":
        "the charter's cards are population-adopted: the metrics layer is ceded (§IV.a)",
    r"(^|[/.])norms?(\.|\[|#|$)":
        "the norms are the norm house's, behind the read-only wall (§IV.a)",
    r"/request/[^/]+/world_contract":
        "the norms (§IV.a); the fixed wrapper is linted as institutions/world_contract_*",
}

#: Headers of the rendered request, in the order ``Request.sections`` writes them.
REQUEST_HEADERS = ("WORLD CONTRACT", "OPERATING ACCESS", "BASE CAPABILITIES",
                   "INSTITUTIONS", "YOU", "WORLD UPDATE", "REQUEST", "INPUTS",
                   "SUBJECT PROPENSITY", "SCORING", "OUTCOME SCHEMA", "OUTCOME CONTRACT",
                   "COMPLETION CRITERION")
#: The modules that write a request's sections (``Request.sections`` and the
#: schematics it carries): the headers are read from their code.
REQUEST_BUILDER_MODULES = ("factorylab/cortex/request.py", "factorylab/cortex/schematics.py")


def request_headers_in_code() -> set[str]:
    """Every section header the request builders write, from their source: a string that
    opens with a header line (capitals and spaces, then a newline), and every module
    constant named ``*_HEADER``."""
    import ast

    header = re.compile(r"^([A-Z][A-Z0-9 ]{1,40})\n")
    found: set[str] = set()
    for rel in REQUEST_BUILDER_MODULES:
        tree = ast.parse((ROOT / rel).read_text())
        for node in ast.walk(tree):
            values = []
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                values.append(node.value)
            elif isinstance(node, ast.JoinedStr) and node.values \
                    and isinstance(node.values[0], ast.Constant):
                values.append(str(node.values[0].value))
            for value in values:
                match = header.match(value)
                if match:
                    found.add(match.group(1))
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                    and isinstance(node.value.value, str) \
                    and any(isinstance(t, ast.Name) and t.id.endswith("_HEADER")
                            for t in node.targets):
                found.add(node.value.value.split("\n", 1)[0])
    return found


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.casefold()).strip("_")


def flatten(prefix: str, value: Any) -> Iterator[Leaf]:
    """Every string in ``value`` under its JSON path; list indices collapse to ``[]``.

    A prefix ending in ``/`` is a section: its JSON path starts after the slash, and a
    section whose value is itself a string is the section's own leaf.
    """
    if isinstance(value, str):
        yield prefix.rstrip("/"), value
    elif isinstance(value, dict):
        for key in sorted(value, key=str):
            child = f"{prefix}{key}" if prefix.endswith("/") or not prefix else f"{prefix}.{key}"
            yield from flatten(child, value[key])
    elif isinstance(value, list | tuple):
        for item in value:
            yield from flatten(f"{prefix}[]", item)


def surface_id(path: str) -> str:
    """The surface a leaf belongs to, without its world and its JSON path: what the
    coverage registry (``class2_surfaces.toml``) names."""
    parts = path.split("/")
    kind = parts[1] if len(parts) > 1 else ""
    if kind == "request":
        return "/".join(parts[1:4])
    if kind == "system":
        return "system"
    if kind == "genesis":
        return "genesis/" + re.split(r"[.\[#]", parts[3])[0] if len(parts) > 3 else "genesis"
    return "/".join(parts[1:3])


def excluded(path: str) -> str | None:
    """The reason a leaf's path is outside the lint, or None."""
    for pattern, reason in EXCLUDED.items():
        if re.search(pattern, path):
            return reason
    return None


def launchable_worlds() -> list[str]:
    """Every world ``load_manifest`` accepts, outside ``worlds/history``."""
    from factorylab.runtime.worlds import load_manifest

    names = []
    for path in sorted(WORLDS.glob("*.toml")):
        try:
            load_manifest(path.stem)
        except Exception:  # noqa: BLE001 - an unloadable world is not launchable
            continue
        names.append(path.stem)
    return names


def raw_world(name: str) -> dict:
    return tomllib.loads((WORLDS / f"{name}.toml").read_text())


def simulated_manifest(name: str):
    """``name``'s launch identity on the simulated venue (``fastloop.simulation_manifest``);
    a world that is already simulated (the scripted worlds) as it loads."""
    from factorylab.runtime.worlds import load_manifest
    from scripts import fastloop

    manifest = load_manifest(name)
    if manifest.exchange.kind == "fake":
        return manifest
    return fastloop.simulation_manifest(WORLDS / f"{name}.toml", 1)


def static_runtime(name: str):
    """A runtime of ``name`` on its simulated venue, built and never run."""
    from factorylab.runtime.loop import Runtime
    from scripts import fastloop

    return Runtime(simulated_manifest(name), events=0, seed=1, initial_balance_micro=None,
                   ledger_path=None, router_gamma=0.1,
                   provider=fastloop.PolicyProvider(WORLDS / f"{name}.toml"))


#: The one tool kind a seat, not the kernel, writes (``registry`` population tools).
POPULATION_TOOL_KIND = "population"


class UnknownSection(ValueError):
    """A request carries a header line the corpus does not know: its text would fold into
    the section before it."""


#: A line that reads as a request section header: capitals, digits and spaces only.
_HEADER_LINE = re.compile(r"^[A-Z][A-Z0-9 ]{1,40}$", re.M)


def render_static(name: str, rt=None) -> list[Leaf]:
    """Every seat-visible string of ``name`` that exists before any seat acts."""
    from factorylab.cortex import schematics
    from factorylab.cortex.schematics import INSTITUTION_SECTIONS
    from factorylab.settlement.vocabulary import COMMISSIONED_JUDGE_REFUSAL

    rt = rt or static_runtime(name)
    # The kernel's own fixed tools (the connector fetch, treasury and web tools) are
    # published on first use; render them as a seat sees them.
    rt._ensure_connector_tool()
    leaves: list[Leaf] = []
    own = set(rt.population_tools)
    for aid, assembly in sorted(rt.assemblies.items()):
        leaves.append((f"{name}/system/{aid}", assembly.spec.system_prompt))
    fixed = {
        "world_contract_opening": schematics.WORLD_CONTRACT_OPENING,
        "world_contract_closing": schematics.WORLD_CONTRACT_CLOSING,
        "capability_header": schematics.CAPABILITY_HEADER,
        "institutions_header": schematics.INSTITUTIONS_HEADER,
        "institutions_compact_header": schematics.INSTITUTIONS_COMPACT_HEADER,
        "addressing": schematics._ADDRESSING,
    }
    for key, text in fixed.items():
        leaves.append((f"{name}/institutions/{key}", text))
    for section in sorted(INSTITUTION_SECTIONS):
        leaves.extend(flatten(f"{name}/institutions/{section}/",
                              _plain(rt.institution_section(section))))
    for tool_id, spec in sorted(rt.tool_specs.items()):
        # Excluded by provenance only: a population tool is the population's own text.
        # Every kernel tool (connector.fetch included) is read.
        if tool_id in own or spec.get("kind") == POPULATION_TOOL_KIND:
            continue
        leaves.extend(flatten(f"{name}/tools/{tool_id}/", _plain(spec)))
    leaves.append((f"{name}/refusal/judging/commissioned", COMMISSIONED_JUDGE_REFUSAL))
    for key, text in sorted(_websearch_refusals().items()):
        leaves.append((f"{name}/refusal/websearch/{key}", text))
    for case, reason in registration_refusals():
        leaves.append((f"{name}/refusal/registration/{case}", reason))
    for spec in rt.m.assemblies:
        leaves.extend(flatten(f"{name}/genesis/{spec.id}/", spec.initial_state))
    # The committee's ballots and testimony, which no short run reaches, built through
    # the real builders on this world (``render_governance``).
    leaves.extend(require_complete(render_governance(name)).leaves)
    return [(p, t) for p, t in leaves if isinstance(t, str) and excluded(p) is None]


#: The pseudo-world of the seat text written in the kernel's code (``class2_seat_text``):
#: the same in every world, so it is read once, not once per world.
KERNEL = "kernel"


def render_seat_text(scan: Any | None = None) -> list[Leaf]:
    """Every string the kernel's code can put before a seat as a tool result, a refusal
    reason or an error message, whether or not a short run reaches it
    (``class2_seat_text``), under ``kernel/seat_text/<kind>/<source>/<digest>``."""
    from tests.audit import class2_seat_text

    scan = scan or class2_seat_text.scan()
    return [(f"{KERNEL}/{t.leaf}", t.text) for t in scan.texts]


def _plain(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def _websearch_refusals() -> dict[str, str]:
    """The web-search tool's refusal texts: a seat reads them as a tool result."""
    from factorylab.runtime import websearch

    return {str(k): str(v) for k, v in dict(websearch.REFUSALS).items()}


#: Malformed proposals whose refusal reasons are seat-visible (they come back to the
#: proposer as a public reason): one per registration family.
MALFORMED = {
    "tool": {"kind": "tool", "id": "X!", "description": "", "args_schema": {}, "code": ""},
    "observation": {"kind": "observation", "id": "a", "description": "", "unit": "",
                    "range": [1, 0], "code": ""},
    "predicate": {"kind": "predicate", "id": "a", "description": "", "code": ""},
    "amendment": {"kind": "amendment", "id": "a"},
    "assembly": {"kind": "assembly", "id": "a", "role": "producer"},
    "learner": {"kind": "learner", "assembly_id": "a", "learner": "exp3", "actions": []},
    "challenge": {"kind": "challenge", "card_id": "c"},
    "router": {"kind": "router", "event_kind": "Nope", "learner": "exp3"},
    "unknown": {"kind": "nonsense"},
}


def registration_refusals() -> list[tuple[str, str]]:
    """The public reason each malformed proposal is refused with, from the pure parser."""
    from factorylab.cortex.registration import parse_proposals

    out = []
    for case, item in sorted(MALFORMED.items()):
        _accepted, rejected = parse_proposals(
            {"register": [item]}, event_kinds=frozenset({"Tick"}), known_models=frozenset(),
            known_assemblies=frozenset(), tool_jail=False)
        out.extend((case, row.reason) for row in rejected)
    return out


# --- the rendered requests (gate) -----------------------------------------------------------


class RenderFailed(RuntimeError):
    """A world's requests could not be rendered completely: nothing may be built on them."""


def require_complete(rendered: Rendered) -> Rendered:
    """``rendered``, if its run completed and sent requests; else ``RenderFailed``.

    Guarantees no consumer (the auditor's corpus, the triage baseline, the surface
    registry) is built from a partial render: what a failed run rendered before it
    failed is not the set of surfaces under audit.
    """
    if rendered.status != "completed" or not rendered.requests or not rendered.leaves:
        raise RenderFailed(f"{rendered.world}: the rendered run did not complete "
                           f"({rendered.status}; {rendered.requests} requests)")
    return rendered


@dataclass
class Rendered:
    """A world's recorded requests, as leaves, with what the population emitted."""

    world: str
    leaves: list[Leaf] = field(default_factory=list)
    emitted: set[str] = field(default_factory=set)
    forms: set[str] = field(default_factory=set)
    #: The request builders (``REQUEST_BUILDERS``) whose requests were rendered.
    builders: set[str] = field(default_factory=set)
    requests: int = 0
    status: str = "not run"


def split_request(text: str) -> dict[str, str]:
    """The request's sections by header, in the order they were written.

    Refuses (``UnknownSection``) a header line that is not one of ``REQUEST_HEADERS``: a
    renamed or added section would otherwise fold its text into the section before it.
    ``test_class2_static`` derives the headers from ``Request.sections`` itself and
    fails when this list is not theirs."""
    unknown = sorted({m.group(0) for m in _HEADER_LINE.finditer(text)} - set(REQUEST_HEADERS))
    if unknown:
        raise UnknownSection(f"request headers the corpus does not know: {unknown}")
    pattern = re.compile(r"^(" + "|".join(re.escape(h) for h in REQUEST_HEADERS) + r")$", re.M)
    marks = [(m.start(), m.group(1)) for m in pattern.finditer(text)]
    out: dict[str, str] = {}
    for (start, header), nxt in zip(marks, [*marks[1:], (len(text), None)], strict=True):
        body = text[start + len(header):nxt[0]].strip("\n")
        out[header] = body
    return out


def section_leaves(prefix: str, body: str) -> Iterator[Leaf]:
    """A section's prose paragraphs and the leaves of each JSON line it carries."""
    prose: list[str] = []
    for line in body.split("\n"):
        stripped = line.strip()
        if stripped[:1] in "{[":
            try:
                value = json.loads(stripped)
            except ValueError:
                prose.append(line)
                continue
            yield from flatten(prefix, value)
        else:
            prose.append(line)
    text = "\n".join(prose).strip()
    if text:
        # Wrapped prose is one paragraph: join its hard line breaks.
        yield prefix.rstrip("/") + "/#prose", re.sub(r"(?<![.:;\n])\n(?!\n)", " ", text)


def render_dynamic(name: str, *, ticks: int = 60) -> Rendered:
    """Run ``name`` on the launch path with the scripted population and record requests."""
    from factorylab.world.scripted import _inputs_from_prompt, request_form
    from scripts import fastloop

    rendered = Rendered(name)
    Recording = _recorder(fastloop.PolicyProvider, name, rendered, set(),  # noqa: N806
                          _inputs_from_prompt, request_form)

    from factorylab.runtime.loop import Runtime

    manifest = simulated_manifest(name)
    directory = Path(tempfile.mkdtemp(prefix="class2-"))
    runtime = Runtime(manifest, events=ticks, seed=manifest.seed, initial_balance_micro=None,
                      ledger_path=str(directory / "ledger.jsonl"), router_gamma=0.1,
                      provider=Recording(WORLDS / f"{name}.toml"), kill_at_end=True)
    surface = getattr(runtime, "polymarket", None)
    if surface is not None and not surface.writes:
        # As fastloop does: a live-read world's reads are answered by the simulated venue.
        from factorylab.runtime.polymarket import simulate_reads

        simulate_reads(runtime)
    try:
        runtime.run()
        rendered.status = "completed"
    except Exception as exc:  # noqa: BLE001 - what was rendered before a failure still counts
        rendered.status = f"failed: {type(exc).__name__}: {exc}"[:300]
    # What the kernel writes is kernel text whoever else says it too; read only for a
    # completed run (a failed one is refused whole by ``require_complete``).
    kernel = ({t for _p, t in render_static(name)} | {t for _p, t in render_seat_text()}
              if rendered.status == "completed" else set())
    rendered.leaves = [(p, t) for p, t in rendered.leaves
                       if excluded(p) is None
                       and not _population_authored(t, rendered.emitted, kernel)]
    return rendered


def render_governance(name: str) -> Rendered:
    """The committee's requests, built statically through the real builders.

    A short scripted run never reaches a governance boundary, so the ballot and the
    testimony a seat is sent (``GovernanceMixin._hold_vote``, ``_testify``) are built
    here on a world that has run nothing: a committee is seated by the charter book's
    own sortition, and one motion of each ballot form is put to it (a cards amendment,
    a lambda motion, a clock motion, a retirement, a connector, a holdout challenge and
    a metric challenge), then one norm edition is testified on. Every request goes
    through the recording provider exactly as a run's would.
    """
    from dataclasses import replace as dc_replace

    from factorylab.charter.amendment import Amendment, PredictedEffect
    from factorylab.cortex.registration import ConnectorProposal
    from factorylab.runtime.governance import Retirement
    from factorylab.runtime.loop import Runtime
    from factorylab.world.scripted import _inputs_from_prompt, request_form
    from scripts import fastloop

    rendered = Rendered(name)
    recorder = _recorder(fastloop.PolicyProvider, name, rendered, set(), _inputs_from_prompt,
                         request_form)
    rt = Runtime(simulated_manifest(name), events=0, seed=1, initial_balance_micro=None,
                 ledger_path=None, router_gamma=0.1, provider=recorder(WORLDS / f"{name}.toml"))
    rt._manage_reserve_window()
    # The one piece of state a fresh world lacks: seats qualify for a committee by
    # settled experience (``_committee_eligible``), which no run has given them yet.
    rt.eligibility_tally = {aid: rt.m.committee.min_settled for aid in rt.assemblies}
    eligible = rt._committee_eligible()
    card = rt.charter.cards[0]
    effect = PredictedEffect(card.id, "increase", 1)
    restated = dc_replace(card, description=f"{card.description} (restated)")
    base = Amendment(id="class2-cards", proposer_handle="class2", edition_base=rt.charter.edition,
                     add=(), replace=(restated,), remove=(), predicted_effect=effect)
    charter_motions = [
        base,
        dc_replace(base, id="class2-lambda", replace=(), proposed_prices=((card.id, 0.2),)),
        dc_replace(base, id="class2-clock", replace=(), tick_interval="20s",
                   predicted_effect=PredictedEffect(None, "decrease", 1,
                                                    observation="burn_per_window")),
        dc_replace(base, id="class2-holdout-challenge"),
        dc_replace(base, id="class2-metric-challenge"),
    ]
    # A challenge-originated motion shows its voters the trial (C7): its record, as
    # ``_register_challenge`` keeps one, with no trial window measured yet.
    for motion, holdout in ((charter_motions[3], "class2-predicate@1"),
                            (charter_motions[4], None)):
        rt.challenges[motion.id] = {
            "id": motion.id, "handle": "class2", "card_id": card.id, "evidence": "class2",
            "incumbent": card, "replacement": restated, "trial_windows": 1,
            "start_window": rt.window.index, "observations": {}, "series": [],
            "status": "balloted", "amendment_id": motion.id, "predicted_effect": effect,
            **({"holdout": holdout} if holdout else {})}
    try:
        # Charter motions are proposed to the charter book, so the standing committee
        # the book seats has them on its agenda, exactly as a boundary would.
        for motion in charter_motions:
            rt.charter_book.propose(motion, rt.observations)
        standing = rt.charter_book.seat(1, eligible, rt.rng, size=rt.m.committee.seats,
                                        quorum=rt.m.committee.quorum,
                                        learners=rt._seat_learners(eligible))
        if standing is None:
            raise RenderFailed(f"{name}: no committee can be seated in this world")
        for motion in charter_motions:
            rt._hold_vote(motion, standing)
        # Retirements and connectors keep their own per-motion sortition
        # (``_seat_internal``): the factory's organization, not charter governance.
        retirement = Retirement("class2-retire", "class2", next(iter(rt.assemblies)), 1, effect)
        committee = rt._seat_internal(retirement.id, eligible)[0]
        # The record ``_propose_retirement`` keeps beside its ballot.
        rt.retirement_proposals[retirement.id] = {"proposal": retirement,
                                                  "committee": committee, "ballots": {},
                                                  "status": "voting"}
        rt._hold_vote(retirement, committee)
        connector = ConnectorProposal("class2-source", "class2", "https://example.org")
        rt._hold_vote(dc_replace(base, id="class2-connector"),
                      rt._seat_internal("class2-connector", eligible)[0], connector=connector)
        rt._testify({"sequence": 1, "norms": list(rt.charter.norms)}, standing)
        rendered.status = "completed"
    except Exception as exc:  # noqa: BLE001 - what rendered before a failure still counts
        rendered.status = f"failed: {type(exc).__name__}: {exc}"[:300]
    rendered.leaves = [(p, t) for p, t in rendered.leaves
                       if excluded(p) is None
                       and not _population_authored(t, rendered.emitted, frozenset())]
    # A partial governance render is never returned: every caller gets the refusal.
    return require_complete(rendered)


#: Every kernel function that builds a request a seat is sent, by ``file::function``.
#: A continuation (a tool round's follow-up call) is built inside ``compute._invoke``
#: and marked by its ``continuation`` input. ``tests/audit/test_class2_static.py``
#: scans the code for request builders and fails on one this registry does not name;
#: the corpus records which of them it actually rendered.
REQUEST_BUILDERS = frozenset({
    "factorylab/runtime/loop.py::_producer_step",
    "factorylab/runtime/loop.py::_evaluator_step",
    "factorylab/runtime/loop.py::_meta_step",
    "factorylab/runtime/loop.py::_counter_step",
    "factorylab/runtime/governance.py::_hold_vote",
    "factorylab/runtime/governance.py::_testify",
    "factorylab/runtime/composition.py::_invoke_child",
    "factorylab/runtime/compute.py::_invoke",
})
#: The constructor every builder calls; it states no request of its own.
REQUEST_HELPERS = frozenset({"factorylab/runtime/compute.py::_request"})


def builder_of(inputs: dict) -> str | None:
    """The registered builder that made the request now being sent, read off the stack:
    the innermost builder frame (a child request is built inside the step that asked
    for it), or ``compute._invoke`` for a continuation."""
    import inspect

    if isinstance(inputs.get("continuation"), str):
        return "factorylab/runtime/compute.py::_invoke"
    for frame in inspect.stack(context=0):
        try:
            rel = Path(frame.filename).resolve().relative_to(ROOT).as_posix()
        except ValueError:
            continue
        key = f"{rel}::{frame.function}"
        if key in REQUEST_BUILDERS and key != "factorylab/runtime/compute.py::_invoke":
            return key
    return None


def _recorder(base, name, rendered, seen, inputs_from_prompt, request_form):
    """A provider class recording every request it is sent: its leaves, its form, and the
    builder that made it."""
    class Recording(base):
        def complete(self, req):
            text = "\n".join(str(m.get("content", "")) for m in req.messages)
            inputs = inputs_from_prompt(text)
            form = request_form(req, text, inputs)
            builder = builder_of(inputs)
            if builder is not None:
                rendered.builders.add(builder)
            response = super().complete(req)
            rendered.requests += 1
            rendered.forms.add(form)
            rendered.emitted.update(_strings(json.loads(response.text)))
            for leaf in [(f"{name}/request/{form}/system", req.system),
                         *(leaf for header, body in split_request(text).items()
                           for leaf in section_leaves(f"{name}/request/{form}/{slug(header)}/",
                                                      body))]:
                if leaf not in seen:
                    seen.add(leaf)
                    rendered.leaves.append(leaf)
            return response
    return Recording


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _population_authored(text: str, emitted: Iterable[str], kernel: Iterable[str]) -> bool:
    """Whether a leaf is text the population wrote, by provenance: the leaf is a whole
    string a seat emitted (its value carried into a request as written), and not a
    string the kernel itself writes. A kernel string a seat echoes stays in the corpus,
    and a leaf is never dropped because a seat's text is a substring of it."""
    return text in set(emitted) and text not in set(kernel)
