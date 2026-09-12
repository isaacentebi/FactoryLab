**Seat 1 — Class 3 cold audit, round two**

Audited code: `edd6efc`; checkout `0ec4df4` differs only in `.gitignore` and the round-two brief. Verdict: the architecture still prevents Class 3 self-assembly at launch. Encryption, autonomous execution and editable charter prose do not remove that restriction.

Required reading completed, including all code and tests, subject to this seat's three explicit implementation exclusions. No other round-two report consulted; no other seat contacted. Reproductions below used in-memory fixtures, synthetic accounts and no network. No key-file contents accessed, funded manifest created, or mainnet action performed.

**1. Blocker — not Class 3: the population cannot dismantle its prescribed organization.**

Locations: `factorylab/runtime/loop.py:2182`, `Runtime._role_for_kind`; `:2294`, `_invoke`; `factorylab/cortex/registration.py:124`, `_assembly`; `factorylab/cortex/assembly.py:26`, `SEED_SYSTEM_PROMPT`.

Essay II.I, “The Primitive”: “A hard-coded pipeline of agents is literally just a waterfall”.

`ProducerReturn` always routes within the evaluator tier; verdicts route within meta tiers. Registration rejects a producer accepting evaluation events and admits only four roles. New identities can compete, but cannot replace these dispatch contracts. Children now execute, resolving the earlier inert-request defect, yet cannot execute tools or continue further. Every seed prompt announces that restriction.

Reproduction: parent requests helper; helper requests a grandchild to finish its work. `_invoke` makes exactly three model calls—parent, helper, parent—and records `child invocations answer once; no continuation`. The necessary grandchild never executes. Separately, registering a producer accepting `ProducerReturn` returns `producers do not accept evaluation events`. Thus an affordable research→analysis→execution composition cannot establish its own recursive contract graph; adding names or router alternatives cannot remove the prescribed evaluation pipeline.

Fix: retain kernel accounting, capabilities and bounded resource admission, but make work/evaluation composition registrable contracts with metered continuations and replaceable routing. Bound resources rather than permanently prescribing semantic tiers. This is the unresolved architectural core of round-one `fidelity-codex.md` #2 and `fidelity-opus.md` #6, against the partial fix.

**2. Serious — not Class 3: negotiated measurements stop at the architect's catalogue.**

Locations: `factorylab/runtime/loop.py:3082`, `_propose_amendment`; `:1676`, `_derive_regions`; `:1734`, `_close_price_window`; `factorylab/runtime/cards.py:64`, `region_for`; `factorylab/runtime/observations.py:187`, `observation_for`.

Essay II.IV.a, “Self-Writing”: “continuously add holdout test criteria”.

The population can change card names, bounds, responsibility and prices, but cannot register a measurement. Pricing recognizes only the 22 catalogue observations and five inequality forms plus the previous-median phrase. This fixes the available quantification of norms outside negotiation. Prose evaluators may discuss other quantities; the negotiated price mechanism cannot use them.

Reproduction: qualify the committee with five settled decisions per member; approve a card under the existing care norm, `observation="downside_variance"`, `acceptable_region="below 0.2"`, `lambda=0.8`. After the cadence deadline, edition 2 is adopted, but the card has no region and yields `price.unparsed` for an unknown observation. The population's approved risk criterion receives no measured penalty. Launch validation rejects this same unknown observation; amendment admission does not.

Fix: support bounded, isolated, versioned observation contracts with evidence and an executable measurement check before voting. Reject unsupported candidates until that exists. Merely expanding the handwritten catalogue repeats the architect's intervention. Round-one `fidelity-codex.md` #4 remains in this narrower form; arbitrary card IDs and responsibility binding were repaired.

**3. Serious — will break: resume accepts a different venue account as the same world.**

Locations: `factorylab/runtime/resume.py:370`, `runtime_state`, and `:407`, `restore_runtime`; `factorylab/world/exchange.py:593`, `HyperliquidExchange.__init__`; `factorylab/world/treasury.py:364`, `Treasury.snapshot`.

Essay I.IV, “How to Move in the Dark”: “visible, irrevocable, self-binding”.

Checkpoints bind adapter name and determinism, not the venue account. The exchange derives its address from current credentials. Treasury snapshots bind only the rail name; `LiveRail.__init__` (`treasury_rails.py:75`) checks the current signer against the current exchange, not the original venue identity.

Reproduction: snapshot synthetic live adapter A, then restore into B with the same manifest and adapter name `hyperliquid-testnet`, both nondeterministic. Restore succeeds despite different addresses and retains A's lot state. A changed manifest seed is correctly rejected. This is a direct restoration test, not a claim of exercised CLI recovery or a live transfer.

An operator credential replacement after a crash therefore lets subsequent polling and orders use B while learning, ownership and positions describe A. Equal balances need not trigger even the drift diagnostic (`runtime/live.py:187`, `Reconciler.snapshot`). Fix: commit public chain/account identities in genesis/checkpoints and compare them before external I/O. Credential rotation is permissible only when identity is unchanged. Round-one `defects-opus.md` #15 remains.

**4. Serious — not Class 3: measured governance separation can collapse to zero.**

Locations: `factorylab/runtime/cadence.py:46`, `GovernanceCadence.slowest_period_ns`, and `:65`, `ready`; `factorylab/runtime/loop.py:3300`, `_activate_charter_if_due`.

Essay II.IV.c, “Clocking the Factory”: “periodicity of the factory’s slowest loops”.

The purported slowest period is p90 of the last 200 completed forecast latencies, capped at the current 200-event backstop. Outstanding slow loops contribute nothing. Internal events may share a timestamp (`Runtime._process_event`, `loop.py:1356`).

Reproduction: record 200 terminal forecasts with increasing event indices but equal opening/settlement nanoseconds; queue two approved amendments. `slowest_period_ns(60e9)` returns zero. At the first hourly activation boundary, activate one; `ready` immediately admits the second at the identical timestamp. The runtime's activation `while` loop can apply both without any intervening consequence evidence. More generally, frequent short forecasts evict the slow process governance should wait for.

Fix: include outstanding consequence age and an explicit positive separation bound; require fresh elapsed consequence time after each activation. Do not identify a pooled completed-event percentile with the slowest causal loop. Round-one `fidelity-codex.md` #13 is not closed by the new measurement machinery.

**5. Serious — unclean: a charter verdict is forced to predict the wrong unit of consequence.**

Locations: `factorylab/runtime/loop.py:2582`, `_evaluator_step`; `factorylab/settlement/consequence.py:120`, `ReturnConsequences.seal_verdict`; `factorylab/settlement/lots.py:208`, `LotTable.resolve`; `factorylab/runtime/loop.py:1284`, `_mix_with_standing`.

Essay II.III.b, “Overfitting and Adversarial Populations”: “whether a given verdict predicted real downstream outcomes”.

The evaluator is asked for charter quality, then the identical number becomes `q` for profitability of lots opened by this return. Pure closing, research and tool construction necessarily receive `y=0` when they open no lot, regardless of benefit elsewhere.

Reproduction: each of three handles costs 100 micro-USD. A opens one BTC at $100; B closes it at $110; C supplies research without fills. With no fees, A resolves `y=1`, net 10,000,000 micro-USD; B and C resolve `y=0`, net zero. Judging B useful at 0.9 earns Brier 0.19; judging it 0.1 earns 0.99. That calibration enters evaluator selection at weight 0.3. Optional forecasts also enter standing, so this is a bias, not proof that profit is the sole objective.

Fix: separate charter quality from an explicitly stated payoff probability; define an immutable consequence attribution contract that can credit closing and enabling work. I reject the broad round-one dissent that a fixed financial consequence is intrinsically forbidden: II.III.b explicitly permits a consequence metric “fixed at the architect’s Stackelberg move”. The narrower quality/forecast conflation survives.

**6. Minor — unclean: card window contracts describe measurements the runtime does not make.**

Locations: `factorylab/charter/charter.py:84`, `seed_charter`; `worlds/edition1-example.toml:199`; `factorylab/runtime/loop.py:1734`, `_close_price_window`.

Essay II.IV, “The Charter and the Loop”: “precise enough to prevent overfitting”.

The 100-return and 50-forecast windows are prose. Most observations use the current novelty-reserve window; forecast skill uses cumulative standing. Reproduction: close a window containing 100 successful zero-cost returns, then one containing a successful 1,000-micro-USD return. The next cost observation is 1,000; the declared rolling last-100 mean is 10. This changes when scarcity gets penalized. Fix: encode and execute amendable window semantics, including per-evaluator sampling; at minimum make the charter truthful. Residual round-one `fidelity-codex.md` #15/#16.

**Launch inheritance and the two dissents**

The classification below concerns the committed input, not whether any chosen value is empirically adequate. Abbreviations: `loop.py`, `worlds.py`, `cli.py` and `resume.py` are in `factorylab/runtime/`; manifests are in `worlds/`; other module paths are relative to `factorylab/`.

| Inheritance and source/function | Ruling |
| --- | --- |
| `runtime/worlds.py:380`, `manifest_from_dict`, consumes both manifests. Testnet: seed 4, $100, 60s tick, BTC/ETH testnet, no drip; exchange fallbacks seed 0, $100, no shocks (`ExchangeSpec`, :59). | Allowed initial resources, environment and randomness; no instruction to trade a particular strategy. |
| Eight OpenRouter model seeds (`worlds/testnet.toml:17`, also edition1): GLM-5.3-flash .15/.50; DeepSeek-v4.1-flash .20/.60; v4-flash-0731 .065/.18; Qwen3.8-flash .15/.47; Qwen3.7-flash .03/.13; HY3 .0825/.33; GPT-5.6-luna .20/1.20; Muse-spark-1.3 1.25/4.25, USD/million input/output tokens. Reasoning respectively low/disabled/none/200 tokens/none/none/low/low; GLM Exa fast, five results, $.007. `WorldManifest.price_table`, :210. | Resource offers/reservation ceilings, allowed. These are committed manifest figures, not independently verified current prices. `_register` (`loop.py:2946`) admits catalogue models and reasoning variants; the seed menu is not permanent. |
| Nine assemblies (`testnet.toml:78`): observer GLM/1,000 tokens/MarketMid+Funding; decider DeepSeek4.1/1,200/Tick+Fill; four evaluators GLM/Qwen3.8/HY3/Luna/1,500/ProducerReturn; antagonist Qwen3.8/1,000/Tick+MarketMid; metas DeepSeek4.1/Qwen3.7/800/Verdict. All low effort, memory none. `Runtime.__init__`, `loop.py:724`. `AssemblySeed` fallback: producer, 1,024, medium, none; direct `AssemblySpec`: 2,048, medium. | Permissible seed bias if supersedable. Permanent role/composition restrictions are finding 1. |
| `cortex/assembly.py:26` seed JSON/schema/refusal prompt; `Request.prompt_text`, `cortex/request.py:46`; producer/evaluator/meta/vote descriptions in `loop.py:2499/:2582/:2754/:3180`. | Local task contracts are allowed; the fixed organization is not. Newly registered prompts are population-authored. |
| Public examples (`loop.py:1024`, `PROPOSAL_SHAPES`; :1078, `A_RETURN_MAY_INCLUDE`; :2582, `_evaluator_step`): producer/Tick, EXP3 gamma.1/replacement, tool timeout2s, card lambda.1, tick30s, ETH buy size.004, size illustration.005, wallet-up q.4; maximum four tool calls, two children, one continuation. | Examples bias initial proposals but are not required choices. Resource counts are constraints; the unalterable continuation organization is finding 1. |
| `charter/charter.py:76/:84`: truth, scarce-resource care, inquiry, revision; testnet cards cost below previous median, well-formed ≥.9, forecast skill above zero, windows 100/100/50. Edition1 adds cost≤500 and revision>.05; substitutes verdict mean≥.8 for forecast skill (`edition1-example.toml:191`). | Norms and provisional quantification, allowed: `CharterBook.activate_due` (`charter/book.py:113`) can replace/remove cards. Four root norms remain fixed; the essay permits read-only normative inputs. Failures are findings 2/6, not the mere presence of starting thresholds. |
| `PricesSpec` (`worlds.py:114`): eta .5, decay .1, lambda cap 1, minimum one event, damping kappa .5. Missing initial lambdas become zero; `PriceController.register_pending/set_price/observe` (`charter/controller.py:124/:144/:189`). | Allowed bounded pricing machinery; population can negotiate lambdas and remove cards. No immutable “maximize these five cards” instruction. |
| `NoveltySpec/EvaluationSpec/TimingSpec/ClockSpec/CommitteeSpec` (`worlds.py:125`): novelty .1/hour, three trials, $.10 trial; consequence .3, two forecasts/verdict, timeout20, coverage .5, horizon10, backstop200; timing ratio3, jitter.2, sample200; tick floor10s, derived ceiling20min; five settled decisions for eligibility, up to five committee seats (`charter/committee.py:62/:70`). | Allowed protected novelty, information/settlement and governance casts; cadence implementation fails in finding 4. |
| `ToolsSpec/TreasurySpec` (`worlds.py:90`): 50 micro/call, leverage3, routers3; insolvency20 events, discovery URL, reserve unset, both gas budgets0; fee caps $2/withdrawal$1/CCTP$.10, fake fee$.01, request cap$.50. Termination floor0, no manifest event maximum. | Capability, exposure and capital constraints, allowed. An unconfigured reserve is not evidence of autonomous replenishment. |
| `ImmuneSpec` (`worlds.py:147`): k3, bins3, TV.2, gap.8, gain.05, gamma cap.5, decay.1. `run_world` (`loop.py:3772`): EXP3 gamma.1, 200 events, drip enabled, no forced kill; `Runtime.__init__` reconciliation10, integrity verification1024. | Allowed controller/diagnostic casts and rehearsal budget. Deployment commits its own event budget. |
| `registration.py:67/:124/:170/:188`: three proposals, prompt4,000 chars, tokens16–4,096/default512, low/medium/high/defaultlow, EXP3 or Blum–Mansour, gamma.1, tool code8,000 chars/description500/timeout1–5s. `loop.py:863/:1366/:2542`: feedback8, mids20, memory3; catalogue search defaults20/cap50 (:794). | Memory/resource bounds allowed by II.II.b. Choosing the two learner families is an allowed primitive supply; freezing their semantic organization is finding 1. |
| `settlement/vocabulary.py:48/:123`, seed predicates/`Observer.observe`: wallet-up, fill, rejection, liquidation, drawdown; positive horizon, drawdown fraction[0,1]. `scoring.py:21`, Brier; kernel-only FIFO payoff in `lots.py:208`. | Allowed fixed external measurement/scoring casts. Finding 5 concerns attribution and conflation, not immutability itself. |

Public scoring dissent: `_scoring_block` (`loop.py:3389`) publishes settlement formulas, not realized counterfactual payoffs or other learners' private distributions. Producer reward is verdict minus penalty; evaluator reward is meta conformity minus penalty; terminal metas receive well-formedness; antagonist exposure is binary failure against baseline. II.I.b distinguishes public schematics from thin, delayed scores. I found no standalone demotion from publishing this contract; I compared it with `_world_block` (:1105), requests and private standing. Its consequence contract has finding 5's defect. Disclosure alone does not establish that the architect can reconstruct the evolving objective.

**Architect, darkness, time and commitment**

Seed population plus edition 1 can constitute one committed move. Drafting/rehearsals before identity creation are not subsequent interventions. `Ledger` genesis receives the canonical manifest (`Runtime.__init__`, `loop.py:580`); `_resume_runtime` (`resume.py:483`) authenticates it and restores saved configuration. Changed-manifest restoration was rejected. The concrete second-move opening is finding 3.

`deploy/start.sh:7` and `deploy/factorylab.service:17` restart the existing ledger automatically, with 30s→15min backoff and final-exit suppression. These implement a precommitted recovery policy. They do not ask the architect to comprehend and repair the factory. `deploy/backup.sh:14` and `deploy/factorylab-backup.timer:5` copy an encrypted recoverable prefix nightly. Remote machine replacement remains manual (`deploy/README.md:272`): treating that rescue as ordinary unattended survival would exceed what is implemented.

`collect_wake` (`runtime/wake.py:155`) exports five aggregate views, timing and optional account observations; `write_wake` (:229) writes presentation files. `_cmd_wake` (`cli.py:384`) has no world-control action. Its hourly timer and `deploy/alert.sh` are observation/notification, not a governor. Source inspection and wake tests found no intervention path; I did not run a key-reading wake against a ledger file. Neither the page nor encrypted logs prove epistemic darkness. I.I requires decorrelation of information and utility; I.IV makes the whole-factory self-model subject to the architect's informational limits. `_world_block` publishes a roster/contracts but no global private learner state. The prohibited prescribed assembly remains finding 1.

Prelaunch wallet/provider credit, prepaid hosting, gas budgets and a committed drip are legitimate resource endowments. `Treasury.transfer` (`world/treasury.py:97`) and `LiveRail.prepare/send` (`treasury_rails.py:234/:307`) perform population-requested main-key withdrawals without a human confirmation loop. No live transfer was tested.

OpenRouter credit is finite; Venice's exactly-$5 purchase is a CLI action (`cli.py:186`, `_cmd_reserve`), not a population tool. `VeniceProvider._request` (`world/venice.py:67`) does not automatically refill. Manual postlaunch purchases would be outside this covenant. `build_provider` (`runtime/live.py:230`) supplies the alternative x402 market, and `_register` admits sellers: lack of Venice refill alone therefore does not prove human intervention is necessary. This audit establishes no funded renewable-compute acceptance. Mainnet is not blanket-refused by the kernel: `WorldManifest.validate` (`worlds.py:314`) restricts it to the named launch world; that is a prelaunch guard.

The factory can negotiate its 60s tick within 10s–20min (`_propose_amendment`, `loop.py:3091`; `_activate_charter_if_due`, :3300). Speed itself is not a prerequisite; governance separation still fails in finding 4. `Termination.check/kill` (`kernel/termination.py:54/:68`) makes bankruptcy, explicit teardown and ledger failure terminal. Explicit teardown need not numerically drain the wallet to implement zero further token spending; it is the separately permitted destruction of this world. I found no resurrection in the termination/resume tests or scripted bankruptcy run.

**Verification and audit decisions**

Unmodified-source gate, exit 0; terminal result lines verbatim:

```text
All checks passed!
================= 1685 passed, 19 skipped in 303.30s (0:05:03) =================
```

Both requested scripted commands exited 0, with ledger verification and wallet conservation true. Scripted/500/seed1 remained alive at 59,644,062 micro-USD; crash/600/seed2 terminated `balance_zero` and released the seal. These are simulated outcomes.

Environment decision: ordinary `uv` failed on inaccessible cache and then resolver initialization. Used `uv run --no-sync` with `UV_CACHE_DIR=/private/tmp/factorylab-audit-uv-cache`, `UV_OFFLINE=1`, `UV_PROJECT_ENVIRONMENT=/Users/isaacentebi/Desktop/FactoryLab/.venv`, `PYTHONPATH=.` and `PYTHONDONTWRITEBYTECODE=1`; imports and pytest root targeted this checkout. No dependency changes or fixes. Sole authored file: `docs/audits/v2/class3-codex.md`.

Open: the essay does not determine ideal model prices, controller gains, finite hardware capacity, sufficient empirical darkness, or a uniquely correct consequence attribution. These require experiments, not invented fidelity requirements.
