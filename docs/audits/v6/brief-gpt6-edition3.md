# Third brief for GPT-6 Pro: edition 3, everything

Paste everything below the line as the message, with the zip attached. The zip is the
repository at the commit in its directory name, the essay (`Superdark Factory.md`), this brief
(`BRIEF.md`), your two earlier readings and our triage of both (`docs/audits/v4/gpt6/`,
`docs/audits/v5/`), your architect reading and our plan from it (`docs/audits/v6/gpt6/`,
`docs/plans/edition3.md`), and the evidence from the first live runs of edition 3 under
`docs/audits/v6/`.

---

You audited this twice and then designed its third edition. We built the edition you drew:
five workstreams in one night, merged, ratified on testnet, and run live. This time we want
all of it from you at once: the ontology, the bugs, the economics, the prompts, the code you
would fix yourself, and the things you would delete. Be as expansive as the material deserves.
Nothing is off limits except the class of the thing: it stays Class 3 in the essay's sense,
the population sets its own objectives, the architect makes one move and then only kills.

## 1. What edition 3 is now

Read `docs/plans/edition3.md` (the five contracts, C1 to C5, decided from your reading) and
`docs/manifest.md` (the code map). In one paragraph: a seat keeps a working state it writes and
gets back verbatim next wake, and receives every settled consequence addressed to the decision
that caused it (C1). A tick is one folded world update per seat; a seat owns its subscription,
can defer, can register a watcher program that wakes it (C2). The charter carries five norms
with their definitions and one card, the narrowed censorship bound; the paid-off and
forecast-skill cards are gone, and so is the hardwired path that trained judge standing on
`return_paid_off` alone; a fidelity objection is a structured, contestable verdict field (C3).
Every request carries a `you` block: the seat's own entitlement, holds, unsettled bills,
runway, next release and its share, provider inventory, open commitments, unread outcomes,
a directory of notes and artifacts; the catalogue is a compact index with schemas fetched on
demand (C4). Nine seats on two routes, $300 of thinking released 120/60/60/60, $120 of trading
principal, and kill cancels, closes and sells before the world is declared dead (C5).

What we changed from your proposal, and why: no seat on Sol (its $1.30 a day was a fifth of
the burn for one seat's opinions; the population can buy it through the registration route);
no seat on DeepSeek or Qwen, because on our 45-case arithmetic screen they flip the sign of
funding on a third to half of cases and are off by ten on carry (`docs/audits/v6/calibration.md`);
project funding contracts and commissioned judges deferred on purpose (a population with
memory can ask for contracts).

## 2. What happened when it ran

`docs/audits/v6/rehearsal.md`, three runs. Run 3 is the one that matters: five hours at a
two-minute tick, 147 ticks, 525 model calls, $2.94, the final roster.

The constructor seat (GLM 5.3 flash, lens "reusable capability") ran a funding-carry trade from
a plan it kept in its own working state across about a dozen wakes: pulled twenty hours of
funding history, wrote the plan, sent a BTC short, was refused by the kernel for collateral,
wrote a diagnosis with two hypotheses about what margin the world allows, probed with a size
that passes under both, read the fill, rejected one hypothesis, sent the ETH leg with an exit
rule written into state, and closed the leg two ticks after funding flipped sign, citing the
two prints that triggered the rule. Net minus nine cents. `working-state-sequence.json` is
every state it and the other seats wrote, verbatim; `orders-and-refusals.json` is every order
and refusal; `returns-*.json` is every answer of every seat; `sample-constructor-request.json`
is the exact system and user text a seat receives (rendered offline at genesis);
`subscriptions-and-defers.json` and `diary-kinds.json` are what they say.

Also in the run: 78 of 101 producer answers were defers and 482 draws reached nobody, so the
population already thinks less than it is offered (about $2.90 a day at the ten-minute tick);
judges and meta spent 74% of the money grading defers and holds; nobody proposed a rule,
registered anything, or acknowledged an outcome; the opportunity seat narrowed its subscription
to BTC and raised its own cadence floor; GLM cut off 28 answers at its output limit (raised
since); seats wrote `cadence_floor: "2m"` where an integer of ticks was wanted (stated in the
prompt since).

Three defects the run exposed, all repaired in this snapshot: orders were checked against
the thinking pot instead of the venue's free collateral (that is why a $383 short was refused
on an account with $851 of perps cash; now weighed against equity minus margin used, PR #95),
the diary was 194 MB because every full-venue mids map was recorded on every prompt build (now
one read a tick, as the instrument listing already was), and GLM's output limit (4,096 now).

One the repair left open, and we want your answer on it: a fill's fee and every trading loss
still settle against the compute wallet, so a bad trade draws the thinking pot down, and in
the funded world that is fiction, because a loss on Hyperliquid cannot reduce OpenRouter
credit. Should venue P&L settle on the venue pot only, with the thinking pot touched by
nothing but thoughts, rent and the bridge? What does that do to the request line and the
reward line, which today learn from the same number?

## 3. What we want from you, all of it

**Ontology.** The essay's apparatus (request line, reward line, propensity, cascade, sortition,
seal, kill) and the three currencies you named (learning score, seat entitlement, real money).
Does edition 3 implement them, or something that merely rhymes? Where does the code's
ontology diverge from the essay's, and which side is wrong? What is a "seat" now that it has
memory, a subscription, an inbox and a lineage: an agent, a process, an account, a project?
Are there objects in the code that should not exist, and objects the essay implies that do
not exist? Name them.

**Bugs, cold.** Read the code as an adversary. `factorylab/runtime/continuity.py`,
`subscriptions.py`, `routing.py`, `venue.py`, `feedback.py`, `kernel/budget.py`,
`kernel/artifacts.py`, `settlement/`, `charter/`, `cortex/schematics.py`, `cortex/request.py`,
`runtime/resume.py`, `runtime/witness.py`. Every place money can be minted, lost, or moved to
the wrong pot; every place a seat can see what it should not or lose what it should keep;
every place a restore can diverge from the recorded run; every place the population can be
steered by the architect after launch without a kill; every place a kill can fail to be final.
Write the characterization tests as you did before, under `tests/audit/`. Say what you ran,
what you read, what you infer.

**The run itself.** Read the returns. Is the constructor's carry an instance of learning or
an instance of a lens that says "build" being obeyed? Separate the two with evidence. What do
the 78 defers tell you: informed restraint, a router that offers too much, a prompt that makes
deferring the cheapest way to look competent, or seats that cannot tell a two-minute tick from
a ten-minute one? Is the judge and meta spend (74%) grading anything that can be graded, and
what would you have them do instead, within Class 3? Why did nobody acknowledge an outcome and
does it matter?

**Economics, re-derived.** Given the measured numbers (per-call cost by route, per-tick cost,
defers, the constructor's trade), redo the $500 budget and release schedule. Say whether the
thinking pot and the trading principal should stay separate pots, and how the collateral
check should read once they are. Say what "earned continuity" would require in dollars a day
at this cadence and whether any service the population could plausibly sell gets there.

**Prompts.** `sample-constructor-request.json` is 67,000 characters, 54 KB of it a stable
prefix. Rewrite what you would rewrite: the seat prompt (contract plus lens), the `you` block,
the world block, the outcome schema text. Give us the text, not a description of it. Keep the
byte-stable prefix property and say what you moved and why.

**What you would delete.** Cards, organs, roles, tools, pots, events. Anything that exists
because an earlier reading asked for it and the run shows is dead weight. Be specific and be
willing to reverse yourself.

**What you would fix yourself.** For every defect you find that has an unambiguous fix, write
the fix as a unified diff against this snapshot. We will apply the ones we agree with under
review. Tests under `tests/audit/` are welcome; production diffs go in your answer, not in
the tree.

**What would make you stop, and what would make you launch.** The funded manifest is one
step away: `worlds/edition3-testnet.toml` with a name, `mainnet = true`, a namespace, and the
real balances. Say plainly whether you would launch this on $500 tomorrow, what you would
change first if not, and what you would expect to read in the diary after a week if it is
working, and after a week if it is not.

Rules: no mainnet, no fund movement, no manifest named `funded`, never read a `*.key`. Write
code only as tests under `tests/audit/` and as diffs in your answer. Separate what you ran from
what you read from what you infer.
