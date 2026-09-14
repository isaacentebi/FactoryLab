# DigitalOcean deployment and resource verification

Status: functional verification is complete and the exact release is installed on
DigitalOcean. The local required gate passed 2,662 tests; the separate local slow
suite passed 35. Linux passed all 2,661 applicable normal tests with one
macOS-only skip, plus five service-user recovery cases. Actual encrypted runtime
restore and the final reboot/isolation checks passed. The final host test pipeline
exited zero at 14:16:09 UTC on September 14; reboot verification passed at 14:19 UTC.

Mainnet remains stopped. No funded manifest or account credential files are
installed. Signed testnet trading and paid inference proofs used ephemeral
credentials through encrypted SSH stdin. No new trading/spending caps or commits
were made. External first-move setup remains below; this is not an activated
or one-click-ready funded world.

## Host

The existing `superdarkfactory` droplet is ID `599960972`, public IPv4
`152.42.221.133`, Ubuntu 24.04.4 LTS, SGP1, two vCPUs, approximately 4 GiB RAM and
80 GB disk. The authenticated DigitalOcean page and the SSH metadata endpoint
agree on the droplet ID. SSH uses the existing Stack deployment key; no private
key contents were displayed. The page shows $24/month. No new droplet or paid
backup subscription was purchased. The authenticated billing page showed
$19.82 remaining prepayment against two droplets, with an existing primary and
backup payment method. The page says its information was last updated September
13 at 12:56 AM CST. A full year of dedicated prepaid hosting has not been verified;
no payment, auto-reload setting or billing-team change was made.

The source archive includes the current uncommitted runtime fixes. No commit
was made. The final 391-file archive SHA-256 is
`d8ebca6bca04156ae81cd5f40630808dda27956fdf4125a2aa33034ca83ec77b`.
The per-file manifest is `/srv/factorylab/source-files.json`; the release package
is retained under `/root/factorylab-digitalocean-stage/`. This report itself was
written after the source archive was frozen and is not part of that archive.

## Overnight execution and repairs

Two new, independently namespaced testnet worlds used the exact 13-card ratified
charter and a ten-minute deadline. Both terminated at their budget boundary,
released their seals, verified their ledgers and conserved the integer wallet.
They finish an in-progress cascade; they are not sixty-tick guarantees.

| Observation | First rehearsal | Second rehearsal |
| --- | ---: | ---: |
| Order intents / acknowledged fills / ingested fills | 10 / 10 / 10 | 0 / 0 / 0 |
| Rejected / uncertain orders | 0 / 0 | 0 / 0 |
| Delivered ticks | 7 | 8 |
| Median achieved tick gap | 99.181292 s | 78.141756 s |
| Model calls | 83 | 79 |
| Successful / malformed / failed invocations | 82 / 1 / 0 | 78 / 0 / 1 |
| Model spend, micro-USD | 223331 | 261514 |

The second population generally held its existing testnet positions, citing no
new signal and its own charter's turnover/concentration cards. One antagonist
invocation returned `OpenRouterError` at zero recorded cost; the loop continued.
No provider uptime or profitability guarantee follows from these runs. Neither
world automatically closed the existing venue book when it ended.

After the additional preparation repairs, a separate testnet-only SOL check
submitted a real buy, deliberately discarded its acknowledgement, and retried the
same identity. Exactly one dispatch occurred; lookup recovered order 60081293268
as filled. Reducing close 60081295470 filled, two fills were ingested, and SOL
ended flat. This fixture introduced no runtime order-size or exposure limit.

The live network suite passed 8 tests, including a confirmed three-testnet-USDC
perps-to-spot transfer. Its provider-key-gated catalogue test was skipped during
collection and then run separately through the CLI credential loader: 1 passed.
A Venice wallet-authenticated completion returned `OK`, reporting 96 micro-USD
cost and request `chatcmpl-d618e332ee1d84d79dcd290a2f581940`. Existing Venice credit
therefore has actual paid-completion evidence, not only a balance observation.

Order repairs verified by 140 focused tests:

- Read fresh quotes and positions before entering ambiguous write handling.
  Preparation errors are known rejections; write-phase failures retain their
  stable identity and require reconciliation.
- Preserve the explicitly requested side of a reduce-only market order even if
  the fresh position differs from cached account data.
- Read fresh spot availability and reject failed/malformed preparation without
  pretending an order may have been sent.
- Reject invalid order identities and unrepresentable SDK quantities/prices
  before dispatch. Keep the SDK's existing market-price calculation.
- Reconcile a repeated uncertain cancellation against its original target even
  when a caller supplies a different target later.
- Project the diagnostic probe onto its requested coins without restricting the
  population's wider market access.

The actual `age`/`rclone` synthetic recovery proof also restores the exact launch
manifest, authenticates the restored ledger and excludes a torn trailing record.
It uses generated fixture credentials. It is not a configured off-host backup.

The content-hashed source, full rehearsal artifacts and safe proof logs are saved
under `/Users/isaacentebi/Documents/FactoryLab-releases/2026-09-14-d8ebca6b/`
(mode 0700), independent of temporary files. No live wallet keys are in the source
archive. All final gate transcripts are retained there too.

## Fresh balances, read-only

Observed at `2026-09-14T14:16:39.857700+00:00`. The read submitted zero transactions and
used zero cached account fallbacks. Full JSON is in
`evidence/final-readonly-balances.json` in the retained release directory.

| Resource | Observed value |
| --- | ---: |
| Hyperliquid mainnet equity | $100 |
| Hyperliquid open positions / resting orders | 0 / 0 |
| OpenRouter account credit | $90.523095 |
| Existing OpenRouter key remaining allowance | $89.314147 |
| Venice wallet credit, rounded down to micro-USD | $4.976524 |
| Base reserve USDC | $4.966000 |
| Base reserve native ETH | 0 wei |
| HyperEVM reserve native HYPE / USDC | 0 wei / $0 |
| Hyperliquid Core spot HYPE | No balance entry |

Existing Venice credit paid for an actual completion. The earlier authenticated
billing response reported a $5 minimum additional top-up; the Base reserve is
$0.034 below that amount. The cross-chain replenishment route also needs native
gas, and the implementation has no native-gas acquisition operation to bootstrap
those empty balances. Existing credit and future replenishment are distinct.

The sum of the four observed resource pots (venue equity, full OpenRouter account
credit, Venice credit and Base USDC) is 200465619 micro-USD. The key allowance
is not an additional pot. It is an existing upstream restriction that still needs
the owner's provider setting to match full-account intent; no new allowance was
imposed here. Startup initializes its wallet from the exact manifest, while
reconciliation reports discrepancies without rewriting it. Refresh all resources
before freezing that manifest; the $90 testnet accounting fixture is not the
funded world's starting balance.

## Host fault repaired

The service-user jail initially failed with:

```text
bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted
```

Kernel audit records identified AppArmor's `unprivileged_userns` denial of
`setpcap` and `net_admin`. Installed the unchanged upstream AppArmor 4.0 stacked
bubblewrap profile, pinned in `deploy/bwrap-userns-restrict`, and added its
installation to `deploy/cloud-init.yaml`. The profile's source and hash are in
`deploy/README.md`. This follows [Ubuntu's targeted bubblewrap guidance](https://discourse.ubuntu.com/t/understanding-apparmor-user-namespace-restriction/58007).

The factory-user check then passed under the production unit's NoNewPrivileges,
PrivateTmp, ProtectSystem and ProtectHome settings. Separate benign/adversarial
probes confirmed normal Python execution and rejection of socket creation,
process creation, and a known host-side canary file. The canary was removed.
The global `kernel.apparmor_restrict_unprivileged_userns` remains `1`.

Public Hyperliquid info, OpenRouter model catalogue and Venice model catalogue
returned HTTP 200 from the droplet. That initial check proved endpoint connectivity. The later signed testnet
service-user proof below separately establishes trading success from this host;
the paid model proof below separately establishes authenticated inference from
the same protected service account.

The service definitions are installed in `/etc/systemd/system` and passed
`systemd-analyze verify`. `factorylab.service` and `factorylab-backup.timer` are
disabled and inactive; `factorylab-wake.service` is static and inactive. Installing
these stopped definitions is separate from verifying their runtime behavior.

## Remaining first-move work

- Finish credential installation and the fresh, ratified funded manifest only
  after launch requirements are met. No funded manifest exists in this release.
- Resolve native gas for replenishment and the existing OpenRouter key allowance.
- Configure and verify encrypted off-host backup/restore and failure notifications.
  These have not been claimed operational merely because scripts are installed.
  The operator confirmed there is no existing storage bucket or webhook destination.
  No new storage subscription or notification service was purchased.

## Capacity evidence and limits

Lifetime capacity remains finite and measured. The prior
  11-minute live rehearsal produced a 40,094,667-byte encrypted ledger. The current
  disk reader already streams; recovery retains the latest snapshot and its streamed
  tail. Kernel history indexes and runtime state still grow. Earlier wording in this
  report and the runbook incorrectly described the retired full-file reader. This
  4 GiB machine has no proven long-run capacity guarantee; the rehearsal size is not
  a linear growth forecast. A separate disk-backed scripted 400-tick fixture
  completed with a verified 245,520,459-byte ledger. Its process reached 777,224,192
  bytes maximum RSS on macOS; a separate aggregate reader reached 641,957,888 bytes,
  opened in 0.607 seconds and projected aggregates in 4.275 seconds. The largest
  record was a 36,500,356-byte snapshot. An initial measurement harness called a
  nonexistent Ledger convenience method after the successful world run; the
  separate reader correctly exercised the production wake API. These are local
  fixture measurements, not a Linux lifetime guarantee or a linear forecast.

## Changes and decisions in this verification/deployment step

Full changed-file list (includes preceding uncommitted repairs):

- `README.md`
- `deploy/README.md`
- `deploy/backup.sh`
- `deploy/bwrap-userns-restrict`
- `deploy/cloud-init.yaml`
- `docs/audits/v3/caps-removal.md`
- `docs/audits/v3/digitalocean-deployment.md`
- `docs/audits/v3/evidence/retired-execution-drills/execution-drill-final.toml.txt`
- `docs/audits/v3/evidence/retired-execution-drills/execution-drill-final.used.txt`
- `docs/audits/v3/evidence/retired-execution-drills/execution-drill-recheck.toml.txt`
- `docs/audits/v3/evidence/retired-execution-drills/execution-drill-recheck.used.txt`
- `docs/audits/v3/evidence/retired-execution-drills/execution-drill.toml.txt`
- `docs/audits/v3/evidence/retired-execution-drills/execution-drill.used.txt`
- `docs/audits/v3/evidence/retired-execution-drills/testnet-10m.toml.txt`
- `docs/audits/v3/evidence/retired-execution-drills/testnet-10m.used.txt`
- `docs/audits/v3/order-lifecycle-repair.md`
- `docs/audits/v3/production-readiness.md`
- `docs/audits/v3/short-experiments.md`
- `docs/charter/edition1-short-draft.md`
- `docs/charter/edition1-short-ratification-v2.json`
- `docs/charter/edition1-short-ratification.json`
- `docs/charter/edition1-short-ratified.toml`
- `docs/charter/edition1-short.toml`
- `docs/history/launch-decisions-2026-09-13.md`
- `docs/launch-decisions.md`
- `docs/manifest.md`
- `factorylab/cortex/assembly.py`
- `factorylab/cortex/schematics.py`
- `factorylab/runtime/cli.py`
- `factorylab/runtime/compute.py`
- `factorylab/runtime/governance.py`
- `factorylab/runtime/live.py`
- `factorylab/runtime/loop.py`
- `factorylab/runtime/propensity.py`
- `factorylab/runtime/resume.py`
- `factorylab/runtime/summary.py`
- `factorylab/runtime/venue.py`
- `factorylab/runtime/worlds.py`
- `factorylab/settlement/consequence.py`
- `factorylab/world/exchange.py`
- `factorylab/world/models.py`
- `factorylab/world/openrouter.py`
- `factorylab/world/probe.py`
- `scripts/calibrate_rehearsal.py`
- `scripts/draft_edition1.py`
- `scripts/ratify_charter.py`
- `scripts/rehearsal.py`
- `tests/audit/test_accelerated_repair.py`
- `tests/audit/test_order_lifecycle_repair.py`
- `tests/cortex/test_tools.py`
- `tests/runtime/test_resume.py`
- `tests/runtime/test_wake.py`
- `tests/world/test_order_acknowledgement.py`
- `tests/world/test_order_preparation.py`
- `tests/world/test_probe.py`
- `tests/world/test_spot.py`
- `tests/world/test_venue_tools.py`
- `worlds/testnet-10m-roster.toml`

The earlier runtime repairs remain uncommitted and are included in the release.
No economic cap or policy was introduced or made optional. Used a content-hashed
working-tree release because a Git bundle would omit the uncommitted fixes and
the task forbids commits. Reused the existing droplet and SSH key. Staging leaves
OS automatic-update settings unchanged. No paid resources were added.

## Earlier verification attempts

Local required gate passed: 2631 tests in 2428.18 seconds. The runtime source was
unchanged during this verification; the deployment profile and test-only changes
received the separate host checks described above. The first host recovery run
reported 4 passed and 1 failed in 1556.98 seconds: the failure was the 180-second
subprocess watchdog, before the planned 125-tick crash point. That same case passed
with FACTORYLAB_TEST_CHILD_TIMEOUT=1800: 1 passed in 983.60 seconds. Its recovery
and conservation assertions are unchanged. Together these runs passed all five
selected recovery cases. The duplicate full host suite was deliberately interrupted
after 4533.44 seconds: 1 failed, 1185 passed, 1 skipped. It is not a completed host
gate. The failure was `test_tool_environment_does_not_inherit_path_home_or_keys`:
bubblewrap synthesized `PWD=/work`, which the test's key-name whitelist omitted.
The corrected test poisons host PWD too, rejects every inherited sentinel value,
and allows PWD only when its value is exactly `/work`. Runtime isolation was not
changed. All 80 tests in that module passed locally in 2.03 seconds after this
test-only change. The focused Linux fast gate passed: 2460 passed, 1 skipped in 456.23 seconds.
The operator subsequently authorized overnight testing. The final local normal
and slow suites passed; the exhaustive final Linux batches are recorded below. Historical local log:
`/tmp/factorylab-digitalocean-gate.log`; remote logs:
`/root/factorylab-install.log`, `/root/factorylab-recovery-check.log`, and
`/root/factorylab-recovery-recheck.log`.

### Earlier local required gate output, verbatim

```text
All checks passed!
============================= test session starts ==============================
platform darwin -- Python 3.13.11, pytest-9.1.1, pluggy-1.6.0
rootdir: /Users/isaacentebi/Desktop/FactoryLab
configfile: pyproject.toml
testpaths: tests
plugins: xdist-3.8.0, anyio-4.15.1
created: 14/14 workers
14 workers [2631 items]

........................................................................ [  2%]
........................................................................ [  5%]
........................................................................ [  8%]
........................................................................ [ 10%]
........................................................................ [ 13%]
........................................................................ [ 16%]
........................................................................ [ 19%]
........................................................................ [ 21%]
........................................................................ [ 24%]
........................................................................ [ 27%]
........................................................................ [ 30%]
........................................................................ [ 32%]
........................................................................ [ 35%]
........................................................................ [ 38%]
........................................................................ [ 41%]
........................................................................ [ 43%]
........................................................................ [ 46%]
........................................................................ [ 49%]
........................................................................ [ 51%]
........................................................................ [ 54%]
........................................................................ [ 57%]
........................................................................ [ 60%]
........................................................................ [ 62%]
........................................................................ [ 65%]
........................................................................ [ 68%]
........................................................................ [ 71%]
........................................................................ [ 73%]
........................................................................ [ 76%]
........................................................................ [ 79%]
........................................................................ [ 82%]
........................................................................ [ 84%]
........................................................................ [ 87%]
........................................................................ [ 90%]
........................................................................ [ 93%]
........................................................................ [ 95%]
........................................................................ [ 98%]
.......................................                                  [100%]
====================== 2631 passed in 2428.18s (0:40:28) =======================
```

## Final release local gate, verbatim

Command: `uv run ruff check . && uv run pytest`

```text
All checks passed!
============================= test session starts ==============================
platform darwin -- Python 3.13.11, pytest-9.1.1, pluggy-1.6.0
rootdir: /Users/isaacentebi/Desktop/FactoryLab
configfile: pyproject.toml
testpaths: tests
plugins: xdist-3.8.0, anyio-4.15.1
created: 14/14 workers
14 workers [2662 items]

........................................................................ [  2%]
........................................................................ [  5%]
........................................................................ [  8%]
........................................................................ [ 10%]
........................................................................ [ 13%]
........................................................................ [ 16%]
........................................................................ [ 18%]
........................................................................ [ 21%]
........................................................................ [ 24%]
........................................................................ [ 27%]
........................................................................ [ 29%]
........................................................................ [ 32%]
........................................................................ [ 35%]
........................................................................ [ 37%]
........................................................................ [ 40%]
........................................................................ [ 43%]
........................................................................ [ 45%]
........................................................................ [ 48%]
........................................................................ [ 51%]
........................................................................ [ 54%]
........................................................................ [ 56%]
........................................................................ [ 59%]
........................................................................ [ 62%]
........................................................................ [ 64%]
........................................................................ [ 67%]
........................................................................ [ 70%]
........................................................................ [ 73%]
........................................................................ [ 75%]
........................................................................ [ 78%]
........................................................................ [ 81%]
........................................................................ [ 83%]
........................................................................ [ 86%]
........................................................................ [ 89%]
........................................................................ [ 91%]
........................................................................ [ 94%]
........................................................................ [ 97%]
......................................................................   [100%]
======================= 2662 passed in 662.78s (0:11:02) =======================
```

## Final release local crash/recovery suite, verbatim

Command: `uv run pytest -m slow -n 2 --durations=5`

```text
============================= test session starts ==============================
platform darwin -- Python 3.13.11, pytest-9.1.1, pluggy-1.6.0
rootdir: /Users/isaacentebi/Desktop/FactoryLab
configfile: pyproject.toml
testpaths: tests
plugins: xdist-3.8.0, anyio-4.15.1
created: 2/2 workers
2 workers [35 items]

...................................                                      [100%]
============================= slowest 5 durations ==============================
44.46s call     tests/audit/test_a1_composition.py::test_a1_scripted_world_exercises_each_composition_freedom_once
32.94s call     tests/runtime/test_resume.py::test_sigkill_after_clock_amendment_preserves_interval_and_summary[activation]
24.76s call     tests/runtime/test_resume.py::test_sigkill_after_clock_amendment_preserves_interval_and_summary[snapshot]
24.28s call     tests/runtime/test_resume.py::test_sigkill_resume_matches_every_summary_field[between_windows]
22.42s call     tests/runtime/test_resume.py::test_sigkill_resume_matches_every_summary_field[event250]
======================== 35 passed in 117.00s (0:01:56) ========================
```

## Reboot verification before the final Linux batches

A host inspection found pending kernel/libc updates requiring restart. With no
funded world or credentials installed, the pre-reboot test run was interrupted
(301 passed, 1092.82 seconds; not a completed gate), and the droplet was rebooted.
It now runs `6.8.0-139-generic`, reports no pending reboot, has synchronized time,
and reports no failed systemd units. All funded/supporting services remain disabled.

Both the normal jail probe and the three adversarial service-user checks passed
after reboot: sockets, process creation and a real host-side canary were blocked.
The global unprivileged user-namespace restriction is still 1. The final Linux
verification runs as a managed systemd job and continues across an SSH disconnect.
Only SSH and loopback DNS were listening during the stopped deployment check.

A scan of all 391 archived source files against the loaded account credentials
found no matches. Credential values were never printed by the scan.

## Linux memory failure and exhaustive batch verification

The single-process-group Linux release gate was OOM-killed at about 43% on
September 14 at 07:19 UTC. systemd recorded a 3.5 GB memory peak and no swap.
That invocation is incomplete, not a passing gate. Its original log is retained.

An 8 GiB, mode-0600 swapfile was added on the existing disk and recorded in
`/etc/fstab`; 66 GiB disk remained free afterward. No paid resource was added.
The fresh-install script now includes this configuration. The same normal test
coverage ran in two fresh processes: 2,492 `fast` cases with two workers
and 170 `world` cases with one process. Collection proved these sets are disjoint
and their union is exactly all 2,662 normal cases. No assertion or runtime
behavior was weakened to accommodate the host. Both exhaustive batches passed.

The production treasury subsequently completed an actual three-mock-USDC CCTP
round trip on Base Sepolia and Hyperliquid testnet, retaining the original
transaction references while awaiting finality. Both directions and all seven
step receipts passed; the receipt evidence is recorded below. This is separate
from the earlier testnet internal transfers.

At 07:28 UTC a separate mainnet spot-state read found no HYPE balance.
This is distinct from the reserve wallet's zero HyperEVM HYPE: the current
Core-to-EVM withdrawal path needs HYPE in the venue spot account. The population
has a spot trading interface, but replenishment cannot assume gas already exists.
The Base-side route also needs reserve ETH, which is absent on mainnet. Testnet
native balances were funded and passed both route preflights; they say nothing
about mainnet gas. These checks submitted no mainnet transaction.

### Final Linux fast batch, verbatim

Command: `.venv/bin/ruff check .` followed by
`.venv/bin/pytest -m 'not network and not slow and fast' -n 2 --durations=10`.
The one skip is the macOS-only sandbox-profile assertion; it passed locally.

```text
All checks passed!
============================= test session starts ==============================
platform linux -- Python 3.13.15, pytest-9.1.1, pluggy-1.6.0
rootdir: /srv/factorylab/repo
configfile: pyproject.toml
testpaths: tests
plugins: anyio-4.15.1, xdist-3.8.0
created: 2/2 workers
2 workers [2492 items]

........................................................................ [  2%]
........................................................................ [  5%]
........................................................................ [  8%]
........................................................................ [ 11%]
........................................................................ [ 14%]
........................................................................ [ 17%]
........................................................................ [ 20%]
........................................................................ [ 23%]
........................................................................ [ 26%]
........................................................................ [ 28%]
........................................................................ [ 31%]
........................................................................ [ 34%]
........................................................................ [ 37%]
........................................................................ [ 40%]
........................................................................ [ 43%]
........................................................................ [ 46%]
........................................................................ [ 49%]
........................................................................ [ 52%]
........................................................................ [ 54%]
........................................................................ [ 57%]
........................................................................ [ 60%]
.......................................s................................ [ 63%]
........................................................................ [ 66%]
........................................................................ [ 69%]
........................................................................ [ 72%]
........................................................................ [ 75%]
........................................................................ [ 78%]
........................................................................ [ 80%]
........................................................................ [ 83%]
........................................................................ [ 86%]
........................................................................ [ 89%]
........................................................................ [ 92%]
........................................................................ [ 95%]
........................................................................ [ 98%]
............................................                             [100%]
============================= slowest 10 durations =============================
204.00s call     tests/audit/test_c3_resume_wedges.py::test_finding_2_death_during_a_population_tool_run_wedges_the_scripted_world
123.90s call     tests/runtime/test_connectors.py::test_scripted_world_admits_by_real_sortition_then_fetches_and_parses[stub-parser]
113.43s call     tests/audit/test_r3_a_spot.py::test_durable_spot_inventory_write_replays_once_after_interruption
113.25s call     tests/runtime/test_connectors.py::test_scripted_world_admits_by_real_sortition_then_fetches_and_parses[actual-jail]
13.34s call     tests/runtime/test_venue_spot.py::test_scripted_provider_exercises_spot_and_transfer
12.99s call     tests/audit/test_a14_revision.py::test_scripted_windows_with_divergence_produce_a_sampling_raise_item
12.78s call     tests/learners/test_delayed.py::test_investment_trap_at_5000_seed_zero_separates_delivery_from_adaptation
11.22s call     tests/audit/test_r3_mg_regressions.py::test_a_world_whose_window_never_closes_scores_no_verdict_at_all
10.70s call     tests/audit/test_r3_f2_balance_floor.py::test_t30_scripted_crash_dies_below_its_zero_floor_with_money_conserved
8.33s call     tests/runtime/test_ledger_lock.py::test_two_processes_cannot_own_one_world[run-resume]
================= 2491 passed, 1 skipped in 447.03s (0:07:27) ==================

```

## Confirmed native testnet CCTP round trip

The actual production `Treasury` and `LiveRail` completed both directions using
Base Sepolia (84532), HyperEVM testnet (998) and HyperCore testnet. The first
three-mock-USDC transfer was submitted at 07:18:40 UTC and confirmed at 07:38:30;
the return was submitted at 07:38:47 and confirmed at 07:56:23. Both credited
exactly 3,000,000 micro-USDC. Five outbound and two return step receipts were
verified, including venue ledger credit, canonical system withdrawal evidence,
Circle attestation and finalized destination mint.

The encrypted fixture ledger verifies. Reserve USDC returned to 11,000,000 micro.
The test wallet retained its original 993,068,445 micro balance: native gas is
accounted separately, and Circle charged zero USDC on these two transfers.
Economic gas fees were 3,654 and 3,100 micro-USD, respectively; native expenditure
was 1,957,003,869,080 wei Base ETH and 52,489,300,000,000 wei HYPE across the
EVM/core steps. Existing testnet positions can move venue mark-to-market equity;
its small change during this fixture is not an inferred transfer loss.

The original public Base burn hash is
`0x1c8ba711ff03ed00cd9814f03e79af49d9abab10772d9294990f7892caec91ea`;
the return Base mint hash is
`0x7c006814dab98884fe1cc6e537b9a35216bf8ab198d6c8cad34416018fdda006`.
All receipts, checkpoint, encrypted ledger and fixture key are retained in the
release's `cctp-testnet-roundtrip/` directory. The driver's progress log used an
incorrect display-field name for transaction hashes, showing null; the receipt
projection uses the actual `tx_hash` field and verifies the ledger. This was a
diagnostic display issue, not a change to the transaction reference.

No mainnet transaction was submitted. These completed native testnet transfers
do not supply the missing mainnet gas or establish a mainnet transfer result.

The swapfile is active with mode 0600 and size 8,589,934,592 bytes. `findmnt
--verify` found zero parse errors and zero errors; its remaining warning identifies
the regular file used as swap. `systemctl daemon-reload` generated and activated
`swapfile.swap` from `/etc/fstab`.

## Actual signed testnet order from the DigitalOcean service account

The deployment-specific check ran as `factory` under NoNewPrivileges, PrivateTmp,
ProtectSystem=strict and ProtectHome. Only the Hyperliquid signing credential
was delivered through encrypted SSH stdin into that temporary process. No
credential was placed in command arguments or written to a remote file. The
helper files were removed afterward. The source release was unchanged.

The official testnet URL and a flat SOL position were asserted before submission.
The original buy's successful venue acknowledgement was deliberately discarded.
Retrying its same client identity made exactly one dispatch total and recovered
filled order 60089760773. Reducing close 60089761481 filled, both SOL fills were
ingested, and a fresh venue read confirmed SOL flat. The process exited zero.
This fixture used 0.15 testnet SOL and introduced no production order-size cap.

```text
{"network": "hyperliquid-testnet", "client_identity": "host-proof-5a57b733a8734158b6684f9a9b55f337", "size": "0.15", "credential_files_installed": false}
{"network": "hyperliquid-testnet", "size": "0.15", "dispatch_count_after_retry": 1, "lost_ack_result": {"order_id": "60089760773", "status": "filled", "filled_size": "0.15", "avg_px": null, "error": null}, "same_identity_result": {"order_id": "60089760773", "status": "filled", "filled_size": "0.15", "avg_px": null, "error": null}}
{"close": {"order_id": "60089761481", "status": "filled", "filled_size": "0.15", "avg_px": "101.19", "error": null}}
{"final_SOL_flat": true, "polled_SOL_fills": 2}

```

## Paid inference from the DigitalOcean service account

The configured producer model `z-ai/glm-5.3-flash` returned `OK` from the droplet
under the same service protections. OpenRouter reported 21 input tokens, 25
output tokens, a normal stop, and 12 micro-USD cost. Request ID:
`gen-1789375348-3QfLVoUQjFfq37xMrLon`. Only the OpenRouter credential was supplied
through encrypted SSH stdin; no credential file was installed.

```text
{"provider": "openrouter", "served_model": "z-ai/glm-5.3-flash", "text": "OK", "input_tokens": 21, "output_tokens": 25, "reported_cost_micro": 12, "stop_reason": "stop", "request_id": "gen-1789375348-3QfLVoUQjFfq37xMrLon", "credential_files_installed": false}

```

## Leverage interface verification

The population has `venue.set_leverage`. The live adapter accepts a positive
integer and calls the SDK's `update_leverage(..., is_cross=True)`, leaving the
per-market permitted maximum to the venue. Spot rejects leverage. The runtime
uses acknowledged leverage when calculating required collateral; an unknown
live setting receives no assumed collateral discount. This is source/interface
verification, not a mainnet leverage change. No new leverage cap was introduced.

A final bridge-checkpoint assertion also confirmed that the wallet balance equals
its initial balance, reservations and uncertain bills are empty, and both the
principal and fee hold identifiers are cleared. No completed transfer left funds
reserved in the fixture wallet. The proof is retained as
`cctp-testnet-roundtrip/hold-release-proof.json`.

## Final Linux world batch, verbatim

Command: `.venv/bin/pytest -m 'not network and not slow and world' -n 0 --durations=10`.
Both independent 800-event histories passed authenticated reading, conservation,
charter activation checks and equality of the complete versioning reports.

```text
============================= test session starts ==============================
platform linux -- Python 3.13.15, pytest-9.1.1, pluggy-1.6.0
rootdir: /srv/factorylab/repo
configfile: pyproject.toml
testpaths: tests
plugins: anyio-4.15.1, xdist-3.8.0
collected 2706 items / 2536 deselected / 170 selected

tests/audit/test_a17_wake.py ...........                                 [  6%]
tests/audit/test_a3_immune.py ..                                         [  7%]
tests/audit/test_a4_prices.py .                                          [  8%]
tests/audit/test_a5_exposure.py .                                        [  8%]
tests/audit/test_audit_b13_jail.py ...                                   [ 10%]
tests/audit/test_audit_b14_venice.py ..                                  [ 11%]
tests/audit/test_audit_liveness.py ...                                   [ 13%]
tests/audit/test_audit_money_paths.py ....                               [ 15%]
tests/audit/test_audit_resume_wedges.py .....                            [ 18%]
tests/audit/test_b10_account.py .                                        [ 19%]
tests/audit/test_b16_launch.py ....                                      [ 21%]
tests/audit/test_r3_f1_kill.py .....                                     [ 24%]
tests/audit/test_r3_f1_wake.py ...                                       [ 26%]
tests/audit/test_r3_g_verdict_consequence.py ...........                 [ 32%]
tests/audit/test_r3_h_scripted.py ................{"activations": [{"amendment_id": "turnover-card", "edition": 2, "kind": "charter.activate", "round": 1, "seq": 74550, "ts": 121000000000}, {"amendment_id": "scripted-fill-card", "edition": 3, "kind": "charter.activate", "round": 2, "seq": 240012, "ts": 361000000000}], "cadence": [{"activation_event": 3852, "activation_ns": 121000000000, "amendment_id": "turnover-card", "earliest_ns": 60000000000, "kind": "charter.cadence", "outstanding_forecasts": 0, "previous_activation_event": 1, "previous_activation_ns": 0, "seq": 74556, "slowest_period_events": 20, "slowest_period_ns": 20000000000}, {"activation_event": 8111, "activation_ns": 241000000000, "amendment_id": "retire:decision-720:1", "earliest_ns": 181000000000, "kind": "charter.cadence", "outstanding_forecasts": 0, "previous_activation_event": 3852, "previous_activation_ns": 121000000000, "seq": 157029, "slowest_period_events": 20, "slowest_period_ns": 20000000000}, {"activation_event": 12385, "activation_ns": 361000000000, "amendment_id": "scripted-fill-card", "earliest_ns": 301000000000, "kind": "charter.cadence", "outstanding_forecasts": 0, "previous_activation_event": 8111, "previous_activation_ns": 241000000000, "seq": 240028, "slowest_period_events": 20, "slowest_period_ns": 20000000000}], "retirements": [{"assembly_id": "eval-a", "kind": "assembly.retired", "proposal_id": "retire:decision-720:1", "seq": 157021, "version": 1}], "stats": {"amendments_activated": 2, "amendments_passed": 2, "amendments_proposed": 2, "assembly_learners_registered": 1, "censored": 1850, "clock_changes": 0, "closes_credited": 356, "conformities": 1222, "consequences_by_assembly": {"antagonist-a": 183, "eval-a": 106, "eval-b": 324, "eval-c": 1090, "eval-d": 1235, "funding-watcher": 246, "meta-a": 265, "meta-b": 466, "return-observer": 267, "seed-decider": 908, "seed-observer": 298, "web-observer": 481}, "consequences_pending": 0, "decisions": 6738, "epochs": 7, "events": 17365, "exclusions": 0, "exposures_settled": 183, "exposures_won": 0, "fast_settlements": 731, "fills": 361, "forecasts_sealed": 8403, "forecasts_settled": 8403, "immune_windows": [{"charter_edition": 1, "index": 1, "profile": {"card:cost_per_return": null, "card:forecast_skill": -0.2896954247360059, "card:well_formed_rate": 1.0, "conformity": 0.8, "consequence": -0.4491976599920603, "exposure": 0.0, "fast": null, "registrations": 13.0, "revision": 0.009735744089012517, "verdict": 0.3746621621621622}, "regions": {"card:forecast_skill": {"card_id": "forecast_skill", "hi": null, "kind": "min", "lo": 0.0, "scale": 2.0}, "card:well_formed_rate": {"card_id": "well_formed_rate", "hi": null, "kind": "min", "lo": 0.9, "scale": 0.9}}}, {"charter_edition": 2, "index": 2, "profile": {"card:cost_per_return": 3190.0, "card:forecast_skill": -0.32848105177653386, "card:turnover": 193.84413281327193, "card:well_formed_rate": 1.0, "conformity": 0.8, "consequence": -0.46204158390257677, "exposure": 0.0, "fast": null, "registrations": 0.0, "revision": 0.0012755102040816326, "verdict": 0.33091715976331365}, "regions": {"card:cost_per_return": {"card_id": "cost_per_return", "hi": 500.0, "kind": "max", "lo": null, "scale": 500.0}, "card:forecast_skill": {"card_id": "forecast_skill", "hi": null, "kind": "min", "lo": 0.0, "scale": 2.0}, "card:turnover": {"card_id": "turnover", "hi": 5.0, "kind": "max", "lo": null, "scale": 5.0}, "card:well_formed_rate": {"card_id": "well_formed_rate", "hi": null, "kind": "min", "lo": 0.9, "scale": 0.9}}}, {"charter_edition": 2, "index": 3, "profile": {"card:cost_per_return": 3110.0, "card:forecast_skill": -0.34101935100806696, "card:turnover": 210.2723140811534, "card:well_formed_rate": 1.0, "conformity": 0.7999999999999999, "consequence": -0.4682973644318313, "exposure": 0.0, "fast": null, "registrations": 2.0, "revision": 0.0026845637583892616, "verdict": 0.2759475218658892}, "regions": {"card:cost_per_return": {"card_id": "cost_per_return", "hi": 2500.0, "kind": "max", "lo": null, "scale": 2500.0}, "card:forecast_skill": {"card_id": "forecast_skill", "hi": null, "kind": "min", "lo": 0.0, "scale": 2.0}, "card:turnover": {"card_id": "turnover", "hi": 5.0, "kind": "max", "lo": null, "scale": 5.0}, "card:well_formed_rate": {"card_id": "well_formed_rate", "hi": null, "kind": "min", "lo": 0.9, "scale": 0.9}}}, {"charter_edition": 3, "index": 4, "profile": {"card:cost_per_return": 3390.0, "card:forecast_skill": -0.3306330460563416, "card:scripted-fills": 99.0, "card:turnover": 218.93967742745104, "card:well_formed_rate": 1.0, "conformity": 0.8, "consequence": -0.4702036564110506, "exposure": 0.0, "fast": null, "registrations": 0.0, "revision": 0.0013717421124828531, "verdict": 0.32115942028985506}, "regions": {"card:cost_per_return": {"card_id": "cost_per_return", "hi": 2500.0, "kind": "max", "lo": null, "scale": 2500.0}, "card:forecast_skill": {"card_id": "forecast_skill", "hi": null, "kind": "min", "lo": 0.0, "scale": 2.0}, "card:scripted-fills": {"card_id": "scripted-fills", "hi": 1000.0, "kind": "max", "lo": null, "scale": 1000.0}, "card:turnover": {"card_id": "turnover", "hi": 5.0, "kind": "max", "lo": null, "scale": 5.0}, "card:well_formed_rate": {"card_id": "well_formed_rate", "hi": null, "kind": "min", "lo": 0.9, "scale": 0.9}}}], "invocation_status": {"ok": 5891}, "invocations": 5891, "invocations_by_assembly": {"antagonist-a": 189, "composition-helper": 2, "eval-a": 106, "eval-b": 327, "eval-c": 1094, "eval-d": 1236, "funding-watcher": 252, "meta-a": 266, "meta-b": 470, "return-observer": 269, "seed-decider": 911, "seed-observer": 302, "web-observer": 482}, "invocations_by_role": {"child": 2, "evaluator": 2755, "meta": 731, "producer": 2383, "voter": 20}, "last_window_values": {"amendments_activated": 1.0, "amendments_proposed": 0.0, "censored_share": 0.11296845510919386, "consequence_paid_off_rate": 0.019580419580419582, "cost_per_return": 3318.7919463087246, "exposure_win_rate": 0.0, "fills": 99.0, "forecast_skill": -0.4702036564110506, "market_purchases": 0.0, "meta_verdict_mean": 0.8, "noop_share": 0.8518518518518519, "position_concentration": 2.4651490866243804, "realized_pnl_usd": -1.439776, "registration_rejections": 0.0, "registrations": 0.0, "revision_rate": 0.0013717421124828531, "scripted-fill-count": 99.0, "tool_calls": 0.0, "turnover": 218.93967742745104, "verdict_mean": 0.32115942028985506, "verdict_std": 0.37592604974253413, "well_formed_rate": 1.0}, "lots_closed": 263, "lots_opened": 265, "marked": 265, "max_settlement_latency_events": 20, "meta_verdicts": {"2": 731}, "noops": 847, "not_paid_off": 6701, "observations_registered": 1, "orders_placed": 408, "orders_rejected": 49, "paid_off": 37, "pathologies": {"learning_death": false, "stable_failure": false, "thrash": true}, "penalized_settlements": 1670, "population_tools_registered": 2, "price_skipped": 0, "price_updates": 15, "producer_returns": 3096, "reconciliations": 0, "registered_window": {"antagonist-a": 0, "composition-helper": 1, "eval-a": 0, "eval-b": 0, "eval-c": 0, "eval-d": 0, "funding-watcher": 1, "meta-a": 0, "meta-b": 0, "return-observer": 1, "seed-decider": 0, "seed-observer": 0, "web-observer": 1}, "registrations_accepted": 15, "registrations_rejected": 1, "reserve_windows": 5, "resumes": 0, "routers_replaced": 3, "sample_propensity": {"action_ids": ["antagonist-a", "seed-observer", "NOOP"], "chosen": "seed-observer", "handle": "decision-3", "probs": [0.15, 0.425, 0.425], "rng_seed": 71443919313006467319876364715437247515}, "stop_reasons": {"end_turn": 5891}, "timeouts": 0, "tool_call_failures": 1, "tool_calls": 20, "transfer_intents": 2, "upward_releases": 2397, "verdicts": 2596, "votes_cast": 20}}
.                      [ 42%]
tests/audit/test_r3_l_floor.py .........                                 [ 48%]
tests/audit/test_r3_mg_regressions.py .                                  [ 48%]
tests/runtime/test_cli_reserve.py .                                      [ 49%]
tests/runtime/test_live.py ......                                        [ 52%]
tests/runtime/test_loop.py ...........................................   [ 78%]
tests/runtime/test_wake.py ...................................           [ 98%]
tests/versioning/test_end_to_end.py ..                                   [100%]

============================= slowest 10 durations =============================
11858.93s call     tests/versioning/test_end_to_end.py::test_scripted_800_event_diaries_have_equal_summaries
2360.85s call     tests/audit/test_audit_b13_jail.py::test_scripted_world_registers_and_calls_a_population_tool_in_the_jail
1947.78s setup    tests/audit/test_a3_immune.py::test_a3_scripted_diary_agrees_with_live_flags_and_genesis
1740.90s call     tests/audit/test_a5_exposure.py::test_scripted_run_exposure_win_rate_is_below_sixty_percent
793.20s call     tests/audit/test_r3_h_scripted.py::test_readme_500_event_acceptance
596.90s call     tests/runtime/test_loop.py::test_scripted_clock_amendment_changes_next_tick_deterministically
508.40s setup    tests/audit/test_a17_wake.py::test_a17_every_section_is_present_on_the_scripted_world
499.58s setup    tests/audit/test_r3_f1_wake.py::test_a_fake_worlds_wake_reads_neither_the_venue_nor_the_reserve
478.85s call     tests/audit/test_audit_resume_wedges.py::test_overflowing_card_region_is_refused_or_unpriced_not_fatal
387.22s call     tests/runtime/test_loop.py::test_every_producer_decision_is_judged_or_censored
============== 170 passed, 2536 deselected in 22480.11s (6:14:40) ==============
```

## Final Linux service-user recovery, verbatim

Two real SIGKILL cases compared every resumed summary field and preserved the
historical ledger prefix. Three simulated accepted-write interruptions recovered
the original place, close and cancel identities with a fake venue. These ran as
`factory` under the production unit protections.

```text
============================= test session starts ==============================
platform linux -- Python 3.13.15, pytest-9.1.1, pluggy-1.6.0
rootdir: /srv/factorylab/repo
configfile: pyproject.toml
plugins: anyio-4.15.1, xdist-3.8.0
collected 5 items

tests/runtime/test_resume.py .....                                       [100%]

============================= slowest 5 durations ==============================
546.41s call     tests/runtime/test_resume.py::test_sigkill_resume_matches_every_summary_field[between_windows]
520.73s call     tests/runtime/test_resume.py::test_sigkill_resume_matches_every_summary_field[event250]
462.35s setup    tests/runtime/test_resume.py::test_sigkill_resume_matches_every_summary_field[event250]
5.07s call     tests/runtime/test_resume.py::test_live_order_process_cut_after_acceptance_recovers_original_handle[place_market]
4.23s call     tests/runtime/test_resume.py::test_live_order_process_cut_after_acceptance_recovers_original_handle[cancel]
======================== 5 passed in 1542.65s (0:25:42) ========================
```

## Final encrypted runtime restore, verbatim

This used actual `age` encryption/decryption and `rclone` copy into a local fixture
destination. It restored a deliberately interrupted runtime, retained the exact
launch manifest and acknowledged ledger prefix, excluded a torn final line, and
matched every uninterrupted summary field after normalizing only the resume
counter. All fixture credential modes were 0600. This does not establish an
operational off-host backup destination.

```text
{"restored_runtime_matches_uninterrupted": true, "exact_launch_manifest_restored": true, "actual_age_encrypt_decrypt": "passed", "actual_rclone_copy": "passed", "acknowledged_prefix_preserved": true, "partial_tail_excluded": true, "restored_ledger_authentication": "passed", "restored_fixture_key_modes": "0600", "real_account_credentials_used": false, "off_host_storage_configured": false}
```

## Final post-reboot verification, verbatim

The boot ID changed, kernel `6.8.0-139-generic` remained selected, time synchronized,
and the 8 GiB swapfile activated through `/etc/fstab`. All 391 installed source
hashes matched. Funded services/timers remained disabled and inactive; account
credential files and the funded manifest/ledger remained absent. Normal jailed
execution and socket, process and real host-file rejection passed as `factory`
under the production protections. Temporary verification helpers were removed.
Only SSH and loopback DNS listened, and no systemd units were failed.

The first immediate check returned before emitting results; startup was then
confirmed settled with synchronized time. The next attempt exposed a mistake in
the external verification harness: it called a nonexistent CLI `jail-check`
subcommand. The harness was corrected to invoke `python -m
factorylab.cortex.sandbox`, as the deployment's own script does. The complete
check then exited zero. This changed no application source. The failed command
and both service invocations remain in the retained evidence.

```text
NAME      TYPE SIZE USED PRIO
/swapfile file   8G   0B   -2
391 source hashes verified after reboot
factorylab jail-check: ok (/usr/bin/bwrap confines /opt/factorylab-python/cpython-3.13-linux-x86_64-gnu/bin/python3.13)
socket_creation: blocked
process_creation: blocked
host_files: blocked
service-user isolation checks passed
  UNIT LOAD ACTIVE SUB DESCRIPTION

0 loaded units listed.
Netid State  Recv-Q Send-Q Local Address:Port Peer Address:PortProcess                                               
udp   UNCONN 0      0         127.0.0.54:53        0.0.0.0:*    users:(("systemd-resolve",pid=584,fd=16))            
udp   UNCONN 0      0      127.0.0.53%lo:53        0.0.0.0:*    users:(("systemd-resolve",pid=584,fd=14))            
tcp   LISTEN 0      4096      127.0.0.54:53        0.0.0.0:*    users:(("systemd-resolve",pid=584,fd=17))            
tcp   LISTEN 0      4096   127.0.0.53%lo:53        0.0.0.0:*    users:(("systemd-resolve",pid=584,fd=15))            
tcp   LISTEN 0      4096         0.0.0.0:22        0.0.0.0:*    users:(("sshd",pid=943,fd=3),("systemd",pid=1,fd=91))
tcp   LISTEN 0      4096            [::]:22           [::]:*    users:(("sshd",pid=943,fd=4),("systemd",pid=1,fd=92))
Post-reboot source, swap, stopped-launch-state and service-user isolation checks passed.
```

The final host pipeline journal records 3.5 GiB memory peak and 4.2 GiB swap peak;
the separate service-user recovery unit recorded 350.5 MiB memory and no swap.
These are test-process measurements, not a production lifetime guarantee. All
391 local source hashes and the source archive hash were verified again at
13:54 UTC; `git diff --check` passed and the 58 changed files remain uncommitted.
The full journals, source integrity proof, current balances, corrected boot-check
harness and every final gate transcript are retained with the release.
