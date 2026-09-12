# Wiring audit, seat 5: does it actually work, end to end?

Cold audit, round two, on `audit/wiring` at commit `0a90f06`, 12 September 2026. I read the essay, edition 1, the specs, the build log, the handoff and round one's five reports, then ran everything the population is told it can do, once each, on this macOS host and against Hyperliquid testnet, Venice and two x402 sellers. Receipts, balances and transaction hashes are in `docs/runs/audit-wiring.md` (gitignored directory, written anyway). Total on-chain spend $0.020 of the $0.50 allowed; Venice credit $0.0229; no top-up, no mainnet, no pot moved.

## Checklist outcomes

| # | Item | Outcome |
|---|---|---|
| 1 | `uv run pytest`, scripted worlds, slow resume tests | worked differently: 1685 passed, 19 skipped (4 are the jail tests); both scripted worlds run to their ends, but with 0 population tools and 0 transfer intents (F1, F4); the handoff's slow-test command exits 4, a corrected one passes 30 |
| 2 | Testnet world, 30 events, 10 s tick | worked differently: the loop kept 4 ticks in 156 s; no order intent reached the venue in this run or the 8-tick run B because every producer returned `hold`/`noop` |
| 3 | Registration and routing | worked as told for a valid proposal (registered, epoch, 61 invocations) and for a refused one (reason public in 325 of 326 later requests); did not work for a list with one mistyped field (F3) |
| 4 | Tools | did not work: no Python ran inside the jail on this host (F4); `catalogue.search` and `market.discover` worked; `treasury.transfer` cannot be called from any return (F1) |
| 5 | Compute rails | worked as told for the Venice probe, discovery, FarOuter and the Venice reseller; in a running world the routers selected the x402 rail nine times with nine settlements; the Venice rail was selected eleven times and returned nothing (F5) |
| 6 | Compute insolvency | worked as told: provider balance 0, 20 unaffordable events, `insolvency:compute`, $100 still in the wallet, seal released |
| 7 | Treasury | worked as told for the pots view; the two-phase journal read and its interruption tests pass; the population's transfer path is dead (F1) |
| 8 | Kill and resume | did not work for the handoff's own command (F2); worked as told on a plain testnet world: continuous chain, contiguous `n`, unique ids, 0 timeouts, kill-at-end honoured, exit 3 |
| 9 | `factorylab wake` | worked as told on run B; publishes the five views plus balances, positions with entry prices and gross realized P&L; all-unavailable on run A (F2) |
| 10 | `factorylab versions` | worked as told on both testnet diaries |
| 11 | `deploy/` against a fresh box | could not try; read; steps needing a human listed at the end |

## Findings, by severity

### F1. `treasury.transfer` cannot be called by the population; the only money mover is a human's CLI

**blocker · not Class 3** · `factorylab/runtime/loop.py:786` (the published `usd` schema, `"type": ["string", "integer"]`), `factorylab/cortex/assembly.py:201-217` (`_validate_schema` computes `kind not in types` on that list and raises `TypeError: unhashable type: 'list'`), `loop.py:916-928` (`_validate_output_contract` runs it on every `tool_calls` entry), `assembly.py:118-119` (any exception makes the reply `malformed`).

Essay, Chapter II.IV: "a continuous, reciprocal flow of capital is an objective requirement of any factory". The world block advertises `treasury.transfer` to every assembly and the README says the venue and reserve move "both ways by `treasury.transfer`". A producer that returns exactly the shape published (`{"action": "hold", "tool_calls": [{"tool": "treasury.transfer", "args": {"direction": "to_venue", "usd": "1", "reason": "audit"}}]}`) is recorded `malformed`, its action and every other field discarded, no `tool.call`, no `treasury.*` item, no reason anywhere. The same with `usd: 5`. The scripted world's own scripted transfer at producer call 120 is the one malformed invocation in the 500-event run (`transfer_intents: 0`); spec v0.6 §8.3's completion claim has not held since the schema became a type list. Even bypassing the validator, every testnet world refuses the tool ("treasury.reserve_address and gas budgets are not configured") because `worlds/testnet.toml` has no `[treasury]`; the only transfers ever made went through `treasury-testnet`, a CLI with a hard-coded spec that a human runs. After launch the seed credits deplete, the population cannot move venue money to the reserve, and the world ends in `insolvency:compute` with money in the venue: "died of its own liquidity management" for a lever it never had.

Fix: write the schema as `anyOf` (which `_validate_schema` supports) or a single string type; add a test that every published tool schema accepts its own example arguments through `_validate_output_contract`; give `testnet.toml` a `[treasury]` so rehearsals exercise the rail. No demotion needed.

### F2. The documented rehearsal world can be neither resumed nor woken

**serious · will break** · `factorylab/runtime/cli.py:265` (`_cmd_run` replaces `tick_interval_ns` before the manifest is hashed into the genesis), `cli.py:347-373` and `factorylab/runtime/wake.py:76-84` (`resume` and `wake` reload `worlds/<name>.toml` unchanged; neither accepts the override).

Essay, Chapter I.II: the tower becomes "a single button: kill". A process death is not that button; the handoff and the deploy README require a rehearsal of "process death, restart" on a testnet world, and the handoff's own command carries `--tick-interval 10s`. Run A (that command), SIGKILLed after four ticks: `factorylab resume` prints `recovery unavailable` and exits 1; the swallowed exception is `LedgerIntegrityError("genesis header changed")`, and the header's genesis hash equals the hash of `testnet.toml` with the tick replaced (receipts, item 8). `wake` on the same ledger reports every ledger field `unavailable` and exits 1. Run B, same manifest without the flag, killed and resumed cleanly: 219 events, `n` contiguous across the cut, every event id unique, no timeouts, `Terminated explicit_kill:budget`, exit 3. The funded unit passes no override, so the funded world is not affected; the rehearsal that is supposed to prove the funded world can survive a reboot cannot be run as documented, and a failed resume tells the operator nothing (the CLI prints one fixed line; the unit nulls stderr).

Fix: hash the manifest exactly as resolved and let `resume`/`wake` rebuild it from the genesis record (store the resolved manifest in the header), or accept the same override flags and refuse mismatches with the reason printed; or remove the override and make the tick manifest-only, which is also the essay's preference.

### F3. One mistyped field in one proposal silently voids the whole return

**serious · will break** · `factorylab/cortex/assembly.py:283-297` (`_validate_return` types every proposal field and raises), `assembly.py:118-119` (the reply becomes `malformed`), `factorylab/runtime/loop.py:2896-2933` (`_apply_registrations`, which would have refused each proposal with a reason, never runs).

Essay, Chapter II.I.b: "very difficult to learn from dynamics where the cause of an effect is unstated". A return carrying `[assembly on an unknown model, router with "add": "maybe", a valid new assembly]` produced 0 acceptances, 0 `registration.rejected` items, nothing in `registration_feedback`, and the valid assembly never existed; the whole reply sits in `raw`. The same list minus the router registers the newcomer, which is then routed to 61 times in 335 invocations; the unknown-model proposal alone is refused with `model_id must name a registered model`, visible in 325 of the next 326 requests. So the run-6 fix ("refused registrations carry a public reason") holds only for proposals that survive the typed pre-check; any wrong type (`gamma: 0`, `max_tokens: "512"`, a non-boolean `add`) kills the return, including an `order` in the same reply, with no reason. Run 8's three router proposals with `add` sent as a string show the population makes exactly this kind of slip.

Fix: drop the per-proposal typing from `_validate_return` and let `parse_proposals` refuse each proposal individually (it already does); or, when the validator trips on a proposal, record `registration.rejected` with the reason and keep the rest of the return.

### F4. Population tools do not exist on this host, and the gate does not notice

**serious · will break** (macOS); **could not try** (Linux) · `factorylab/cortex/sandbox.py:93-101` (the deny-default `sandbox-exec` profile), `sandbox.py:104-110` (`jail_available`), `factorylab/cortex/registration.py:216-217` (tool proposals refused when no jail), `tests/cortex/test_jail.py:52,69` (skips), `deploy/cloud-init.yaml:35-36` (provisioner runs the same gate).

Essay, Chapter II.I: composability means the factory can "discover available primitives, assess their contracts, and assemble them". The tool the population writes itself is the one primitive it is not given. On macOS 26.3.1 with the uv-installed CPython 3.13.11 the jailed interpreter dies with SIGABRT before `print("ok")`, with the auditor's own shell sandbox on or off; `sandbox-exec` works with `(allow default)`, and the shipped profile works once `(allow file-read*)` is added, so the profile denies a startup read the interpreter needs. Consequences: `jail_available()` is False, the world block says `population_tools: {available: false}`, every tool proposal is refused ("no jail on this host"), the scripted world's `spread-check` tool is never registered and its six calls fail, spec v0.6 §7.2's condition "one population tool registered and called" fails silently with exit 0, and the four jail tests skip so `uv run pytest` is green. Fail-closed is the right physics; the promise in the world block, the README and the specs is not kept, and nothing in the gate or the runbook says so. The bubblewrap path could not be exercised here; what a fresh Ubuntu 24.04 box must prove is listed under item 11.

Fix: make the macOS profile start the interpreter (bisect the read grants from `(allow file-read*)` down; the crash reports under `~/Library/Logs/DiagnosticReports/python3.13-*.ips` name the process); make the gate fail, not skip, when the jail cannot start; have `run` refuse a manifest whose world advertises tools when `jail_available()` is False, or state the absence in the manifest hash.

### F5. The Venice rail is selectable and paid, and on the seeded tier returns nothing

**serious · will break** · `factorylab/world/venice.py:93-131` (`_configuration` sends `reasoning_effort: none` and `venice_parameters.disable_thinking: true`), `factorylab/runtime/loop.py:2398-2411` (the `invocation` item records cost, status and stop reason but not reasoning tokens), `docs/runs/venice-proof.md` ("the current assembly does not persist provider raw metadata").

Essay, Chapter III: "a healthy and vectoralized distribution of dependencies". Venice is the rail the build names as the seed's alternative to OpenRouter ("both are on Venice at parity"). In the mixed-rail world the routers selected the Venice assemblies eleven times; every return was `{"raw": ""}` with stop reason `length`, 20,155 µUSD of credit metered for nothing, and the x402 judge marked them 0.05. Replaying the evaluator-shaped prompt directly: 8,662 input tokens, `max_tokens` 800, thinking switch sent, response `reasoning_tokens: 800`, content empty; a 6,061-token prompt on the same model answers with 35 reasoning tokens. Venice ignores the switch above some prompt size, so the seeded evaluator budgets (1,500) and producer budgets (1,000–1,200) on world-sized prompts are consumed by hidden reasoning, exactly run 1's failure on OpenRouter. The diary cannot show it: the ledger has no reasoning-token field, so a post-mortem reader sees only `malformed`/`length`. The `@none` suffix the population may register maps to the same ignored switch.

Fix: record `reasoning_tokens` and the provider's finish reason in the `invocation` item; treat `reasoning_tokens == max_tokens` with empty content as a provider fault (release the reservation or at least ledger it as such); test the switch at the prompt sizes the world actually sends before seeding a Venice tier.

### F6. The pre-merge resume gate cannot be run as written

**minor · unclean** · `docs/handoff.md` "Everyday gate" (`uv run pytest -m slow -p no:xdist tests/runtime/test_resume.py`), `pyproject.toml:35` (`addopts = "-m 'not network and not slow' -n auto"`).

Essay, Chapter I.II: "a process gate becoming a checkpoint and then a formality". The documented command exits 4 with `unrecognized arguments: -n`; the 30 slow tests pass only with `-o addopts=""`. Every merge since `-n auto` entered `addopts` either used an undocumented invocation or skipped the gate. Fix: move `-n auto` out of `addopts` or document the working command.

### F7. The pots identity is never checked in rehearsal, and the reserve is reported as 0

**minor · unclean** · `factorylab/world/treasury.py:14-36` (`provider_pots` takes OpenRouter `limit_remaining`, which is null for a key without a limit), `factorylab/runtime/live.py:214-216` (drift check needs a complete view), `factorylab/world/treasury.py:470-479` and `worlds/testnet.toml` (no `[treasury]`, so `UnconfiguredRail` reports `reserve: 0`).

Essay, Chapter I.IV: "the factory must have some kind of self-model". On the testnet manifest `refresh_pots()` returns `{'venue': 982749116, 'reserve': 0, 'seed': None, 'sellers': {'venice': 4985087}, 'complete': False, 'total_micro': None}` while the reserve holds 4.97 USDC; run B's only `Reconciled` event carries `pots_micro: None, within_tolerance: None`. The $0.50 identity check of spec v0.7 §5.1 has never run in any live run, and will not run in the funded world either if its OpenRouter key has no limit. Fix: read the key's `usage`/`limit` pair or the credits endpoint instead of `limit_remaining`; configure `[treasury]` on testnet.

### F8. The advertised clock floor cannot be kept

**minor · unclean** · `factorylab/runtime/worlds.py:450` (`min_tick` 10 s for live worlds, published as `world.clock.min_tick`), `factorylab/runtime/loop.py:1356-1400` (synchronous invocations inside the tick), `factorylab/world/market.py:75-131` (`discover` pages the whole index).

Essay, Chapter II.IV: "a factory cannot move slower than its environment". Run A managed 4 ticks in 156 s at a 10 s tick; one `market.discover` with a URL substring took 44.8 s inside the loop (about 145 index pages). The committee may amend the tick down to 10 s and get a clock the loop cannot honour; nothing tells it. Fix: publish the measured tick, or derive `min_tick` from measured invocation latency; page the discovery index by a server-side filter or cache it.

### F9. The wake shows more than balances

**minor · unclean** · `factorylab/runtime/wake.py:118-136` (`positions` with size and entry price, `realized_to_date_micro`), `deploy/README.md` "Wake schema".

Essay, Chapter I.III: read "the outcomes that this factory produces", not its interior. The published fields are exactly the five views plus `world`, `manifest_hash`, `uptime_ns`, `last_event_time_ns`, `venue {equity, positions[coin,size,entry_px], realized_to_date}`, `reserve {usdc, venice}`. Positions with entry prices are the factory's current bets, and `invocations_by_assembly` and `action_frequencies` name every assembly, including population-registered ones, so the roster's growth is readable from outside. Whether that exceeds the essay's control tower is seat 6's question; as wiring, the page contains no script, no address and no key material, and matches its documentation.

### F10. Killing the documented wrapper leaves the world running

**minor · unclean** · `README.md` "How to run it" (`uv run ...`), `deploy/factorylab.service` (`KillMode=control-group`, which is correct).

SIGKILL of the `uv run` wrapper (PID 16816) returned the shell prompt while the Python world (PID 16818) kept trading and holding the ledger lock until killed by its own PID. Operators following the README who kill the wrapper will believe a rehearsal world is dead. Note it in the README; the funded unit is unaffected.

## Areas pointed at where I found nothing

Kernel money paths in the runs: conservation and chain verification true in every world (scripted, crash, testnet A and B, mixed); the crash world died `balance_zero` from the venue with the seal released; the insolvency rule fired at exactly 20 events with the wallet intact. The x402 client: three sellers quoted and paid at the quote, settlement receipts `success: true` with the reserve as payer, nine world-routed payments each with a ledger triple and a Base hash, `compute.unaffordable` 0, no `x402.unresolved`. Resume on a plain world: chain intact, no duplicate or lost event, the saved budget and `--kill-at-end` honoured. `versions` runs in 0.2 s on both diaries. Registration's positive path and its public refusals work.

## Item 11: `deploy/` against a fresh Ubuntu 24.04 box, read only

Steps that fail or need a human, in order of the runbook:

1. `cloud-init.yaml` needs `PINNED_COMMIT` and `REPO_URL` substituted by hand; for the private repository the first clone fails by design and a human must `scp` a bundle and rerun `factorylab-provision`.
2. `worlds/funded.toml` does not exist; it must carry `[treasury] reserve_address` and gas budgets or the treasury tool is refused (and, per F1, unreachable anyway).
3. Keys, `ops.env`, the `age` identity, the `rclone` remote, the webhook receiver and the prepaid billing are all manual, as documented.
4. The provisioner runs `uv run --no-sync pytest` as root; if `bwrap` cannot start in that environment the jail tests skip and the gate is green anyway (F4). Nothing in the runbook runs `jail_available()` as the `factory` user under the unit's own restrictions (`NoNewPrivileges=true`, `ProtectSystem=strict`, `PrivateTmp`). Ubuntu 24.04 restricts unprivileged user namespaces through AppArmor; `bubblewrap`'s profile is meant to permit `/usr/bin/bwrap`, but that, `--unshare-all`, the seccomp filter and the read-only bind of `/opt/factorylab-python` must be proven on the box with a probe that fails loudly.
5. `start.sh` enters `resume` on every restart; any resume that exits 1 (a genesis mismatch as in F2, a key-mode error, a provider outage) loops with 30 s to 15 min backoff forever, emitting only `failed_resume` with no cause because stdout and stderr are nulled. A human must ssh in and run `resume` by hand to learn why.
6. The `.venv` is created by root inside the repo and used by the `factory` user; it works only because the umask left it world-readable, which nothing checks.
7. The wake timer's `TimeoutStartSec=15min` and the whole-diary read (round one's finding 13, still present) mean the hourly wake stops within weeks of a growing diary; the README says so and leaves sizing to a human.
8. Backup restore, machine replacement, disk exhaustion, credential expiry and a hung process that never exits are declared outside the covenant.

## Open, per the essay

Whether the wake's positions and per-assembly counts are within "minimal sufficient disclosure" (F9); whether a 5-minute funded tick and synchronous invocations satisfy "requisite velocity"; how a factory with no reachable treasury should be read against "the numéraire resolves upward" once F1 is fixed.

Report: `docs/audits/v2/wiring-fable.md`. Receipts: `docs/runs/audit-wiring.md`.
