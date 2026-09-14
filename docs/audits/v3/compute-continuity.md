# Compute continuity verification — 14 September 2026

The existing router excludes a provider whose available credit is below a call's reservation. It does not substitute another provider inside a selected assembly. The original rehearsal roster has no Venice seed assemblies, so funded Venice credit alone does not keep that population running when OpenRouter is empty.

## Changes and decisions

- `factorylab/cortex/schematics.py`: the public world context now states that OpenRouter is prepaid seed credit, Venice is a separate credit pot, `treasury.transfer` with `to_venice` buys a $5 tranche, and `catalogue.search` exposes Venice model IDs. This is resource/tool information, not a mandatory survival strategy or a charter amendment.
- `worlds/compute-continuity-roster.toml`: a separate candidate preserves the nine seed contracts and gives Venice coverage across producer, evaluator, meta and antagonist roles, including two evaluators. Three assemblies remain on OpenRouter. Venice prices came from its authenticated catalogue. DeepSeek thinking is disabled initially after the paid probe below exposed an empty response at its token limit. The population can still register other configurations.
- `tests/runtime/test_compute_continuity.py`: eight regression cases cover initial exhaustion, mid-run exhaustion, a 402 race, restart, both providers empty, the original roster's inability to continue, role coverage/provenance, and public resource disclosure.
- This report and `evidence/compute-continuity-2026-09-14/` retain the proof harnesses and results.

No new trading or production spending cap was added. The candidate inherits rehearsal settings and fake-USD accounting; it is not a funded production manifest. No commits or mainnet activation were performed.

## Deterministic and live-runtime checks

All eight focused cases passed. Each continuity case ran 12 ticks with ledger verification and wallet conservation intact, including verdicts, conformities and settled forecasts. After the injected credit loss, subsequent dispatches used Venice exclusively. With both providers empty, the world terminated and released its seal without dispatch or billing.

The restart test passes a fresh provider facade to recovery rather than wrapping the old journal proxy. Replaying recorded calls must not reuse a prior runtime's journal transport. The original failed test harness supplied that old proxy; it was corrected without changing production recovery code.

An additional network-blocked probe exercised the live runtime branch using a fake exchange and controlled providers. It continued for 12 ticks and reconciled all 12 times after OpenRouter loss. Its result is `evidence/compute-continuity-2026-09-14/live-path-result.json`.

## Paid inference proof

The paid probe uses actual OpenRouter/Venice completions inside Runtime, a fake exchange and treasury, and a blocked external-seller transport. It makes OpenRouter report zero to this process after one completed request. It does not drain the account, change its cap, place real orders, or buy credit. The candidate uses its default charter as an explicitly unratified test fixture.

The first run completed four ticks in 230.72 seconds: one OpenRouter completion and 19 Venice completions, with every request after the cutoff sent to Venice. Recorded inference cost: 51,754 micro-USD ($0.051754). Ledger verification and conservation passed; the world did not terminate. Nineteen responses passed the runtime contract; one DeepSeek response ended at `length` with no visible answer. This failure is preserved in `paid-result-first.json`. It motivated disabling that seed model's hidden thinking before the repeat.

The final repeat completed four ticks in **119.10 seconds**: one OpenRouter completion and 12 Venice completions. Eleven Venice calls followed the cutoff; every one completed successfully, and OpenRouter received no further dispatch. All 13 runtime invocations were `ok`, with no malformed response or length stop. The run produced seven verdicts, one conformity and 29 settled forecasts. Ledger verification and wallet conservation passed, and the world remained alive. Recorded inference cost: **28,031 micro-USD ($0.028031)**. All recorded DeepSeek responses used zero reasoning tokens. See `paid-result-final.json` and `paid-probe-final.py.txt`.

Combined recorded inference cost of the two probes: 79,785 micro-USD ($0.079785). These are adapter/meter costs, which can include catalogue estimates when a provider omits an explicit cost; the harness did not retain cost-source metadata or independently reconcile provider billing.

The final roster's focused routing and Venice adapter gate:

```text
42 passed in 2.42s
```

Full repository command: `uv run ruff check . && uv run pytest` (exit 0). Its complete output is in `evidence/compute-continuity-2026-09-14/full-gate.log`.

```text
All checks passed!
======================= 2670 passed in 647.10s (0:10:47) =======================
```

The full suite included the new continuity tests and the public-context code change. The candidate's DeepSeek reasoning setting was adjusted while that suite ran; the subsequent 42-test focused run, network-blocked live-runtime probe, paid repeat and final Ruff check verified the final configuration.

## Deployment boundary

This change is a tested candidate, not a launched population. The old ratified charter is bound to the original roster; the regression test confirms that it cannot be relabelled for this candidate. At the time of this exhaustion test, charter approval and a prepared manifest were pending. The subsequent 5–0 adoption closed both of those testnet preparation steps; see [charter-adoption.md](charter-adoption.md). The previously verified DigitalOcean release has not been replaced in this task.

Existing Venice credit and the ability to buy more credit are distinct. These tests prove continuation on available Venice credit. They do not establish that current mainnet treasury/gas funding can replenish it. The previous handoff's funding prerequisites remain separate launch work. Nor can a Class 3 population be guaranteed to retain its Venice assemblies or make useful decisions indefinitely; the test establishes infrastructure continuity for the stated starting population.
