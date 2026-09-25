"""The corpus the Class 2 audit reads: every seat-visible string, one leaf per stable path.

Phase-2 design B1. Source literals are the wrong input (they are dominated by
docstrings and error messages); what a seat sees is assembled at runtime. So the
audit reads rendered leaves, ``(path, text)``, with paths of the form
``<world>/<surface>/<section>/<json.path>`` and list indices collapsed to ``[]`` so a
path is stable across calls.

* ``render_static`` (check tier) reads a world without running it: the system prompts,
  the fixed texts of the stable prefix, every institution section, every published
  tool, the kernel's refusal texts, and each seat's genesis state.
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
REQUEST_HEADERS = ("WORLD CONTRACT", "BASE CAPABILITIES", "INSTITUTIONS", "YOU",
                   "WORLD UPDATE", "REQUEST", "INPUTS", "SUBJECT PROPENSITY", "SCORING",
                   "OUTCOME SCHEMA", "OUTCOME CONTRACT", "COMPLETION CRITERION")


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


def render_static(name: str, rt=None) -> list[Leaf]:
    """Every seat-visible string of ``name`` that exists before any seat acts."""
    from factorylab.cortex import schematics
    from factorylab.cortex.schematics import INSTITUTION_SECTIONS
    from factorylab.settlement.vocabulary import COMMISSIONED_JUDGE_REFUSAL

    rt = rt or static_runtime(name)
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
        if tool_id in own or spec.get("kind") in ("population", "connector"):
            continue
        leaves.extend(flatten(f"{name}/tools/{tool_id}/", _plain(spec)))
    leaves.append((f"{name}/refusal/judging/commissioned", COMMISSIONED_JUDGE_REFUSAL))
    for key, text in sorted(_websearch_refusals().items()):
        leaves.append((f"{name}/refusal/websearch/{key}", text))
    for case, reason in registration_refusals():
        leaves.append((f"{name}/refusal/registration/{case}", reason))
    for spec in rt.m.assemblies:
        leaves.extend(flatten(f"{name}/genesis/{spec.id}/", spec.initial_state))
    return [(p, t) for p, t in leaves if isinstance(t, str) and excluded(p) is None]


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
    requests: int = 0
    status: str = "not run"


def split_request(text: str) -> dict[str, str]:
    """The request's sections by header, in the order they were written."""
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
    seen: set[Leaf] = set()

    class Recording(fastloop.PolicyProvider):
        def complete(self, req):
            text = "\n".join(str(m.get("content", "")) for m in req.messages)
            form = request_form(req, text, _inputs_from_prompt(text))
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
    rendered.leaves = [(p, t) for p, t in rendered.leaves
                       if excluded(p) is None and not _authored(t, rendered.emitted)]
    return rendered


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _authored(text: str, emitted: Iterable[str]) -> bool:
    """Whether a leaf carries text the population itself wrote (provenance, not lint)."""
    return any(len(s) >= 12 and s in text for s in emitted)
