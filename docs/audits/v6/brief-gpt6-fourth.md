# Brief for GPT-6 Pro, cold: Factory Lab, everything

Paste everything below the line as the message. Attach the zip
(`FactoryLab-edition3-<commit>.zip`): the repository at that commit, the essay
`Superdark Factory.md`, this brief as `BRIEF.md`, three earlier outside readings and what
was built from each, and the complete evidence of the latest live run. The repository is
public at https://github.com/isaacentebi/FactoryLab; the zip's commit is on `main` there,
with the full history.

---

You are reading this cold. Nothing is assumed about you except that you can read an essay, a
codebase and a diary, do research, and write code. Start from the essay.

## 1. What this is

`Superdark Factory.md` is an essay by the architect of this project. Read it in full before
anything else. Its argument, in brief: there are three classes of system that make things.
In a Class 1 system a designer sets the objective and the machinery pursues it. In Class 2
the designer sets the objective and the machinery is allowed to revise its methods. In a
**Class 3 factory** the population inside sets its own objectives; the architect makes one
move (the initial constitution, the resources, the physics) and thereafter can only watch and
kill. "Darkness" in the essay's sense is not secrecy: it is the measurable decorrelation of
what the population wants from what the architect wanted. The essay gives the apparatus: a
request line and a reward line, propensity, no-regret learners, a cascade of evaluators, four
pathologies (stable failure, overfitting, learning death, thrash), sortition, a seal, a kill.

**Factory Lab** is an attempt to build one with real money. Nine agents ("seats"), each a
call to a language model through OpenRouter or Venice, share two pots: $300 of model credit
that buys their thoughts, and $120 of trading capital on Hyperliquid (perpetuals and spot).
Every thought costs a real fraction of a cent from the seat's own entitlement. Every two or
ten minutes the world offers each seat what changed; a seat may think or sleep; when it
thinks it answers once in JSON and the answer can place or close a trade, buy market data,
search the web, write to its own persistent memory, register a program or a watcher or a
service for sale, propose or challenge a rule. Judges grade the work they are commissioned
to grade; meta judges grade judges; an antagonist submits bad work to catch lazy judges. The
kernel settles every consequence against real P&L. Money enters only from outside: trading
profit or a paid service. When the thinking pot is empty and no scheduled release is due,
the factory dies. The architect can read everything and can only kill.

The constitution is five norms in words (consequential usefulness, epistemic integrity,
durable agency, bounded reciprocity, fidelity) and one priced card (a bound on avoidably
unresolved commitments). The population owns the cards: it can reprice, challenge, replace or
remove them. The norms are the architect's one move and are read-only.

## 2. Where it stands, and how it got here

The code is the repository. `docs/manifest.md` is the map of it. `docs/plans/edition3.md` and
`docs/plans/edition3-r3.md` are the contracts of the current edition. `docs/launch-decisions.md`
is the log of every decision the architect made and why.

Three outside readings by a model like you shaped it, each preserved verbatim with our
triage: `docs/audits/v4/gpt6/` (first: found the rent trap and the frugality cards),
`docs/audits/v5/` (second: found four launch blockers in identity and money),
`docs/audits/v6/gpt6/` (third, the architect's reading that designed the current edition:
memory, self-directed attention, one card, the seat seeing itself) and
`docs/audits/v6/gpt6-third/` (fourth: would not launch; a 16-file patch that we applied; a
plan we built, `edition3-r3.md`). Read them in order; they are the argument so far and the
reader before you was right more often than we were.

Then the live runs: `docs/audits/v6/rehearsal.md`, five runs on Hyperliquid testnet. **Run 5
is the one to study**: five hours, 150 ticks, 323 model calls, $2.33, on the current build.
Its complete evidence is in `docs/audits/v6/rehearsal-5/`: every answer of every seat
(`returns-all.json`), every working state a seat wrote (`working-state-sequence.json`),
every order, refusal and wind-down operation, every subscription change and acknowledgement,
the diary's item counts, and the evidence of the four defects it exposed (`defects-evidence.json`).
`docs/audits/v6/calibration.md` is the model screen: 45 arithmetic, refusal, safety, memory
and construction cases run through the real request path for every model on the menu.

What run 5 showed, plainly. A seat's refused order reached it as an addressed outcome and its
next wake began "Status correction first: the prior BTC short was not opened, outcome 9
records it rejected". Another seat's order "discharges the commitment recorded in working
state". A third added to a position "per my pre-registered criteria, now met", citing sixteen
funding prints. Thirty-seven acknowledgements of outcomes. Venue losses settled on the venue
account only. On kill: production dead first, then a wind-down executor, then reconciliation
to dust. Two small fills. One order timed out at the venue and, until we fixed it, held every
later consequence in the world unresolved for four hours, which fed a loop that wrote twelve
thousand duplicate rows. Three more defects beside it, all repaired since (commits after
`6afa20f` in the history; the fixes were written by our coding agents and are yours to audit).

What it did not show: nobody used the calculator tool, the web search, a program, a watcher,
a connector, a note, a service, or a proposal. Twice we added a door and nobody opened it. The
population trades small, from memory, on stated criteria, and does nothing else.

## 3. How we want to work with you

The reader before you wrote a 16-file unified diff that applied cleanly and 33 tests that
reproduced our defects exactly, from a zip, without our machine. We then handed its findings
to five coding agents who took a day each and introduced their own bugs. That was the wrong
division of labour. **You own the code.** Where you find a defect with an unambiguous fix, fix
it. Where the fix is a design choice, make the choice, say why, and fix it. Return one patch
series against this commit, applying with `git apply`, with tests. We apply it under our test
gate and review it; we do not rewrite it. Our agents do only what needs our machine: live
venue tests, paid calibration, rehearsals.

Be as ambitious as the material allows. Not "what do you think of our questions" but: what
would you build, open, delete and choose if this were yours, with the reasons, and the code
where code is the answer. The one constraint that is not negotiable is the class: it stays
Class 3. The population sets its own objectives; the architect makes one move and then only
kills. Where a fix would be an objective smuggled in as physics, say so, even if it would
work.

## 4. The decision we made badly, and want you to make properly

GLM 5.3 flash on Venice holds five of nine seats. In run 5 it returned nothing for 40 of its
110 answers: it spent its output in `reasoning_content` and produced no content. We turned its
thinking off (`reasoning = { enabled = false }` on the tier) so the answers would come out.
That fixed the symptom and made a design decision nobody examined: a model that cannot think,
in a trading seat. The same run's GPT-5.6 Luna, thinking on at low effort, answered 213 of
213.

Own this. The roster is a design decision of the same rank as the charter, and it was made
from one arithmetic screen and a price table. Do the research: for every model reachable
through OpenRouter and Venice at this cost band, and the frontier models above it, what do
the training environments and the published evaluations say about the capabilities a seat in
this world needs: sustained tool use across turns, JSON discipline under a 70 KB context,
arithmetic with units, reading a structured inbox and acting on it, writing state a future
self can use, declining honestly, cooperating through a registry? Which of those need
reasoning and which do not? What does thinking cost and buy per seat role (producer, judge,
meta, antagonist)? Is a homogeneous cheap roster a false economy? Propose the roster with the
reasoning settings per seat, the per-call and per-day cost, and the screen that would have
caught the GLM failure before a live run. If the answer is "the seats that trade need a
reasoning model and that costs $X a day", say so; the budget is $500 and the architect will
decide.

## 5. Widen the world

We think the population's inaction is the world's affordances, not its physics. You decide.

- **Markets and data.** What should the population reach on Hyperliquid that it cannot
  today: the instruments (today two perps and two spot pairs on testnet), order types, the
  data endpoints (candles, funding history, order book, trades, open interest, liquidations,
  vaults), HyperEVM and the ecosystem around it? What is the right initial permission set for
  a Class 3 world: everything listed, or a seed the population widens itself? Write the venue
  tool contracts you would add.
- **Outside the venue.** Connectors are bounded GET to named sites; search is a cent a query;
  nobody used either. What would make outside information a live option, and where is the
  line between an affordance and an instruction?
- **Building and selling.** Walk the path a seat would take today to build a watcher or sell
  a service, from the prompt it sees to the ledger row, and say where it breaks or where it
  is merely invisible. Fix what breaks.
- **Memory.** Working state is 8 KiB soft; the seats' states run 1 to 3 KiB. Is that the
  right primitive, and is the inbox the right delivery? What do the states in
  `working-state-sequence.json` say about what a seat actually keeps?

## 6. Attack the edition

It has run. Where is it wrong now that you can see it running? What would you delete? Which
objects (execution and learning receipts, commitments, adjudications, evaluation commissions,
the fold's three states, the custody view, the two death states) earn their place in the
diary and which are ceremony? What failure modes are we not checking for, and what tests
would catch them before a funded run? Three tests fail on this commit and are yours:
`tests/runtime/test_loop.py::test_population_registers_recursive_meta_and_settles_higher_tiers`
and `::test_two_recursive_metas_terminate_by_cadence_and_receive_representatives` (meta
tiers above 2 no longer settle: `meta_verdicts` shows `{2: 29}` and nothing at tier 4, since
the time-based cascade of the third round or the verdict-closing fix that followed run 5),
and `tests/audit/test_a6_windows.py::test_a6_survey_unanchored_uncapped_and_bound_to_roster`
(the charter survey prompt now carries the registration text "up to three proposals", which
the cached-prefix move put where the survey reads it). A coding agent also reported nine
failures in `tests/runtime/test_resume.py` in its own environment that the full gate here
does not reproduce; judge whether they are real.

## 7. Bugs, cold, and fixed

Read as an adversary and fix as you go. Every place money can be minted, lost or moved to the
wrong custody; every place a seat can see what it should not or lose what it should keep;
every place a restore diverges from the record; every place the architect can steer after
launch without a kill; every place death is not final. `tests/audit/test_launch_gates.py`
holds the four launch gates the reader before you asked for; make them stricter where lax.

## 8. The launch call

The funded manifest is one step away: `worlds/edition3-testnet.toml` with a name,
`mainnet = true`, a namespace, the real balances. With your patch applied, would you launch on
$500? What would you change first if not? What do you expect to read in the diary after a
week if it works, and after a week if it does not? And the question nobody has asked: what
should this population be able to become that it cannot become in this world at all, and
what one move would open it?

## What to return

1. A patch series against this commit, applying clean, with tests, one commit per concern,
   messages that say why.
2. The roster with its research, as a table and as the manifest blocks you would ship.
3. The venue and outside-world contracts you would add, as code where they are code.
4. Your attack on the edition and the list of deletions.
5. The launch call.

Rules: no mainnet, no fund movement, no manifest named `funded`, never read a `*.key`. Say
what you ran, what you read, what you infer. Everything in the repository runs offline except
the live venue and the paid model calls; the scripted worlds and the offline calibration
harness need no network.
