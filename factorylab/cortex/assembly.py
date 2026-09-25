"""Assemblies: ephemeral executors built from registered parts.

An ``AssemblySpec`` names a model capability, a system prompt, tool ids, and a
memory policy. Invoking it is stateless: one request in, one metered model
call, one ``Return`` out. Nothing persists on the assembly between calls; if
a memory policy is set, the memory lives in the registry-controlled store the
runtime passes in, keyed by handle scope.

The system prompt is world-supplied. It must not describe kernel rules (the
kernel enforces them); it describes only how to answer a request. The system
*message* is that prompt and nothing else. Everything the kernel discloses about
the world — the charter text, the tool, observation, work and metric catalogues —
is population-authored the moment a member registers into it, so it travels in
the user message, where it is material to read rather than an instruction that
outranks the assembly's own. An assembly that writes a tool description is
writing to its peers' inputs, never to their system role.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from copy import deepcopy
from dataclasses import KW_ONLY, dataclass, field, replace
from decimal import Decimal
from typing import Any

from factorylab.cortex.registration import (
    MAX_CONTRACT_DESCRIPTION_CHARS,
    output_contracts,
    reward_contracts,
    seed_emits,
)
from factorylab.cortex.request import (
    CONTINUITY_RETURN_FIELDS,
    ChildRequest,
    Request,
    Return,
    validate_propensity,
)
from factorylab.cortex.sandbox import MAX_PROGRAM_TIMEOUT_S
from factorylab.kernel.artifacts import ArtifactError
from factorylab.kernel.ledger import utf8_text
from factorylab.world.metering import BillingUncertain, Infeasible, MeteredModel
from factorylab.world.models import ModelRequest

SEED_SYSTEM_PROMPT = (
    "You receive one request. Reply with a single JSON object that satisfies the "
    "outcome schema. If the request cannot be completed, reply with a JSON object "
    'containing "status": "cannot" and "reason". '
    'A return may also carry "register" proposals and "requests" for work from other '
    "assemblies. Both are bounded, and whatever is not admitted comes back with a public "
    "reason. The world input is what you know about this world."
)


@dataclass(frozen=True)
class AssemblySpec:
    id: str
    version: int
    model_id: str
    system_prompt: str = SEED_SYSTEM_PROMPT
    # Not read at runtime — an assembly's tools come from the registry — but every
    # snapshot serialises this dataclass field by field, so it is part of the
    # recorded evidence and of the resume format. Removing it changes the diary.
    tool_ids: tuple[str, ...] = ()
    memory_policy: str = "none"  # "none" | "handle-scoped"
    max_tokens: int = 2048
    effort: str = "medium"
    accepts: frozenset[str] = frozenset({"Tick", "MarketMid", "Funding", "Fill", "Verdict"})
    role: str = "producer"  # descriptive label; dispatch depends only on accepts/emits
    emits: tuple[str, ...] | None = None
    schemas: dict[str, dict] = field(default_factory=dict)
    # The public self-description carried with the contract (essay II.I: "the
    # contract has to carry enough self-description that a primitive can be picked
    # up against a constraint that did not exist when the contract was written").
    # Empty means the catalogue publishes the contract's own line
    # (``public_description``). A checkpoint written before this field restores "".
    description: str = ""

    def __post_init__(self) -> None:
        if self.memory_policy not in ("none", "handle-scoped"):
            raise ValueError("unknown memory policy")
        if not isinstance(self.role, str) or not self.role.strip():
            raise ValueError("assembly role must be a nonempty label")
        if (not isinstance(self.description, str)
                or len(self.description) > MAX_CONTRACT_DESCRIPTION_CHARS):
            raise ValueError(
                f"description must be text of at most {MAX_CONTRACT_DESCRIPTION_CHARS} chars")
        emits, schemas = output_contracts(
            self.emits if self.emits is not None else seed_emits(self.role), self.schemas)
        object.__setattr__(self, "emits", emits)
        object.__setattr__(self, "schemas", schemas)
        if type(self.max_tokens) is not int or self.max_tokens <= 0:
            raise ValueError("max_tokens must be a resolved positive integer")


#: What a return of each seed kind is and how it settles: one neutral line each,
#: stating the contract and never how to behave under it (essay II.I.b: an agent
#: card is "complete, semantically rich, but also neutral self-description").
SEED_KIND_LINES: dict[str, str] = {
    "ProducerReturn": "an answer to the accepted event, which may act on the world; it "
                      "settles on the mean verdict of the judges that read it",
    "Verdict": "a verdict in [0, 1] on one return; it settles on the grade of the tier "
               "above and the world's score of the verdict against the measured outcome",
    "MetaVerdict": "a conformity grade in [0, 1] of one judgement; it settles on the grade "
                   "of any tier above and the world's score of the grade",
    "Exposure": "an answer to the accepted event, which may act on the world; it settles "
                "on how far its judges' verdicts missed its measured outcome",
    "CounterVerdict": "a verdict in [0, 1] on the return another verdict judged; it settles "
                      "on how far it beat that verdict against the measured outcome",
}


def contract_line(accepts: Any, emits: Any) -> str:
    """One line naming what a contract takes and what each emitted kind is.

    Guarantees a string built from the contract alone (accepted kinds, emitted
    kinds, and each seed kind's ``SEED_KIND_LINES`` entry), at most
    ``MAX_CONTRACT_DESCRIPTION_CHARS`` long, so two seats with one contract carry
    one line and nothing about either seat's lens reaches the catalogue.
    """
    kinds = [f"{kind}: {SEED_KIND_LINES[kind]}" if kind in SEED_KIND_LINES
             else f"{kind}: a declared kind; its schema is in event_schemas"
             for kind in emits or ()]
    line = f"accepts {', '.join(sorted(accepts))}; emits " + "; ".join(kinds)
    return line[:MAX_CONTRACT_DESCRIPTION_CHARS]


def public_description(spec: AssemblySpec) -> str:
    """The description an assembly publishes: its own, else its contract's line."""
    return spec.description or contract_line(spec.accepts, spec.emits)


@dataclass
class Assembly:
    """Executes requests through a metered model.

    Guarantees: the model is invoked at most once per request; the returned
    ``cost`` is exactly what the wallet was charged; a model refusal, a
    malformed reply, or an infeasible reservation each produce a ``Return``
    with the matching status rather than an exception, so the router always
    gets an addressable outcome for the handle it opened.
    """

    spec: AssemblySpec
    model: MeteredModel
    memory: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    child_factory: Callable[[Request, dict[str, Any], int], Request] | None = None
    validator: Callable[[dict[str, Any], Request], None] | None = None

    def build_model_request(self, req: Request) -> ModelRequest:
        """Render the exact prompt this assembly will be billed for.

        The executor's own id is stamped here, where the prompt is rendered, and
        nowhere else: an identity added after a caller has priced the request
        would make every ceiling derived from that request — the metered
        ceiling, the router's affordability check — smaller than the prompt
        actually sent. Stamping it here also means a parent cannot forge its
        child's identity: whatever ``inputs`` carried, the assembly overwrites it
        with its own.

        Guarantees the system message is exactly ``spec.system_prompt``: no
        population-authored text ever reaches the system role. The registered
        catalogues carry prose their authors chose — a tool's description, an
        observation's, a predicate's, a metric card's, the charter's norms — and
        in the system role, shared by every later call, that prose would be a
        standing instruction one member wrote for the rest of the population.
        The prompt's stable prefix (``Request.stable_prefix``) therefore heads
        this user message, which is where the world block belongs and where it
        reads as material.

        The cache hit that placement keeps is the per-assembly one, which is
        where the volume is: this assembly's system text is a constant, so its
        own consecutive calls open with the identical ``system`` message
        followed by the identical stable block, which is all DeepSeek's and
        OpenAI's automatic prompt caching asks for — they key on an identical
        leading token sequence and need no ``cache_control`` marker, so none is
        sent. Handle-scoped memory, when a world registers it, is the one thing
        that precedes the block and costs that assembly the hit.
        """
        req = replace(req, inputs={**req.inputs, "you": self.spec.id})
        messages: list[dict[str, Any]] = []
        if self.spec.memory_policy == "handle-scoped" and req.parent_handle:
            messages.extend(self.memory.get(req.parent_handle, []))
        messages.append({"role": "user", "content": req.prompt_text()})
        return ModelRequest(
            model_id=self.spec.model_id,
            system=self.spec.system_prompt,
            messages=tuple(messages),
            max_tokens=self.spec.max_tokens,
            effort=self.spec.effort,
            json_object=True,
            response_schema=wire_schema(req.outcome_schema, self.spec.emits,
                                        policy=req.scoring_channel == "policy"),
        )

    def invoke(self, req: Request) -> Return:
        try:
            mreq = self.build_model_request(req)
            ceiling = self.model.ceiling(mreq)
            if ceiling > req.cost_ceiling:
                return Return(
                    req.handle, {"reason": "ceiling exceeds request cost_ceiling"}, 0, "failed"
                )
            metered = self.model.complete(mreq, handle=req.handle)
        except Infeasible as exc:
            return Return(req.handle, {"reason": f"infeasible: {exc}"}, 0, "failed")
        except BillingUncertain as exc:
            # The call was made and may be billed: the provider was handed the request.
            return Return(req.handle, {"reason": str(exc)}, exc.cost, "failed",
                          delivered=True)
        except Exception as exc:
            # Refused before any bill (unbilled, or raised before the meter ran the
            # call): nothing consumed the request.
            return Return(req.handle, {"reason": type(exc).__name__}, 0, "failed")
        # From here the provider answered: every return below was delivered.
        resp = metered.result
        cost = metered.cost
        if (not isinstance(resp.text, str) or not isinstance(resp.model_id, str)
                or not isinstance(resp.stop_reason, str) or type(resp.refused) is not bool):
            return Return(req.handle, {"reason": "invalid response metadata"}, cost, "malformed",
                          delivered=True)
        provider = _provider_report(resp, mreq.max_tokens)
        if resp.refused:
            return Return(
                req.handle, {"reason": "refused"}, cost, "refused", served_by=resp.model_id,
                provider=provider, delivered=True,
            )
        # Reply bytes outside UTF-8 (an emoji cut by max_tokens) are seen exactly as the
        # journal can store them, so a live call and its replay parse the same text.
        resp = replace(resp, text=utf8_text(resp.text))
        parsed = _parse_json_object(resp.text)
        dropped: tuple[dict[str, Any], ...] = ()
        rejected: list[dict[str, Any]] = []
        validation_error = "answer is not a JSON object"
        if parsed is not None:
            try:
                parsed, dropped = validate_return_sections(
                    parsed, req.outcome_schema, self.validator, req, rejected=rejected,
                    kind=answer_kind(self.spec.emits, parsed, req))
            except (ValueError, TypeError, ArithmeticError, RecursionError) as exc:
                validation_error = str(exc)[:200] or type(exc).__name__
                parsed = None
        if parsed is None:
            return Return(
                req.handle,
                {"raw": resp.text, "validation_error": validation_error,
                 "rejected_sections": rejected},
                cost,
                "malformed",
                served_by=resp.model_id,
                stop_reason=resp.stop_reason,
                provider=provider,
                delivered=True,
            )
        if self.spec.memory_policy == "handle-scoped":
            scope = req.parent_handle or req.handle
            self.memory.setdefault(scope, []).extend(
                [
                    {"role": "user", "content": req.prompt_text()},
                    {"role": "assistant", "content": resp.text},
                ]
            )
        children = self._children(req, parsed)
        raw_calls = parsed.get("tool_calls")
        tool_calls = (
            tuple(c for c in raw_calls if isinstance(c, dict) and isinstance(c.get("tool"), str))
            if isinstance(raw_calls, list)
            else ()
        )
        outputs = {k: v for k, v in parsed.items() if k not in ("requests", "tool_calls")}
        return Return(
            req.handle,
            outputs,
            cost,
            "ok",
            children=children,
            served_by=resp.model_id,
            stop_reason=resp.stop_reason,
            tool_calls=tool_calls,
            provider=provider,
            dropped=dropped,
            delivered=True,
        )

    def _children(self, req: Request, parsed: dict[str, Any]) -> tuple[ChildRequest | Request, ...]:
        return _children(req, parsed, self.child_factory)


def _children(req: Request, parsed: dict[str, Any],
              child_factory: Callable[[Request, dict[str, Any], int], Request] | None,
              ) -> tuple[ChildRequest | Request, ...]:
    raw = parsed.get("requests")
    if not isinstance(raw, list):
        return ()
    if child_factory is not None:
        return tuple(child_factory(req, item, i) for i, item in enumerate(raw))
    return tuple(ChildRequest(item["target"], item["description"], item["inputs"],
                              item["outcome_schema"],
                              validate_propensity(item["propensity"])
                              if "propensity" in item else None,
                              item["chosen"].strip() if "propensity" in item else None)
                 for item in raw)


# --- programs as seats (contract C8) -----------------------------------------

PROGRAM_MODEL_ID = "program"
MAX_PROGRAM_CODE_CHARS = 16_000
MAX_PROGRAM_STATE_BYTES = 65_536
PROGRAM_STATE_POLICIES = ("none", "private")


@dataclass(frozen=True)
class ProgramAssemblySpec(AssemblySpec):
    """A seat whose executor is population Python in the jail rather than a model.

    ``code`` reads one JSON object from stdin — ``prompt`` (the rendered request,
    exactly what a model would read), ``description``, ``inputs``,
    ``outcome_schema`` and ``state`` — and prints the same Return JSON a model
    would. With ``state_policy = "private"`` the object it prints under
    ``state`` is archived as an artifact owned by this seat and handed back on
    its next call; the archive is versioned by content hash, one artifact per
    call that changes it. ``reward_shapes`` carries admitted shapes for custom
    emitted kinds, as ``WorkAssemblySpec`` does for model seats.
    """

    code: str = ""
    timeout_s: int = 10
    state_policy: str = "none"
    reward_shapes: dict[str, str] = field(default_factory=dict)
    # A watcher (edition 3, C2): the predicate the kernel settles from world state
    # each tick, without a model call and without a debit. Empty for a program
    # seat that is not a watcher.
    trigger: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.model_id != PROGRAM_MODEL_ID:
            raise ValueError("a program seat's model_id is program")
        if not isinstance(self.code, str) or not self.code.strip():
            raise ValueError("a program seat needs code")
        if len(self.code) > MAX_PROGRAM_CODE_CHARS:
            raise ValueError(f"code exceeds {MAX_PROGRAM_CODE_CHARS} chars")
        if type(self.timeout_s) is not int or not 1 <= self.timeout_s <= MAX_PROGRAM_TIMEOUT_S:
            raise ValueError(f"timeout_s must be an int in [1, {MAX_PROGRAM_TIMEOUT_S}]")
        if self.state_policy not in PROGRAM_STATE_POLICIES:
            raise ValueError("state_policy must be none or private")
        if self.trigger:
            from factorylab.runtime.subscriptions import validate_trigger

            object.__setattr__(self, "trigger", validate_trigger(dict(self.trigger)))
        object.__setattr__(self, "reward_shapes", reward_contracts(self.emits, self.reward_shapes))


@dataclass(frozen=True)
class _ProgramCeiling:
    """What routing asks a seat's model: the ceiling of one call. A program's is zero.

    The jail is the world's own and pays no one, so a program call has no price
    (the wallet moves only when money moves; essay II.II.b).
    """

    def ceiling(self, req: Any) -> int:
        return 0


@dataclass
class ProgramAssembly:
    """Executes requests by running the seat's program in the jail.

    Guarantees, matching ``Assembly``: the program runs at most once per
    request; ``cost`` is exactly what the wallet was charged, which is zero: the
    call runs through the meter under the reason ``model:program`` with a ceiling
    and a cost of zero, so it is ledgered beside a model call and no money moves,
    because the jail pays no one; every failure — no jail, a
    non-zero exit, a wall timeout, a reply that is not the Return JSON the
    validator accepts — is a ``Return`` with status ``malformed`` (or ``failed``
    when nothing ran), never an exception. Private state is loaded from and
    saved to the artifact archive around the call, and the hash of the state
    the call left behind is ledgered (``program.call``) before the Return
    leaves, so the diary names the machinery's memory as well as its answer.
    """

    spec: ProgramAssemblySpec
    runner: Any  # ProgramRunner or its journal proxy: run(code, stdin=, timeout_s=) -> dict
    meter: Any  # Meter over the world wallet
    # Everything after the meter is named: a fourth positional argument (the flat
    # price the executor once took) is refused rather than read as something else.
    _: KW_ONLY
    artifacts: Any | None = None  # ArtifactStore; required for state_policy "private"
    memory: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    child_factory: Callable[[Request, dict[str, Any], int], Request] | None = None
    validator: Callable[[dict[str, Any], Request], None] | None = None
    record: Callable[[dict[str, Any]], Any] | None = None
    state_sha: str | None = None
    # Why this version may not write private state now (``retired``), or None: a
    # retired version's pending return may settle, but it writes no state.
    state_gate: Callable[[], str | None] | None = None
    model: _ProgramCeiling = field(init=False)

    def __post_init__(self) -> None:
        self.model = _ProgramCeiling()

    def build_stdin(self, req: Request, state: Any) -> str:
        """Render what the program reads: the request as a model would see it, plus state.

        Guarantees the world block's ``seats`` carries this program's own row and no
        other: the partition the prompt already applies (``Request.seat_block``),
        applied to the raw inputs too, because a program reads them whole
        (information audit C3; essay II.I.b, local state is private).
        """
        inputs = {**req.inputs, "you": self.spec.id}
        world = inputs.get("world")
        if isinstance(world, dict) and isinstance(world.get("seats"), (list, tuple)):
            inputs["world"] = {**world, "seats": [
                row for row in world["seats"]
                if isinstance(row, dict) and row.get("seat_id") == self.spec.id]}
        req = replace(req, inputs=inputs)
        return json.dumps({
            "prompt": req.prompt_text(),
            "description": req.description,
            "inputs": req.inputs,
            "outcome_schema": req.outcome_schema,
            "state": state,
        }, sort_keys=True, ensure_ascii=False)

    def _load_state(self) -> tuple[Any, str | None]:
        """The state the last successful call left, or None and why it could not be read."""
        if self.spec.state_policy != "private" or self.state_sha is None:
            return None, None
        if self.artifacts is None:
            return None, "no artifact archive"
        try:
            return json.loads(self.artifacts.get(self.state_sha).decode("utf-8")), None
        except Exception as exc:
            return None, type(exc).__name__

    def invoke(self, req: Request) -> Return:
        state, state_error = self._load_state()
        if state_error is not None:
            # The seat has state and it cannot be read: the call does not run with
            # different memory (an empty one) and report ok. Nothing is billed; the
            # failure names why, and the last good state hash is kept for the
            # operator who restores the bytes (second reading, P1-02).
            if self.record is not None:
                self.record({
                    "kind": "program.call", "assembly_id": self.spec.id, "handle": req.handle,
                    "status": "failed", "cost": 0, "state_in": self.state_sha,
                    "state_out": self.state_sha, "state_error": state_error,
                })
            return Return(req.handle, {"reason": f"state unavailable: {state_error}"}, 0,
                          "failed")
        try:
            stdin = self.build_stdin(req, state)
        except (TypeError, ValueError) as exc:
            return Return(req.handle, {"reason": type(exc).__name__}, 0, "failed")
        code, timeout_s = self.spec.code, self.spec.timeout_s

        def execute() -> dict:
            return self.runner.run(code, stdin=stdin, timeout_s=timeout_s)

        try:
            metered = self.meter.run(
                handle=req.handle, reason=f"model:{PROGRAM_MODEL_ID}", ceiling=0,
                execute=execute, cost_of=lambda _r: 0,
            )
        except Infeasible as exc:
            return Return(req.handle, {"reason": f"infeasible: {exc}"}, 0, "failed")
        except BillingUncertain as exc:
            # The run was made and may be billed: stdin was handed to the jail.
            return Return(req.handle, {"reason": str(exc)}, exc.cost, "failed",
                          delivered=True)
        except Exception as exc:
            return Return(req.handle, {"reason": type(exc).__name__}, 0, "failed")
        cost = metered.cost
        result = metered.result if isinstance(metered.result, dict) else {}
        state_in = self.state_sha
        # The jail ran the program on its stdin: whatever it answered, it was delivered.
        ret = replace(self._interpret(req, result, cost, state_error), delivered=True)
        if self.record is not None:
            self.record({
                "kind": "program.call", "assembly_id": self.spec.id, "handle": req.handle,
                "status": ret.status, "cost": cost, "state_in": state_in,
                "state_out": self.state_sha,
            })
        return ret

    def _interpret(self, req: Request, result: dict, cost: int,
                   state_error: str | None) -> Return:
        provider = {"finish_reason": "stop", "input_tokens": None, "output_tokens": None,
                    "reasoning_tokens": None, "cached_tokens": None,
                    "max_tokens": self.spec.max_tokens, "state_sha": self.state_sha}
        if state_error is not None:
            provider["state_error"] = state_error

        def malformed(outputs: dict, finish: str) -> Return:
            return Return(req.handle, outputs, cost, "malformed", served_by=PROGRAM_MODEL_ID,
                          stop_reason=finish, provider={**provider, "finish_reason": finish})

        if "error" in result:
            return malformed({"reason": str(result["error"])[:200]}, "error")
        if result.get("timed_out") is True:
            return malformed({"reason": "timeout"}, "timeout")
        if result.get("returncode") != 0:
            return malformed({"reason": f"exit {result.get('returncode')}",
                              "stderr": str(result.get("stderr", ""))[:500]}, "error")
        text = utf8_text(result.get("stdout", "")) if isinstance(result.get("stdout"), str) else ""
        parsed = _parse_json_object(text)
        new_state: Any = None
        dropped: tuple[dict[str, Any], ...] = ()
        rejected: list[dict[str, Any]] = []
        validation_error = "answer is not a JSON object"
        if parsed is not None:
            # The state is the program's, not the return's: it never reaches the
            # outcome schema, the judges or the ledger's outputs field.
            new_state = parsed.pop("state", None)
            if self.spec.state_policy != "private" and new_state is not None:
                validation_error = "program has no private state policy"
                parsed = None
            else:
                try:
                    parsed, dropped = validate_return_sections(
                        parsed, req.outcome_schema, self.validator, req, rejected=rejected,
                        kind=answer_kind(self.spec.emits, parsed, req))
                except (ValueError, TypeError, ArithmeticError, RecursionError) as exc:
                    validation_error = str(exc)[:200] or type(exc).__name__
                    parsed = None
        if parsed is None:
            return malformed({"raw": text[:4000], "validation_error": validation_error,
                              "rejected_sections": rejected}, "stop")
        if new_state is not None:
            try:
                encoded = json.dumps(new_state, sort_keys=True, allow_nan=False,
                                     ensure_ascii=False).encode("utf-8")
            except (TypeError, ValueError):
                return malformed({"reason": "state is not JSON"}, "stop")
            if len(encoded) > MAX_PROGRAM_STATE_BYTES:
                return malformed({"reason": f"state exceeds {MAX_PROGRAM_STATE_BYTES} bytes"},
                                 "stop")
            if self.artifacts is None:
                return malformed({"reason": "no artifact archive"}, "stop")
            # One private state per program is retained: the put releases the
            # superseded one's reference, whose bytes are collected once no durable
            # checkpoint names them (the disk is a limit, not a price). A state the
            # world refuses (a retired version, or the retained private state cap, as
            # a full disk would) is refused as a seat's working state is: ledgered
            # ``state.refused``, the previous state kept, and the return stands.
            refused = self.state_gate() if self.state_gate is not None else None
            if refused is None:
                try:
                    self.state_sha = self.artifacts.put(encoded, owner=self.spec.id,
                                                        kind="program.state",
                                                        supersedes=self.state_sha)
                except ArtifactError as exc:
                    refused = str(exc)[:200]
            if refused is not None and self.record is not None:
                self.record({"kind": "state.refused", "assembly_id": self.spec.id,
                             "handle": req.handle, "state_kind": "program.state",
                             "reason": refused})
            provider["state_sha"] = self.state_sha
        children = _children(req, parsed, self.child_factory)
        raw_calls = parsed.get("tool_calls")
        tool_calls = (
            tuple(c for c in raw_calls if isinstance(c, dict) and isinstance(c.get("tool"), str))
            if isinstance(raw_calls, list)
            else ()
        )
        outputs = {k: v for k, v in parsed.items() if k not in ("requests", "tool_calls")}
        return Return(
            req.handle, outputs, cost, "ok", children=children, served_by=PROGRAM_MODEL_ID,
            stop_reason="stop", tool_calls=tool_calls, provider=provider, dropped=dropped,
        )


def _provider_report(resp: Any, max_tokens: int) -> dict[str, Any]:
    """Return the provider's own account of the completion; unreported fields are None."""
    raw = resp.raw if isinstance(raw := getattr(resp, "raw", None), dict) else {}
    reasoning = raw.get("reasoning_tokens")
    # Input the provider served from its own prompt cache. Absent where the
    # provider does not report it, which is not a miss and must not read as one.
    cached = raw.get("cached_tokens")
    return {
        "finish_reason": resp.stop_reason,
        "input_tokens": resp.input_tokens if type(resp.input_tokens) is int else None,
        "output_tokens": resp.output_tokens if type(resp.output_tokens) is int else None,
        "reasoning_tokens": reasoning if type(reasoning) is int else None,
        "cached_tokens": cached if type(cached) is int else None,
        "max_tokens": max_tokens,
    }


def _parse_json_object(text: str) -> dict[str, Any] | None:
    """Return the first JSON object in ``text`` or None. Tolerates code fences."""
    if not isinstance(text, str):
        return None
    decoder = json.JSONDecoder()
    for start, char in enumerate(text):
        if char != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(text, start)
        except (ValueError, RecursionError):
            continue
        if isinstance(obj, dict):
            try:
                _finite_json(obj)
                _utf8_json(obj)
            except (ValueError, RecursionError):
                return None
            return obj
    return None


def _utf8_json(value: Any) -> None:
    """Reject strings the JSON escape syntax admits but UTF-8 cannot carry (lone surrogates)."""
    if isinstance(value, str):
        value.encode("utf-8")
    elif isinstance(value, dict):
        for key, item in value.items():
            _utf8_json(key)
            _utf8_json(item)
    elif isinstance(value, list):
        for item in value:
            _utf8_json(item)


def _finite_json(value: Any) -> None:
    """Reject non-finite JSON numbers at every depth, including unused extension fields."""
    if type(value) is float and not math.isfinite(value):
        raise ValueError("nonfinite model number")
    if isinstance(value, dict):
        for item in value.values():
            _finite_json(item)
    elif isinstance(value, list):
        for item in value:
            _finite_json(item)


def validate_schema(value: Any, schema: dict, *, partial: bool = False) -> None:
    """Enforce the supported JSON-schema types, required fields, enums and numeric bounds.

    Guarantees a refusal names what failed: an absent required field by name, and a
    value no alternative admits by each alternative's own reason, so a refused
    answer reads against the published schema it failed.
    """
    if not isinstance(schema, dict):
        raise ValueError("schema must be an object")
    if "anyOf" in schema:
        reasons = []
        for alternative in schema["anyOf"]:
            try:
                validate_schema(value, alternative, partial=partial)
                break
            except (ValueError, TypeError) as exc:
                reasons.append(str(exc) or type(exc).__name__)
        else:
            raise ValueError("no matching alternative: " + " | ".join(reasons))
    kind = schema.get("type")
    types = {"object": dict, "array": list, "string": str, "boolean": bool,
             "integer": int, "number": (int, float), "null": type(None)}
    kinds = kind if isinstance(kind, list) else [kind]
    if kind is not None and not any(
        isinstance(k, str) and k in types and isinstance(value, types[k])
        and not (k in ("number", "integer") and isinstance(value, bool)) for k in kinds
    ):
        raise ValueError("wrong field type")
    if "enum" in schema and not any(type(value) is type(v) and value == v
                                    for v in schema["enum"]):
        raise ValueError("field is outside enum")
    if type(value) in (int, float):
        for key, invalid in (("minimum", lambda b: value < b),
                             ("maximum", lambda b: value > b),
                             ("exclusiveMinimum", lambda b: value <= b),
                             ("exclusiveMaximum", lambda b: value >= b)):
            if key in schema and invalid(schema[key]):
                raise ValueError("number out of range")
    if isinstance(value, dict):
        absent = [k for k in schema.get("required", []) if k not in value] if not partial else []
        if absent:
            raise ValueError("required field absent: " + ", ".join(map(str, absent)))
        properties = schema.get("properties", {})
        for key, item in value.items():
            if key in properties:
                try:
                    validate_schema(item, properties[key])
                except ValueError as exc:
                    raise ValueError(f"{key}: {exc}") from None
            elif schema.get("additionalProperties") is False:
                raise ValueError(f"unexpected field: {key}")
            elif isinstance(schema.get("additionalProperties"), dict):
                validate_schema(item, schema["additionalProperties"])
    if isinstance(value, list):
        if (len(value) < schema.get("minItems", 0)
                or len(value) > schema.get("maxItems", len(value))):
            raise ValueError("array length out of range")
        for item in value:
            validate_schema(item, schema.get("items", {}))


def declines(outputs: Any) -> bool:
    """Whether a reply is the refusal form: its reserved ``status`` reads ``cannot``.

    Guarantees one answer everywhere a decline is recognised, whatever else the
    reply carries: ``status`` is the refusal form's field in the universal envelope
    (``reserved_return_fields``), so its value decides and ``reason`` never does,
    and case or surrounding space does not turn a decline into an instruction.
    """
    return (isinstance(outputs, dict)
            and str(outputs.get("status", "")).strip().lower() == "cannot")


def reserved_return_fields(*, max_children: int | None = None,
                           max_tool_calls: int | None = None) -> dict:
    """Publish the universal envelope: the reserved names and types every return may carry.

    Guarantees the envelope is the protocol and nothing else (primitive audit F7;
    essay II.I: "you can easily limit the types of patterns available ... by
    overspecifying the primitive"): continuity, propensity, registrations,
    requests, tool calls and forecasts, plus the refusal form (``status``,
    ``reason``), the selected kind (``emits``) and the handle a return is about.
    No venue's order semantics and no seed role's fields: those belong to the
    kinds that own them (``kind_return_fields``), so a population kind may give
    ``action`` or ``verdict`` its own meaning.
    """
    properties = {k: {"type": "string"} for k in ("reason", "status", "emits",
                                                  "about_handle")}
    properties.update({
        **CONTINUITY_RETURN_FIELDS,  # working_state and ack_through (C1)
        # The deciding agent's own distribution over its own actions.
        "propensity": {"type": "object"},
        "register": {"type": "array"},
        "tool_calls": {"type": "array", "items": {
            "type": "object", "properties": {"tool": {"type": "string"},
                                                "args": {"type": "object"}},
            "required": ["tool", "args"]}},
        "requests": {"type": "array", "items": {
            "type": "object", "properties": {
                "target": {"type": "string"}, "description": {"type": "string"},
                "inputs": {"type": "object"}, "outcome_schema": {"type": "object"},
                # The requester's own distribution over the alternatives it chose
                # among, and the one it took: forwarded with the request (M1).
                "propensity": {"type": "object"}, "chosen": {"type": "string"}},
            "required": ["target", "description", "inputs", "outcome_schema"]}},
        "forecasts": {"type": "array", "items": {"type": "object", "properties": {
            "predicate": {"type": "string"},
            "q": {"type": "number", "minimum": 0, "maximum": 1},
            "params": {"type": "object", "properties": {
                "horizon_events": {"type": "integer", "minimum": 1, "maximum": 200}}}},
            "required": ["predicate", "q", "params"]}},
    })
    if max_children is not None:
        properties["requests"]["maxItems"] = max_children
    if max_tool_calls is not None:
        properties["tool_calls"]["maxItems"] = max_tool_calls
    return properties


_UNIT = {"type": "number", "minimum": 0, "maximum": 1}
#: The seed kinds whose answer may be a market order on this world's venue
#: (``{"action": "order", "coin", "side", "size"}``). A return of any other kind
#: never trades through its answer: it trades, if at all, through venue tools.
ANSWER_ORDER_KINDS = frozenset({"ProducerReturn", "Exposure"})
_ORDER_FIELDS = {"action": {"type": "string"}, "rationale": {"type": "string"},
                 "coin": {"type": "string"}, "side": {"type": "string"}}
#: The fields each seed kind owns, with their types (primitive audit F7): the
#: producer kinds own the answer order, a Verdict its verdict and payoff, a
#: MetaVerdict its conformity. A population kind owns what its schema declares.
KIND_RETURN_FIELDS: dict[str, dict[str, dict]] = {
    "ProducerReturn": _ORDER_FIELDS,
    "Exposure": _ORDER_FIELDS,
    "Verdict": {"verdict": _UNIT, "payoff": _UNIT, "rationale": {"type": "string"}},
    "MetaVerdict": {"conformity": _UNIT, "rationale": {"type": "string"}},
    "CounterVerdict": {"verdict": _UNIT, "rationale": {"type": "string"}},
}


#: The trade a return declined, as the contracts of the judged and exposure kinds
#: publish it (essay II.III.b: an evaluator is graded by realized consequence, which
#: includes the priced road not taken). The runtime says when it is required
#: (``runtime.grounded.counterfactual_refusal``); this is only its published shape.
COUNTERFACTUAL_FIELD: dict[str, Any] = {
    "type": "object",
    "description": "a declined trade, coin and side",
    "properties": {"coin": {"type": "string"}, "side": {"enum": ["buy", "sell"]}},
    "required": ["coin", "side"],
}


def with_counterfactual(schema: Any) -> Any:
    """``schema`` with ``COUNTERFACTUAL_FIELD`` published on every object answer shape.

    Guarantees a copy (``schema`` is never mutated) in which each answer shape
    (``_answer_shapes``) that is an object schema and does not already name
    ``counterfactual`` names it, so a closed contract can still carry it; any other
    schema is returned as a copy, unchanged.
    """
    schema = deepcopy(schema)
    if not isinstance(schema, dict):
        return schema
    alternatives = schema.get("anyOf")
    if (set(schema) == {"anyOf"} and isinstance(alternatives, list)
            and all(isinstance(a, dict) for a in alternatives)):
        return {"anyOf": [with_counterfactual(a) for a in alternatives]}
    properties = schema.get("properties")
    if schema.get("type") != "object" and not isinstance(properties, dict):
        return schema
    properties = dict(properties) if isinstance(properties, dict) else {}
    properties.setdefault("counterfactual", deepcopy(COUNTERFACTUAL_FIELD))
    return {**schema, "properties": properties}


def producing_contract(shape: Any, *, listed: Any, answer_order: bool) -> Any:
    """The final answers a producing kind's ``shape`` admits, as the kernel enforces them.

    Chapter II §II.b: physics is enforced, not announced, and the published contract
    is the enforced one. The one builder of this union: the request publishes it and
    the kernel validates the reply against it, and ``wire_schema`` carries the same
    forms to a constrained decoder, so the three cannot diverge.

    ``listed`` is the coins the world lists now, or None for a decision that already
    acted (a venue write accepted or pending, lots or earnings). Guarantees a copy
    (``shape`` is never mutated), and:

    - ``listed`` None: ``shape`` publishing ``COUNTERFACTUAL_FIELD``, not required;
    - ``listed`` empty: ``shape`` without the field, which then names no listed coin;
    - otherwise the union of (a) ``shape`` with ``counterfactual`` required, its coin
      one of ``listed``, and, when ``answer_order`` (a kind that owns the answer order,
      from a decision that may write), (b) ``shape`` with ``action`` ``"order"`` and
      ``coin``, ``side`` and ``size`` required. A lone form is returned bare.

    A shape that is not an object answer is returned as a copy, unchanged.
    """
    shape = deepcopy(shape)
    if not isinstance(shape, dict) or not (
            shape.get("type") == "object" or isinstance(shape.get("properties"), dict)):
        return shape
    properties = dict(shape.get("properties") or {})
    required = [k for k in shape.get("required", []) if k != "counterfactual"]
    if listed is None:
        properties["counterfactual"] = deepcopy(COUNTERFACTUAL_FIELD)
        return {**shape, "properties": properties, "required": required}
    listed = sorted(str(coin) for coin in listed)
    if not listed:
        properties.pop("counterfactual", None)
        return {**shape, "properties": properties, "required": required}
    named = deepcopy(COUNTERFACTUAL_FIELD)
    named["properties"]["coin"] = {"type": "string", "enum": listed}
    properties["counterfactual"] = named
    forms = [{**shape, "properties": properties, "required": [*required, "counterfactual"]}]
    if answer_order:
        action = _intersect(properties.get("action", {"type": "string"}),
                            {"type": "string", "enum": ["order"]})
        if action is not None:
            forms.append({**shape, "properties": {
                **properties, "action": action,
                "coin": {"type": "string"}, "side": {"enum": ["buy", "sell"]},
                "size": {"type": ["string", "number"]}},
                "required": [*required, *(k for k in ("action", "coin", "side", "size")
                                          if k not in required)]})
    return forms[0] if len(forms) == 1 else {"anyOf": forms}


def cap_continuation(schema: Any, *, tool_calls: bool) -> Any:
    """``schema`` stating that this round admits no children, and tool calls only if allowed.

    Guarantees a copy in which every open object answer shape bounds ``requests`` to
    no items, and ``tool_calls`` too unless ``tool_calls``: the kernel refuses a
    child in any continuation round, and treats the answer to a round that grants no
    further tools as final. A closed shape that does not name a list already forbids
    it and is left as it is.
    """
    schema = deepcopy(schema)
    if not isinstance(schema, dict):
        return schema
    alternatives = schema.get("anyOf")
    if (set(schema) == {"anyOf"} and isinstance(alternatives, list)
            and all(isinstance(a, dict) for a in alternatives)):
        return {"anyOf": [cap_continuation(a, tool_calls=tool_calls) for a in alternatives]}
    properties = schema.get("properties")
    if schema.get("type") != "object" and not isinstance(properties, dict):
        return schema
    properties = dict(properties) if isinstance(properties, dict) else {}
    closed = schema.get("additionalProperties") is False
    for key in ("requests", *(() if tool_calls else ("tool_calls",))):
        if key in properties:
            properties[key] = {**properties[key], "maxItems": 0}
        elif not closed:
            properties[key] = {"type": "array", "maxItems": 0}
    return {**schema, "properties": properties}


def kind_return_fields(kind: str | None) -> dict:
    """The reserved fields ``kind`` owns beside the universal envelope; {} for any other."""
    return {name: dict(shape) for name, shape in KIND_RETURN_FIELDS.get(kind or "", {}).items()}


def answer_kind(emits: Any, parsed: Any, req: Request | None = None) -> str | None:
    """The kind a reply answers as: its selected ``emits``, else its contract's only kind.

    Guarantees None for a policy ballot (it answers no contract) and for a
    polymorphic contract whose reply selected none of its kinds, so no kind's
    fields are imposed on a reply that did not choose that kind.
    """
    if req is not None and req.scoring_channel == "policy":
        return None
    kinds = tuple(emits or ())
    chosen = parsed.get("emits") if isinstance(parsed, dict) else None
    if isinstance(chosen, str) and chosen in kinds:
        return chosen
    return kinds[0] if len(kinds) == 1 else None


class SectionError(ValueError):
    """One optional section of a return, or one item of a list section, is invalid.

    Raised by a return validator (the runtime's output contract) for a fault that
    belongs to that section alone: the section or item is dropped with ``reason``
    and the rest of the return is validated again. Anything else a validator
    raises is a fault in the answer itself and voids the return.
    """

    def __init__(self, section: str, reason: str, index: int | None = None, *,
                 atomic: bool = True) -> None:
        super().__init__(reason)
        self.section, self.reason, self.index = section, reason, index
        # False when the validator knows the batch writes nothing: a bad read then
        # drops only itself, and the reads beside it still run.
        self.atomic = atomic


#: What a return may carry beside its answer. A section here (or one item of a
#: list section) that does not validate is dropped with its reason and the answer
#: stands. A continued turn may also discard malformed request-specific draft
#: fields; final answers and core fields — the envelope's emits, about_handle and
#: status, and the fields the answer's kind owns (a producer kind's action and
#: order, a Verdict's verdict and payoff, a MetaVerdict's conformity) — validate
#: strictly.
OPTIONAL_SECTIONS = ("rationale", "working_state", "ack_through", "propensity",
                     "register", "tool_calls", "requests", "forecasts")
_LIST_SECTIONS = frozenset({"register", "tool_calls", "requests", "forecasts"})
#: List sections that are one batch of effects written together: a seat may pair a
#: venue write with another call or a child with its sibling, so one bad item drops
#: the whole batch and no part of it runs alone. Registrations and forecasts are
#: admitted and scored one by one, so there only the bad item goes.
_ATOMIC_SECTIONS = frozenset({"tool_calls", "requests"})
_FAULTS = (ValueError, TypeError, ArithmeticError, RecursionError, KeyError, AttributeError)


def validate_return_sections(parsed: dict, schema: dict, validator=None, req=None,
                             *, rejected: list[dict[str, Any]] | None = None,
                             kind: str | None = None,
                             ) -> tuple[dict, tuple[dict[str, Any], ...]]:
    """Return the reply with invalid optional sections dropped, and what was dropped.

    Guarantees the answer is validated exactly as strictly as a whole return was:
    the pruned reply passes ``_validate_return`` (under ``kind``, the kind the
    reply answers as; ``answer_kind``) and ``validator`` in full, or this
    raises and the return is malformed. Only a section named in
    ``OPTIONAL_SECTIONS``, or one item of a list section, may be dropped. On a
    continuation only, an invalid task-specific answer field may also be dropped:
    it is an unfinished answer beside valid effects, not an effect itself. Core
    reserved fields, order size, and explicitly declared emitted bodies remain
    atomic. Every fault has a bounded reason and, for an item, its original index.
    A dropped section is gone from the reply, so nothing in it reaches an effect.
    If supplied, ``rejected`` retains section faults even when the whole answer fails.
    """
    parsed = dict(parsed)
    required = schema.get("required", ()) if isinstance(schema, dict) else ()
    # Two habits every model has, neither of which changes what an answer says:
    # ``null`` for a field it is leaving out, and its explanation under ``reason``
    # when the contract names it ``rationale``. A null optional field is absent; a
    # missing required rationale is read from a string reason. Nothing else is
    # coerced, and a null in a required field still fails.
    parsed = {k: v for k, v in parsed.items() if v is not None or k in required}
    if ("rationale" in required and "rationale" not in parsed
            and isinstance(parsed.get("reason"), str) and parsed["reason"].strip()):
        parsed["rationale"] = parsed["reason"]
    dropped: list[dict[str, Any]] = [] if rejected is None else rejected
    origin: dict[str, list[int]] = {}

    def drop(section: str, reason: str, index: int | None = None) -> None:
        entry: dict[str, Any] = {"section": section, "reason": str(reason)[:200]}
        if index is not None:
            entry["index"] = index
        dropped.append(entry)

    reserved = reserved_return_fields()
    owned = kind_return_fields(kind)
    declared = _declared_fields(schema)
    for section in OPTIONAL_SECTIONS:
        if section not in parsed:
            continue
        shapes = [s for s in (reserved.get(section), owned.get(section), declared.get(section))
                  if isinstance(s, dict)]
        value = parsed[section]
        if section not in _LIST_SECTIONS:
            try:
                for shape in shapes:
                    validate_schema(value, shape)
            except _FAULTS as exc:
                drop(section, str(exc) or type(exc).__name__)
                del parsed[section]
            continue
        if not isinstance(value, list):
            drop(section, f"{section} must be a list")
            del parsed[section]
            continue
        kept, where, faults = [], [], []
        for index, item in enumerate(value):
            try:
                for shape in shapes:
                    validate_schema(item, shape.get("items", {}))
                if section == "requests":
                    _check_child(item)
            except _FAULTS as exc:
                faults.append((index, str(exc) or type(exc).__name__))
                continue
            kept.append(item)
            where.append(index)
        limits = [s["maxItems"] for s in shapes if type(s.get("maxItems")) is int]
        if limits and len(kept) > min(limits):
            faults.extend((index, f"more than {min(limits)} {section}")
                          for index in where[min(limits):])
            kept, where = kept[:min(limits)], where[:min(limits)]
        # A round whose contract admits no tool calls at all (``cap_continuation``) has no
        # slot to answer one in: the section goes whole, as a child request's does.
        closed_round = bool(limits) and min(limits) == 0
        if faults and section == "tool_calls" and validator is not None and not closed_round:
            # Each refused call keeps its slot, marked, and is answered there with its
            # reason; the runtime validator voids the whole batch if it writes, so a
            # write never runs beside a refused call and a read-only turn survives.
            reasons: dict[int, str] = {}
            for index, reason in sorted(faults):
                reasons.setdefault(index, reason)
            rebuilt = []
            for index, item in enumerate(value):
                if index not in reasons:
                    rebuilt.append(item)
                    continue
                base = item if isinstance(item, dict) else {}
                rebuilt.append({
                    "tool": base.get("tool") if isinstance(base.get("tool"), str) else "",
                    "args": base.get("args") if isinstance(base.get("args"), dict) else {},
                    "invalid": reasons[index][:200]})
                drop(section, reasons[index], index)
            parsed[section], origin[section] = rebuilt, list(range(len(rebuilt)))
            continue
        if faults and section in _ATOMIC_SECTIONS:
            index, reason = min(faults)
            drop(section, f"item {index}: {reason}")
            del parsed[section]
            continue
        for index, reason in faults:
            drop(section, reason, index)
        if faults and not kept:
            del parsed[section]  # every item went: the section is gone, not empty
            continue
        parsed[section], origin[section] = kept, where
    # A continuation is a turn boundary, not a partial final answer. Some models
    # fill the final schema with placeholders while asking for the evidence that
    # will produce the real answer. Preserve a valid continuation by removing only
    # malformed fields declared by this request's outcome schema. Reserved return
    # fields and order size still validate atomically, and an explicit ``emits``
    # keeps its declared event body atomic rather than laundering it as a draft.
    continuation = bool(parsed.get("tool_calls") or parsed.get("requests"))
    status_shape = declared.get("status") if isinstance(declared, dict) else None
    if (continuation and "status" in parsed and not declines(parsed)
            and isinstance(status_shape, dict) and "enum" in status_shape
            and parsed["status"] not in status_shape["enum"]):
        # "pending" beside tool calls is a draft of an answer not yet given.
        drop("status", f"unfinished continuation field: {parsed['status']!r}")
        del parsed["status"]
    if continuation and "emits" not in parsed:
        protected = {*reserved, *owned, "size"}
        for section, shape in declared.items():
            if section not in parsed or section in protected or not isinstance(shape, dict):
                continue
            try:
                validate_schema(parsed[section], shape)
            except _FAULTS as exc:
                drop(section, f"unfinished continuation field: {exc}")
                del parsed[section]
    # The answer, strictly; a validator names a fault that belongs to one section.
    for _ in range(1 + sum(len(v) for v in origin.values()) + len(OPTIONAL_SECTIONS)):
        try:
            _validate_return(parsed, schema, kind)
            if validator is not None:
                validator(parsed, req)
        except SectionError as exc:
            if exc.section not in OPTIONAL_SECTIONS or exc.section not in parsed:
                raise ValueError(exc.reason) from None
            if (exc.section in _ATOMIC_SECTIONS and not exc.atomic and exc.index is not None
                    and exc.section in origin
                    and 0 <= exc.index < len(parsed[exc.section])
                    and not parsed[exc.section][exc.index].get("invalid")):
                # A read that cannot run stays in its slot, marked, and is answered
                # there with its error: the turn remains a continuation even when
                # every read in it was wrong, and nothing unvalidated is dispatched.
                drop(exc.section, exc.reason, origin[exc.section][exc.index])
                parsed[exc.section] = [
                    {**item, "invalid": exc.reason[:200]} if i == exc.index else item
                    for i, item in enumerate(parsed[exc.section])]
                continue
            if (exc.index is None or exc.section not in origin
                    or exc.section in _ATOMIC_SECTIONS):
                where = "" if exc.index is None else f"item {exc.index}: "
                drop(exc.section, where + exc.reason)
                del parsed[exc.section]
                continue
            if not 0 <= exc.index < len(parsed[exc.section]):
                raise ValueError(exc.reason) from None
            drop(exc.section, exc.reason, origin[exc.section].pop(exc.index))
            parsed[exc.section] = [item for i, item in enumerate(parsed[exc.section])
                                   if i != exc.index]
            if not parsed[exc.section]:
                del parsed[exc.section], origin[exc.section]
            continue
        if dropped and not parsed:
            raise ValueError("nothing in the return validated")  # no answer to keep
        order = {section: index for index, section in enumerate(OPTIONAL_SECTIONS)}
        dropped.sort(key=lambda d: (order.get(d["section"], len(order)),
                                    d.get("index", -1)))
        return parsed, tuple(dropped)
    raise ValueError("return sections did not settle")


def _declared_fields(schema: Any) -> dict:
    """The field shapes a contract declares for every answer of one kind.

    Guarantees a plain object contract's own properties, and, for a union of answers
    of one kind (``producing_contract``: every alternative pins the same ``emits``, or
    none does), the properties every alternative declares identically; {} for a union
    over several kinds, whose alternatives each declare their own.
    """
    if not isinstance(schema, dict):
        return {}
    if set(schema) != {"anyOf"} or not isinstance(schema.get("anyOf"), list):
        properties = schema.get("properties", {})
        return properties if isinstance(properties, dict) else {}
    shapes = _answer_shapes(schema)
    if not shapes or not all(isinstance(s.get("properties"), dict) for s in shapes):
        return {}
    pins = {json.dumps(s["properties"].get("emits"), sort_keys=True) for s in shapes}
    if len(pins) != 1:
        return {}
    first = shapes[0]["properties"]
    return {k: v for k, v in first.items()
            if all(s["properties"].get(k) == v for s in shapes[1:])}


def _check_child(child: dict) -> None:
    """A child request names a target and a task, carries no authorship, and a real schema.

    Guarantees a forwarded propensity is a distribution (``validate_propensity``)
    whose ``chosen`` action it declares with positive mass, and that ``chosen``
    never travels without the distribution it was chosen from.
    """
    if not child["target"] or not child["description"].strip():
        raise ValueError("child needs target and description")
    if any(k in child["inputs"] for k in ("author", "author_id", "requester", "lineage")):
        raise ValueError("child inputs contain author metadata")
    _schema_definition(child["outcome_schema"])
    if "propensity" in child:
        declared = validate_propensity(child["propensity"])
        chosen = child.get("chosen")
        if not isinstance(chosen, str) or declared.get(chosen.strip(), 0) <= 0:
            raise ValueError("a request's propensity names its chosen action with positive mass")
    elif "chosen" in child:
        raise ValueError("chosen needs the propensity it was chosen from")


#: Fields a venue tool takes that an answer's market order cannot honour, and the
#: venue SDK's own names for fields it can. Each would change which trade executes.
_NOT_AN_ANSWER_ORDER = ("is_buy", "sz", "limit_px", "price", "tif", "reduce_only",
                        "reduceOnly", "order_type", "orderType")


def _validate_return(parsed: dict, schema: dict, kind: str | None = None) -> None:
    """Validate reply effects; each registration is admitted independently by the runtime.

    Guarantees the universal envelope on every reply, and the fields ``kind`` owns
    (``kind_return_fields``) on a reply of that kind: the answer-order rules apply
    to the producer kinds alone (``ANSWER_ORDER_KINDS``), so an ``action`` of
    ``"order"`` in any other kind's reply is that kind's word, never a trade.
    """
    validate_schema(parsed, {"type": "object", "properties": reserved_return_fields()})
    validate_schema(parsed, {"type": "object", "properties": kind_return_fields(kind)})
    # "order" is both an instruction and the name of a trade already made through a
    # tool. An answer carrying any order field is an instruction and validates
    # whole; one carrying none reports what the decision did (the runtime refuses
    # it to the seat if nothing was done), so a report is never a malformed return.
    if kind in ANSWER_ORDER_KINDS and parsed.get("action") == "order" and any(
            k in parsed for k in ("coin", "side", "size", *_NOT_AN_ANSWER_ORDER)):
        named = sorted(k for k in _NOT_AN_ANSWER_ORDER if k in parsed)
        if named:
            # An answer's order is a market order in this world's names. A limit
            # price, a time in force or reduce-only would be silently dropped, and an
            # SDK name like is_buy would leave the side to a default: each would
            # execute a different trade from the one written.
            raise ValueError(
                f"an answer order cannot carry {', '.join(named)}: it is a market order "
                '{"action": "order", "coin", "side": "buy"|"sell", "size"}; a limit, '
                "reduce-only or close is a venue tool call")
        validate_schema(parsed, {"properties": {
            "side": {"enum": ["buy", "sell"]}}, "required": ["coin", "side", "size"]})
        positive_wire_decimal(parsed["size"])
    # Tool/child requests may precede the final answer, but fields already supplied are typed.
    continuation = bool(parsed.get("tool_calls") or parsed.get("requests"))
    cannot = parsed.get("status") == "cannot" and isinstance(parsed.get("reason"), str)
    validate_schema(parsed, schema, partial=continuation or cannot)
    for child in parsed.get("requests", []):
        _check_child(child)


def wire_schema(schema: Any, emits: Any = None, *, policy: bool = False) -> dict | None:
    """The contract as a provider's constrained decoder carries it, or None for no schema.

    ``emits`` is the executing seat's emitted kinds and ``policy`` marks a policy
    ballot, exactly as ``answer_kind`` reads them.

    Guarantees, for the answer forms ``_validate_return`` distinguishes (the final
    answer, a continuation through a non-empty ``tool_calls`` or ``requests``, and
    the refusal ``status: "cannot"`` with a string ``reason``):

    - each form is the intersection of what the kernel checks a reply against: the
      universal envelope (``reserved_return_fields``), the fields the answer's kind
      owns (``kind_return_fields``) and the contract itself, so a reply that
      satisfies the wire passes those checks (an optional section the contract
      does not name binds only a form that requires nothing, since elsewhere the
      kernel drops an invalid one rather than refusing the reply);
    - a form the kernel cannot accept is not sent: a closed contract (one with
      ``additionalProperties: false``) that does not name ``tool_calls`` or
      ``requests`` has no continuation through it, and one that cannot hold
      ``status``/``reason`` has no refusal;
    - where two constraints cannot be intersected faithfully, the form they meet in
      is dropped rather than sent looser, and with no form left the result is None
      (the route then asks for JSON alone);
    - every object left open is marked open (``additionalProperties: true``, the
      JSON-schema default), so a decoder whose default is closed cannot forbid a
      field the kernel accepts, such as ``working_state``;
    - a contract that is a union of answers is carried as those answers: a producing
      kind's union (``producing_contract``) reaches the decoder as the same forms the
      request publishes and the kernel validates, never rebuilt here.

    What the wire cannot state stays the kernel's alone, and a wire-valid reply may
    still fail it there: the answer-order rules of the producer kinds, a child
    request's semantic checks (``_check_child``), and the runtime's own validator.
    The kernel also accepts a few habits the wire does not produce: a null optional
    field, ``reason`` read as a missing ``rationale``, and an invalid optional
    section it drops. The kernel's validation stays the authority over what a reply
    means; this is its transport. ``schema`` is never mutated.
    """
    # Chapter II §II.b: physics is enforced, not announced. The I/O contract is
    # physics, so it is handed to the decoder that samples the reply, not only
    # printed in the prompt the reply is sampled from.
    if not isinstance(schema, dict):
        return None
    schema = deepcopy(schema)
    kinds = tuple(emits or ())
    reserved = reserved_return_fields()
    forms: list[dict] = []
    for shape in _answer_shapes(schema):
        named = shape.get("properties")
        named = named if isinstance(named, dict) else {}
        checks = [reserved, *(kind_return_fields(k) for k in _shape_kinds(shape, kinds, policy))]
        # A field the kernel checks strictly binds every form, and so do the two lists
        # a continuation stands on. Any other optional section (``OPTIONAL_SECTIONS``)
        # that does not validate is dropped, not refused, so it binds only where the
        # contract names it: that keeps the wire the size of the contract. A final
        # answer that requires nothing is the exception (below).
        merged: dict | None = shape
        for fields in checks:
            strict = {k: v for k, v in fields.items()
                      if k not in OPTIONAL_SECTIONS or k in named
                      or k in ("tool_calls", "requests")}
            if merged is not None:
                # The contract leads, so its own field order is the order on the wire.
                merged = _intersect(merged, {"type": "object", "properties": strict})
        if merged is None:
            continue  # the contract contradicts the envelope or its kind: see above
        final: dict | None = merged
        if not merged.get("required"):
            # A reply of nothing but invalid optional sections is refused ("nothing in
            # the return validated"), so in a final answer that requires nothing they
            # all bind. The continuation and refusal forms require a field, so there
            # an invalid optional section is only dropped.
            for fields in checks:
                if final is not None:
                    final = _intersect(final, {"type": "object", "properties": fields})
        if final is not None:
            forms.append(final)
        properties = merged.get("properties", {})
        for key in ("tool_calls", "requests"):
            if key not in properties:
                continue  # a closed contract without ``key``: the kernel refuses it
            listed = _intersect(properties[key], {"minItems": 1})
            if listed is None or listed.get("maxItems", 1) == 0:
                continue  # this contract admits no continuation through ``key``
            partial = _partial(merged)
            partial["properties"] = {**properties, key: listed}
            partial["required"] = [key]
            forms.append(partial)
        if "status" not in properties or "reason" not in properties:
            continue  # a closed contract that cannot carry the refusal form
        status = _intersect(properties["status"], {"enum": ["cannot"]})
        reason = _intersect(properties["reason"], {"type": "string"})
        if status is None or reason is None:
            continue  # the contract's own status or reason cannot say it
        refusal = _partial(merged)
        refusal["properties"] = {**properties, "status": status, "reason": reason}
        refusal["required"] = ["status", "reason"]
        forms.append(refusal)
    if not forms:
        return None
    # Every reply is an object (``_validate_return``'s envelope), so the root says so.
    return deepcopy(_open({"type": "object", "anyOf": forms}))


def _shape_kinds(shape: dict, kinds: tuple[str, ...], policy: bool) -> tuple[str, ...]:
    """The kinds whose owned fields bind a reply answering as ``shape``.

    One emitted kind binds every reply (``answer_kind``). Among several, a shape
    that pins ``emits`` to one of them is that kind's; a shape that does not may be
    answered as any of them, so all of their fields bind it, which is never looser
    than the one the kernel will apply.
    """
    if policy:
        return ()
    if len(kinds) <= 1:
        return kinds
    pinned = (shape.get("properties") or {}).get("emits")
    enum = pinned.get("enum") if isinstance(pinned, dict) else None
    if isinstance(enum, list) and len(enum) == 1 and enum[0] in kinds:
        return (enum[0],)
    return kinds


_LOWER_BOUNDS = ("minimum", "exclusiveMinimum", "minItems")
_UPPER_BOUNDS = ("maximum", "exclusiveMaximum", "maxItems")
#: Keywords that only describe, never constrain: either side's is kept.
_ANNOTATIONS = ("description", "title")


def _intersect(a: Any, b: Any) -> dict | None:
    """One schema that admits exactly what both ``a`` and ``b`` admit, or None.

    Guarantees the result is never looser than either operand. Keywords that
    appear in only one side are conjunctive as siblings and are kept as they are.
    Where both sides state a keyword: ``properties`` intersect key by key (a
    closed side's unnamed keys stay forbidden), ``required`` is the union,
    ``type`` and ``enum`` are the common values, bounds take the tighter,
    ``items`` and ``additionalProperties`` intersect. None means the pair cannot be
    stated as one schema without ``allOf``, which hosts' decoders support unevenly,
    or admits nothing at all.
    """
    if not isinstance(a, dict) or not isinstance(b, dict):
        return None
    if a == b:
        return deepcopy(a)
    out: dict[str, Any] = {}
    for key in [*a, *(k for k in b if k not in a)]:
        if key not in b or key not in a:
            out[key] = deepcopy(a[key] if key in a else b[key])
            continue
        x, y = a[key], b[key]
        if x == y or key in _ANNOTATIONS:
            out[key] = deepcopy(x)
        elif key == "type":
            xs = x if isinstance(x, list) else [x]
            ys = y if isinstance(y, list) else [y]
            common = [t for t in xs if t in ys]
            common += [t for t in ("integer",) if t not in common and (
                (t in xs and "number" in ys) or (t in ys and "number" in xs))]
            if not common:
                return None
            out[key] = common[0] if len(common) == 1 else common
        elif key == "enum":
            if not isinstance(x, list) or not isinstance(y, list):
                return None
            common = [v for v in x if any(type(v) is type(w) and v == w for w in y)]
            if not common:
                return None
            out[key] = common
        elif key == "required":
            if not isinstance(x, list) or not isinstance(y, list):
                return None
            out[key] = [*x, *(k for k in y if k not in x)]
        elif key in (*_LOWER_BOUNDS, *_UPPER_BOUNDS):
            if not all(type(v) in (int, float) for v in (x, y)):
                return None
            out[key] = max(x, y) if key in _LOWER_BOUNDS else min(x, y)
        elif key == "items":
            items = _intersect(x, y)
            if items is None:
                return None
            out[key] = items
        elif key in ("properties", "additionalProperties"):
            continue  # stated together below: each depends on the other side's
        else:
            return None  # e.g. two different anyOf: not one schema without allOf
    if {"properties", "additionalProperties"} & {*a, *b}:
        merged = _intersect_properties(a, b)
        if merged is None:
            return None
        out.update(merged)
    for lower, upper in (("minimum", "maximum"), ("minItems", "maxItems")):
        if lower in out and upper in out and out[lower] > out[upper]:
            return None
    return out


def _intersect_properties(a: dict, b: dict) -> dict | None:
    """``properties`` and ``additionalProperties`` of the intersection of two object schemas."""
    pa, pb = a.get("properties") or {}, b.get("properties") or {}
    ea, eb = a.get("additionalProperties", True), b.get("additionalProperties", True)
    if not isinstance(pa, dict) or not isinstance(pb, dict):
        return None
    properties: dict[str, Any] = {}
    for name in [*pa, *(k for k in pb if k not in pa)]:
        # A name one side does not list is governed there by its additionalProperties.
        left = pa[name] if name in pa else ea
        right = pb[name] if name in pb else eb
        if left is False or right is False:
            continue  # forbidden by a closed side: not listed, so still forbidden
        left = {} if left is True else left
        right = {} if right is True else right
        shape = _intersect(left, right)
        if shape is None:
            return None
        properties[name] = shape
    if ea is False or eb is False:
        extra: Any = False
    elif ea is True or eb is True:
        extra = eb if ea is True else ea
    else:
        extra = _intersect(ea, eb)
        if extra is None:
            return None
    out: dict[str, Any] = {"properties": properties} if properties or pa or pb else {}
    if "additionalProperties" in a or "additionalProperties" in b:
        out["additionalProperties"] = extra
    return out


def _answer_shapes(schema: dict) -> list[dict]:
    """A contract's alternatives, flattened when it is nothing but a union of them."""
    alternatives = schema.get("anyOf")
    if (set(schema) == {"anyOf"} and isinstance(alternatives, list) and alternatives
            and all(isinstance(a, dict) for a in alternatives)):
        return [shape for a in alternatives for shape in _answer_shapes(a)]
    return [schema]


def _partial(shape: dict) -> dict:
    """``shape`` as ``validate_schema(partial=True)`` reads it: no top-level required."""
    out = {k: v for k, v in shape.items() if k != "required"}
    if isinstance(out.get("anyOf"), list):
        out["anyOf"] = [_partial(a) if isinstance(a, dict) else a for a in out["anyOf"]]
    return out


def _open(schema: Any) -> Any:
    """Every object schema without ``additionalProperties`` states the default, true."""
    if isinstance(schema, list):
        return [_open(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    out = {}
    for key, value in schema.items():
        if key in ("properties", "patternProperties") and isinstance(value, dict):
            out[key] = {name: _open(sub) for name, sub in value.items()}
        elif key in ("items", "additionalProperties", "anyOf", "oneOf", "allOf", "not"):
            out[key] = _open(value)
        else:
            out[key] = value
    kind = out.get("type")
    if (kind == "object" or (isinstance(kind, list) and "object" in kind)
            or "properties" in out) and "additionalProperties" not in out:
        out["additionalProperties"] = True
    return out


def validate_proposal(proposal: dict) -> None:
    """Reject malformed proposal fields before any registration effect."""
    fields = {k: {"type": "string"} for k in (
        "kind", "id", "model_id", "openrouter_id", "role", "system_prompt", "effort",
        "event_kind", "learner", "description", "code", "tick_interval",
        "unit", "assembly_id", "state_policy")}
    # A tool's timeout stays within [1, 5] (checked where tools are parsed); a
    # program seat's may reach MAX_PROGRAM_TIMEOUT_S.
    fields.update({"gamma": {"type": "number", "minimum": 1e-300, "maximum": 1},
                   "max_tokens": {"type": ["integer", "null"], "minimum": 16},
                   "timeout_s": {"type": "integer", "minimum": 1,
                                 "maximum": MAX_PROGRAM_TIMEOUT_S},
                   "accepts": {"type": "array", "items": {"type": "string"}},
                   "emits": {"type": "array", "items": {"type": "string"}},
                   "schemas": {"type": "object"},
                   "trigger": {"type": "object"},
                   "endowment_micro": {"type": "integer", "minimum": 1},
                   "range": {"type": "array", "items": {"type": "number"}},
                   "actions": {"type": "array", "items": {"type": "string"}},
                   "args_schema": {"type": "object"},
                   "returns_schema": {"type": "object"}})
    validate_schema(proposal, {"type": "object", "properties": fields, "required": ["kind"]})
    if proposal["kind"] == "router" and "add" in proposal:
        add = proposal["add"]
        if not isinstance(add, bool) and not (isinstance(add, str)
                                              and add.lower() in ("true", "false")):
            raise ValueError("add must be a boolean")
    if proposal["kind"] == "amendment":
        from factorylab.charter.amendment import effect_schema
        from factorylab.charter.windows import window_schema

        validate_schema(proposal, {"properties": {
            "predicted_effect": effect_schema(),
            "lambda": {"type": "object"},
            "add": {"type": "array", "items": {"type": "object"}},
            "replace": {"type": "array", "items": {"type": "object"}},
            "remove": {"type": "array", "items": {"type": "string"}}}})
        for card in proposal.get("add", []) + proposal.get("replace", []):
            validate_schema(card, {"properties": {
                **{k: {"type": "string"} for k in (
                    "id", "norm", "description", "units", "acceptable_region",
                    "observation", "answers_for")},
                "window": window_schema(),
                "lambda": {"type": "number", "minimum": 0}}})


def positive_wire_decimal(value: Any) -> None:
    """Model amounts must be positive, finite, and representable by the venue wire format."""
    if type(value) not in (str, int, float):
        raise ValueError("invalid decimal amount")
    number = Decimal(str(value))
    if (not number.is_finite() or number <= 0 or not math.isfinite(float(number))
            or float(number) == 0):
        raise ValueError("decimal amount out of range")


def _schema_definition(schema: Any) -> None:
    """Composition schemas are type-checked and cannot silently request unsupported constraints."""
    if not isinstance(schema, dict):
        raise ValueError("schema must be an object")
    allowed = {"type", "properties", "required", "enum", "minimum", "maximum", "minItems",
               "maxItems", "additionalProperties", "items", "description", "title", "default",
               "anyOf", "exclusiveMinimum", "exclusiveMaximum"}
    if schema.keys() - allowed:
        raise ValueError("unsupported child outcome schema keyword")
    if "type" in schema and schema["type"] not in (
        "object", "array", "string", "boolean", "integer", "number", "null"
    ):
        raise ValueError("unsupported schema type")
    for key in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"):
        if key in schema and type(schema[key]) not in (int, float):
            raise ValueError("schema bound must be a number")
    for key in ("minItems", "maxItems"):
        if key in schema and (type(schema[key]) is not int or schema[key] < 0):
            raise ValueError("schema item bound must be a nonnegative integer")
    if "required" in schema and (not isinstance(schema["required"], list)
                                 or any(not isinstance(k, str) for k in schema["required"])):
        raise ValueError("schema required must contain strings")
    if "enum" in schema and not isinstance(schema["enum"], list):
        raise ValueError("schema enum must be an array")
    if "properties" in schema:
        if not isinstance(schema["properties"], dict):
            raise ValueError("schema properties must be an object")
        for prop in schema["properties"].values():
            _schema_definition(prop)
    if "items" in schema:
        _schema_definition(schema["items"])
    if "additionalProperties" in schema and type(schema["additionalProperties"]) is not bool:
        _schema_definition(schema["additionalProperties"])
    if "anyOf" in schema:
        if not isinstance(schema["anyOf"], list) or not schema["anyOf"]:
            raise ValueError("anyOf must contain schemas")
        for alternative in schema["anyOf"]:
            _schema_definition(alternative)
