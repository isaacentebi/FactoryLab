# Opus capability audit: what a participant can actually reach

Read-only source audit at `6c4c864` plus the current uncommitted patch. Scope is
capability reachability: whether a seat can create, invoke, persist, address,
sell, fund and amend, and which of those paths are broken as opposed to merely
unused or switched off. The broad institutional reading belongs to the Astra
audit and is not repeated here.

Method: README, AGENTS, `docs/essay.md` in full, then caller paths traced from
the proposal schema a seat is shown through validation, registration, dispatch
and settlement. Live claims come only from the running control arm's
`observer/report.json`; its diary, ledger and state were not opened.

Three evidence grades are used throughout and never mixed:

- **Offered** — named in the capability index or proposal shapes a seat reads.
- **Locally verified** — a complete caller path with a test that exercises it.
- **Live demonstrated** — observed in a paid rehearsal's own aggregates.

## Capability matrix

| Capability | Offered | Path complete | Locally verified | Live demonstrated | Gate in practice |
| --- | --- | --- | --- | --- | --- |
| Register a jailed tool | yes | yes | yes (`tests/cortex/test_tools.py`) | no | host jail + proposer's own entitlement ≥ trial |
| Register a predicate or observation | yes | yes | yes | no | host jail + trial; seed ids not redefinable |
| Invoke a registered tool | yes | yes | yes | partial (calc/catalogue only, via probe) | schema retrieval then a second round |
| Persist: artifacts, private program state | yes | yes | yes (`tests/cortex/test_programs.py`) | no | `state_policy = "private"` and the jail |
| Register a program seat | yes | yes | yes (gate, `test_programs.py:99`) | no | `model_id = "program"` + jail |
| Watchers (trigger predicates) | yes | yes | yes | no | kernel-settleable trigger kinds only |
| Address a peer | **not in these arms** | yes | yes (`test_address.py`, `test_address_integration.py`) | no (0 attempted; withheld by design) | `[tools] address_enabled`, false in every manifest I inspected |
| Sell output, receive income | yes | **in-world half only** | yes for settlement | no (all income zero) | novelty reserve share; then operator-run `deploy/serve.py` + facilitator |
| Fund a child seat | yes | yes | yes (`test_founder_endowment.py`) | no (0 children) | founder's free entitlement ≥ endowment |
| Amend cards, prices, tick | yes | yes | yes | not in this window | proposal + sortition committee |
| Amend norms | no, by construction | n/a | yes (refusal tested) | n/a | `charter/amendment.py` has no such operation |

## Ranked findings

**1. The live arms' zeros describe an intentionally narrowed world, and must not
be read as population behavior.** `observer/report.json` reports `messages` 0
attempted, 0 delivered; `income` zero across external, internal and injection;
`funded_children` 0; `reusable_calls` empty in both lineage classes; all five
questions `unknown` for want of independently resolved evidence; `forecast` 0
settled against 71 unmeasured over 70 paid calls and 387,617 micro-USD. These
runs isolate one variable — realized against verdict producer feedback — and
addressing is held off in both arms so that the comparison is not confounded by
a second new capability. That is a deliberate experimental choice, correctly
made. The report line it licenses is "capability withheld by design", and the
only claim it forecloses is a claim about composition, which these arms were
never built to make.

**2. Three kinds of absence are being reported in one column, and they mean
different things.** A capability can be unavailable in source, present but
intentionally withheld for isolation, or present and enabled but unexercised.
Addressing is the middle case: `ToolsSpec.address_enabled` defaults false
([worlds.py:147](/Users/isaacentebi/Desktop/FactoryLab/factorylab/runtime/worlds.py:147)),
and no manifest I inspected sets it: the shipped `worlds/` set and the two live
arms. An earlier address experiment did enable it, so the capability has been
exercised before and this is a per-run choice rather than a standing state. The
implementation is complete and careful —
sender pays, no recipient wake or bill, replay refused before capacity, body
confined to the recipient's inbox
([address.py](/Users/isaacentebi/Desktop/FactoryLab/factorylab/runtime/address.py:1)) —
with two test files behind it. Nothing is broken and nothing is missing; a later
arm turns it on when communication is the variable under test. The finding is
only that the aggregate's zero should carry that label, because a zero that
means "withheld" and a zero that means "offered and declined" support opposite
conclusions.

**3. Income is the one capability the population cannot reach on its own.**
`_register_service` freezes the program source and price and says so plainly:
"Nothing here opens a socket"
([governance.py:974](/Users/isaacentebi/Desktop/FactoryLab/factorylab/runtime/governance.py:974)).
Serving requires an operator to run `deploy/serve.py` behind a public HTTPS route
with a facilitator; only then does `seller.py` verify payment and credit
`income.earned`. Registration, discovery, publication and settlement are
therefore split across a human boundary that no seat can cross. Everything
downstream of a real payment is implemented and testable; everything upstream of
it is an architect action.

**4. The affordance index tells a seat it can sell.** The proposal line reads
"sell a registered tool's output to outside buyers over x402"
([schematics.py:663](/Users/isaacentebi/Desktop/FactoryLab/factorylab/cortex/schematics.py:663)).
In a world with no serving host, registering a service sells nothing and buys no
discoverability, yet the seat spends a share of the window's novelty reserve on
it and is told the capability is selling. This is the one place I found where
the published affordance overstates what the code delivers.

**5. Registration is priced two different ways, and only one of them is the
author's own money.** Correcting an earlier version of this finding: the two
charges are distinct and must not be added together.

A *personal entitlement debit* applies where the code both requires and moves
the trial. The tool path does exactly that — `_require_trial` then
`_move_trial(..., to=None)`
([governance.py:738](/Users/isaacentebi/Desktop/FactoryLab/factorylab/runtime/governance.py:738)),
which reaches `budget.debit(proposer, amount, reason)`
([governance.py:650](/Users/isaacentebi/Desktop/FactoryLab/factorylab/runtime/governance.py:650)) —
as do the learner path and the voted path
([:381](/Users/isaacentebi/Desktop/FactoryLab/factorylab/runtime/governance.py:381),
[:587](/Users/isaacentebi/Desktop/FactoryLab/factorylab/runtime/governance.py:587)).

A *novelty reserve receipt* is the other charge: `_register_with_trial` takes
`reserve.reserve_for(contract, amount)`
([routing.py:409](/Users/isaacentebi/Desktop/FactoryLab/factorylab/runtime/routing.py:409),
[reserve.py:85](/Users/isaacentebi/Desktop/FactoryLab/factorylab/kernel/reserve.py:85))
against the window's protected share, and releases it if registration fails. The
service path at
[governance.py:974](/Users/isaacentebi/Desktop/FactoryLab/factorylab/runtime/governance.py:974)
takes only this receipt: it calls neither `_require_trial` nor `_move_trial`, so
registering a service does not debit its author's entitlement at all. A child
endowment is a third thing again — a conserved transfer from founder to child
([:852](/Users/isaacentebi/Desktop/FactoryLab/factorylab/runtime/governance.py:852)),
with lineage adoption so that spawning cannot enlarge a release share.

Neither charge is external cash burned at a provider. With
`trial_amount_usd = "0.05"` (50,000 micro) in both live manifests and a mean
paid call of 387,617 ÷ 70 ≈ 5,537 micro from the control's own aggregate, a tool
costs its author roughly nine calls' worth of its own entitlement. A service
costs it none, and instead competes for the window's novelty share, which the
live manifests set to `share = 0.1` with `trials = 3`. My earlier claim that a
service costs about eighteen of an author's calls was wrong.
These are priced barriers rather than defects. Whether either one explains the
absence of observed capability creation is an untested hypothesis: I have no
causal evidence, no seat was observed attempting a registration and being
refused, and the alternative explanations — that seats did not consider
authoring, or considered and declined it — are equally consistent with the
aggregate. Test 1 below is what would distinguish them.

**6. The jail works; only this run's own availability row is still unread.**
Predicates, observations, tools, program seats and services each raise
`Infeasible("no jail on this host")` before spending anything
([governance.py:270](/Users/isaacentebi/Desktop/FactoryLab/factorylab/runtime/governance.py:270),
[:305](/Users/isaacentebi/Desktop/FactoryLab/factorylab/runtime/governance.py:305),
[:726](/Users/isaacentebi/Desktop/FactoryLab/factorylab/runtime/governance.py:726),
[:802](/Users/isaacentebi/Desktop/FactoryLab/factorylab/runtime/governance.py:802),
[:988](/Users/isaacentebi/Desktop/FactoryLab/factorylab/runtime/governance.py:988)).
That is nearly everything a seat can author, including the two kinds —
predicates and observations — through which a population makes its own claims
measurable and names them on a card, so host isolation is a single point that
decides most of the authoring surface. That point is verified: the deployed
Linux jail probe passed, and jailed execution on the production route passed
locally in the nonfinancial probe. What remains open is narrow — this
particular run records its own result once as `sandbox.availability`
([bootstrap.py:634](/Users/isaacentebi/Desktop/FactoryLab/factorylab/runtime/bootstrap.py:634)),
in the diary I must not read while the world is alive. Reading it after death
confirms for this run what the probes already establish for the platform.

**7. Governance scope matches the essay, and the norm wall is real.** Amendments
carry metric cards only; the module opens by saying frozen proposals can change
cards and never norms, and refuses a non-card with "norms are read-only"
([amendment.py:1](/Users/isaacentebi/Desktop/FactoryLab/factorylab/charter/amendment.py:1),
[:129](/Users/isaacentebi/Desktop/FactoryLab/factorylab/charter/amendment.py:129)).
The essay's requirement is exactly this: the factory contributes to cards, prices
and λ, and meets a read-only wall at norms. No finding here.

**8. The reachability fix is in the source the live control is running.** Before
it, a seat that retrieved a contract could not call what it had just learned:
the retrieval consumed the decision's only tool round
([compute.py:1347](/Users/isaacentebi/Desktop/FactoryLab/factorylab/runtime/compute.py:1347)),
and `calc` is not among the kinds the old continuation permitted. The patch also
binds a grounded finding's citable references to the commission's own supplied
refs, which is the precise failure the paid probe recorded — `records.json` shows
`parse_failure: ValueError` on the `independent_use` arm while the same model
answered the `contrary_result` arm correctly. Release `a4e0226` carries all four
current source diffs and the final validation, and the local control runs that
fixed source, so the old one-round limit is not a confound for these arms: a
seat in them can look a contract up and call it inside the same decision.

## Minimal fixes and deletions

- Change the service proposal line to state what registration does: freeze a
  priced program and make it servable where a host serves it. One string in
  `schematics.py`; no behavior change.
- Label withheld capabilities as withheld wherever a count is reported. A world
  that does not publish addressing should report the message row as "capability
  not published in this arm" rather than zero, so an isolation choice is never
  read as a declined opportunity.
- Do not add capabilities on the strength of these arms. Nothing here is missing
  a feature: one capability is withheld by design, one is split across a human
  boundary, and the rest are priced.
- No deletions recommended. Every path I traced is complete on its own side of
  its gate, and the unused ones are unused for stated reasons.

## Class 3 implications

The essay's test is that the factory derives what it is trying to do from norms
and constraints, and that its survival is its own stake. Two gaps bear on that
directly, and they are different in kind. The survival loop does not close
inside the factory: compute is bought with money the architect supplied, and the
only path to replenishing it by earning runs through an operator-run server, so
the factory cannot fund its own continuation without the architect acting. That
is a structural property of the current design and it holds whatever any run
shows. Composition is the other kind: peer addressing exists, works and is
deliberately withheld from these arms, so `reusable_calls` being empty says
nothing about whether participants would compose, and the question stays open
until an arm is run with the capability published.

Neither observation says the design fails the essay. The first names a real
boundary the architect still occupies. The second names an experiment not yet
run, which is a schedule rather than a defect.

## Decisive cheap tests

1. **After the live world dies**, read two things from its diary: the single
   `sandbox.availability` row, and every `registration.rejected` or `Infeasible`
   reason. The first confirms for this run what the deployed and local jail
   probes already establish; the second separates "could not afford" from "did
   not choose", which is the live question behind finding 5.
2. **When composition is the variable, publish addressing in one arm and change
   nothing else.** The machinery is tested and the flag is one line. Doing it
   while feedback is the variable would confound both; doing it never leaves the
   A hypothesis untested.
3. **Quantify the entitlement wall offline**, in a scripted world, on the tool
   path where the debit actually falls: a seat one micro below the trial gets
   `Infeasible("proposer's entitlement is below the trial amount")`, a seat at
   the trial registers. Free, deterministic, and it turns an average into a
   threshold. Run the service path beside it to confirm the contrast — no
   entitlement debit, a novelty receipt that returns to the window on failure.
4. **Close the income loop locally against a fixture facilitator**: a registered
   service, a paid call through `deploy/serve.py`, `income.earned`, and the
   wallet credit that follows. That proves everything except the public route,
   and it makes explicit that the public route is the architect's move.

## Dissent

My disagreement is not with the design of these arms, which isolate feedback
correctly, but with how their output will read six months from now. Both arms
exercise the same narrow decide-and-judge loop by intent, and the only
hypothesis they can separate is whether realized feedback disciplines learning
differently from verdict feedback. The aggregate nonetheless emits populated
zero rows for messages, income, children and reuse, and those rows will outlive
the memory of why they are zero. I would not let a later summary cite them as
findings about the population, and I would rather the report said "not published
in this arm" than zero.

I also disagree with the instinct to respond to an absence of composition by
adding mechanisms. The reachability objection is closed: release `a4e0226`
carries the continuation fix the control is running. What remains is a priced
hypothesis I cannot yet support causally — a tool costs its author about nine of
its own calls, though a service costs it none — and measuring that is cheaper
than designing anything new.

## What I did not read

The live world's diary, ledger, events, artifacts, state and lock under
`live-control`, per the standing instruction; only `observer/report.json` and the
two live manifests were opened. No `*.key`, no manifest named `funded`, and no
`.claude/`. No source was edited, no tests were run, no external action taken.

## Addendum: post-death verification of the control arm

The control world has since completed and released its seal, so `report.json`
and `events.json` were read. They settle the one question this audit left open
and confirm the corrections above. No `*.key` was opened.

**The jail was available and nothing was ever authored.** `sandbox.availability`
is `available: true` at `seq 69`. Every one of the 40 `registry.register` rows
sits at `seq ≤ 41` with no handle, which is the seed roster admitted at launch;
no contract is registered after it. `registration.rejected` appears zero times.
So finding 6 resolves in the direction the platform probes predicted, and
finding 5's hypothesis is now sharper and weaker at once: no seat was refused a
registration because no seat attempted one. Price could still deter an attempt;
this run cannot establish that causal explanation. What the run shows is that authoring was
available, affordable to at least some seats, and not attempted.

**The capability surface actually exercised was two read calls.** The only
`tool.call` rows are two `venue.funding_history` reads, both `ok`. One earlier
attempt was dropped as a malformed optional section — `tool_calls: item 0:
venue.funding_history: args: missing argument n` — leaving the rest of that
answer standing, which is the isolation behaviour working as intended. One
`order.intent` was recorded and one fill followed (the lead verified the
report's execution summary and `consequence.fill` row); the venue ends flat after the
runner's automatic wind-down.

**Scale.** 97 `invocation` rows and 98 `novelty.invocation` rows, with 100
`wallet.commit` rows totalling 542,781 micro-USD. The lead's read of 98 calls at
540,781 micro excludes the two successful data-tool calls, each priced at
1,000 micro in its `tool.call` row. The lead reconciled that 2,000 micro
difference; it is not a web search. The producer action histogram — 53 returns as 36 defer, 15 hold, 1 order,
1 investigate — is the lead's read, not independently recomputed by me.

**Release chronology confirmed from the world's own launch record.** The `Launch`
event carries `release_digest: a4e022622894d906…`, which is the release said to
contain all four current source diffs. The control therefore ran the fixed
source, and the continuation limit is not a confound for it.

One correction to my own framing follows from this. I wrote that the arms'
zeros describe an intentionally narrowed world, which remains true of
addressing and income. It is not true of authoring: tools, programs, predicates
and observations were available, the jail was up, and the population did not
use them. That is a real observation about behaviour in this configuration
rather than a withheld capability — still only one short run of one roster under
verdict feedback, and not evidence about what a population would do with more
time, but the first fact here that is about the seats rather than the gates.
