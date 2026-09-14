# Existing charter adopted by the mixed-provider roster

On 14 September 2026, the five-seat seed committee voted **5 yes, 0 no, 0 invalid ballots** to adopt the existing edition 1 charter unchanged. All 13 cards, their thresholds, windows, observations, prices, and the four norms are identical to the earlier ratified charter. This was whole-charter adoption, not a re-draft or selection of cards.

The committee was drawn using the existing seeded, role-covering sortition: `meta-a`, `seed-observer`, `eval-c`, `eval-d`, and `seed-decider`. Each seat answered through its own configured model on OpenRouter or Venice. The recorded total cost was 2,889 micro-USD ($0.002889).

## Artifacts

- `docs/charter/edition1-compute-continuity-ratified.toml`: unchanged charter, approved for the mixed roster.
- `docs/charter/edition1-compute-continuity-adoption.json`: every request, response, reason, provider request ID, cost, drawn seat, and the approval result.
- `worlds/compute-continuity-testnet.toml`: prepared testnet manifest containing that exact charter and roster, with a fresh client namespace. This is not a funded mainnet manifest.
- `scripts/adopt_charter.py`: verifies the original charter against its source roster, obtains whole-charter ballots from the target roster, and exports approval only after a strict majority. Malformed, truncated or failed responses count as abstentions. The original charter and original approval are untouched.
- `tests/runtime/test_charter_adoption.py`: six regression cases for incomplete ballots, rejection, majority threshold and duplicated seats.
- `docs/audits/v3/compute-continuity.md`: records that its previously pending charter step is now closed.
- `docs/audits/v3/evidence/charter-adoption-2026-09-14/`: preflight, combined charter/exhaustion checks and full gate output.

Charter SHA-256: `9aaa4681cfad14a10c4417316ea12fcbb8b19a53129f27845557336d07afcc81`.

Mixed-roster SHA-256: `3de164c6f93917c0d47dbe5579e67a296b39eb3f8fe2aad15d3184876e2769fb`.

## Verification

The exported charter is structurally identical to the original, and rehearsal preflight confirms the prepared manifest contains the exact approved charter and correct roster. Both network-blocked exhaustion checks also pass with this actual 13-card charter: OpenRouter empty at launch and exhausted mid-run. Each completes 12 ticks through Venice with conservation and ledger verification intact.

Focused adoption gate: `6 passed in 0.87s`. The prepared manifest also passes the all-worlds fidelity/hash test (`1 passed in 1.95s`).

Full gate: `uv run ruff check . && uv run pytest` exited 0. Complete output: `evidence/charter-adoption-2026-09-14/full-gate.log`.

```text
All checks passed!
======================= 2676 passed in 638.83s (0:10:38) =======================
```

## Scope and decisions

The user authorized adoption of the existing charter. I used an all-or-nothing ballot with the existing five-seat committee and strict-majority rule, preserving every card. No ballots were retried, no threshold was relaxed, and no new caps were introduced.

This closes the roster/charter approval gap. No living world was modified, no mainnet trading or transfer was performed, and no service was activated. DigitalOcean deployment and the funded mainnet configuration remain separate from this prepared testnet manifest. No commits were made.
