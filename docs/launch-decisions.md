# Launch decisions, in plain language

Written for the experimenter, who is not technical, and for any agent who picks this up later.
Everything here is the state on `main` at commit `f8cd839` or later. The code is finished; the
gate is green; there are no more audits. What remains is a small set of decisions, then a
sequence of hands-on steps. Each decision below says what it is, what it changes, the options,
and a recommendation. The recommendation is not the only sane choice; it is the one to take
if nothing else is decided.

## The money, and how it drains

There are two separate pots of real money and two small reserves.

| Pot | Where | Now | What it is for |
|---|---|---|---|
| Thinking money | OpenRouter credit, spent through `openrouter.key` | about $92 | every model call the population makes |
| Trading money | Hyperliquid mainnet perps, `hyperliquid.key` | $100 USDC | positions the population opens |
| Base reserve | USDC on Base, `reserve.key` | about $4.97 | x402 payments for paid data (agents' on-chain spend cap is $0.50) |
| Venice reserve | Venice credit | about $4.98 | an alternative model seller; agents can never top it up |

Inside the world there is one ledgered wallet, `initial_balance_usd` in the manifest, that
meters thinking spend. The world dies when that wallet hits `balance_floor_usd`. The OpenRouter
credit must always cover the wallet, so keep OpenRouter credit at or above `initial_balance_usd`.

Measured on testnet after the prompt fix (`docs/audits/v3/recheck2.md`): about nine model calls
per tick, $0.0035 per call, so about **$0.032 per tick**. The tick interval is therefore the
main cost dial:

| Tick | Ticks per day | Thinking spend per day | A $100 wallet lasts (no earnings) |
|---|---|---|---|
| 2 min (current testnet) | 720 | about $23 | about 4 days |
| 5 min | 288 | about $9 | about 11 days |
| **10 min** | 144 | **about $4.60** | **about 3 weeks** |
| 20 min (the maximum allowed) | 72 | about $2.30 | about 6 weeks |

Two facts to hold onto. First, the cost of learning is per call, not per tick: a slower tick
does not buy more thinking per dollar, it spreads the same money over more days. Second, a
slower tick does give each call more information, because real prices move more between
decisions; at two minutes BTC has barely moved since the last look. That is the real argument
for slowing down.

Every clock inside the world is counted in events, not minutes. Six world events arrive per
tick. So slowing the tick stretches every horizon by the same factor: the 100-returns-per-role
window that priced the cost card in about three hours at a two-minute tick takes about fifteen
hours at ten minutes; the 60-event consequence horizon becomes about 100 minutes. Nothing else
needs to change for that.

The largest tick the kernel allows is the novelty window divided by three. The novelty window
is `1h` in the manifest, so the maximum tick is 20 minutes. To go slower, raise
`[novelty] window` as well.

## Decision 1: tick interval

**Recommendation: 10 minutes** (`tick_interval = "600s"`). It keeps the thinking wallet alive
for about three weeks before any earnings, lets markets move between decisions, and keeps the
governance loop inside a day. Five minutes if you want to see results faster and accept an
eleven-day runway. Do not launch at two minutes.

Where: `worlds/funded.toml` (created from `worlds/edition1-example.toml`, see the steps),
line `tick_interval`. Leave `consequence_backstop_events = 60` and
`forecast_horizon_events = 10` as they are.

## Decision 2: thinking wallet size

**Recommendation: $100** (`initial_balance_usd = "100"`), matching the OpenRouter credit. If
you top OpenRouter up, you may raise the wallet to match; never the other way around. A bigger
wallet buys days, not a different experiment.

## Decision 3: trading pot and the death floor

**Recommendation: keep the $100 USDC already on Hyperliquid mainnet, `max_leverage = 3`,
`balance_floor_usd = "0"`.** The floor is the balance of the thinking wallet at which the world
dies; zero means they die when they can no longer buy a thought, which is the essay's design.
Raising the floor (say to `"5"`) ends the world a little earlier and leaves a receipt; it does
not protect the trading money. The trading money is protected only by leverage and by the
population's own care, which is the point.

## Decision 4: the roster (which models think)

The seeds are all the cheapest flash tier on purpose: the population is meant to buy better
thinking itself, and the `[[models]]` table is the menu it can buy from. Meta Muse Spark
($1.25 per million input tokens, eight times GLM) is on the menu for exactly that reason.

Current seats after removing Tencent: two producers (GLM 5.3 flash, DeepSeek 4.1 flash), four
evaluators (GLM 5.3 flash, Qwen 3.8 flash, DeepSeek 4.1 flash, GPT 5.6 luna), one antagonist
(Qwen 3.8 flash), two meta judges (DeepSeek 4.1 flash, Qwen 3.7 flash).

Open questions, with recommendations:

- **eval-b on Qwen 3.8 flash.** It returned six OpenRouter errors in the first re-check and was
  clean in the second. Two options: leave it, or pin the seat to one upstream provider through
  OpenRouter's provider routing so it stops bouncing between sellers. **Recommendation: leave
  it for edition 1.** Errors count against the well-formed rate, the charter prices that, and
  the population can reseat it. If you want to pin it, that is a manifest field on the
  `[[models]]` entry, and an agent should check the exact OpenRouter field name before adding it.
- **A Muse Spark seat.** At a third of a cent per call it is affordable now; one evaluator
  seat on it would cost about $0.03 per call. **Recommendation: no seeded seat.** Leave it on
  the menu and let a producer register it if it judges the price worth paying. Seeding it is
  the architect picking the winner.
- **Reasoning budgets.** `max_tokens` per seat (1000 to 3000) held in the second re-check with
  zero length stops. Leave them.

## Decision 5: the prompts

There is very little prompt to decide. The only text the architect writes is the seed system
prompt, about 480 characters in `factorylab/cortex/assembly.py` under `SEED_SYSTEM_PROMPT`,
which says what an assembly is and how to answer. Everything else an agent sees is disclosed
by the kernel from the manifest: the charter cards and their current prices, the mechanics,
the tool catalogue, the account, recent prices, the request itself. The manifest cannot set a
per-seat prompt; registered assemblies may carry their own, written by the population.

**Recommendation: leave the seed prompt as it is.** It was rehearsed. The one thing worth a
read before launch is that it does not promise anything the kernel no longer does (it should
not mention the venue listing being in the prompt; it does not).

## Decision 6: charter edition 1

`docs/charter-explained.md` sets the cards against the essay: why they are thin on purpose, how each can be gamed and what catches it, and why a card is a price rather than a wall.

The charter is the population's constitution: four norms and a set of cards, each card a
measurable observation with an acceptable region, priced by the controller. Edition 1 as
drafted in `worlds/edition1-example.toml` is what the closure review judged against.
**Recommendation: ship it as drafted, with two checks:** the `well_formed_rate` region stays
at "at least 0.9" (the second re-check measured 0.94), and every card's window is one the
population can actually close at a ten-minute tick (a 100-returns-per-role window closes in
about fifteen hours; that is acceptable).

## Things that do not change after launch

The architect makes one move, the manifest, and then never intervenes. The only control is
`factorylab kill`. No top-ups mid-run, no reseating mid-run, no prompt edits mid-run. If the
world dies, the post-mortem is the result, and a second edition is a new world.

## The steps, in order

1. **One clean testnet hour.** Nothing else running on the machine. This confirms the tick
   holds at its declared interval (the second re-check could not, because the test gate was
   loading the machine) and closes the first price window so T47's card value is finally
   measured. Run it with the tick you chose in Decision 1 set in `worlds/testnet.toml`:

   ```bash
   uv run factorylab run --world testnet --duration 70m --ledger /tmp/final-rehearsal.jsonl --kill-at-end
   ```

   Then `uv run factorylab report /tmp/final-rehearsal.jsonl /tmp/final-rehearsal.jsonl.key`.
   You want: ticks arriving on time, a `price.window` item with `well_formed_rate` at or above
   0.9, zero orders (testnet has no real trading money), and thinking spend near $0.032 per tick.

2. **Write `worlds/funded.toml`.** Copy `worlds/edition1-example.toml`, then set:
   `name = "funded"`, `tick_interval` per Decision 1, `initial_balance_usd` per Decision 2,
   `[exchange] mainnet = true`, and keep an explicit `[charter]` section (mainnet refuses any
   manifest without one, and refuses any name other than `funded`). Remove the Tencent model
   if the copy still has it (main's copy already does not). Do not create this file until
   step 1 is clean; its existence is the launch gate.

3. **Validate it and record its hash.**

   ```bash
   uv run factorylab manifest worlds/funded.toml
   ```

   Save the printed hash in `docs/handoff.md`. That hash is the sealed identity of the world.

4. **First-move review.** Read the manifest once as the architect: roster, tick, wallet, floor,
   charter cards, model menu. This is the one move. After it there are no edits.

5. **Provision the droplet** from the pinned commit, following `deploy/README.md`. You place
   the three key files yourself at the repository root with mode 0600; no agent ever reads,
   prints or copies them. Install bwrap for the tool jail. Confirm OpenRouter credit is at or
   above the wallet.

6. **Launch** on the droplet:

   ```bash
   uv run factorylab run --world funded --ledger ledger/funded.jsonl
   ```

   Then leave it alone. `factorylab wake` reads the world; `factorylab kill` ends it.

7. **Watch, do not touch.** Read the observatory page and the ledger. If the population dies,
   `factorylab postmortem` decrypts the diary with the released key.

## Small loose ends (not blocking)

- `tests/cache_contract_check.py::test_marker_partition_includes_transitive_fixtures` fails on
  main and the gate never collects it. Fix or delete, five minutes, any agent.
- Prompt caching hits on DeepSeek and Qwen 3.8, rarely on GLM, never on GPT 5.6 luna. That is
  the sellers' behaviour, not ours; nothing to do unless cost matters more later.
- The rendered prompt is not stored in the ledger, only its hash. If you ever want to audit
  what an agent saw, a prompt digest or body would need to be recorded. Not needed for launch.

## Decisions taken (interview on 13 September)

Applied to both `worlds/testnet.toml` (for the clean one-hour rehearsal) and the edition 1
draft `worlds/edition1-example.toml`, which becomes `worlds/funded.toml` at step 2.

| Decision | Taken | Where |
|---|---|---|
| Tick | 10 minutes | `tick_interval = "600s"` |
| Thinking wallet | $90, inside the $92 OpenRouter credit that cannot be refilled; more thinking is bought through Venice from trading profit | `initial_balance_usd = "90"` |
| Death floor | $0 | `balance_floor_usd = "0"` |
| Markets seeded | BTC and ETH perps only; the draft's testnet spot pair removed. Any listed perp or spot pair is one proposal away (`{"kind": "market", "coin"}` or `{"kind": "market", "pair"}`, one novelty trial); the disclosure already shows the form and points at the full listing | `[exchange] coins`, no `[venue]` block in the draft |
| eval-b | Meta Muse Spark replaces Qwen 3.8 flash as the fourth evaluator (about a dollar a day extra) | `model_id = "meta/muse-spark-1.3"`, `max_tokens = 1500` |
| Muse Spark otherwise | stays on the menu for the population to buy | `[[models]]` |
| Cost card | at most a quarter of a cent per answer (2,500 micro-dollars); the draft's 500 was unreachable by every seat | `model_cost_efficiency.acceptable_region` |
| Consequence horizon | 60 events in the draft, matching testnet (the draft had the 200 default) | `consequence_backstop_events = 60` |
| Seed prompt | unchanged | `factorylab/cortex/assembly.py` |
| Cost rule style | fixed line, a fine not a wall: expensive thinking pays in proportion and can still be worth it | `acceptable_region = "at most 2500"` |
| forecast_skill card | added back from the seed charter: evaluators' settled forecasts must beat the base rate; gives "useful inquiry" a card and is the one measure words cannot hack | fifth `[[charter.cards]]` |
| Trading card | none; the wallet alone judges trading | |
| Norms | the four kept, read-only for the population | `[charter] norms` |
| Other three cards | unchanged | `[[charter.cards]]` |

Testnet keeps its own spot pair and the seed charter; only the tick, wallet and eval-b seat
changed there, so the rehearsal prices the same calls the funded world will make.

### How a tick wakes them, in plain words

Nothing happens between ticks. Every ten minutes the kernel takes one snapshot: a `Tick`
event, then the current price of each seeded market (one `MarketMid` each) and the current
funding rate of each seeded perp (one `Funding` each). Six events at launch. Each seat
declares which events it wakes on: the decider wakes on the tick and on fills, the observer on
prices and funding, the antagonist on the tick and prices, evaluators whenever a producer
answers, meta judges whenever an evaluator answers. A wake is a chance to think, not an
obligation: most wakes end in a noop that costs nothing. Prices moving between snapshots wake
nobody; a seat can look them up with the venue read tools if it is already awake. A registered
market joins the snapshot from the next tick.
