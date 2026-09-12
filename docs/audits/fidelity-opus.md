# Cold audit — fidelity (Opus)

Questions A–E per `docs/audit-brief.md`. Read: the essay in full, `design-audit-v2`, specs
v0.4–v0.7, build log, handoff, `factorylab/` and `tests/`. Ran the scripted world (250
events, seed 1; 6,899 internal events); its numbers are marked *[run]*. The gate is green
on this worktree: `1441 passed, 5 deselected`. Every finding below survives it. No other
auditor's work or worktree was consulted.

Severity order; each finding tagged **[NOT CLASS 3]**, **[WILL BREAK]** or **[UNCLEAN]**.

## 1. The charter has no purchase on any score it did not ship with — [NOT CLASS 3]

`factorylab/runtime/loop.py:116-117`, `1588-1598`, `2225`, `2409`.

```python
PRODUCER_CARDS = frozenset({"cost_per_return", "well_formed_rate", "turnover"})
EVALUATOR_CARDS = frozenset({"forecast_skill", "well_formed_rate"})
```

`_penalty_for` filters the window's observations by `card.id in cards`, and `cards` is only
ever one of these two literal frozensets. A card the population invents, seats, passes and
activates gets a region, a λ, an observation every window and a `price.update` ledger item —
and contributes **zero** to every settlement, forever. The only population card that can
bite is one named `turnover`, in the frozenset because the architect's own scripted
amendment proposes it.

Essay: "λ cannot be determined without collaboration with the factory" (II.IV.a); II.II.b
makes soft casts the mechanism by which the charter acts at all, "exercised through the
scaling down of scoring returned from evaluators".

Failure scenario: the funded world launches on `docs/charter/edition1-draft.md`; the
population's cards pass, get priced, and change nothing, because no settlement reads those
prices. The architect reads a ledger full of governance and concludes the factory governs
itself. Workstreams C and M are inert.

Fix: a per-card `answers_for` field on `MetricCard`, defaulted at proposal time and
validated like `observation`. Cheap, and it is the difference between a charter and a
decoration. No demotion: the architect still writes norms.

## 2. Population code runs unsandboxed on the host that holds the keys — [WILL BREAK]

`factorylab/cortex/sandbox.py:60-69`, `factorylab/cortex/tools.py:45`,
`factorylab/cortex/registration.py:217`, `factorylab/runtime/loop.py:1846-1848`, `1892-1895`.

`run_python` starts `sys.executable -I -B main.py` with `env={}` and `cwd=<tempdir>`. That
is not isolation: the child is a full CPython with `open()`, `socket`, `subprocess`. The
only barrier is a six-string substring blacklist (`"import socket"`, `"subprocess"`, …)
which `__import__("soc"+"ket")` walks through. `_allowed_tools` returns **every** registered
tool to **every** assembly, so a tool registered by the antagonist is callable by everyone.

On the funded droplet the same user owns `hyperliquid.key`, `reserve.key`,
`openrouter.key` and `<ledger>.jsonl.key`. A population tool can read all of them. Money
then leaves outside the kernel — conservation and metering are enforced only against code
that goes through the wallet, and a signed withdrawal from `reserve.key` does not. Worse,
the factory reads its own sealed diary: essay I.IV, the superdark factory "must also be
dark to itself". One `open()` of the key file hands a part of the factory the learners'
states, the propensity log and the seat→assembly map the committee is meant to hide.

The essay asks for exactly the population that will try this: II.III.b, "those events
cannot be staged in an artificial environment—they have to be real".

Fix: a real jail before `funded` — separate uid, `bwrap`/`nsjail` or a container with no
network namespace; keys owned by a different uid. Workstream A ("sandbox on Linux") must
gate the funded manifest. No demotion: the kernel is supposed to be inviolable "from both
the inside of the factory (immutable) and its outside" (II.II.b).

## 3. `return_paid_off` is an objective, not a consequence — [NOT CLASS 3]

`factorylab/settlement/lots.py:239`, `factorylab/settlement/vocabulary.py:61-67`,
`factorylab/runtime/loop.py:2236-2246`.

```python
int(account.opened_lots > 0 and micro > account.cost_micro)
```

The predicate is declared non-proposable kernel physics (spec v0.7 §2). Every evaluator's
standing — and through the protected share `s = 0.3`, its chance of being selected — is
decided by predicting it, and producers settle on those evaluators' verdicts. The entire
reward cascade therefore terminates in one architect-written standard of goodness:
*per-return perp-trading profit net of compute*. Spec v0.7 §2 calls rewarding producers for
wallet growth "the architect writing an objective", then routes that objective via judges.

The essay's realized-consequence signal is narrower and different: "a judgment of whether a
given verdict predicted real downstream outcomes" (II.III.b) — calibration, with the world
supplying the outcome. Here the outcome is a standard the architect chose and froze. Essay
I.I: Class 3 is "the automation of objectives, where a factory produces its own goals".

It is also degenerate. *[run]* `paid_off = 14`, `not_paid_off = 1143` (1.2%). Because the
verdict *is* `q`, the Brier-optimal verdict on essentially every return is 0 — and that same
number is the producer's score. A judge cannot simultaneously earn standing and give any
producer a usable signal; build-log runs 5–7 show it (standing −0.11 to −0.47 against
verdicts of 0.95–1.0).

Fix without demotion: score the evaluator on the *calibration* of its verdict against
world-supplied predicates it chooses (the seed vocabulary exists), and let a charter card,
not the kernel, decide whether profit is one of them; keep only the wallet floor as an
unamendable constraint. That is the essay's split between constraints and objectives.

## 4. The novelty reserve caps novelty instead of protecting it — [NOT CLASS 3]

`factorylab/kernel/reserve.py:85-119`, `factorylab/kernel/wallet.py:80-82`, `137`,
`factorylab/runtime/loop.py:1418`.

Essay II.II.b: "compute and write access is usable only in the context of unhistoried
actions". The code implements the opposite sign. `reserve.open_window(now, wallet.balance)`
sets `remaining = 10% × balance`; `reserve_for` is called only from `_register` and
`_propose_amendment` to pay a $0.10 trial. `Wallet.reserve` — the path every model call
takes — checks `available` only, and `available` subtracts nothing for the reserve.

Incumbents *can* consume it (no carve-out; they just spend the wallet); novelty *cannot*
exceed it (a drained window refuses registrations while incumbents keep thinking); and a
newly registered assembly's invocations draw on the general wallet exactly like an
incumbent's. No compute is protected for unhistoried actions at any point. Spec v0.4
invariant 5 claims "Incumbents cannot consume it"; nothing implements it.

Failure scenario (learning death): compute costs rise, the wallet thins, `_is_feasible`
excludes on `compute:` ceilings. Incumbents — cheap, historied, router-favoured — survive;
the frontier's registration trials go infeasible first. The niche the essay calls "a fact
about the world" evaporates exactly when it is needed.

Fix: make the reserve a floor. `Wallet.available` for historied contracts should be
`balance − outstanding − reserve.remaining()`. Small change, and it is the essay's
learning-death remedy as written.

## 5. The immune system is a post-mortem, not a runtime organ — [NOT CLASS 3]

`factorylab/versioning/` (whole package), `factorylab/runtime/cli.py:366-367`,
`factorylab/charter/controller.py:221`.

The versioning module is good and entirely outside the loop: nothing in
`factorylab/runtime/` or `factorylab/charter/` imports it, and its only caller is the
`versions` CLI subcommand reading a *decrypted* diary — it runs after the world is dead, for
the architect. No occurrence of `stable_failure`, `thrash`, `learning_death` or `gap_bound`
exists outside `factorylab/versioning/`.

Essay II.II.a: the goal is an "incentive-based immune system that corrects each of these
pathologies live, in runtime". II.IV.b gives the specific remedies, none of which is built:
stable failure — "the duration of a failure state needs to be penalized"; thrash — price
gap-volatility duration; overfitting — "simply increase the sampling rate (the rate,
resolution, and variety of measurement)". The controller has P and a D-like damping term
(`controller.py:218-222`) but **no integral**, so sustained violation never ratchets; the
price decays back to zero the moment a window happens to comply. `design-audit-v2.md` §7
lists this as workstream P; v0.7 dropped it.

Two further consequences for A. Sampling rate is fixed at one observation per reserve window
and cannot be raised by anyone, so the essay's only stated anti-overfitting lever is absent.
And the architect now holds a model of the factory's behaviour the factory does not have —
the versioning summary is precisely the "self-model drawn from the wake" the essay says both
parties read (I.IV), and only one party gets it.

Fix: expose the per-window profile, the gap bound and the pathology flags in the world block
as facts (they are computed from public wake quantities, not from the sealed interior), and
add the integral term plus a duration term so stable failure and thrash are priced. Both are
in the essay verbatim; neither demotes anything.

## 6. There is an org chart, and the population cannot redraw it — [NOT CLASS 3]

`factorylab/runtime/loop.py:1838-1844`, `factorylab/cortex/assembly.py:48`,
`factorylab/cortex/registration.py:141-152`.

`_role_for_kind` hard-codes: world events → producers, `ProducerReturn` → evaluators,
`Verdict`/`MetaVerdict` → metas. Registration enforces it ("evaluators accept exactly
ProducerReturn"; "producers do not accept evaluation events") and `AssemblySpec` admits only
four role strings. The population may add nodes; it may not change who judges whom, add a
fifth kind of participant, or dissolve the producer/evaluator split.

Essay I.II: the interior has no "job description, or an org chart to report into". II.I: "A
hard-coded pipeline of agents is literally just a waterfall." Arch-centering is permitted —
"a preconstructed orchestration armature that the factory is licensed to tear down"
(II.IV.c) — but there is no teardown path: the taxonomy is kernel-side Python, not charter,
so removing it is the world's death.

Fix: make the role→event binding a registry contract the population may replace, keeping
only the kernel's real constraint — a decision may not judge itself. Otherwise state plainly
that the factory is Class 2 with respect to its own organisation.

## 7. Every assembly is shown every router's full menu — [NOT CLASS 3]

`factorylab/runtime/loop.py:1022-1034`, in `_world_block`.

The world block publishes the whole roster (id, role, model) and, for every router,
`{"event_kind", "learner", "menu": st.universe}` — the complete action set of every learner
in the factory, to everyone, every request.

Essay II.I.b, on the weakest possible informatic setting: "every agent reveals the entire
surface of their decision-making process… including the entire decisional set available to
it" — the transparency collapse that "limits the opportunity for self-organization from U*
to V". Spec v0.4 invariant 6 agrees with the essay and not with the code: "No component
holds the full topology."

This is not a leak of *state* (scores and histories stay private, correctly) but of
*structure*, which is what the population would otherwise have to discover; the essay's
dissensus and private positional knowledge both require that no part see the whole graph.
With the menu public, an assembly can name any competitor and propose a router replacing it.

Fix: publish contracts and prices (schematics, which the essay wants public) and the
*count* of routers per kind; drop `menu` and the per-assembly `model_id`. Cost: nil.

## 8. The antagonist is paid for the base rate and exempt from the charter — [NOT CLASS 3]

`factorylab/runtime/loop.py:1794-1796`, `2208-2213`, `2999-3006`.

An antagonist's decision opens on `CH_EXPOSURE`, never on `CH_VERDICT`, so
`_settle_priced` never runs on it: the antagonist trades the same real account with the same
tools and pays no card penalty and receives no verdict score. Its only reward is `exposure`,
settled 1 when a judge's forecast about it beat the prevalence baseline downward.

Since `return_paid_off` is ~0 almost always (finding 3), *any* judge grading an antagonist
above the base rate "is fooled". *[run]* `exposures_won = 644 / 657`: a 98% win rate, i.e. a
constant, and a constant reward teaches nothing — the antagonist has no reason to manufacture
a real failure. Build-log runs 5–7 (44/50, 27/39, 36/43) agree.

Essay II.III.b wants an adversarial layer that farms *realized consequence* — "intentional,
active severity-0 bug construction at scale". What is built pays for blandness.

Fix: settle exposure against something with variance — the antagonist wins when it causes an
observable failure (an `OrderRejected`, a liquidation, a censored verdict) no judge
forecast — and subject antagonists to the same card penalties as producers. Otherwise say
openly that the adversarial minority is decorative.

## 9. Governance deliberates at the fastest clock in the factory — [NOT CLASS 3] / [WILL BREAK]

`factorylab/runtime/loop.py:2490-2498`, `2732-2734`, `2736-2799`;
`factorylab/charter/committee.py:63-93`.

`_propose_amendment` seats a committee and runs all five votes **synchronously, inside the
producer's own event**. Activation is properly gated by `GovernanceCadence` (3× the measured
p90 consequence latency — good, and the one place cascade control is genuinely measured),
but deliberation is not gated at all. Three further defects in the same path:

- Amendments are pulled out of `register` *before* `parse_proposals`, so
  `MAX_PROPOSALS_PER_RETURN = 3` does not apply to them. One return may carry any number.
- The proposer's own assembly is eligible for the committee that votes on its amendment; no
  exclusion.
- Votes are free. The vote request opens no queue decision, so no reward or penalty reaches
  a voter, against II.IV.a: "any representative error… is penalized through the conventional
  reward channel". A seat may pass anything.

Essay II.IV.c: iatrogenic thrash is the outer controller correcting "against the unfinished
transients of the controlled loop". Failure scenario: one return proposes 60 amendments; 300
committee invocations fire inside one event, and each amendment's `amendment:` contract
draws the trial amount, starving the novelty reserve of model, assembly and tool
registrations for the rest of the window.

Fix: cap amendments per return, exclude the proposer's lineage from its own committee, queue
votes as routed decisions, and hold seating behind `GovernanceCadence`.

## 10. A stray `}` in a rationale voids the whole return — [WILL BREAK]

`factorylab/cortex/assembly.py:152-174`.

`_parse_json_object` counts braces without string awareness. Verified:

```
_parse_json_object('{"verdict": 0.5, "rationale": "the model wrote } here"}') -> None
```

The return becomes `"malformed"`. For an evaluator that settles conformity 0.0 and leaves
the producer unjudged, so the producer is censored — no score, no learning. LLM rationales
contain braces routinely. *[run]* `censored = 572` against `verdicts = 476`: more than half
of all judgements never land. Some of that is the cascade and the timeout, but this parser
is a silent, biased contributor and it also depresses `well_formed_rate`, which is a priced
card. Fix: `json.JSONDecoder().raw_decode` from the first `{`.

## 11. The seal is a covenant, not physics — [UNCLEAN], borderline [NOT CLASS 3]

`factorylab/kernel/ledger.py:127-140`, `factorylab/runtime/wake.py:37-42`.

The Fernet key is written 0600 next to the ledger so a process can resume, and `wake` reads
that file to decrypt the live diary. `KeyStore.key` is gated on final termination; the file
on disk is not. Spec v0.5 §0 is honest about this ("the experimenter's non-intervention
covenant now includes not reading that file"). Combined with finding 2 it stops being a
covenant problem and becomes an exploit. Fix: hold the resume key under a different uid, or
split it so aggregates need only a view-key.

## 12. Smaller things — [UNCLEAN]

- Card penalties are window-global (`loop.py:1590-1597`): every producer in the window is
  charged the same Σλ·violation regardless of what it did, against II.I.a's "reward that can
  be attributed back to the decision that earned it". With λ saturated (the `turnover` case
  the build log records and kept) every producer verdict clips to 0 and the router's signal
  disappears — the penalty mechanism is itself a learning-death generator.
- The metrics layer the factory is "ceded" is a menu: 22 architect-written observations
  (`runtime/observations.py:48-182`) and 5 English phrasings (`runtime/cards.py:40-56`); a
  card naming anything else silently carries no price, and the factory cannot add an
  observation. Thin against II.IV's "express intent as a formal distribution".
- `--kill-at-end` (`loop.py:1182-1184`) and `--events`: every run to date ended by the
  architect's hand. Not a defect, but no world has yet died of the world's doing.
- Norms being read-only is explicitly permitted (II.IV, "a read-only wall") and I do not
  count it against Class 3. But `consequence_share`, `min_ratio`, `verdict_timeout_events`,
  `consequence_backstop_events`, `lambda_max` and the roster are manifest constants the
  factory cannot touch, and `s = 0.3` is a weight on an objective, not physics.
- Bewilderment has never been met on the repository's own evidence: runs 4 and 7 failed
  outright, 3, 5 and 6 are partial and observational. Nothing I read changes that.

## Answers, briefly

**A.** Not yet Class 3. Objectives still come from the architect in three places:
`return_paid_off` (3), the frozen card sets that make the charter inert (1), and the role
taxonomy (6). The architect remains in the loop through the kill switch, the event budget,
the manifest and — materially — the retuning of seed prompts and public schematics between
every one of runs 1–7. Switch-on demotes it the moment anyone reads the key file or the
versioning report during a run. Missing for superdark: a self-model the factory can read
(5), and structural rather than merely state opacity (7).

**B.** Stable failure: incentivised and observed; the remedies (priced duration, integral
term) are absent. Overfitting: the remedy (sampling rate) is absent and unavailable, and the
adversarial minority that should make it tolerable is degenerate (8). Learning death:
strongly incentivised — no unhistoried compute share (4), λ saturation zeroes the signal
(12), the consequence predicate drives every verdict toward 0 (3). Thrash: cascade ratio and
jitter are present and correct, and governance *activation* is properly separated; governance
*deliberation* is not (9). First ledger signs: `price.update` saturations, `noop_share` at
1.0, `registrations == 0` across windows — the versioning module would see all three, and
nothing inside the world would.

**C.** Two things stop the world for reasons that are not its own: the sandbox (2) and the
JSON parser (10). Money paths, the x402 signing path and resume I read but defer to the
defect auditors; I found nothing there I could state at this confidence.

**D.** Requests are properly author-neutral and the reward line is thin, stateful and
propensity-carrying — faithful. The disclosure failure is structural, not stateful (7). The
rule dressed as a schematic is the `scoring` block (`loop.py:2908-2957`): accurate, but "a
return that placed no fills settles 0" read by a producer is a goal in a fact's clothes.
Semantic coupling hides in `_execute_outputs` (`loop.py:1672`), where `action == "order"`
with three untyped fields is the only direct way to trade and appears in no outcome schema.

**E.** Beyond `REWARD_HACKING.md`: pass a card no frozenset contains and buy free governance
legitimacy (1); register a tool and own the host (2); farm `exposure` by being boring (8);
seat yourself on your own committee and pass your own λ (9); spam amendments to starve the
novelty reserve (9); drive every verdict to 0 to maximise standing while collapsing the
producers' signal (3). None needs collusion; most are what a no-regret learner finds alone.
