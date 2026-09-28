"""Public structural admission facts (Chapter II §I.b, informational schematics)."""

from copy import deepcopy
from typing import Any

from factorylab.cortex.assembly import (
    _ATOMIC_SECTIONS,
    _LIST_SECTIONS,
    _NOT_AN_ANSWER_ORDER,
    _TYPES,
    ANSWER_ORDER_KINDS,
    ASSEMBLY_MEMORY_POLICIES,
    CHILD_AUTHOR_FIELDS,
    CHILD_SCHEMA_DEF,
    CHILD_SCHEMA_KEYWORDS,
    FIELD_NAME_PATTERN,
    KIND_RETURN_FIELDS,
    MAX_CONTRACT_DESCRIPTION_CHARS,
    MAX_PROGRAM_CODE_CHARS,
    MAX_PROGRAM_STATE_BYTES,
    MAX_PROGRAM_TIMEOUT_S,
    OPTIONAL_SECTIONS,
    PROGRAM_MODEL_ID,
    PROGRAM_STATE_POLICIES,
    child_schema_shape,
    proposal_field_schema,
    reserved_return_fields,
)


def structural_admission_schematics() -> dict[str, Any]:
    """Return fresh JSON-compatible facts about assembly structural admission.

    Numeric limits and schemas come from the same definitions admission uses;
    this section states local checks, not the runtime's additional admission rules.
    """
    return deepcopy({
        "scope": "assembly structural checks; runtime validators apply additional constraints",
        "reply_json": {
            "parse": "first decodable JSON object; surrounding text and code fences permitted",
            "numbers": "finite at every depth, including extension fields",
            "strings_and_keys": "UTF-8 encodable at every depth; lone surrogates rejected",
            "field_names": FIELD_NAME_PATTERN,
            "field_name_match": "schema propertyNames uses regex search; child reply names use "
                                "fullmatch",
        },
        "schema_validation": {
            "schema": "object",
            "types": list(_TYPES),
            "type_unions": "type may be one supported type or a list of supported types",
            "numeric_types": "boolean is neither integer nor number",
            "enum": "equal value and identical Python type",
            "references": "local #/$defs/<name> to an object in root $defs; siblings also bind",
            "anyOf": "at least one alternative validates; sibling constraints also bind",
            "numeric_bounds": ["minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"],
            "objects": ["properties", "required", "additionalProperties", "propertyNames",
                        "minProperties", "maxProperties", "dependentRequired"],
            "additionalProperties": "false rejects unnamed fields; object validates them; "
                                    "otherwise open",
            "propertyNames": "pattern uses regex search; remaining name schema validates each key",
            "arrays": ["minItems", "maxItems", "items"],
            "partial": "required and dependentRequired skipped at the current object and its "
                       "anyOf/ref alternatives; nested property and item schemas validate fully",
            "unimplemented": "other keywords do not constrain values; string pattern outside "
                             "propertyNames, minLength, maxLength, uniqueItems, allOf and oneOf "
                             "are not enforced by this validator",
        },
        "return_envelope": reserved_return_fields(),
        "kind_fields": KIND_RETURN_FIELDS,
        "kind_selection": "policy ballots have no answer kind; a declared emits value selects its "
                          "kind, otherwise a sole declared kind applies, otherwise none",
        "return_normalization": {
            "null": "top-level null fields removed unless named in the schema's top-level required",
            "rationale": "when an answer form requires rationale and it is absent, a nonblank "
                         "string reason supplies it, except for declines",
            "decline": "status stripped and lowercased equal to cannot normalizes to cannot; "
                       "reason optional and string when present",
            "partial_answer": "nonempty tool_calls or requests, or a decline, enable partial "
                              "schema validation; supplied fields still validate",
        },
        "optional_sections": {
            "names": list(OPTIONAL_SECTIONS),
            "list_sections": sorted(_LIST_SECTIONS),
            "atomic_batches": sorted(_ATOMIC_SECTIONS),
            "field_checks": "each present section validates against envelope, kind-owned and "
                            "common declared field schemas",
            "common_declared_fields": "plain object properties, or identical field shapes among "
                                      "answer alternatives with the same emits pin; multi-kind "
                                      "unions have no common declared fields",
            "non_list_failure": "invalid optional field removed with reason",
            "list_failure": "non-list removed; items checked individually; retained items bounded "
                            "by the tightest declared maxItems",
            "atomic_failure": "one invalid child removes requests whole; tool_calls removed whole "
                              "without a runtime validator or when maxItems is zero",
            "tool_slots": "with a runtime validator and a nonzero call allowance, refused tool "
                          "items retain their original slot as tool/args/invalid; runtime decides "
                          "read-only versus atomic write batch behavior",
            "independent_items": "invalid registration and forecast items removed individually; a "
                                 "list with faults and no surviving items is removed",
            "continuation_drafts": "non-cannot status removed on continuation; without explicit "
                                   "emits, invalid request-specific fields removed except envelope "
                                   "fields, kind-owned fields and size",
            "final_validation": "pruned return passes envelope, kind fields, outcome schema and "
                                "runtime validator, or fails whole; empty after drops fails; "
                                "unsettled section validation fails",
        },
        "children": {
            "fields": reserved_return_fields()["requests"]["items"],
            "target": "nonempty string (whitespace is not stripped)",
            "description": "nonblank string after stripping",
            "forbidden_input_keys": list(CHILD_AUTHOR_FIELDS),
            "authorship_scope": "direct keys of child inputs",
            "propensity": "when present, passes request.validate_propensity; chosen is a string "
                          "whose stripped label has positive mass; chosen without propensity fails",
            "outcome_schema": {
                "$defs": {CHILD_SCHEMA_DEF: child_schema_shape()},
                "$ref": f"#/$defs/{CHILD_SCHEMA_DEF}",
            },
            "outcome_keywords": sorted(CHILD_SCHEMA_KEYWORDS),
            "outcome_names": "properties and required names at the reply root and all its anyOf "
                             "alternatives are fullmatch identifiers; nested value keys "
                             "unrestricted",
            "outcome_bounds": "numeric bounds are exact int or float, not boolean; item bounds are "
                              "exact nonnegative integers",
            "outcome_annotations": "description, title and default accept any value",
            "outcome_recursion": "properties, items, additionalProperties schemas and nonempty "
                                 "anyOf recursively checked; additionalProperties also accepts "
                                 "boolean",
            "batch_limit": "world and outcome-schema bounds are additional to these local checks",
        },
        "answer_orders": {
            "kinds": sorted(ANSWER_ORDER_KINDS),
            "activation": "action equals order and at least one coin/side/size or forbidden field "
                          "is present; order without any such field is a report, not this check",
            "forbidden_fields": list(_NOT_AN_ANSWER_ORDER),
            "required": ["coin", "side", "size"],
            "coin": "string",
            "side": ["buy", "sell"],
            "size": "exact string, integer or float, parsed as Decimal; positive and finite; "
                    "conversion to float finite and nonzero",
            "other_kinds": "action order does not activate answer-order semantics",
        },
        "proposals": {
            "generic_schema": proposal_field_schema(),
            "kind_schema": "a string kind with published registration.proposal_schemas forms "
                           "validates against those forms first",
            "router_add": "boolean or case-insensitive true/false string; no whitespace stripping; "
                          "published kind schema also applies",
            "amendment": "predicted_effect validates against charter.amendment.effect_schema; "
                         "lambda is object; add and replace are arrays of objects; remove is an "
                         "array of strings; card "
                         "id/norm/description/units/acceptable_region/observation/answers_for are "
                         "strings; card window validates against charter.windows.window_schema; "
                         "card lambda is a nonnegative number",
            "additional_admission": "registration, trigger and reward-contract validators apply "
                                    "their own constraints after these structural checks",
        },
        "assembly_spec": {
            "memory_policy": list(ASSEMBLY_MEMORY_POLICIES),
            "role": "nonblank string",
            "description": {"type": "string", "max_characters": MAX_CONTRACT_DESCRIPTION_CHARS},
            "max_tokens": "exact positive integer",
            "outputs": "emits and schemas pass registration.output_contracts; absent emits use "
                       "registration.seed_emits(role)",
        },
        "program_spec": {
            "inherits": "assembly_spec",
            "model_id": PROGRAM_MODEL_ID,
            "code": {"type": "nonblank string", "max_characters": MAX_PROGRAM_CODE_CHARS},
            "timeout_s": {"type": "exact integer", "minimum": 1,
                          "maximum": MAX_PROGRAM_TIMEOUT_S},
            "state_policy": list(PROGRAM_STATE_POLICIES),
            "trigger": "truthy trigger passes runtime.subscriptions.validate_trigger",
            "reward_shapes": "registration.reward_contracts validates emitted-kind reward shapes",
        },
        "program_return": {
            "state": "state removed before return admission; non-null state requires private "
                     "policy",
            "state_encoding": "JSON with sort_keys=true, allow_nan=false, ensure_ascii=false, "
                              "encoded UTF-8",
            "state_max_bytes": MAX_PROGRAM_STATE_BYTES,
            "state_archive": "non-null state requires artifact archive; a refused archive "
                             "write refuses only the state write and keeps previous state; "
                             "other returned fields remain admissible",
            "state_read": "unavailable previous private state fails the invocation before "
                          "execution",
        },
    })
