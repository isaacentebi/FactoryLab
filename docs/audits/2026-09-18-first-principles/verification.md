# Verification and limitations

Review date: 2026-09-18. HEAD: `efa751b28f5ea78f91636bc4396249eb72c90f21`.

The review creates only this audit directory. It changes no implementation, configuration,
manifest, or existing test. The pre-existing changes to `scripts/draft_edition1.py` and
`tests/runtime/test_loop.py`, and the untracked `.claude/` directory, were preserved. No commit
was made. A final SHA-256 comparison of every copied tracked file found no change between
the verification snapshot and the working tree during the audit.

**Gate**

The required command `uv run ruff check . && uv run pytest` was attempted in the working tree.
Ruff printed `All checks passed!`; that initial pytest process ended with exit 143 and a
`BrokenPipeError` before producing a useful result. The cause was not established.

To avoid interfering with another active testing process, tracked working-tree files,
including the two pre-existing edits, were copied to an isolated temporary directory:

`/var/folders/f5/7kp8jy796_b3tvgq6rsf4j7h0000gn/T/factorylab-audit-20260918-w7fz8380`

Keys, symlinks and untracked files were not copied. This was a source snapshot, not a new
deployment or git checkout. It reused the installed environment, disabled uv synchronization,
put the snapshot first on PYTHONPATH, and capped auto workers at two:

```sh
UV_PROJECT_ENVIRONMENT=/Users/isaacentebi/Desktop/FactoryLab/.venv \
UV_NO_SYNC=1 PYTHONPATH="$PWD" PYTEST_XDIST_AUTO_NUM_WORKERS=2 uv run ruff check .

UV_PROJECT_ENVIRONMENT=/Users/isaacentebi/Desktop/FactoryLab/.venv \
UV_NO_SYNC=1 PYTHONPATH="$PWD" PYTEST_XDIST_AUTO_NUM_WORKERS=2 uv run pytest
```

The default marker selection was unchanged: network and marked slow tests were excluded.
3296 items were selected. After roughly ten minutes, both workers were still CPU-bound and
had reached approximately 13–14% of the suite. At 9:31, one sampled worker had about 1.79 GB
RSS and the other about 1.46 GB. The lead sent SIGINT only to this audit's pytest coordinator,
then verified that its coordinator and workers had exited. Other tasks' processes were left
alone.

Exact final pytest summary:

```text
================== 472 passed, 1 skipped in 582.60s (0:09:42) ==================
```

This was an interrupted run, exit 2. **The full gate did not pass or finish.** The short
summary does not mean the remaining tests passed. Crash/resume tests were not rerun as a full
separate gate. A 20-second recursive-meta test timeout from a bounded agent check is a timing
observation, not evidence that its assertion fails.

The complete captured isolated-gate output, including `KeyboardInterrupt`, is preserved
verbatim in [gate-output.txt](/Users/isaacentebi/Desktop/FactoryLab/docs/audits/2026-09-18-first-principles/evidence/gate-output.txt).
Ruff was also rerun against the final working tree and again printed exactly:

```text
All checks passed!
```

**Reproductions**

The `.py.txt` suffix identifies review evidence rather than production/test source. Python
can execute these files. Run from the repository root with its existing uv environment:

```sh
PYTHONPATH="$PWD" uv run python docs/audits/2026-09-18-first-principles/evidence/double-income.py.txt
PYTHONPATH="$PWD" uv run python docs/audits/2026-09-18-first-principles/evidence/censored-trials.py.txt
PYTHONPATH="$PWD" uv run python docs/audits/2026-09-18-first-principles/evidence/censored-committee.py.txt
PYTHONPATH="$PWD" uv run python docs/audits/2026-09-18-first-principles/evidence/directory-scaling.py.txt
```

The first three were independently rerun by the lead and each exited 0 while demonstrating
the problematic behavior. Their exact outputs accompany them. They import `make_runtime`
from the existing test fixtures, use fake world components, and make no paid or network call.
The income probe specifically calls the real `LiveRail.verify_receipt` against one stubbed
chain proof; constructing that rail with `object.__new__` avoids key-loading initialization.
It then calls the real treasury booking and runtime income-credit methods.

Observed income result:

```text
chain_transfer_count 1
verified_income_items 2 earned_micro 2000000
wallet_delta_micro 2000000
wallet_conservation True
```

Observed novelty result:

```text
censored: external_unobservable
trial_count_before: 0
committee_before: {}
trial_count_after: 1
committee_after: {}
min_settled: 5
```

Observed voter-eligibility result:

```text
censored_payoffs: 5
eligible: {'seed-decider': 'producer'}
uncensored_payoffs: 0
```

An additional lead-run lot-table probe bound an order size of 1 BTC and called `fill` with
2 BTC. Exact output:

```text
{'ordered_BTC': '1', 'recorded_lot_BTC': '2', 'remaining_BTC': '0'}
```

The service-death finding is a verified source-control-flow finding, not a live payment test.
The evaluator-tool finding combines source tracing, a local validator acceptance check, and
the absence of tool attempts in the historical exported evaluator returns. It does not
claim the kernel forbids judge reads.

**Performance evidence**

Directory-scaling results were measured by Sol using the included script. Their exact table
is preserved in `evidence/directory-scaling.output.txt`. They were obtained under concurrent
local load and are baselines, not speedup claims. Separate cProfile results include
instrumentation overhead; overlapping cumulative function times are not additive. A native
sample of one audit worker supported a CPU-bound Python path but did not establish a precise
Python-level attribution on its own. No optimization was implemented or benchmarked after.

**Launch and source checks**

The current Edition 3 testnet manifest and current ratified charter passed an offline preflight
when a temporary copy was assigned the deliberately required fresh namespace. Historical
Run 5 rejecting the newer charter is correct protection, not a new defect. An offline
scripted calibration run passed 45/45 for six configured routes; because it used scripted
answers, it does not validate those actual models. These distinctions were checked after
challenging the initial subagent findings.

No claim is made about fresh venue balances, the deployed host, paid-model behavior after
the latest changes, production service reachability, or mainnet readiness. Older handoff
notes contain superseded roster and gate statements; source, current artifacts, and the
specific dated evidence take precedence.

**Files added by this review**

- `review.md`
- `verification.md`
- `evidence/gate-output.txt`
- `evidence/double-income.py.txt` and `evidence/double-income.output.txt`
- `evidence/censored-trials.py.txt` and `evidence/censored-trials.output.txt`
- `evidence/censored-committee.py.txt` and `evidence/censored-committee.output.txt`
- `evidence/directory-scaling.py.txt` and `evidence/directory-scaling.output.txt`

**Audit decisions not specified by the original request**

The lead isolated verification to preserve concurrent work, limited test CPU usage, stopped
the incomplete gate after 582.60 seconds, and created reproducible evidence instead of making
unreviewed financial or architectural changes. Recommendations prioritize persistent shared
projects based on the user's clarification that autonomous society matters more than merely
earning compute. Prediction markets are proposed as a separate, measurable addition, with
no external venue integration or financial activity initiated.
