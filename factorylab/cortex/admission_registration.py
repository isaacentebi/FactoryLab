"""Retrievable registration facts; Chapter II §I.b, without new admission policy."""

from factorylab.cortex import registration as r
from factorylab.world.market import X402_MAX_MODEL_ID_CHARS


def registration_admission_schematics() -> dict:
    """Expose parser bounds and conditional refusals from their enforcement constants."""
    from factorylab.runtime.observations import (
        MAX_OBSERVATION_CODE_CHARS,
        MAX_OBSERVATION_DESCRIPTION_CHARS,
    )

    return {
        "identifiers": {
            "pattern": r.SLUG.pattern,
            "min_characters": r.SLUG_MIN_CHARS,
            "max_characters": r.SLUG_MAX_CHARS,
            "normalisation": "Observation ids are stripped and lowercased before validation. "
            "Assembly, role, tool, connector and service program ids are not normalised. "
            "Amendment id normalisation is stated in governance admission.",
            "matching": "Full match except assembly id, whose parser uses regex match "
            "(the pattern's final $ also matches before a final newline).",
        },
        "register": {
            "type": "list of objects; absent or null means no proposals",
            "considered_positions": r.MAX_PROPOSALS_PER_RETURN,
            "ordering": "Only the first considered_positions list positions are admitted "
            "for validation; every later item is refused, whether or not an earlier item "
            "was accepted. Runtime amendment handling is described separately.",
            "forms": r.proposal_schemas(),
            "unknown_kind": "refused",
        },
        "event_names": "Nonempty strings without whitespace or nonprintable characters. "
        "accepts is a nonempty list; emits is a nonempty list or tuple; duplicate names "
        "are removed preserving order. accepts need not name an already registered kind.",
        "outputs": {
            "schemas": "Object mapping only declared emits kinds to schemas. A custom kind "
            "has an executable object schema. Built-in schemas cannot be replaced. World "
            "and kernel event kinds cannot be population returns.",
            "reserved_case_insensitive_names": sorted(r.RESERVED_SCOPES),
            "built_in_returns": sorted(r.BUILTIN_RETURNS),
            "reward_shapes": list(r.REWARD_SHAPES),
            "reward_mapping": "Only declared emits kinds are keys. Existing and built-in "
            "reward shapes cannot change; an undeclared custom kind defaults to judged.",
        },
        "model": {
            "max_id_characters": r.MAX_MODEL_ID_CHARS,
            "x402_max_id_characters": X402_MAX_MODEL_ID_CHARS,
            "syntax": "openrouter_id is a string containing /, no literal space, "
            "and at most one @, at most max_id_characters long. An id beginning x402: or "
            "venice: is namespaced and skips that syntax: an x402: id is at most "
            "x402_max_id_characters long; each namespace's provider adds its own checks.",
        },
        "assembly": {
            "identity": "id is not already live and is not NOOP. Retired ids can be "
            "reused subject to runtime ownership. role is a full-match slug, default producer.",
            "model": "model_id names a registered model or is program. kind program "
            "allows only omitted model_id or model_id program.",
            "prompt": "Nonempty after strip; raw length bounded; program default is program.",
            "max_prompt_characters": r.MAX_PROMPT_CHARS,
            "min_max_tokens": r.MIN_ASSEMBLY_TOKENS,
            "max_tokens": "null or an exact integer, excluding booleans",
            "effort": list(r.EFFORTS),
            "endowment_micro": "When present, an exact positive integer, excluding booleans.",
            "description": "Text; stripped length bounded; omitted means empty.",
            "max_description_characters": r.MAX_CONTRACT_DESCRIPTION_CHARS,
        },
        "program": {
            "code": "String, nonempty after strip; raw length bounded; a jail is available.",
            "max_code_characters": r.MAX_PROGRAM_CODE_CHARS,
            "timeout_s": [1, r.MAX_PROGRAM_TIMEOUT_S],
            "timeout_type": "exact integer, excluding booleans",
            "state_policy": list(r.STATE_POLICIES),
            "model_seats": "code, timeout_s, state_policy and trigger are refused on model seats.",
            "trigger": "An optional trigger passes the watcher validator.",
        },
        "router": {
            "event_kind": "A known world or population event kind.",
            "learners": list(r.LEARNERS),
            "gamma": "Refused: a learner's exploration is its own schedule.",
            "add": "Boolean, or case-insensitive text true/false; default false.",
        },
        "tool": {
            "id": "A fresh full-match slug; existing tool ids are refused.",
            "description": "String, nonempty after strip, bounded raw length.",
            "max_description_characters": r.MAX_CONTRACT_DESCRIPTION_CHARS,
            "args_schema": "Object with type object and a properties object.",
            "code": "String, bounded raw length (empty string is shape-valid).",
            "max_code_characters": r.MAX_TOOL_CODE_CHARS,
            "timeout_s": [1, r.MAX_TOOL_TIMEOUT_S],
            "timeout_type": "exact integer, excluding booleans",
            "returns_schema": "Optional non-null schema passes the object schema validator.",
            "execution": "A jail is available.",
        },
        "observation": {
            "id": "A normalised full-match slug, not a seed observation id.",
            "description": "String, nonempty after strip; raw length bounded.",
            "max_description_characters": MAX_OBSERVATION_DESCRIPTION_CHARS,
            "unit": "String, nonempty after strip; raw length bounded; stored stripped.",
            "max_unit_characters": r.MAX_UNIT_CHARS,
            "range": "List or tuple of exactly two finite numeric values excluding bool; "
            "both are float-representable and lower is strictly below upper.",
            "code": "String containing observe; bounded raw length; a jail is available.",
            "max_code_characters": MAX_OBSERVATION_CODE_CHARS,
        },
        "retire": "assembly_id names a registered assembly that is not already retired.",
        "learner": {
            "assembly_id": "A registered assembly id.",
            "learners": list(r.LEARNERS),
            "actions_count": [2, r.MAX_DECLARED_ACTIONS],
            "actions": "List of strings, nonempty after strip, raw length bounded; "
            "unique after stripping; stored stripped in order.",
            "max_action_characters": r.MAX_ACTION_ID_CHARS,
            "gamma": "Refused: a learner's exploration is its own schedule.",
        },
        "connector": {
            "fields": "Exactly kind, id, description, origin, with optional preflight_path, "
            "pay, max_call_usd after runtime predicted_effect extraction.",
            "id": "Full-match slug.",
            "description": "String, nonempty after strip; raw length bounded.",
            "max_description_characters": r.MAX_CONTRACT_DESCRIPTION_CHARS,
            "origin_path": "origin passes origin_host; preflight_path passes validate_path, "
            "default /; their network constraints appear in tool admission.",
            "payment": "pay is null/absent or x402. max_call_usd without pay is refused. "
            "x402 requires max_call_usd as string or exact integer, not bool; conversion "
            "to nonnegative integer micro-USD is exact.",
        },
        "market": {
            "fields": "Exactly kind and coin, or kind and pair.",
            "name": "Nonempty printable string without whitespace. pair contains exactly "
            "one / and ends /USDC; perpetual coin contains no /.",
            "max_name_characters": r.MAX_MARKET_NAME_CHARS,
        },
        "service": {
            "fields": "Exactly kind, program_id, price_micro, description.",
            "program_id": "A full-match slug naming a registered population tool.",
            "price_micro": [1, r.MAX_SERVICE_PRICE_MICRO],
            "price_type": "exact integer, excluding bool",
            "description": "String, nonempty after strip, raw length bounded; stored stripped.",
            "max_description_characters": r.MAX_CONTRACT_DESCRIPTION_CHARS,
        },
        "predicate": "Exactly kind, id, description, code after runtime extraction. "
        "The definition passes validate_predicate_definition and a jail is available.",
        "challenge": {
            "fields": "Exactly kind, card_id, evidence, replacement, trial_windows after "
            "runtime predicted_effect extraction.",
            "card_id": "String, nonempty after strip, raw length bounded; stored stripped.",
            "max_card_id_characters": r.MAX_CARD_ID_CHARS,
            "evidence": "String, nonempty after strip, raw length bounded.",
            "max_evidence_characters": r.MAX_EVIDENCE_CHARS,
            "trial_windows": [1, r.MAX_CHALLENGE_TRIAL_WINDOWS],
            "trial_windows_type": "exact integer, excluding bool",
            "replacement": "Object with observation, rule, value, window and optional "
            "description, units, answers_for; no other keys. Present text fields are "
            "strings nonempty after strip with bounded raw length.",
            "max_replacement_text_characters": r.MAX_REPLACEMENT_TEXT_CHARS,
            "rules": list(r.CHALLENGE_RULES),
            "value": "Finite int or float, excluding bool.",
            "window": "Object with kind and n, optionally per, and no other keys; "
            "runtime applies the window contract.",
        },
    }
