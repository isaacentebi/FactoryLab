# Design brief for GPT-6 Pro: be the architect

Paste everything below the line as the message, with the zip attached. The zip holds the
repository at the commit in its directory name, the essay (`Superdark Factory.md`), this brief
(`BRIEF-ARCHITECT.md`), your two earlier readings and our triage of both under
`docs/audits/v4/gpt6/` and `docs/audits/v5/`, and the evidence named below.

---

You have audited this twice. Both times we fixed what you found. This time we are not asking
for bugs. We are asking you to sit where the architect sits, with everything he knows, and
reason about the design: what it is, what it did when it ran, why, and what it should become.
You have permission to change anything, including the things we are proudest of. Use it with
judgement: a lot here works and was expensive to get right, so do not redesign for the sake of
it; but if something is wrong at the root, say so and draw the replacement.

The one constraint that is not negotiable is the class of the thing. It must stay a Class 3
factory in the essay's sense: the population sets its own objectives; the architect makes one
move, then only kills. Every proposal, ours or yours, has to pass that test. Where a fix would
be a Class 2 patch, an objective smuggled in as physics, say so, even if it would work.

## 1. What the thing is, as its architect would explain it to a child

Nine agents share two pots. One pot buys thoughts ($90 of model credit); one is bet on a
market ($100 on Hyperliquid perps and spot). Each thought costs a real fraction of a cent from
the thinker's own slice of the first pot. Every ten minutes the router wakes a few of them.
A woken producer sees a description of the world and answers once, in JSON; the answer can
place or close trades, write a public note, forecast something checkable, register a new agent
or a program, propose or challenge a rule. Judges grade producers afterwards and forecast
whether a decision will pay off; meta judges grade judges; an antagonist submits bad work to
catch lazy judges. The kernel settles every forecast against real P&L. Money enters only from
outside, by trading profit or a paid service; it can be moved into thinking credit through a
bridge to Venice. When the thinking pot is empty and no scheduled release is due, the factory
dies. The architect can watch everything and can only kill.

The architect sets four values in words (consequential usefulness, epistemic integrity,
durable agency, bounded reciprocity) and one more we added after your second reading,
fidelity: a measurement stands for a value; satisfying the measurement without serving the
value is failure, and a judge's duty is to say so. Under the values, the population owns the
measurable rules (cards), their prices, and the right to challenge and replace them.

## 2. What we changed after your readings, and why

Read `docs/audits/v4/gpt6-triage.md`, `docs/plans/edition2.md`, `docs/launch-decisions.md`
("Edition 2"), `docs/audits/v5/gpt6-second-reading-triage.md`. In short:

- Your first reading's edition: programs as seats, per-seat entitlements, scheduled endowment
  with dormancy, rent by byte-time, release digest and kill witness, Venice on debit evidence,
  the grading fixes, the metric challenge route, the x402 seller, the open wake.
- Then, from the rehearsals: every frugality card removed (cost, well-formed, tool calls,
  turnover) because under per-seat entitlements the wallet already prices them, and the
  half-cent cost cap had made looking at the market a violation; the concentration card
  removed because a card prices a bet after it is on and the kernel's leverage wall is the hard
  cast. Three cards remain: consequence paid off, forecast skill, censorship bound. The wake
  now publishes every agent's answer live: sealing the diary was secrecy, not darkness.
- Your second reading's four P1s and the economy findings are being repaired in three pull
  requests (identity finality and program memory across restore; a commons release when every
  seat is broke but the pool is not; service income as new money into the root; late losses
  charged to the opener; releases split per lineage; learning death redefined; votes scored on
  their promise). Some may not be merged in this zip; the triage says what each does.

## 3. What happened when it ran

`docs/audits/v5/rehearsal.md` (three runs), `docs/audits/v5/rehearsal-returns.json` (every
answer from all three runs, verbatim), `docs/audits/v5/sample-producer-request.json` (the exact
system and user text a producer receives), `docs/audits/v5/calibration/` (every model on the
menu run through the real contracts), and a rendered wake under `deploy/www/` if you want the
shape of the public page.

The short version: ninety minutes across three runs, about fourteen producer wakes each,
fourteen holds each, zero tool calls, zero trades. In run 1 the rationales quoted the cost and
concentration cards. After we removed those cards, run 3's rationales read the market instead:
"a 0.16% move, inside noise", "an entry would need to clear the $10 minimum notional", "no edge
identified". The decider carried small buy and sell probabilities. Ticks were exact to the
millisecond; the money accounting held; the new bill settlement worked live.

## 4. The conversation we want you inside of

These are the distinctions the architect and his engineer argued about this morning. Take each
seriously, disagree where we are wrong, and go further.

**Darkness is not secrecy.** You said it; we acted on it. The seal was the architect protecting
himself from reading. He does not want protecting. Is there anything else in the design that is
manufactured opacity rather than the real thing?

**Cards versus the wallet.** Our reasoning: a card that measures what money already prices
(cost, waste, fees) punishes the same thing twice and biases toward timidity. Frugality is
what the wallet is for. Ruin is what the leverage wall is for. So only cards about consequences
in the world survive. Is that right? Is the paid-off card itself redundant with real P&L
settling into standing? Should a Class 3 factory launch with three cards, one, or none?

**Fidelity as a norm.** "Don't reward hack" written as a value, not a rule: it gives judges the
basis to mark down hollow compliance, and it makes the antagonist's role a duty. Genius or
retarded? Would you word it differently?

**Why nobody trades, and whether that is a design flaw.** Our current reading has three
layers. First, at a ten-minute tick with mid prices and funding, there is no edge; a frontier
model would hold too. Second, structural money (funding carry: long spot, short perp, collect
funding) exists on this venue and needs no forecast, but it has to occur to a seat and survive
several wakes. Third, building machinery (programs, notes, challenges) is where model strength
shows. The architect's counter: every real trader has only public information and still takes
shots, so if ours never do, something in the world is wrong. Our diagnosis: (a) scarcity is
invisible at the tick: a seat sees wallet $89.9 and cash $851, nothing says "you are dying",
whereas capitalism's pressure is rent with a date; (b) the paid-off card already counts a hold
as "did not pay off", so pressure exists but has nowhere to go, because (c) there is no
variation: two producers, similar cheap models, one seed prompt, so the router, the judges and
the prices have nothing to select between. A market of two identical cautious traders never
trades however hard you price it.

**Our proposals, which we want you to test against Class 3.** (1) Seed diversity: four or
five producers with genuinely different starting dispositions and models (a contrarian, a
funding-carry seeker, a momentum follower, a cautious one), on the argument that starting
propensities are what a Stackelberg move is allowed to set and the population can retire the
bad ones. (2) Legible scarcity: the world block shows runway (days of thinking left at the
current burn, P&L to date, and the sentence that only trading or a paid service refills it),
on the argument that this is physics made visible, not an instruction. (3) Days, not minutes,
before reading anything. (4) A stronger producer model, since the calibration shows the best
model costs the same per call as the cheap ones. Which of these are Class 2 in disguise? A
disposition like "carry seeker" is close to an objective; where is the line? Is "runway" a
neutral fact or a nudge toward trading?

**Whose success survives.** Your second reading's question. Seat, lineage, coalition, model.
The lineage split and the late-loss charge are our answer for money. Is that the right unit?

**Would we accept a factory that correctly does almost nothing?** Yes. Say what evidence
would distinguish informed restraint from a dead frontier, and whether the immune organ, even
redefined, still smuggles in "must look busy".

## 5. Memory and continual learning: does the population actually have them?

Look hard at what a seat receives and keeps. Read `factorylab/cortex/schematics.py`,
`cortex/request.py`, `cortex/assembly.py`, `runtime/notes.py`, `kernel/artifacts.py`,
`runtime/routing.py`, `learners/`, `runtime/propensity.py`, `settlement/`, `charter/`, and
the sample request. As we understand it: a seat wakes with no memory of its last turn unless
the world enables handle-scoped memory; what persists is the world (positions, fills,
balances), public notes (bounded, rented), program private state (an artifact by hash), the
router's learned weights, standing, and the charter. Learning lives in the router (EXP3 and
relatives), in standing from settled consequences, in prices, and in the charter. Is that a
continual-learning system or a stateless policy with a scoreboard? What would you add or
remove so that a discovery on Tuesday still shapes Thursday: notes, episodic memory, a shared
scratchpad, program seats as the memory, a different reward line? What does the essay's own
apparatus (request line, reward line, propensity, cascade) imply that we have not built?

## 6. Money and models, concretely

**$500 all in.** Suppose the architect has at most $500 for the whole first world: model
credit, trading capital, hosting, bridge and gas, everything. Propose the split and the release
schedule, with the reasoning, given the measured burn (about $5 to $7 a day at nine seats and
a ten-minute tick) and given that only trading or a paid service refills thinking. Say what
that buys in days awake and in completed feedback cycles, and what it does not buy.

**Which models.** The calibration table is real data on this prompt: DeepSeek 4.1 flash was the
only model at 100% on every column; GLM 5.3 flash via OpenRouter wrapped its answers even with
the host pinned; Venice's DeepSeek had provider errors. Look at current benchmarks and prices
for the models reachable through OpenRouter and Venice (the venue is Hyperliquid; providers
are constrained to those two, plus x402 pay-per-call) and propose a roster: which model in which
seat, at what price per call, why. Say whether a stronger producer is worth more than more
wakes, and whether the roster should be heterogeneous on purpose.

## 7. What we want back

- Your judgement on each question in section 4 and 5, plainly, from first principles, with the
  essay as the reference and your own reasoning where the essay is silent.
- Any proposal of ours you would veto as Class 2, and why. Any of yours that you know is at
  the edge, flagged as such.
- If you would redesign something at the root, the design: what a seat sees, what it keeps,
  how money moves, who lives and who pays, in enough detail that we can build it. If you would
  not, say what you would leave exactly alone.
- Concrete artifacts: a proposed roster with seat dispositions written out as the seed prompts
  you would use; a $500 budget table and release schedule; the world-block lines you would add
  or remove; the cards you would launch with.
- What you would expect to see in the first week, and what would make you stop.

Rules: no mainnet, no fund movement, no manifest named `funded`, never read a `*.key`. Write
code only as tests under `tests/audit/` if you write any. Separate what you ran from what you
read from what you infer.
