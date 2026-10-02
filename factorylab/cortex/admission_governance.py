"""Public, author-neutral governance admission contracts (Chapter II §I.b)."""

from __future__ import annotations


def governance_admission_schematics(manifest) -> dict:
    """Return detached admission facts without reading seats, histories or credentials."""
    # Chapter II §I.b: schematics expose the hard cast, not instructions to seats.
    # Local imports keep publication independent of runtime mixin import order.
    from factorylab.charter.amendment import BURN_OBSERVATION, CHANGE_CLASSES, effect_schema
    from factorylab.charter.charter import HOLDOUT_RE
    from factorylab.charter.holdout import _FORBIDDEN_NAMES, BEHAVIOURAL_FACTS, PURE_MODULES
    from factorylab.charter.measurement import (
        _RESOLVED_NAME,
        FORECAST_OBSERVATIONS,
        NOT_A_MEAN,
        RETURN_OBSERVATIONS,
    )
    from factorylab.charter.region import LOWER_RULES, PREVIOUS_MEDIAN, UPPER_RULES, region_schema
    from factorylab.charter.windows import window_schema
    from factorylab.cortex.registration import (
        MAX_CHALLENGE_TRIAL_WINDOWS,
        MAX_EVIDENCE_CHARS,
        MAX_PROPOSALS_PER_RETURN,
        SLUG_PATTERN,
    )
    from factorylab.runtime.governance import MODEL_BASE_MAX_CHARS, MODEL_REASONING_LEVELS
    from factorylab.runtime.observations import OBSERVATION_TIMEOUT_S, SEED_IDS

    return {
        "registration_dispatch": {
            "return_status": "Only status ok applies outputs.register; absent/null is ignored.",
            "shape": "register is a list; each proposal is independently validated and admitted.",
            "max_proposals_per_return": MAX_PROPOSALS_PER_RETURN,
            "overflow": "Items beyond the cap are rejected individually, not silently admitted.",
            "state_checks": "Proposal parsing uses current event kinds, priced models, registered "
                            "assemblies/tools, retired ids, seed observations and reward shapes.",
            "refusals": "Rejected registrations record a reason in the ledger and owner inbox.",
        },
        "trial_and_endowment": {
            "trial_amount_micro": manifest.evaluation.trial_amount_micro,
            "admission": "Every registration uses the shared registry and novelty-trial admission.",
            "proposer_entitlement": "Tools, learners, amendments, challenges, holdouts, retirement "
                                    "and connectors check a known proposer's entitlement against "
                                    "the trial amount. Models, observations, predicates, services, "
                                    "markets and routers use shared trial admission directly.",
            "assembly_endowment": "An assembly endowment is a positive integer (not boolean); "
                                  "omission uses the trial amount. A known founder needs enough "
                                  "free entitlement and cannot endow itself. Explicit endowments "
                                  "require a known founder and cannot use the shared pool.",
            "legacy_fallback": "Without a known founder, an omitted endowment may use the pool; "
                               "insufficient pool funds do not undo that admission.",
            "novelty_amount": "The assembly novelty admission uses the fixed trial amount, "
                              "not the founder-selected endowment.",
            "budget_effects": {
                "proposer_to_pool": ["tool", "learner", "amendment", "challenge", "holdout",
                                     "retire", "connector"],
                "founder_to_child": ["assembly", "program"],
                "reserve_only": ["model", "observation", "predicate", "service", "market",
                                 "router"],
                "meaning": "These are compute-entitlement moves, not wallet payments. "
                           "reserve_only consumes a novelty registration receipt without "
                           "an entitlement transfer or separate proposer-entitlement check. "
                           "Unknown proposers have no proposer-to-pool debit. Trial admission "
                           "releases its receipt when registry admission fails.",
            },
        },
        "observation": {
            "jail_required": True,
            "reserved_ids": sorted(SEED_IDS),
            "preflight": "A last closed window is required. Jailed code receives its public "
                         "facts and must return a finite number within the declared inclusive "
                         "unit_range; an error or missing value refuses admission.",
            "timeout_s": OBSERVATION_TIMEOUT_S,
            "versioning": "A population observation id may be re-registered as its next version; "
                          "seed ids cannot be redefined.",
        },
        "predicate": {
            "jail_required": True,
            "preflight": "A last closed window is required; resolve(facts) runs on its public "
                         "facts and must produce a boolean without an execution error. Seed "
                         "and kernel predicate ids cannot be redefined.",
            "timeout_s": OBSERVATION_TIMEOUT_S,
            "forecast_lookup": "A forecast names a seed or world predicate that resolves in "
                               "the current predicate book, and its params must satisfy that "
                               "predicate's declared parameters; a registered predicate is "
                               "refused as a forecast and serves charter holdouts; failure is "
                               "scoped to that forecast item.",
        },
        "learner": {
            "target": "assembly_id names a registered assembly with no existing own learner.",
            "actions": "Action labels are unique after canonicalization. Buy/sell labels "
                       "side:coin:size with xs/s/m/l/xl sizes, the coin every part between (a "
                       "HIP-3 coin carries its dex), lowercase side and size and uppercase "
                       "coin. verdict/conformity numeric labels in [0,1] round to one decimal. "
                       "Other labels remain exact.",
        },
        "model": {
            "unique_version": "The full model id is not already priced in this world.",
            "look_ahead": manifest.look_ahead_rule(),
            "non_x402_base_max_chars": MODEL_BASE_MAX_CHARS,
            "non_x402_base": "The base before the first @ is nonempty and contains no whitespace.",
            "reasoning_levels": list(MODEL_REASONING_LEVELS),
            "reasoning_suffix": "A nonempty suffix after @ is one of reasoning_levels.",
            "price_source": "Non-x402 bases already have a price or occur in this world's "
                            "catalogue; x402 ids use the seller-price admission path.",
        },
        "assembly": {
            "program_jail_required": True,
            "model": "A non-program model is priced in this world; completion limits are resolved "
                     "before admission. Event schemas and emitted reward contracts are checked.",
            "live_id": "A live assembly cannot be re-versioned; retirement precedes a new version.",
            "retired_id": "Only its owner may re-version a retired id: the proposer is that id "
                          "or its lineage key matches the current registrant key.",
            "reader_slots": "A missing venue reader slot does not refuse admission; the seat "
                            "waits without venue read tools in the reader-slot queue.",
        },
        "router": {
            "max_routers_per_kind": manifest.tools.max_routers_per_kind,
            "cap_application": "The per-event-kind cap applies to add=true; add=false replaces.",
        },
        "tool": {"jail_required": True},
        "service": {
            "jail_required": True,
            "program_id": "Names a registered population tool, not merely an assembly/program id.",
        },
        "market": {
            "listing": "The coin or pair occurs in the venue instrument listing for its market.",
            "duplicate": "A market already admitted for trading is refused.",
        },
        "policy_prediction": {
            "schema": effect_schema(),
            "target": "Exactly one nonempty card_id or observation remains after null fields are "
                      "removed; direction is increase/decrease and window is a positive integer "
                      "(not boolean). No other non-null fields are accepted.",
            "connector_retirement": "The target is a current card or a challenge in trial, due "
                                    "or balloted status. Its current/frozen replacement "
                                    "measurement must pass measurement preflight.",
        },
        "connector": {
            "max_paid_call_micro": manifest.treasury.max_request_micro,
            "paid_cap": "For pay=x402, max_call_micro cannot exceed max_paid_call_micro.",
            "caller": "The proposer maps to an assembly or a queued caller decision.",
            "preflight": "A bounded fetch of preflight_path at the proposed origin returns "
                         "without an error before committee admission.",
            "timeout_s": manifest.connectors.timeout_s,
            "max_bytes": manifest.connectors.max_bytes,
            "vote": "Eligible seats exclude the proposer; at least committee.quorum remain. "
                    "A strict majority of all drawn seats votes yes before registration.",
        },
        "committee": {
            "quorum": manifest.committee.quorum,
            "seats": manifest.committee.seats,
            "min_settled": manifest.committee.min_settled,
            "eligibility": "Non-retired assemblies qualify by distinct independently requested "
                           "decisions with observed settled consequences. Censored payoff gives "
                           "no evidence. An independent decision is a root, sampled for that "
                           "assembly, with learner_id equal to actor, and not a policy decision.",
            "internal_vote": "Connector and retirement committees refuse below quorum, draw "
                             "stratified by role and learner type, and pass at floor(seats/2)+1 "
                             "yes votes. Only an ok return carrying a strict boolean is a ballot; "
                             "missing/malformed ballots do not count as yes.",
            "charter_vote": "Standing committees exclude each motion's proposer; insufficient "
                            "eligible seats or motion voters defer rather than waive quorum. "
                            "A strict majority of the motion's voters is required.",
        },
        "retirement": {
            "target": "An available, non-retired assembly version with no voting or passed "
                      "retirement already pending for that same version.",
            "recusal": "Both proposer and target are removed before the quorum check.",
            "activation": "Passed retirements activate at the next window boundary; an already "
                          "retired target or changed version is stale rather than retired again.",
        },
        "challenge": {
            "target": "A current card with no challenge in trial, due or balloted status.",
            "prediction": "An explicit predicted_effect names the challenged card.",
            "replacement": "Keeps the incumbent id, norm and holdouts; differs from the incumbent; "
                           "has a registered accountability scope, finite usable region and "
                           "successful measurement preflight; resulting live cards have no "
                           "overlapping observation bindings.",
            "trial": "Frozen incumbent/replacement cards and observation definitions are measured "
                     "for the declared closed windows. A due trial enters the next charter "
                     "ballot only if ordinary amendment admission still succeeds; dormancy waits.",
        },
        "amendment": {
            "id_pattern": SLUG_PATTERN,
            "change_classes": list(CHANGE_CLASSES),
            "one_class": "One motion carries cards, lambda or clock, not multiple classes. "
                         "A holdout is its own cards motion and cannot include any add, replace, "
                         "remove, lambda or tick_interval keys, even empty ones.",
            "cards_shape": "add/replace are lists of card objects; remove is a list of strings. "
                           "Each card id occurs once across all three operations. A card contains "
                           "no lambda. A cards motion only keeps or drops existing holdouts; "
                           "it cannot append a predicate.",
            "state": "The amendment id has not been proposed; edition_base is the current "
                     "edition. Added ids are new, removed/replaced ids exist, norms are "
                     "read-only and referenced norms exist. Lambda keys are current cards. "
                     "A card prediction names a current or resulting card, including a removed "
                     "current card. The resulting cards, clock or prices actually change.",
            "lambda": "A nonempty mapping from current card ids to finite nonnegative numbers "
                      "(not booleans). The value posted resolves to an existing posted lambda "
                      "and freezes it at proposal time; absence of a post refuses it.",
            "clock": {
                "min_tick_ns": manifest.clock.min_tick_ns,
                "max_tick_ns": manifest.max_tick_ns,
                "duration": "A duration string with decimal nonnegative digits followed by "
                            "ns, s, m, h or d; it represents exact integer nanoseconds inside "
                            "the inclusive bounds (null maximum means unbounded).",
                "prediction_observations": [BURN_OBSERVATION, "any registered observation"],
            },
            "activation": "A passed frozen patch is rechecked against the current edition. "
                          "Conflicts, an unchanged patch, or changed observation versions behind "
                          "its cards can refuse activation. A holdout appends to the then-current "
                          "card, refusing a missing card or already-held predicate.",
        },
        "card": {
            "nonempty_text": ["id", "norm", "description", "units", "observation"],
            "scope": "answers_for names a registered emitted kind or a supported role alias/all.",
            "window_schema": window_schema(),
            "window_numbers": "n is a positive integer, not boolean. Interval level and "
                              "half_width are finite non-boolean numbers; 0<level<1 and "
                              "half_width>0. A null interval is also accepted by MetricWindow.",
            "region_schema": region_schema(),
            "region_bounds": {
                "lo_rules": list(LOWER_RULES), "hi_rules": list(UPPER_RULES),
                "both": "between requires lo < hi", "neither": PREVIOUS_MEDIAN,
                "numbers": "Required bounds are finite numbers, not booleans; an unused bound "
                           "is absent or null. Other fields are refused.",
            },
            "region_text": "Historical region sentences are parsed to the same rules; an "
                           "unreadable sentence fails admission. If region and acceptable_region "
                           "are both supplied they describe the same region.",
            "bindings": "No two live cards bind the same stripped, case-insensitive observation "
                        "to equal answers_for scopes or to overlapping scope all.",
            "holdout_pattern": HOLDOUT_RE.pattern,
            "holdout_unique": "Each predicate id occurs once, regardless of version.",
            "measurement": {
                "observation": "The observation exists; region conversion and a one-unit "
                               "synthetic measurement using the real pricing path produce a value.",
                "returns": sorted(RETURN_OBSERVATIONS),
                "forecasts": sorted(FORECAST_OBSERVATIONS),
                "delivered_verdicts": dict(_RESOLVED_NAME),
                "delivered_verdict_scope": "Keys of delivered_verdicts require windows with "
                                           "per=null; resolved counterparts serve other scopes.",
                "scoped_windows": "per=role/assembly requires an observation in returns or "
                                  "forecasts above, or a registered population observation.",
                "interval_forbidden": sorted(NOT_A_MEAN),
            },
        },
        "holdout": {
            "exact_fields": ["card_id", "predicate", "evidence", "trial_windows"],
            "proposer": "Its emitted contracts measure as evaluator or antagonist.",
            "target": "A current card; no live challenge/holdout trial for it; no version of "
                      "this predicate already held. Predicate is registered code, not a seed.",
            "max_trial_windows": MAX_CHALLENGE_TRIAL_WINDOWS,
            "trial_windows": "An integer (not boolean) from 1 through max_trial_windows.",
            "max_evidence_chars": MAX_EVIDENCE_CHARS,
            "evidence": "Nonempty after whitespace stripping, within max_evidence_chars.",
            "prediction": "An explicit predicted_effect names this card.",
            "id": "A valid amendment slug absent from challenges and pending amendments.",
            "preflight": "A last closed window exists and the versioned predicate resolves "
                         "on its public facts before admission.",
            "code": {
                "facts": sorted(BEHAVIOURAL_FACTS), "imports": sorted(PURE_MODULES),
                "forbidden_names": sorted(_FORBIDDEN_NAMES),
                "structure": "Code parses and defines a top-level resolve with a positional "
                             "parameter. It reads at least one behavioural fact. Every use of "
                             "that parameter is a subscript or .get with a literal allowed "
                             "string key; iteration, aliasing, passing onward and computed "
                             "keys are refused. Dunder/introspection identifiers, relative or "
                             "star imports, other modules and attribute writes are refused.",
            },
        },
    }
