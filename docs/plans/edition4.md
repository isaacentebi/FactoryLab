# Edition 4: keep trading, fix what teaches them, let them reach each other

Decided 19 September 2026, after the v7 audit, the repair of the machine (PR #107), and the
read-only commission. The long version of the commission's reasoning is in
`docs/plans/edition4-long.md`; this is what gets built. Editions 2 and 3 are history.

## The position

Trading stays. It is available, it is real, and it may end up supporting the factory. What is
forbidden is making "maximise trading profit" — or any other architect-supplied target — the
permanent definition of success. The venue is physics, not an objective.

The Class 3 question is separate from profitability: **does the population begin choosing and
revising what is worth doing, and do those choices change what it does next?** A profitable
carry bot would not answer that. A population that trades to finance projects and standards it
develops itself would.

## What is wrong now, in one paragraph

A producer's reward is a judge's verdict, and the judge's own target is attributable blame, so
**"nobody blamed this" scores as success**. Every act costs money immediately; almost nothing
pays later. Careful refusal is therefore the best-paying move in the world, and the population
found it: 59 of 63 readable producer answers in run 5 were hold or defer. Meanwhile a founded
child's entire endowment is $0.05 — a number sized to cover one call, then reused as a life —
so founding anything is paying to create something stillborn. That is why the doors were never
opened. Not laziness: arithmetic.

## The five changes

**1. Fix what teaches the population.** Stop settling producer learning on a judge's immediate
approval. Judges may forecast and interpret evidence; final credit depends on observed
consequence — realized money for a trade, independently observed use for a tool, a resolved
forecast for an investigation. **Not one universal P&L score.** The population must be able to
revise the criteria that connect consequences to its objectives.

**2. Cut the default prompt to ~8,000 characters.** Norms, immediate situation, resources, a
compact action interface; detailed documentation retrieved when needed. This is the cost of
living: at 73k a thought costs ~$0.007 and a child's endowment buys seven of them; at 8k it
buys fifty to a hundred. Measure actual bills and invalid-action rates, not characters.

**3. Remove compulsory jobs and compulsory thinking.** A small, diverse seed population. Sleep
by default, wake on events, run cheap programs between model calls, found funded participants.
Buy evaluation when work needs evaluating, keeping independent checks and protected
exploration. Nine permanent posts are unnecessary; nine expensive calls every tick are worse.

**4. Give them address and reusable work.** Bounded messages to a named participant: the sender
pays, the recipient may ignore. Callable, owned, priced artifacts so one participant can use
what another made. Delete blind hiring and the notebook as their substitutes. The test is a
real dependency: someone does something they could not have done without another's work.

**5. Size the capital against measured cost.** Only after 1–4 are running and the burn is known.
Required daily return = (daily operating cost − other income) ÷ trading capital. At $10/day on
$120 that is 8.3%: untenable. At $1/day on $120 it is 0.83%. At $1/day on $10,000 it is 0.01%.
More capital lowers the bar and exposes more money; neither cheap thinking nor more capital
creates an edge that may not exist. **No punitive rent to force activity. No more money for the
present bureaucracy.**

## How it is tested

Matched runs on the repaired build, changing one thing at a time: baseline; prompt; consequence
reward; wake and evaluation policy; address. Combine only after each is understood. A short
rehearsal can show whether costs fall, whether consequences reach learning, and whether
participants build and use things. It cannot show a sustainable trading return — testnet profit
cannot replenish real model credit, and that needs its own longer evaluation.

Sealed predictions from the commission stand (`edition4-long.md` §5); low samples resolve as
unresolved, not as anyone's win.

## Rules of construction

1. One build, not a review round. The commission is closed.
2. Every institution added deletes one.
3. No new test unless it guards a kernel invariant or one of the five changes. `check` stays
   under 60 seconds; the full gate under two minutes.
4. Never read a `*.key`. No mainnet, no manifest named `funded`, until a rehearsal shows the
   earning loop closing.

## Open for the architect

- How many participants at genesis, now that founding is real and population size is endogenous?
- Who, if anyone, is an independent outside counterparty — and the hard rule: the architect must
  never become the customer after launch.
- Whether to widen the venue (HyperEVM execution, more instruments, richer data) in the same
  pass, or after the five changes are measured.

## The standard

If the architect can predict what the factory becomes, it is not the factory the essay
describes. The architect should be able to predict what is *available*. What the population
makes important must stay open.
