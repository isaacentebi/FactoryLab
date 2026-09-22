# Edition 5: what the architect changed, and what is still not the factory

Branch `fast-loop-harness`. Read against *The Superdark Factory*, Chapter II. Every
item names the essay's requirement, what the code did, and what it does now.

## The finding that reframed everything

PR121's scorecard (`scripts/fastloop.py score work/population-pr121/live/events.json`):
**0 of 124 producer decisions taught a learner.** Every one settled
`realized-consequence-v2-unknown`, and an unknown discarded the learning round. The
population held 215 of 248 times, judges were asked about holds they were told
not to grade, and the one real trade was voided by a malformed claim. The factory
was not failing to learn; it had no channel to learn through.

## Closed on this branch

| Essay requirement | Before | Now |
|---|---|---|
| Reward is always a score, delivered to the decision (II.I.b) | unknown → round discarded | provisional verdict stands when the world is silent; holds priced by the market |
| The world fills the payoff table (II.I.a) | holds unpriceable | a hold names its declined trade (`counterfactual`); the market prices it, net of fees, ex ante — never the best trade in hindsight, which would teach a population to trade noise; a bare hold is exactly neutral (0.5) |
| Judges look at the work like a machine (II.I.b, Yan) | judges shown the whole world, 48k chars | request, answer, executed operations, propensity; −42% |
| The signal grading an evaluator sits outside its loop (II.III) | judges graded by metas only | a verdict on a hold is Brier-scored against the market's price of it |
| Learning death prevented as a fact about the world (II.II.b) | seats declared "order: 0.1" and never ordered | `exploration_share`: the kernel draws the action class; a complied draw records the kernel's own distribution as the propensity |
| A reciprocal flow of capital (II.IV) | converted profit released a hold, minted no authority | confirmed conversion is spending authority (financing, never income), the seat's own profit first |
| Each tier graded on its own scoring (II.III) | metas' scores paid to other judges' unread verdicts | a meta's grade covers only its own judge's verdicts |
| Rich, self-describing requests (II.I.b) | "order" meant instruction and report; answers traded twice | a decision acts once; reports never execute; identical resting orders refused |

Measured on live models (fast harness, fake venue, 4 worlds × 30–40 ticks,
$0.40–0.72 a batch): learning signal 0% → 100%; malformed answers 3.1% → under 1%;
with exploration the population used every action class (build 7, order 6,
govern 5, investigate 3 beside 95 holds), where before it only held and deferred.

## In progress (separate branches, merged after review)

- **Charter prices that bite** (`fix/charter-price-bites`): blame the seat that owns a
  violation instead of splitting it across everyone; a PID controller (the essay's
  Kp, Ki, Kd) instead of integral-only decay to zero; stable failure raises the
  price with its duration instead of halving it.
- **Routers that learn** (`fix/routers-learn`): NOOP ("wake nobody") settles at the
  same neutral 0.5 as a bare hold instead of the free average of the other arms;
  a Blum–Mansour no-swap-regret router at the evaluation core; pending rewards
  carried into a replacement router.

## Still not the factory, in the order they matter

1. **An OpenRouter capital rail.** The roster runs on OpenRouter; profit converted to
   Venice credit is only spendable on a Venice route (both are on the edition 5
   menu, and a seat may move). OpenRouter sells credit for on-chain USDC; a
   reserve → OpenRouter rail would let the whole roster live on what it earns.
   Real money; built and reviewed before any use.
2. **Survival economics.** Measured burn is $0.55–0.92 a day at a ten-minute tick
   (nine seats), $2.75–4.60 at a two-minute tick. Self-funding needs roughly 0.5–0.8%
   a day on $120 of principal, or 0.05–0.09% on $1,000. Funding carry at ~20% a year
   sustains a ten-minute factory from about $1,000–1,700 of capital. Speed is a
   constraint the charter prices (II.IV): a faster factory must earn more.
3. **Governance is episodic, not a standing sortition** (II.IV.a): committees are drawn
   only when a proposal arrives, a one-seat committee can pass, and the first
   amendment waits ~180 ticks. A rotating seated committee on a measured cadence.
4. **The evaluator population is inverted** (II.III): four producers, two judges, one
   judge per return, and five of nine seats on the same model — a shared foundation
   model is the essay's "global forcing function". Route some returns to two judges;
   never to a judge on its author's model.
5. **The novelty reserve protects assemblies, not unhistoried actions** (II.II.b), and
   expires unspent. Exploration draws are exactly such actions; make them eligible.
6. **Seed prompts state kernel rules** (AGENTS.md: "physics is enforced, not
   announced") and lean the population to caution. Changing seat prompts changes the
   roster digest, so it is a re-ratification, done once, deliberately.

## How to iterate

```
uv run python scripts/fastloop.py score <events.json>                         # any diary
uv run python scripts/fastloop.py run --provider scripted --ticks 40 --seeds 1,2,3,4
uv run python scripts/fastloop.py run --provider live --ticks 30 --seeds 1,2,3,4 --exploration 0.2
```

Scripted: free, two seconds, every institution exercised. Live: real models on the
seeded fake venue with a virtual clock, several worlds in parallel, one scorecard.
Testnet (`scripts/edition4_rehearsal.py --world worlds/edition5-testnet-rehearsal.toml`)
only once the harness says the loop closes.
