# Cold review of PRs #58–#64

Reviewed main at `fadf527`, using each merge's first-parent diff and stat, the supplied PR bodies, README, AGENTS, the build spec, current manifest reference, and the claimed triage rows and their source reports. Later fixes on main are credited; superseded defects are not reported. Findings below are verified source-path analyses, not newly executed reproductions: only existing tests were run, and only this report was written.

## PR #58 — new kinds of work (`bd5da7c18`)

Standard: T21, seat 1 finding 6, decision 5, and the existing requirement that higher evaluation tiers remain buffered and progressively slower.

### P1 — Custom conformity bypasses the evaluation cascade

**Location:** `factorylab/runtime/routing.py:497–503`; `factorylab/runtime/loop.py:950–956,1066–1079`.

Routing recognizes a custom `conformity` shape for delivering its score, but the immediately following cascade condition still admits only the literal seed `Verdict` and `MetaVerdict` events. Custom judgments therefore route upward individually, without the minimum release ratio, jitter, or representative-window attribution.

**Failure:** register three distinct conformity kinds in a checking chain, starting from a custom forecast. A single lower-tier judgment can immediately drive a paid invocation at each successive tier at the same world timestamp. Unlike the seed chain, each tier needs no additional lower-tier arrivals. Ancestry exclusion prevents cycling back to an earlier author, but does not enforce buffering between distinct authors. The fixed time separation is therefore bypassed by registering new names. The existing custom-conformity test calls the direct routing helper and verifies one checker; it never exercises this cascade condition.

**Fix:** apply cascade admission by reward shape, extend the cascade's tier/score/handle extraction to custom contracts, and preserve compatible grouping and original-handle attribution. Test an actual `_route` chain of custom checkers: fewer than the release threshold must not invoke the next tier, including after resume.

### P2 — A length cursor loses post-forecast samples when a bounded series rolls

**Location:** `factorylab/runtime/observations.py:355–371`; `factorylab/runtime/feedback.py:258–266`.

The cursor stores list lengths, while public series retain only their latest 1,024 samples. Resolution slices the current list at its old length, without accounting for evicted prefixes.

**Failure:** seal a predicate after a same-window BTC series reaches 1,024 samples. Append a new qualifying BTC observation, evicting the oldest. The current series still has length 1,024, so `[1024:]` supplies no evidence. A predicate testing whether a qualifying observation arrived returns false and receives a scored outcome even though it did arrive. Smaller cursors also skip valid new samples after enough eviction. Existing cursor tests cover append-only short lists.

**Fix:** persist monotonic sample positions and retained-prefix offsets, or retain bounded evidence specifically for outstanding forecasts. Return unavailable evidence, rather than a scored false, if the required interval has been discarded. Cover saturation, rollover and resume.

### P2 — Custom kind names can silently select another accountability scope

**Location:** `factorylab/charter/charter.py:30–42`; `factorylab/cortex/registration.py:48–68`.

Registration accepts case-sensitive custom names such as `Producer` or `ALL`, but `MetricCard` lowercases any name matching a role alias or `all`. Measurement retains the custom kind's original spelling.

**Failure:** register `Producer` and replace the existing cost card with one specifying `answers_for: "Producer"`. The scope becomes `producer`, selecting seed producer rows instead of the new kind's rows. `ALL` becomes a population-wide scope. Admission/preflight validate the normalized alias, so neither catches the mismatch. This contradicts T21's independent emitted-kind accountability.

**Fix:** reserve alias spellings case-insensitively when admitting custom kinds, or represent role aliases and emitted-kind scopes distinctly. Test that an admitted kind's card cannot silently measure a different population.

## PR #59 — wave-one leftovers (`5dccfc57e`)

**No findings.** Checked T20/T51/T52 and the metadata, mainnet, authority, subject-fallback and timeout handoffs against the cited seat findings. Measured gaps are restored and reconstructed during replay without double-counting normal delivery; the declared interval remains the conversion floor. Wallet exhaustion survives a later recovery in the same settlement batch. Existing forbidden targets remain refused rather than falling back. Anonymous rejected orders no longer consume an explicit idempotency identity.

Two launch-floor tests could not reach their assertions because this environment refuses the jail; that is a verification limitation, not a finding against the floor implementation.

## PR #60 — world access (`433e0edda`)

Standard: T10/T22, seat 1 findings 4/7 and perimeter ruling, decision 6, plus the README/build-spec requirement that a return's consequence includes its own compute and tool costs.

### P1 — Recurring note rent is debited but excluded from the writer's consequence

**Location:** `factorylab/runtime/notes.py:80–96`; compare `factorylab/runtime/pricing.py:116–136` and `factorylab/settlement/lots.py:107–113,273–296`.

`charge_window` directly meters rent against the original writer handle, then updates only the note's paid window. It does not add the charge to a consequence account or a priced cost contribution. Invocation costs are recorded separately by `_invoke`; `LotTable.finish` fixes the account's cost once, and payoff resolution later compares proceeds with that fixed amount.

**Failure:** a trading return also writes a note. Its position stays open across a reserve-window boundary and eventually earns ten micro-USD more than its original invocation/tool cost. Fifteen micro-USD of rent is debited before settlement, but the outcome still resolves `return_paid_off = 1`, despite the attributable loss. Subsequent rent can also continue after the writer's outcome is final, with no corresponding scored liability. Wallet conservation remains true, so the existing rent/balance tests miss the erroneous external reward signal.

Rent itself was chosen through `note.put`; the defect is missing cost accountability, not the existence of an automatic recurring charge.

**Fix:** give retained storage an explicit, resumable liability whose charges enter consequence costs and charter cost measurements before its outcome is finalized. Define how renewal charges remain attributable after the original return settles. Add a profitable-before-rent, loss-after-rent case across a real reserve boundary and checkpoint.

The initial whole-venue tick broadcast was repaired by #64 and is not counted again here. Paid-data cap, provisional-debit and no-resubmission tests passed; no live x402 payment was attempted.

## PR #61 — unread verdicts and gate repairs (`2b6479772`)

**No findings.** Checked the T16/seat 2 endorsement-accountability mechanism, identity pricing, scripted registration schedule, connector-test promises and T30's known liquidation overshoot. Unread commitments are explicitly closed without changing normative standing or the base rate; readable blame still scores. The new closed flag is carried by the existing dataclass checkpoint codec. Pricing and execution render the same executor identity.

The changed crash assertion still requires terminal death, a released seal, a material negative balance, conservation and ledger verification. Removing its exact trajectory-dependent balance is not weakening the claimed overshoot invariant.

## PR #62 — three gate interactions (`3955daf4f`)

**No findings.** Population parsing still rejects reward-shape declarations for non-emitted kinds before `AssemblyProposal` resolves inherited fields. Seed tool examples are now applied after notes join the tool catalogue. The no-listing adapter branch avoids inventing exchange attributes; restoration rebuilds schemas and reapplies registered markets. The relevant parser and schema-restoration tests passed.

## PR #63 — performance (`d38843f6e`)

**No findings.** Checked immutable return reuse, channel remapping, canonicalization fast paths, the containers mutated by ledger indexing, durable append handling and periodic integrity verification. The memoized walk retains the documented item-count cadence, while the tail check still runs. Existing corruption tests and both scan-equivalence tests passed. This review does not independently reproduce the PR's timing or byte-identical whole-world benchmark.

## PR #64 — rehearsal repairs (`3e8b26157`)

**No findings.** Checked T53–T56 against `rehearsal.md`. The broadcast callback follows restored trading permissions; filtering does not suppress fills or settled funding. Refusals and `fill.counted` are replayed through the ordinary event execution/journal path. The adjusted order-cut test keeps its assertions and allows the extra tick required by the smaller broadcast. Descriptor checks passed. The documented macOS orphan limitation is not represented as a solved parent-death guarantee.

## Verification

Only the permitted Ruff command and two pytest commands naming explicit files/node ids were run. Coverage included J contracts/runtime/predicates, K paid data/notes and selected market restoration cases, L floor/cadence/authority/subjects, five merge-regression nodes, ledger integrity, both return-scan nodes, and the four rehearsal-fix files. No full-suite gate, new reproduction script, credentials or live-provider run.

Exact result lines:

```text
All checks passed!
========================= 2 failed, 88 passed in 4.16s =========================
=================== 1 failed, 89 passed, 2 skipped in 0.50s ====================
```

All three failures concern the unavailable jail: the two launch-floor tests and `test_predicate_runner_jail_returns_true_and_false`. Their common error is `sandbox-exec: sandbox_apply: Operation not permitted`. The two skipped tests require a living jail. These results establish 177 passing targeted cases, not a passing launch gate or dynamic reproductions of the new findings.

## Every P1 across the seven PRs

- **#58:** Custom conformity kinds bypass the buffered, jittered evaluation cascade (`factorylab/runtime/routing.py:497–503`).
- **#60:** Recurring note rent leaves the wallet but never enters the writer's consequence cost or charter cost contribution (`factorylab/runtime/notes.py:80–96`).
