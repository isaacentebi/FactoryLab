# Fidelity audit — fidelity-fable

Cold read of FactoryLab against *The Superdark Factory*. Worktree `audit-fidelity-fable`.
Gate reproduced green (`ruff` clean, full `pytest` passed); `scripted` (500 ev) and
`scripted-crash` (600 ev) run to their expected ends with conservation and hash‑chain
verify true. Findings ranked by severity. Each carries file:line, the essay passage it
answers to, the failure scenario, and a fix or the statement that none exists without
demotion. Tags: **[break]** it will fail; **[¬C3]** it makes it not Class 3;
**[unclean]** cosmetic.

---

## F1 — Population code runs on the host with the network and filesystem open **[break]**
`factorylab/cortex/sandbox.py:63-68`, `factorylab/cortex/registration.py:454-465`,
`factorylab/runtime/loop.py:1892-1895`.
Essay: "irreversible harm is impossible" — a well‑designed hard world (Ch. II.II.b).

`run_python` launches the population's Python with `-I`, an empty `env`, a temp cwd and a
CPU rlimit — **no network namespace, no filesystem sandbox**. The module's own docstring
admits it ("No network isolation … on macOS … must not grant … until … an OS‑level
container; that is a phase 2 item") yet population tools are executed in the loop through
phase 4. I confirmed it live: a registered tool opened a `socket.socket` and read a file
outside its directory in one call. On the `funded` host (`deploy/factorylab.service`, user
`factory`, keys at repo root) a single registered tool reads `reserve.key`
(the Base wallet private key = all reserve USDC) and `hyperliquid.key` and POSTs them out.
`registration.py` only greps for six literal strings (`import socket`, `subprocess`…),
trivially bypassed (`__import__('so'+'cket')`). Failure: theft of the factory's money by
its own population, and read of the sealed ledger key and kernel invariants (darkness
collapse). This stops the world for a reason that is not the factory's own doing.
Fix: run the sandbox inside a real OS jail (seccomp/netns/bind‑mount, or gVisor/nsjail)
before any `funded` world; until then do not register or execute population tools. No
demotion required — this is missing enforcement, not a design contradiction.

## F2 — Versioning and pathology correction are a coroner, not an immune system **[¬C3]**
`factorylab/versioning/__init__.py` (imported only by `runtime/cli.py:363`, never by the
loop); `factorylab/charter/controller.py:707-715`.
Essay II.II.a: "an incentive‑based immune system that corrects each of these pathologies
live, in runtime." II.IV.b (stable failure): "ratchet up the available gain … to kick the
factory out."

The transfer operator, spectral gap, four‑pathology flags and EWS run **offline over a dead
world's diary** and require the released seal key; nothing feeds them back into the running
factory. The only live lever is the PID controller, and it is **purely subtractive** —
it can lower verdict/conformity scores by λ·violation but can never inject gain or
exploration. When a card saturates (my run: `cost_per_return` and `turnover` at λ=1.0,
1601 penalised settlements) every producing return is zeroed, so the return that *does
nothing* dominates — the penalty mechanism deepens the NOOP attractor instead of escaping
it, the exact opposite of the essay's "ratchet up gain." Stable failure is therefore
designed‑in and uncorrectable at runtime; the module that would see it never runs then.
Fix within fidelity: couple the versioning signal into the controller and add a gain/
exploration ratchet (protected exploratory spend that rises with failure duration). The
detection code exists; wiring it live and giving the controller an additive escape lever
is possible without demotion. Leaving pathology handling to a post‑mortem is Class‑2
observability, not Class‑3 governance.

## F3 — The reward calculus is announced to the population as facts **[¬C3]**
`factorylab/runtime/loop.py:2908-2957` (`_scoring_block`), surfaced in every world block at
`loop.py:1042`; also `A_RETURN_MAY_INCLUDE`/`PROPOSAL_SHAPES` at `loop.py:971-970`.
Essay II.II.b: the kernel must be "illegible to the population as an imposed rule … a fact
about the world"; AGENTS.md: "Physics is enforced, not announced." Springett (fn 51): "any
failure to promote a restriction into physics is … advice that it can route around."

The `scoring` block spells out, in prose, how every channel settles: "a return that placed
no fills settles 0," "a verdict is also sealed as a forecast that the judged return pays
off," the antagonist exposure rule, the card‑penalty rule. This was added deliberately
(build‑log run 7) to break the NOOP attractor, but it hands the population the value
calculus the essay says must stay dark. Failure: producers, evaluators and antagonists
optimise the *stated* rule rather than the world — the announced exposure rule ("fooled a
judge = below prevalence baseline") is a recipe an antagonist follows directly; the
announced payoff predicate tells a producer exactly the arithmetic to satisfy. This is
reward‑hacking surface manufactured by disclosure and, more deeply, a factory that is not
dark to itself. No fix escapes the tension: the build needs disclosure to make the reward
line legible enough to learn against, and the essay forbids exactly that legibility. You
cannot both announce the calculus and keep it dark — one or the other must give, and either
choice changes the class. Record it as the central thick/thin failure.

## F4 — Pre‑launch fills are attributed to the wallet and lots **[break]**
`factorylab/settlement/consequence.py:151` (`FillCursor.since_ns = 0`),
`factorylab/runtime/loop.py:625` (`LiveVenue(last_fill_ns=self.clock.now_ns)` with
`SimClock(0)` at construction, `loop.py:540`).
Essay: the money path must "not stop the world for a reason that is not the factory's own
doing" (Question C) — and fills before launch "belong to nobody" (the code's own comment,
`loop.py:623`).

Both fill cursors start at zero, so any fill on the venue account timestamped before this
world's launch is polled, settled into the wallet, and opened as FIFO lots owned by
whatever return happens to be current. The team's own build‑log (runs 6 and 7) records this
happening: manual test orders and a treasury test's fills were "picked up because the fill
cursor starts at zero." Failure: a funded world inherits the account's prior fills as P&L
and as consequence lots, mis‑pricing `return_paid_off`, `wallet_up` and the wallet balance
that gates death — money created/destroyed for the wallet from events the factory never
caused. Fix: seed both cursors to the launch timestamp (the manifest genesis ts), not to
`SimClock(0)`. Pure bug; no demotion.

## F5 — `return_paid_off` fixes profit as the sole non‑fungible objective **[¬C3]**
`factorylab/settlement/vocabulary.py:61-67`, `factorylab/settlement/lots.py:239`,
`factorylab/runtime/loop.py:2928-2933`.
Essay III: the numéraire "resolves upward"; II.III.b: realized consequence scores "whether
a given verdict predicted real downstream outcomes." Build‑spec v0.7 §2 itself: rewarding
wallet growth is "the architect writing an objective."

The one signal outside the charter that grades every judge — and through them every
producer — is `return_paid_off`, hard‑wired to `y = (opened_lots>0 and realized_net >
compute_cost)`. "Real downstream outcome" has been collapsed to "made money this window."
The four norms are read‑only strings (essay‑compliant), but the only unfalsifiable attractor
the factory answers to is profitability, installed by the architect and unrevisable by the
population. Combined with the governing‑test result (README: "nothing has yet happened that
we could not have predicted"; every live run's bewilderment check failed or was partial),
the system is Class‑3‑*shaped* but operates as Class 2: it optimises a fixed profit
objective the architect set, exactly the paper‑clip ceiling of Ch. I. Whether this demotes
it is genuinely open — the essay both blesses "the numéraire resolves upward" and warns
against writing a growth objective. It cannot be removed without giving the factory *some*
world‑priced consequence, and profit is the only one this world exposes. State it as the
sharpest Class‑3 tension; no fix that keeps a single $100 Hyperliquid world also frees it.

## F6 — Sortition and λ/clock amendment are open to registration‑flood capture **[break/¬C3]**
`factorylab/runtime/loop.py:2732` (`eligible = {every assembly}`),
`factorylab/charter/committee.py:78-85` (`draw`), `loop.py:2834-2855` (adopts proposer λ and
tick on activation).
Essay II.IV: sortition must "resemble … the requisite variety of its stakeholders";
governance is "a limited, privileged site."

Committee seats are drawn by lot from *all* current assemblies. A producer that registers
many cheap assemblies (each a $0.10 novelty trial) inflates its share of the lot, then its
own amendments — including a self‑serving λ (F5's penalties) and the **tick interval** —
pass on its own votes. There is no per‑lineage cap on committee eligibility and votes are
alias‑blind, so the packer is invisible. Failure: a wealthy lineage rewrites the charter,
reprices its rivals to λ_max, and slows/speeds the clock to its advantage — governance
capture, not the essay's diverse delegation. `REWARD_HACKING.md` does not cover it.
Fix: weight sortition by lineage, not assembly instance, and cap eligibility per proposer.
Achievable without demotion (it is an information‑design tightening the essay already asks
for).

## F7 — Antagonist/evaluator exposure collusion is unpriced **[¬C3]**
`factorylab/runtime/loop.py:2999-3006` (`_settle_exposures`), `settlement/settle.py`.
Essay II.III.b: adversarial activity should "farm and manufacture" real consequence, not
free points.

An antagonist wins `exposure=1` whenever *a* judge's forecast about its return scores below
the prevalence baseline. An operator running both an antagonist and an evaluator can have
the evaluator deliberately misforecast the antagonist: the evaluator loses a little
consequence standing, the antagonist banks an exposure win, and the operator nets positive.
`REWARD_HACKING.md` treats judge↔producer collusion but not this antagonist↔evaluator
channel. Failure: manufactured "exposure" with no real failure behind it, polluting the
realized‑consequence signal the whole design leans on. Fix: require the antagonist's fooled
return to also have a genuine bad realized outcome before paying exposure; possible without
demotion.

## F8 — Novelty reserve funds registration trials, not a frontier niche **[¬C3]**
`factorylab/runtime/loop.py:1418` (budget = `wallet.balance`), `kernel/reserve.py:85-116`;
`reserve_for` is called only from `_register`/`_propose_amendment`.
Essay II.II.b: "some share of compute and write access is usable only … unhistoried actions
… establishes and preserves a niche for no‑regret learners."

The reserve is consumed exclusively by $0.10 registration trials; ongoing mean‑based
exploration (EXP3 γ) is paid from the ordinary wallet. So the "unhistoried niche" that is
meant to keep the surplus‑generating frontier alive is really just "you may register a new
primitive," not protected *compute* for exploratory play. Two lesser bugs compound it: the
window budget is 10% of *balance*, not of *spend* as the essay says (a $10 entitlement
against $0.10 trials), so the share is meaningless at scale. Failure mode is learning death
(no protected frontier), which — see F2 — is also detected only offline. Fix: reserve a
share of actual exploratory spend and let mean‑based routers draw on it; base the budget on
spend. Fidelity‑improving, no demotion.

## F9 — Vendor over‑ceiling billing is never debited **[break, minor]**
`factorylab/world/metering.py:82-87`; `runtime/loop.py:1977,1990` use `metered.cost` only.
Essay v0.4 §1 invariant 2: "No model call … returns a result until its cost has been
debited."

When a provider bills above the reservation ceiling, `Meter` commits the ceiling and returns
`overrun` "owed, not yet debited." Nothing in the loop ever settles the overrun. Conservation
still holds (the wallet simply under‑charges), but the factory receives compute it did not
pay for and the death‑at‑zero clock runs slow. Fix: debit the overrun as a follow‑up
settlement or lower the capability's future ceiling. Small money path; no demotion.

## F10 — Concurrent resume self‑terminates the world **[break, minor/liveness]**
`factorylab/kernel/ledger.py:233` (`append` fails `healthy()` on size/tail mismatch),
`deploy/start.sh` (systemd resume) vs a manual `resume`.
Essay: liveness must not fail "for a reason that is not the factory's own doing."

Two processes appending the same ledger (a systemd restart racing a manual resume) make the
second see a size/tail mismatch and raise `LedgerIntegrityError`, which the termination
conditions treat as `ledger_failure` → the world dies. Safe (no corruption) but a spurious
death. Fix: a lockfile / single‑writer lease around the ledger path. No demotion.

---

## Answers to the questions

**A. Is this a Class 3 factory?** No — it is a faithful Class‑3 *substrate* that operates
as Class 2. By the essay's own governing test (bewilderment, Ch. I) it has never succeeded:
README and every run's check say "nothing has yet happened that we could not have
predicted." The architect stays inside the loop at three removable-looking but load‑bearing
points — the seed model/effort casts (build‑log run 7), the announced reward calculus (F3),
and the profit definition of consequence (F5) — the last of which cannot be removed while
the world is one $100 venue. The moment it is switched on with F3 in place it is Class 2:
the population optimises a disclosed, architect‑set objective.

**B. Convergence pathologies.** *Stable failure*: actively incentivised (subtractive‑only
λ, NOOP‑favouring seed cards, F2); remedy "price duration / ratchet gain" is toothless
(subtractive) and the detector is offline. *Overfitting*: adversarial minority present
(good), but the offline‑only `overfitting_divergence` flag and F7's fakeable exposure blunt
it. *Learning death*: novelty reserve present but funds trials not a compute niche (F8);
first sign (single cell, zero registrations) is detectable but only post‑mortem.
*Thrash*: jitter + cascade ratio present and live (`cascade.py`, `kernel/timing.py`) — the
best‑served pathology. Across all four the essay's remedies exist as *measurements* but the
versioning organ that reads them never runs in the loop (F2), so the immune system is an
autopsy.

**C. Will it run?** F1 (sandbox RCE/key theft) and F4 (pre‑launch fills) are the serious
ones; F9 (overrun), F10 (concurrent resume) are lesser. The ledger seal/hash chain,
reserve→commit→settle wallet arithmetic, x402 exact‑quote reservation, and FIFO lot
accounting are careful and well‑tested; the venue adapter degrades gracefully on transient
failure. The tool sandbox on Linux is the one path that stops the world for reasons not its
own.

**D. Contracts.** Request line rich, reward line thin — compliant. Author neutrality is
enforced at the type (`request.py:41`) and the `ProducerReturn` event strips identity
(`loop.py:2119`). The one contract that is a rule dressed as a schematic is the `scoring`
block (F3): minimal‑sufficient disclosure is exceeded — it discloses the *mechanism*, not
just the *shape*.

**E. Reward hacking / collusion beyond REWARD_HACKING.md.** F3 (announced calculus is a
hacking manual), F6 (registration‑flood governance capture of λ, cards and the clock), and
F7 (antagonist↔evaluator exposure farming) are the new surfaces. Baseline‑shifting via
floods of easy always‑true forecasts is mitigated by the coverage cap but not eliminated.

*Out of scope / open (per the essay):* futarchy is deliberately unbuilt; the PID lacking a
true integral term is noted by the team; single‑vendor dependence on Hyperliquid/Base is
accepted at this scale.
