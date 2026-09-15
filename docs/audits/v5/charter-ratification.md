# Edition 2 charter ratified by the seeded committee

On 15 September 2026 the five-seat seed committee of the edition 2 testnet roster
(`worlds/edition2-testnet.toml`, the mixed OpenRouter/Venice roster) balloted on the
architect's eight-card draft (`docs/charter/edition2-draft.toml`, stamped as
`docs/charter/edition2-candidate.toml` with the roster and charter digests). The committee
was drawn by the seeded, role-covering sortition: `meta-a`, `seed-observer`, `eval-c`,
`eval-d`, `seed-decider`, each answering through its own configured model.

| Seat | Model | Selected |
|---|---|---|
| meta-a | venice:deepseek-v4-1-flash | all eight |
| seed-observer | z-ai/glm-5.3-flash | all eight |
| eval-c | venice:deepseek-v4-1-flash | all eight |
| eval-d | openai/gpt-5.6-luna | all eight |
| seed-decider | venice:deepseek-v4-1-flash | five: well-formed floor, cost cap, censorship bound, forecast skill, consequence paid off |

Every card received a strict majority (at least four of five), so the ratified charter is the
full draft: four norms, eight cards, no quota cards. No card conflicts were found in
preflight. Total recorded cost: 3,257 micro-USD ($0.003257). No ballot was retried.

## Artifacts

- `docs/charter/edition2-ratified.toml`: the ratified charter with provenance headers.
- `docs/charter/edition2-ratification.json`: every request, response, cost, drawn seat and the
  accepted set.
- `worlds/edition2-testnet.toml`: carries `charter.ratified_sha256` and `charter.roster_sha256`
  (both popped from the canonical form, so the manifest hash is unchanged).

Charter SHA-256: `929f1b04bee45155e74d196c80b1a6d764a75555fa4ecc5872beffc629578935`.
Roster SHA-256: `3de164c6f93917c0d47dbe5579e67a296b39eb3f8fe2aad15d3184876e2769fb`.

No living world was modified, no mainnet trading or transfer was performed.
