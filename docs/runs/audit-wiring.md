# Receipts: cold audit round two, seat 5 (wiring), 12 September 2026

Worktree `FactoryLab-audit-wiring`, branch `audit/wiring`, audited commit `0a90f06`. Host: macOS 26.3.1 (Darwin 25.3.0), Python 3.13.11 via uv 0.9.18, `bwrap` absent, `/usr/bin/sandbox-exec` present. All times UTC. Every command was run from the worktree root so the CLI loaded the three key files itself; no key value was read, printed or copied. Hyperliquid testnet only; no mainnet pot moved; no Venice top-up. Raw logs are in the session scratchpad (`audit2/logs/`); the numbers below are copied from them.

## Money

Reserve `0x1228e5620944a79D268Afc7522E00891526EdEBb` on Base (chain 8453), read with `factorylab reserve status` (SIWE-signed balance reads, no payment).

| When | USDC | Venice credit | Note |
|---|---|---|---|
| 07:00:27 before anything | 4.989000 | 4.999768 | matches `docs/runs/compute-proof.md` |
| 07:01:58 after the Venice probe | 4.989000 | 4.999631 | −$0.000137 credit |
| 07:02:23 after the two x402 probes | 4.978000 | 4.999631 | −$0.011 USDC |
| 07:10:25 after the mixed-rail world | 4.969000 | 4.979480 | −$0.009 USDC, −$0.020151 credit |
| final (below) | see §Final balances | | |

On-chain USDC spent, total **$0.020** (cap was $0.50):

| Payment | Amount | Base transaction |
|---|---|---|
| FarOuter `glm-5.3-flash`, `factorylab probe --provider x402 --seller https://farouter.tech --model glm-5.3-flash --max-cost-usd 0.005` | $0.001000 | `0x3ce16da35f87d870eccfc01afb77e7089fa7ae92d69ca4ec8eef7a182eeacc2d` |
| aispace (Venice reseller) `z-ai-glm-5-3-flash`, `factorylab probe --provider x402 --seller https://x402.aispace.bot/api --model z-ai-glm-5-3-flash --max-cost-usd 0.02` | $0.010000 | `0x668da50c18f8c970478551da6f7e14754106d63e03234283d44a74bed0d6e572` |
| mixed-rail world (below), 9 FarOuter requests routed by the world's own routers, $0.001 each, ledger items `x402.result` with handles decision-3, 7, 11, 16, 16, 27, 34, 50, 54 | $0.009000 | `0xe22ee539c407f89dffab23a08e58c41e54687de935a9abffdc1b6bcf62379748`, `0x270d7e3edfd0920911fd845d1e27468b44f8ce27f0e374798432f8db8ab81277`, `0x489ad1c4fccc30a9a8eaa6949738fabb25c3fe2f3beff17db6392fff1cbdf5df`, `0x13f8d482bad70d2f23dee843f1df85e23881714f90efafcc1c1e56d036436da8`, `0x3d7109f9f5350d7258e03b53cf2d97054505f1cd154cbe7a7627d6b4ab30dabc`, `0x5689a2c9edce2724fc90ded9cafe0626f6449ccfcde09ffb3f956da2620e5835`, `0x0a88e75c88b78e07d9832d58568ad6c3d85638cd5c21b9055bc2b5fa8649f04a`, `0xac0a5348ea348072cf8ff717862e7fef0dc42598e8fb6c86e746860b72ac7501`, `0x001653309ac27a3eb2db6888e5e4927ffff599295f836ed38a8af9958ac06b58` |

Venice prepaid credit consumed: probe $0.000137 (request `chatcmpl-d37706283e68649713e47463d4e6b7fe`, 901 in / 3 out, reply "OK", cost source `reported`); mixed world $0.020155 (11 metered invocations, all empty, see item 5); two diagnostic calls $0.002631 (931 + 1700 micro). OpenRouter seed credit consumed: run A $0.026638, mixed world $0.007662, run B see below. No OpenRouter top-up, no Venice top-up, no treasury transfer.

## Checklist, each item once

### 1. Gate, scripted worlds, slow resume tests

- `uv run pytest`: **1685 passed, 19 skipped in 338 s**, exit 0. Four of the skips are `tests/cortex/test_jail.py` ("host cannot launch an OS jail"): the gate is green on this host without ever executing population code in a jail.
- `uv run factorylab run --world scripted --events 500 --seed 1`: 14,081 events, 4,886 invocations (4,880 ok, **1 malformed**), 425 orders placed, 395 fills, 5 registrations accepted, 2 rejected, 2 routers replaced, 13 tool calls of which **6 failed**, **0 population tools registered, 0 transfer intents**, 14 price updates, 1,660 penalised settlements, charter edition 2, wallet 59.644062, conservation true, verify true. Exit 0.
- `uv run factorylab run --world scripted-crash --events 600 --seed 2`: terminated `balance_zero`, wallet −3.481919, seal released, conservation true, verify true. Exit 0.
- A second scripted run with a ledger (`--events 200 --kill-at-end`) and `postmortem` shows why the counters above are what they are: `sandbox.availability {"available": false}` at seq 21; `registration.rejected ... "no jail on this host"` for the scripted `spread-check` tool proposal; every later `spread-check` call `ok: false`; no `treasury.*` transfer item at all. The single malformed invocation in the 500-event run is the scripted `treasury.transfer` call at producer call 120 (see item 4).
- Slow resume tests as the handoff prescribes, `uv run pytest -m slow -p no:xdist tests/runtime/test_resume.py`: **exit 4, "unrecognized arguments: -n"** (`pyproject.toml` `addopts` carries `-n auto`, so disabling the xdist plugin breaks the command). With `uv run pytest -m slow -o addopts="" -p no:xdist tests/runtime/test_resume.py`: **30 passed in 100 s**.

### 2. Testnet world, 30 events, 10-second tick (run A)

`uv run python -m factorylab.runtime.cli run --world testnet --events 30 --seed 4 --tick-interval 10s --ledger runs/audit-testnet.jsonl --kill-at-end`, started 07:03:07, SIGKILLed at 07:05:43 for item 8 (see there). In 156 s of wall clock the world reached only **4 ticks** (indices 0–3): each tick's synchronous model calls take longer than the 10 s interval, so the clock lags. Diary (`read_diary` verifies the chain): 1,009 items, events Launch 1, Tick 4, MarketMid 6, Funding 6, ProducerReturn 15, Verdict 8, ForecastSettled 20; 18 invocations (15 ok, 3 malformed; 2 stopped on `length`); spend 26,638 µUSD; producer actions `hold` 5, `noop` 2, one unparsed; **no `order` action, no venue tool call, no `order.*` item, no Fill, no OrderRejected, no registration**. No order intent reached the venue because no producer emitted one. The venue account still carried run 8's open positions (BTC −0.005 at 77,038; ETH 0.005 at 2,491.6) throughout. `Reconciled`: none (needs 10 ticks). Wallet last balance 99.973362.

### 3. Registration

Driven in the scripted world with an auditor-scripted model (`harness_item3_registration.py`, in-memory ledger, seed 7, 60 events):

- One valid assembly proposal (`audit-newcomer`, producer, `fake-haiku`, accepts Tick+MarketMid): `Registered` event `{'id': 'audit-newcomer', 'by': 'decision-1', ...}`, `epoch` items for Tick and MarketMid, routers `router:Tick` and `router:MarketMid` at epoch 2 with the newcomer in their universes, **61 invocations of `audit-newcomer` out of 335**, `novelty.invocation` counts 1, 2, 3 on its first three calls.
- One proposal naming an unknown model: `registration.rejected {'handle': 'decision-1', 'reason': 'model_id must name a registered model', 'index': 0}`; the reason appeared in `world.registration_feedback` of **325 of the 326** later requests.
- A `register` list of three proposals `[assembly with unknown model, router with "add": "maybe", the valid newcomer]`: **0 accepted, 0 rejected, no `registration.rejected` item, no feedback, the newcomer never registered**; the invocation was recorded `malformed` with the whole reply in `raw`. `cortex/assembly.py::_validate_return` raised `add must be a boolean` for the second proposal, which voids the entire return.

### 4. Tools

- Jail: `sandbox.jail_available()` is **False** on this host; `run_python('print("ok")')` returns `returncode=-6` (SIGABRT, crash report `python3.13-*.ips`, `EXC_CRASH SIGABRT`, no stderr), and so does every other probe (algorithm, file read, socket, fork, allocation). Re-run with the auditor's own shell sandbox disabled: identical. Bisection of the shipped `sandbox-exec` profile: adding `(allow file-read*)` (read the whole filesystem) makes the base interpreter start (`rc=0 out='ok'`); adding only `/usr/lib`, `/System`, `/private/var/db/dyld`, `/dev`, `/Library` does not. `sandbox-exec` itself works with `(allow default)`. So on macOS 26 the deny-default profile denies something the uv-installed CPython reads at startup and the jail never opens. Consequences seen: `world.population_tools = {"available": false, "reason": "no jail on this host"}`, every tool proposal refused, `tests/cortex/test_jail.py` skipped. **Could not run any algorithm inside the jail on this host.** The bubblewrap path was not verifiable (no Linux box).
- `catalogue.search` from a producer handle on the testnet manifest (`Runtime(..., events=0)`, `_run_tool`): `{"substring": "glm-5.3", "limit": 6}` → 6 models with prices and context length (OpenRouter and the `:online` variant), cost 50 µUSD, 0.9 s; `{"substring": "venice:z-ai", "limit": 4}` → 4 Venice models, 50 µUSD. Worked.
- `market.discover` from the same handle: `{"url_substring": "farouter", "limit": 2}` → 2 FarOuter resources with Base/Solana `accepts`, 50 µUSD, **44.8 s** (the client pages the whole 14k-resource index at 100 per page until it finds `limit` matches); `{"query": "chat/completions", "limit": 3}` → 3 sellers in 4.5 s. Worked; the world's clock stalls for the duration.
- `treasury.transfer` from a return: a producer reply `{"action":"hold","tool_calls":[{"tool":"treasury.transfer","args":{"direction":"to_venue","usd":"1","reason":"audit"}}]}` (the shape the world block publishes) is recorded **`malformed`**, 0 tool calls, 0 transfer intents; `_validate_output_contract` raises `TypeError: unhashable type: 'list'` because the published schema types `usd` as `["string","integer"]` and `_validate_schema` cannot evaluate a type list. Same with `usd: 5`. Calling `_run_tool` directly (bypassing the validator) on the testnet manifest returns `{'status': 'refused', 'error': 'treasury.reserve_address and gas budgets are not configured'}`: `worlds/testnet.toml` has no `[treasury]`, so the rail is `UnconfiguredRail`.
- Treasury read-only views on the testnet manifest: `wallet.pots()` before any refresh `{'venue': None, 'reserve': None, 'seed': None, 'sellers': {}, 'complete': False}`; `treasury.refresh_pots()` → `{'venue': 982749116, 'reserve': 0, 'seed': None, 'sellers': {'venice': 4985087}, 'pending': False, 'complete': False, 'total_micro': None}`. `seed` is None because OpenRouter's `/key` reports no `limit_remaining` for this key, so the view is never complete and the reconciler's $0.50 identity check cannot run; `reserve` is reported 0 by the unconfigured rail while the reserve actually held 4.97 USDC.

### 5. Compute rails

- Venice, paid from prepaid credit: `uv run factorylab probe --provider venice --model venice:z-ai-glm-5-3-flash` → `{"text": "OK", "input_tokens": 901, "output_tokens": 3, "cost_micro": 137, "cost_source": "reported", "request_id": "chatcmpl-d37706283e68649713e47463d4e6b7fe"}`; balance 4.999768 → 4.999631 (SIWE-signed reads before and after). Worked as told.
- `uv run factorylab market discover --url-substring farouter --limit 5`: FarOuter resources with `exact`/`upto` USDC quotes on Base and Solana. Worked.
- FarOuter and aispace paid for cents: table above; both replies "OK", settlement receipts `success: true`, payer = the reserve address. Worked as told.
- In a running world: a scratch manifest (`audit-mixed.toml`, fake venue, 20 fake USD, three tiers: `z-ai/glm-5.3-flash` on OpenRouter, `venice:z-ai-glm-5-3-flash`, `x402:https://farouter.tech#glm-5.3-flash`; six assemblies across the three rails; `treasury.max_request_micro = 20000`), `uv run factorylab run --world <path> --events 6 --seed 11 --ledger … --kill-at-end`: 62 events, 38 decisions, 25 invocations, conservation and verify true, seal released. The provider is chosen per request by namespace in `world/market.py::MultiProvider._provider` (`x402:` → `X402Provider`, `venice:` → `VeniceProvider`, else OpenRouter) and per assembly in `runtime/loop.py::_instantiate` (`x402:` ids get `_ObservedX402Model`). Ledger evidence of non-OpenRouter completions: 9 `x402.quote`/`x402.submitted`/`x402.result` triples (1,000 µUSD each, settlement hashes above), `market.registered` for the seller at 1,000 µUSD per request (`price_source: x402-quote`), `invocation` items `served_by x402:https://farouter.tech#glm-5.3-flash` (7 evaluator, 1 producer, all `ok`) and `served_by venice:z-ai-glm-5-3-flash` (11, **all `malformed`, `{"raw": ""}`, stop `length`**, 20,155 µUSD metered). Replaying the evaluator-shaped prompt directly on Venice: 8,662 input tokens, `max_tokens 800`, `reasoning_effort: none` and `venice_parameters.disable_thinking: true` sent, response `reasoning_tokens 800`, content empty, finish `length` (the thinking switch is ignored on that prompt size; a 6,061-token prompt answered with 35 reasoning tokens). The x402 judge marked the empty Venice returns 0.05.

### 6. Compute insolvency

Scripted world, auditor provider whose `balance_micro()` returns 0 (`harness_item6_insolvency.py`, seed 3): every candidate excluded `compute: provider balance 0 below ceiling …`, routers choose NOOP, `compute.route unaffordable: true` on 20 consecutive events, `treasury.insolvency consecutive_events 20`, **terminated `insolvency:compute` at event 20 with the wallet untouched at 100.000000 and the seal released**. Control with `balance_micro()` None: 3,095 events, no exclusions, no termination. Worked as told; a world with an affordable x402 seller is not reachable in the scripted world (no seller), only in the mixed world above (`compute.unaffordable 0`).

### 7. Treasury

- `uv run factorylab treasury-testnet status` (no journal, read-only): `{"reserve_address": "0x1228e5620944a79D268Afc7522E00891526EdEBb", "networks": [998, 84532], "venue": 986311616, "venue_available": 943572216, "reserve": 0, "hyperevm_reserve": 0, "hype_wei": 49967511900000000, "base_sepolia_eth_wei": 997972189846042}`. Worked (testnet, nothing moved).
- Two-phase journal after interruption: read `world/treasury.py` (`submitted` → `broadcast` → `pending`/`step_confirmed`/`advance`/`step_submitted` → `confirmed`/`failed`; holds for principal and fee ceiling; `snapshot`/`restore` carry hold ids and never rebroadcast) and the tests that cut the process mid-transfer: `tests/runtime/test_treasury_acceptance.py` (pending checkpoint restores holds and confirms once; crash during confirmation replays bookkeeping once; lost acknowledgement retries the same reference; crash while booking native gas), `tests/runtime/test_resume.py::test_fake_treasury_trading_shock_replays_fee_unfunded_cut`, `tests/world/test_fc_treasury.py`. All pass in the gate. Not driven live; the population's own path to the treasury is dead (item 4).

### 8. Kill and resume on testnet

- Run A (the handoff's command, `--tick-interval 10s`): SIGKILL at 07:05:43 (the `uv run` wrapper died first at 07:05:30 and the Python child kept running; killed by PID). `uv run factorylab resume --world testnet --ledger runs/audit-testnet.jsonl` → **`factorylab resume: recovery unavailable`, exit 1** in under a second. Reproduced the swallowed exception: `Ledger.reopen` raises `LedgerIntegrityError("genesis header changed")`. The ledger header's `genesis_hash` `690b6cb2…` equals the hash of `testnet.toml` with `tick_interval_ns` replaced by 10 s; `worlds/testnet.toml` as loaded by `resume` hashes to `d9daa8b9…`. `resume` and `wake` have no `--tick-interval`. The world is unresumable.
- Run B (same manifest, no override, `--events 8 --seed 4 --ledger runs/audit-testnet-b.jsonl --kill-at-end`, 60 s tick): started 07:09:57, SIGKILL of the Python process at 07:12:50 after 3 ticks (file ended on a complete record); `factorylab resume --world testnet --ledger runs/audit-testnet-b.jsonl` started 07:13:24. Result: see §Run B below.

### 9. `factorylab wake`

- On run A's ledger: exit 1, every ledger-derived field `"unavailable"` (the genesis matches no manifest in `worlds/`), `venue` all `"unavailable"`, `reserve {"usdc_micro": 4970000, "venice_micro": 4979480}`.
- On run B's ledger: see §Run B.

### 10. `factorylab versions`

On run A's diary (`uv run factorylab versions runs/audit-testnet.jsonl runs/audit-testnet.jsonl.key`, 0.2 s): "Factory versions: 1 across 5 windows; Spectral gap lower bound (Dobrushin): 0; delta=1; empirical mixing TV=1; Version 0..4: 5 windows, charter 1; Pathology evidence: none"; EWS lines for verdict, conformity, consequence, balance, disagreement (most `unsupported` at 6w/12w). Worked as told. Run B: see §Run B.

### 11. `deploy/` against a fresh Ubuntu 24.04 box

Read only; see the report. Not tried.

## Run B

`factorylab resume --world testnet --ledger runs/audit-testnet-b.jsonl` continued the world from the launch snapshot plus tail replay: `resume.begin {n: 52}`, `resume.timeouts {handles: []}`, `resume {resumes: 1}`, then ticks 3–7, one `Reconciled` event, `Terminated {reason: explicit_kill:budget}` (the saved `--kill-at-end` was honoured), **exit 3** (the deploy contract's "terminated" code). Summary: 219 events, 89 decisions, 62 invocations (58 ok, 2 malformed, 2 failed), 27 NOOPs, 40 producer returns, 26 verdicts, 16 conformities, 25 censored, 99 forecasts sealed and settled, 6 `MetaVerdict`, 0 orders, 0 fills, 0 registrations, 0 tool calls, `resumes: 1`, `reconciliations: 1`, wallet 99.882109, spend 117,891 µUSD, conservation true, verify true, seal released. Diary continuity (`read_diary` verifies the hash chain): 3,237 items with contiguous `seq`; `runtime.event_done` n = 1..219 contiguous across the cut; every event id unique; Tick indices 0..7 with no gap or repeat; the killed process's last record was complete (no `ledger.repaired`). The only `Reconciled` payload: `openrouter_remaining_micro: None, pots_micro: None, discrepancy_micro: None, within_tolerance: None` (item 4's incomplete pots view). Producer actions across the run: `hold` 14, `noop` 5, 2 unparsed; no order intent reached the venue, so no fill, in either testnet run.

- `factorylab wake --ledger runs/audit-testnet-b.jsonl --out …`: exit 0. Fields published, exactly: `action_frequencies, invocations_by_assembly, last_event_time_ns, manifest_hash, reserve, settlement_latency, spend_by_capability, uptime_ns, venue, wallet_series, world`. `world testnet`, `manifest_hash 61ab7817…`, `uptime_ns 608423949000`; `venue {equity_micro: 982717616, positions: [{BTC, -0.005, 77038.0}, {ETH, 0.005, 2491.6}], realized_to_date_micro: -12250}`; `reserve {usdc_micro: 4969000, venice_micro: 4976850}`; the five views carry per-assembly spend and invocation counts by assembly id and the router action counts (assembly ids and the `q` buckets 0.0–1.0). `wake.html` 9,883 bytes, no `<script>`, CSP present, no address or key material.
- `factorylab versions runs/audit-testnet-b.jsonl runs/audit-testnet-b.jsonl.key`: "Factory versions: 12 across 16 windows; Spectral gap lower bound (Dobrushin): 0; delta=1; empirical mixing TV=0.875", versions 0..4 then one window each, charter 1, pathology evidence none. Worked as told.

## Final balances

07:22 UTC: reserve 4.969000 USDC (from 4.989000: **$0.020 spent on-chain, all listed above**), Venice credit 4.976850 (from 4.999768: $0.022918 consumed), ETH 0. OpenRouter seed credit consumed by the three worlds: 26,638 + 7,662 + 117,891 = 152,191 µUSD (about $0.15). Ledgers left on disk (gitignored): `runs/audit-testnet.jsonl` (unresumable, item 8), `runs/audit-testnet-b.jsonl` (terminated), both with their `.key` files; the scratch worlds' ledgers are in the session scratchpad.
