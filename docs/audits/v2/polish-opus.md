# Seat 7 — the repository as a product

Everything below is **unclean**: it works and it is faithful, and a production reader
would still object. No Class 3 opinion, no defect hunting; things I tripped over are in
"Passed to seat 4". Ordered by value to a stranger who has read the essay and just
cloned this, not by ease. Nothing applied.

Ran: `ruff check .` (clean), `pytest` (1,685 passed, 19 skipped, 294 s), both scripted
worlds (95 s; `scripted-crash` terminates `balance_zero`), every subcommand's `--help`,
`vulture` at confidence 60 and 80 from a scratch venv, plus import-graph and grep sweeps
for dead symbols, dead config keys and dead event kinds. The stranger's ten-minute path
works today; almost everything below is what the stranger is told while walking it.

---

## 1. The essay is not in the repository — `.gitignore:31-32`

`docs/essay.md` is excluded from Git. The README's first sentence, its governing test,
its principle table and its glossary all rest on a document a cloner does not get, and
nothing tells them where it is. Every seat in this audit was handed a copy out of band.
**Change:** either track the essay, or add one line to the README saying it is the
experimenter's document, how to obtain it, and that `docs/essay.md` is the expected
path. The current silence is the worst of the three options.

## 2. The README is stale in six checkable places

Each of these is contradicted by a file in the same commit. A production reader who
checks one and finds it false stops trusting the rest.

| README | Says | Reality |
|---|---|---|
| :136 | "there is no `versions` subcommand yet" | `cli.py:542` registers `versions`; it appears in `--help` |
| :149 | "Findings go to `docs/audits/`. None exists yet." | Five reports in `docs/audits/`; `docs/audit-brief-v2.md` supersedes the brief cited |
| :88-102 | "What runs 1 to 7 taught us"; "Run 8 is the first fair test" | Run 8 is logged in `build-log.md:387`; it traded |
| :90 | "Seven live testnet runs … under $0.50 of compute"; "Summaries are in `docs/runs/`" | Eight runs; run 8 cost $0.70; `docs/runs/` holds three of eight summaries |
| :151 | "one real x402 purchase … so far only unsigned quotes, $0 spent" | `docs/runs/compute-proof.md`: $5 top-up and three paid completions, 12 September |
| :29, :7, :33 | routes the reader to `build-spec-v0.4` … `v0.7-phase4`, "Spec v0.5 §9 condition 6", "spec v0.4 §1" | The stranger is sent into four phase-named specs to learn the current rules |

**Change:** fix the six, and adopt a rule that the README states only what is true of
this commit; run history belongs in the build log.

## 3. Rewrite the README for the stranger, not for the project

The README is written in the project's own vocabulary and its own chronology. Sections
"What runs 1 to 7 taught us" (:88-102) and "Cold audit and launch checklist" (:147-151)
are a lab notebook; "Location" (:176-178) prints the author's laptop path. Proposed
outline, in order:

1. **What this is** — three sentences: a Class 3 factory from *The Superdark Factory*,
   built as one small world with one wallet; ephemeral LLM assemblies buy their own
   thinking, trade, judge each other and amend their own charter; the experimenter
   makes one committed move and then never intervenes.
2. **What it needs** — Python ≥3.12 (reconcile with `.python-version`'s `3.13` and
   `deploy/cloud-init.yaml`'s `uv python install 3.13`), `uv`, nothing else for the
   scripted worlds.
3. **Run it in ten minutes** — `uv sync`, then the two scripted worlds *first*; the
   five-minute `pytest` gate after, not before.
4. **What the files are** — one line per top-level directory and per `factorylab/`
   package. This does not exist anywhere today.
5. **Never do this** — never read, print or commit a `*.key`; never create
   `worlds/funded.toml` without the launch gates; never touch a running world (refill,
   re-manifest, restore, upgrade, read the diary). Today this is scattered across
   :86, :120, :145 and `deploy/README.md`.
6. **Where the essay is** (item 1), **how it maps** (keep :11-27, the best thing in the
   file), **the physics** (keep :31-46), **glossary** (keep).

Cut: the second half of :7 (say the criterion, not its spec coordinate); all of :88-102
→ build log; :149 and :151 → `handoff.md`; :176-178 entirely; :29's spec list → a
one-line pointer.

## 4. An operator at 3 a.m. gets nothing

There is no `logging` anywhere in `factorylab/` (zero hits). `deploy/factorylab.service`
sets `StandardOutput=null` and `StandardError=null`, and `deploy/README.md:220` forbids
verbose logging after launch. Fifty-five `except Exception` sites swallow the cause, and
`cli.py` collapses every failure into one of five constant strings (:378, :574, :586,
:603, :616). The webhook then sends `{"world":"funded","event":"failed_resume"}` and
that is the operator's entire evidence. The reason for the silence is right — secrets
must not reach a log — but silence is not the only way to get it.

**Change:** give every failure path a stable machine-readable reason code
(`ledger_integrity`, `manifest_mismatch`, `credential_missing`, `venue_unreachable`, …)
chosen from a closed enum, print `factorylab resume: recovery unavailable
(reason=manifest_mismatch)`, carry the same code in the webhook body, and write it to a
0600 file under `RuntimeDirectory`. No exception text, no message interpolation, so no
secret can escape a fixed vocabulary.

## 5. Exit codes are a contract nobody publishes completely

`cli.py:17-18` defines `TERMINATED_EXIT = 3` and `LEDGER_BUSY_EXIT = 4`; `2` is returned
from seven places. `deploy/README.md:198-206` documents only 0, 1 and 3 — a supervisor
following that table treats a busy-ledger 4 and a key-mode 2 as unclassified.
**Change:** put the full table in the table, and surface it in `factorylab --help`.

## 6. `worlds/edition1-example.toml` declares `name = "testnet"`

Line 3. It is `worlds/testnet.toml` verbatim for 182 lines plus a `[charter]` block, so
two manifests with different hashes claim one world identity. `load_manifest`
(`runtime/worlds.py:506`) never checks that the file basename matches `name`, and the
name is what the summary (`loop.py:3628`), the ledger genesis, the wake page and the
mainnet guard (`worlds.py:314`) all key off. The essay makes this the whole first move —
"the installation of identity in a space of potential" (I.IV). An example manifest that
lies about its identity is the wrong thing to leave next to the one manifest that must
not.

**Change:** name it `edition1-example`, and have `manifest_from_dict` refuse a manifest
whose declared name differs from its file stem when loaded by name. Also drop the
duplicate card: `model_cost_efficiency` and `cost_per_return` are the same norm and the
same `observation = "cost_per_return"` with different thresholds.

## 7. The manifest schema is undocumented, and the manifest is the Stackelberg move

`runtime/worlds.py` parses about sixty keys. Sixteen appear in no manifest in the
repository: the entire `[treasury]` block (eleven keys, `worlds.py:428-437`),
`consequence_backstop_events`, `tools.max_routers_per_kind`, `termination.max_events`,
`clock.min_tick`, `[drip]`. Their names and defaults exist only in
`worlds.py` and in four phase-named specs. `worlds/funded.toml` — irreversible, written
once — has no reference to be written against.
**Change:** one `worlds/README.md` (or `docs/manifest.md`) listing every key, its type,
its default, whether it is a hard cast, and which ones must be set before launch.

## 8. `runtime/loop.py` is 3,809 lines and one class

`Runtime` spans :527-3665 with a 341-line `__init__` (:530-870, 68 attributes) and about
a hundred methods. It is the file a stranger must read to understand the world, and it
cannot be read. Seams, all of which are already clean method groups:

| New module | Moves | Lines |
|---|---|---|
| `world/scripted.py` | `ScriptedProvider`, `_description_from_prompt`, `_inputs_from_prompt` (:135-336) | ~200 |
| `cortex/schematics.py` | `PROPOSAL_SHAPES`, `A_RETURN_MAY_INCLUDE`, `_world_block`, `_register_schema`, `_forecast_schema`, `_scoring_block` | ~250 |
| `runtime/routing.py` | `RouterState`, `_KeyedLearner`, `_universe_for`, `_make_learner`, `_build_router`, `_retain_router`, `_fresh_router_id`, `_route`, `_route_with`, `_propensity`, `_mix_with_standing` | ~320 |
| `runtime/governance.py` | `_apply_registrations` … `_activate_charter_if_due` (:2896-3336) | ~440 |
| `runtime/venue.py` | `_venue_write`, `_recover_order`, `_record_order_result`, `_reconcile_orders`, `_order_exclusion`, `_order_leverage`, `_settle_exchange_effects`, `_observe_positions` | ~250 |
| `runtime/pricing.py` | `MeasureWindow`, `_derive_regions`, `_close_price_window`, `_penalty_for`, `_settle_priced` | ~180 |
| `runtime/feedback.py` | `_settle_due_forecasts`, `_censor_stale_judgements`, `_deliver_returns`, `_settle_exposures`, `_deliver_consequence_to_memory`, `_standing_for`, `_facts_for` | ~200 |
| `runtime/summary.py` | `RunStats`, `_summary`, `_price_str`, `_equity_or_none`, `_as_unit`, `_duration_str`, `_model_contract`, `_assembly_contract` | ~240 |

What is left is the loop proper: `run`, `_run`, `_process_event`, `_next_event`,
`_emit`, `_snapshot`, `_resume_at`, `_check_termination` and the three step methods —
about 900 lines, and readable. `cortex/schematics.py` is the one I would do first: the
world block is the population's entire sensory surface, the essay's "informational
schematics of the factory should be absolutely public" (II.I.b), and it is currently a
70-line dict literal buried at :1105 of the largest file.

## 9. One concept, two implementations: `violation`

`charter/controller.py:176-187` and `versioning/versions.py:63-70` compute the same
normalised distance; the second's docstring admits it ("match PriceController's
normalized distance"). Worse, `runtime/immune.py:10` imports the *versioning* copy while
the live controller prices with the *charter* copy, so the immune organ and the price
controller reach their verdicts through two code paths in one process. A change to one
silently desynchronises the factory's self-diagnosis from its own prices.
**Change:** move the formula to `charter/controller.py` as a free function over
`CardRegion`, have `versioning` call it with a region built from its dict.

## 10. One concept, six implementations: USD → micro-USD

The README promises "Money is integer micro-USD… decimals appear only at venue
boundaries". `kernel/money.py:30 usd_to_money` is the contract: exact, refuses fractional
micros. Around it:

| Site | Rounding | Raises |
|---|---|---|
| `runtime/worlds.py:33 usd_to_micro` | exact, refuses | `ValueError` |
| `runtime/loop.py:511 _usd_to_micro` | `ROUND_HALF_EVEN` then exact | — |
| `world/x402.py:84 usd_micro` | truncate, or up by flag | `X402Error` |
| `world/openrouter.py:140 / :177` | `ROUND_CEILING` / `ROUND_FLOOR` | `OpenRouterError` |
| `world/treasury_rails.py` (8 sites), `world/treasury.py:479`, `runtime/live.py:202` | `int(Decimal * 1_000_000)`, truncating, unvalidated | — |

`usd_to_micro` and `_usd_to_micro` differ by one underscore, live in the same package and
behave differently. **Change:** one `kernel/money.py` API with an explicit rounding
argument (`exact | down | up`), and delete the other five; `world.*` may import
`kernel.money` (`world` already imports `kernel` once).

## 11. Boundaries reached across

| Site | Reaches into |
|---|---|
| `runtime/wake.py:48,61-62` | `self._Ledger__head`, `self._Ledger__keys._decrypt` — kernel name-mangled privates |
| `runtime/wake.py:98-100` | `exchange._guarded`, `exchange._info`, `exchange._address` |
| `runtime/immune.py:28` | `self._PriceController__decay` across packages |
| `runtime/loop.py:918` | `cortex.assembly._positive_wire_decimal`, `_validate_schema` |
| `runtime/cli.py:263,268` | `runtime.worlds._ns` |
| `settlement/forecast.py:6`, `runtime/resume.py:24`, `runtime/wake.py:17` | `kernel.ledger._canonical` in three packages |
| `scripts/compute_proof.py` | `factorylab.runtime.cli._load_dotenv` |

Each of these is a missing public method. The two that matter: `Ledger` needs a
read-only open/iterate API (`deploy/README.md:236` already documents the workaround as a
known coupling), and `_canonical` should be public — it is the canonicalisation three
packages depend on for hashing.

## 12. Names that record history

| Where | Name | Note |
|---|---|---|
| `tests/runtime/test_fa_defects.py`, `tests/{runtime,cortex,world}/test_fc_*.py` (11 files) | `fa`, `fc` | fix passes FA/FC from `build-log.md:393`. Eight modules import `make_runtime` from `test_fa_defects`, including tests in `cortex/` and `world/` |
| `factorylab/charter/charter.py:3,43,85` | "Phase 2 keeps the charter immutable"; ":25 informational in this phase" | Both false now: amendments exist, observations are live |
| `factorylab/kernel/termination.py:25` | `"phase 1 requires balance_zero, explicit_kill and ledger_failure"` | An operator-visible error message naming a build phase |
| `factorylab/runtime/loop.py:3,19,105,832,1022`, `cli.py:394`, `cards.py:3`, `worlds.py:115`, `live.py:3` | "Spec v0.4 section 4.11", "spec v0.6 section 8.1", "phase 3 packages", "spec v0.7 §1" | Say the rule, cite nothing |
| `factorylab/settlement/REWARD_HACKING.md:1` | "Verdict consequence review (phase 4 Q)" | A review document shipped inside the wheel |
| `factorylab/world/events.py:16` | "Mirrors spec section 4.5" | |
| `deploy/README.md:31,34` | "this FC pass uses the documentation fallback", "The FC synthetic reader probe" | Fix-pass vocabulary in an operations runbook |
| `deploy/start.sh:12` | "run's legacy exit code is 0" | |
| `docs/runs/testnet-{fifth,seventh,eighth}.summary.json` | run ordinals | `testnet-05`, `-07`, `-08` sorts and matches the log |
| `tests/test_scaffold.py` | asserts `import factorylab` | Phase-1 leftover |
| CLI `treasury-testnet` | named after a network | `treasury` with `--network`, or fold into `treasury` |

**Change for the tests:** rename by subject (`test_order_recovery.py`,
`test_child_requests.py`, `test_governance.py`, `test_validation.py`,
`test_exchange_partial_fills.py`, `test_request_cap.py`, …) and move `make_runtime` to a
`tests/conftest.py` fixture. Nothing outside the build log should remember FA and FC.

## 13. Dead things

| File:line | Dead | Evidence |
|---|---|---|
| `kernel/events.py:28-29` | `WALLET_CHANGED`, `RESERVE_WINDOW_OPENED` | Event kinds never emitted, consumed or tested — the only two in the enum with no producer |
| `learners/reference_games.py` (184 lines) | whole module | Imported only by `tests/learners/`; ships in the wheel |
| `runtime/loop.py:3779` | `run_world(horizon_events=10)` | Accepted, never forwarded to `Runtime`; a caller setting it is silently ignored |
| `runtime/loop.py:105-116` | two `try: import … except ImportError: X = None` guards | `venue_tools`, `amendment`, `book` all exist; comment says "hard imports once every workstream is merged" |
| `runtime/cli.py:254-258` | `except ImportError: "the runtime loop is not built yet"` | Phase-1 branch |
| `cortex/assembly.py:42` | `AssemblySpec.tool_ids` | Never read; tools come from `_allowed_tools`. A dead field on the primitive's public contract |
| `cortex/sandbox.py:36,183` | `SandboxResult.cpu_limited` | Computed on every run, read nowhere |
| `kernel/wallet.py:39` | `Reservation.window_start_ns` | Never set, never read |
| `world/metering.py:55` | `cost_source: Literal["reported","table"]` | `market.py:430` passes `"x402-quote"`; the annotation is false |
| `scripts/compute_proof.py`, `scripts/draft_edition1.py` | spent one-shots | Both self-describe as one-off; both have produced their artefacts (`docs/runs/compute-proof.md`, `docs/charter/edition1-draft.md`) and refuse or should not re-run. Move to `scripts/history/` with a README line, or delete and keep the evidence |
| `runtime/worlds.py:428-437` etc. | 16 config keys set in no manifest | See item 7 |
| `kernel/money.py:44`, `kernel/registry.py:166`, `kernel/queue.py:189`, `kernel/timing.py:43`, `charter/book.py:35`, `learners/hedge.py:51`, `kernel/events.py:77` | `per_token_price`, `purchasables`, `register_successor`, `estimated_period`, `editions`, `last_support`, `Bus.subscribe` | Exercised only by their own unit tests; no runtime caller. Keep deliberately or drop, but say which |

Nothing in `deploy/` is dead; `factorylab-static.service` and the wake timer are both
reachable from the runbook. `ruff` is clean and `vulture` at confidence 80 finds only
`horizon_events` and three `urllib` handler signatures.

## 14. Docs: which are live, which are history

| File | Verdict |
|---|---|
| `README.md`, `deploy/README.md`, `docs/charter/edition1-draft.md`, `docs/research/*` | Live. Keep. |
| `docs/handoff.md` | Stale and internally inconsistent: dated 11 September, states 12 September (:15); omits `build-spec-v0.7`; :49 lists "a cold re-audit" as next work that is happening now; :55 is a paragraph explicitly labelled "Previously listed"; :57 names a dead branch `p3b-runtime`. **Change:** rewrite as "state on main + what remains before launch", ten lines, no archaeology. |
| `docs/design-audit-v2.md` | README:29 sells it as "Status per principle"; its header says "phases 1–3 merged, phase 3b on a branch, runs 1–4", every workstream in §7 is done, and §9 says "Population changes to the world clock … deliberately not built" while README:67 and the code say an amendment may change the tick. **Change:** move to `docs/history/`, or refresh §1-§6 as the status table and delete §7-§8. |
| `docs/build-spec-v0.4 … v0.7-phase4.md` | History with binding force. Either fold into one `docs/spec.md` reflecting main, or move to `docs/history/specs/` and stop routing the README through them. |
| `docs/audit-brief.md` | Superseded by `audit-brief-v2.md`. Move to `docs/history/`. |
| `docs/build-log.md` | Live as the record. Keep. |
| `docs/runs/venice-proof.md` | A runbook ("Run this before a world launches") in a directory of run records. Move to `deploy/` or `docs/`. |
| `outputs/` (7 files, ~135 KB, tracked) | Superseded design history at the repository root, referenced only by `AGENTS.md:4`, under a directory name that tells a stranger nothing. Move to `docs/history/`. |
| `factorylab/settlement/REWARD_HACKING.md` | The only Markdown inside the package; ships in the wheel. Move to `docs/`. |

Stale comments in code beyond those in item 12: `charter/charter.py:25` ("informational
in this phase"), `loop.py:1144` `getattr(self, "amendment_feedback", None)` (defensive
`getattr` on the object's own attribute), `world/events.py:3-5` ("The world package does
not import the kernel" — true, but `runtime/worlds.py` and `world/exchange.py` both
import `kernel`, so the invariant as stated is narrower than it reads).

## 15. `factorylab --help` tells a stranger nothing

`build_parser()` (`cli.py:453`) sets no `description` and no `epilog`, so `--help` is a
bare list of eleven verbs. `run --help` gives no help for `--world`, `--events` or
`--seed`, and does not say that `--world` names a file in `worlds/`. `reserve topup`
requires `--usd` and then accepts only the literal `5` (`cli.py:211-218`) — a required
argument with one legal value is theatre; make it `--confirm-5-usd` or drop it.
Sub-subcommands under `market`, `reserve` and `treasury-testnet` carry no `help=`.
**Change:** a two-sentence `description`, per-argument help, an epilog naming the exit
codes and the two things never to do (keys, `funded`).

## 16. Six package `__init__.py` files say `"""Factory Lab."""`

`factorylab/`, `kernel/`, `runtime/`, `charter/`, `learners/`, `cortex/`, `world/` — the
same seven-word placeholder. A stranger opening `kernel/__init__.py` to find out what
the kernel is learns nothing, and the import rule that actually holds ("`factorylab.kernel`
imports nothing from `cortex`, `world`, or `runtime`") lives only in `AGENTS.md:15`.
**Change:** one paragraph per package: what it owns, what it may import, what it may
never import. `versioning/__init__.py` is the opposite problem — 100 lines of logic in an
`__init__`; move `summary`/`render` to `versioning/report.py`.

## 17. Two default thresholds for one concept

`versioning/__init__.py:19-20` defaults `tv_threshold=0.5, gap_threshold=0.5`; every
manifest sets `immune.tv_threshold = 0.2, gap_threshold = 0.8`. `cli.py:398` calls
`summary()` with no arguments, so the post-mortem that tells the experimenter which
pathology the world died of uses different thresholds from the immune organ that was
supposed to correct it. **Change:** have `versions` read the world's manifest (it already
takes a ledger whose genesis names it) or accept `--world`, and delete the second set of
defaults.

## 18. Duplicated provider parsing

`world/openrouter.py:128-155` and `world/venice.py:170-195` are near-identical
OpenAI-shaped response parsers (choices → message → list-of-parts flattening → usage →
`reasoning_tokens` → `request_id`), differing only in cost conversion and the exception
raised — Venice calls `x402.usd_micro`, so a Venice pricing fault surfaces as
`X402Error`. **Change:** one `world/openai_wire.py` parser, cost conversion from
`kernel.money` (item 10), a provider-supplied error type.

## 19. Test organisation

1,685 tests across nine directories mirroring the packages — good. Gaps: no root
`tests/conftest.py`, so the shared `make_runtime` factory lives in
`tests/runtime/test_fa_defects.py` and is imported by eight modules across three
directories; no `tests/audit/` holding the reproductions from either audit round, so a
fixed finding has no home and can silently regress; `tests/test_scaffold.py` asserts
only that the package imports. `AGENTS.md:17-18` requires a violation test per kernel
invariant, but nothing names the invariants, so the claim cannot be checked by reading.
**Change:** root conftest; `tests/audit/`, one test per accepted finding, named for the
finding; delete the scaffold test; rename the FA/FC files (item 12).

## 20. Small, cheap, worth doing

- `README.md:110` puts the five-minute `pytest` gate before the 95-second scripted run;
  swap them so the ten-minute promise holds.
- `README.md:116` uses `--events 400`; `handoff.md:34` and the brief use 500.
- `.python-version` says `3.13`; `pyproject.toml:5` says `>=3.12`; `README.md:106` says
  "3.12 or later"; `cloud-init.yaml` installs 3.13. Say one thing.
- `cli.py:411` `open(args.key, "rb").read()` with no context manager, in the one command
  that touches a released seal key.
- `AGENTS.md:4` names `docs/build-spec-v0.4.md` and `outputs/project-plan.md` v0.3 as
  the sources of truth; both are three phases old.

---

## Passed to seat 4

- `load_manifest` (`runtime/worlds.py:506-512`) accepts an arbitrary path, and the
  mainnet guard (`:314`) tests the manifest's *declared* `name`, so any TOML anywhere
  declaring `name = "funded"` and `mainnet = true` runs mainnet.
- `run_world`'s `horizon_events` parameter (`loop.py:3779`) is accepted and discarded.
- `world/metering.py:55` declares `cost_source: Literal["reported","table"]`;
  `world/market.py:430` passes `"x402-quote"`.

## Open

The essay does not say how a factory's repository should be documented, named or laid
out — items 2, 3, 14, 15, 16, 19 and 20 are craft, not fidelity, and I have not dressed
them in quotations. Where a passage bears I cited it: identity in item 6 (I.IV), public
schematics in item 8 (II.I.b), contracts and semantic coupling in items 9-11 (II.I).
The essay is also silent on whether the architect's build history should survive in the
artefact at all; I assumed it should survive in `docs/` and nowhere else.

*(2,990 words.)*
