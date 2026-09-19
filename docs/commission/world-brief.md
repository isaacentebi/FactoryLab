# Commission: read this world and say what is wrong with it

A brief for a committee of models, convened once. **Read-only: do not write code, do not
patch, do not open a branch.** Read, argue, and tell the architect what you conclude.

---

## 0. What this is

A factory has been built from the essay `Superdark Factory.md`. It runs. It has survived four
outside readings and everything they found has been fixed. It has traded real money on
Hyperliquid testnet across five rehearsals.

And the population inside it does almost nothing. About 85% of its answers are hold or defer.
It has never built a tool, sold a service, written a note, searched the web, run a program it
wrote, or changed a single rule. Every trade it has ever made was the same idea, and the
architect could have predicted it.

**The commission: read the essay, read the code, read what the population actually did, and
tell us why.** Not a list of defects. The causal account: what pressures this world creates,
what behaviour those pressures select for, and why that adds up to a population that reasons
carefully and then declines.

Then say what would change it.

## 1. Read first

1. `Superdark Factory.md`, in full. **Chapter II is the specification** — primitives and
   contracts, the request line and the reward line, propensity, no-regret and no-swap-regret
   populations, recursive and adversarial evaluation, realized-consequence scoring, the
   charter, sortition, the kernel's hard casts, λ and its controller, loop period, phase and
   gain, the four pathologies. Hold the code against it closely.
2. The repository, in detail. `docs/manifest.md` is the map. Read the prompt a seat actually
   receives, the reward path from a decision to a score, what each action costs, and what any
   of it can earn.
3. `docs/audits/v6/rehearsal.md` and `docs/audits/v6/rehearsal-5/` — five hours on testnet:
   every answer of every seat, every state it wrote, every order, every refusal.
4. `docs/audits/v7/first-principles.md` — the most recent audit, and the origin of this brief.
5. `docs/audits/v6/gpt6/`, `gpt6-third/`, `docs/audits/v5/`, `docs/audits/v4/gpt6/` — the four
   earlier readings, in order, and what was built from each.

## 2. The facts

**The world.** Nine seats, each a call to a language model, waking on a tick of one to ten
minutes. Two pots: about $300 of model credit that buys their thinking, $120 of trading
capital. Every thought costs real money from that seat's own entitlement. A seat may think or
sleep; when it thinks it answers once in JSON; that answer may trade, buy market data, search
the web, write to its own memory, register a tool, a program, a watcher, a service, a market
or an observation, or propose a rule change. Judges grade work, meta-judges grade judges, an
antagonist tries to fool them. Money enters only through trading profit or a paid service.
When the thinking pot empties, the world dies. The architect can read everything and can only
kill.

**What five rehearsals showed.** 85% hold or defer. Four orders in five hours. Zero
registrations, proposals, notes, searches, services, programs, watchers or connectors since
edition 1. 77% of spend went to judging; the antagonist was the most expensive seat and never
attacked. Judges scored caution above action (0.77 versus 0.33). The one real act of
self-direction was seats rewriting their own self-descriptions — and they drifted toward
quiescence: the seat told "do not protect inaction as an identity" rewrote itself into
"preserve resources and wait for stronger evidence".

**Just repaired**, so you do not diagnose old symptoms: a quadratic runtime (a 500-event world
went from ten minutes to under a minute); the test suite halved; leverage and principal caps
removed as Class 2 impositions; fourteen money and venue defects; sixteen learning-signal
defects, including timeouts measured in the wrong unit, which had been censoring 95% of all
judge rewards; exact crash recovery; and per-section answer validation, so one malformed
optional field no longer voids a seat's trade. **None of this has been seen live yet**, so say
which of the behaviour above you expect it to change and which you do not.

**Deliberately not repaired, because these are design questions and therefore yours:**

- A producer's reward is an LLM judge's verdict, not what its trade earned.
- The only outcome the world can realize is profit and loss on a perpetual future.
- There is no seat-to-seat delivery: the inbox carries only the kernel's outcomes of a seat's
  own decisions. A seat can speak into a rented notebook nobody is notified of, and can hire
  another seat blind, but cannot address anyone and cannot be answered.
- Nothing is charged for existing, so declining is free.
- Seats are never born and never die of their own economics.
- Governance is gated so hard no rule has ever changed in a live world.
- The prompt is about 73,000 characters, roughly 90% institutional reference.

## 3. What to work out

**First, the pressures.** Go through the mechanics in detail and say what this world actually
rewards. Price a hold against an investigation against a build against a trade: in money, in
blame, in standing, in how soon anything comes back. Say which of the four pathologies this
population is in, and what in the code puts it there. Where the incentives contradict the
prose the seats are given, name both.

**Then the world.** The architect's hypothesis is that these seats have no world to inhabit —
nowhere to speak, nothing to buy, nothing to make, nobody outside who wants anything, no cost
of existing, no death. Test that hypothesis against the evidence rather than adopting it. If
the real cause is elsewhere, show it.

Treat these as open questions about physics, not a feature list. For each: what must the world
provide, what must it refuse to provide, and what in the essay supports that.

- **Address.** What is it for one inhabitant to reach another: who may address whom, at what
  cost, with what delivery, and with what right to ignore? Build the possibility of address,
  not a message board — if they want a forum they should have to build it, so say what out of.
- **Exchange.** What is it to buy something here, from another seat or from outside? What can
  be owned, priced, escrowed, refused, inherited?
- **Production.** What can be made besides a position, and how does a made thing persist, get
  used by someone else, and pay its maker?
- **Demand.** The service seller exists and has never earned a cent, because nobody outside
  can find or want anything. Where does real external demand come from, and what is its
  smallest honest version? What is this factory's interface to its outside, and who are its
  users?
- **Senses.** Outside information means a paid search or a bounded fetch needing a committee
  vote. No X, no Reddit, no news, no forum, no price from anywhere but the venue. What should
  they be able to read, and where exactly is the line between an input and an instruction?
- **Metabolism.** What does it cost to exist, and how do you make idleness expensive without
  building a treadmill that kills everyone who thinks?
- **Birth and death.** Should a seat own capital, go broke, and found children with a model
  and a prompt of its choosing? If not, what makes selection real?
- **Law.** Governance has never fired. What makes a rule change reachable inside a world's
  lifetime, without the essay's iatrogenic thrash?
- **Time.** Should the population own its own tempo?
- **Darkness, as a number.** What quantity over the sealed diary shows the population's
  objectives decorrelating from the seed, and what value counts as success?

**Scale is a legitimate answer.** If your conclusion is that nothing here works at nine seats,
$300 of thinking and $120 of capital — that the population is too small to specialize, too
poor to take a risk worth taking, or too slow to close a loop — say so plainly, and say what
the numbers need to be and why. "This needs $5,000 and forty seats before any of it can
happen" is a finding, not a failure, and the architect will treat it as one. Give the
reasoning, not just the figure.

**So is a wrong medium.** The essay's own subject is the automation of software. A world whose
only realizable outcome is profit and loss on a perpetual future may be incapable of Class 3
whatever is built around it. If that is your judgment, say it, and say what the right medium
is and what it costs.

## 4. What is fixed, and why

- **The class, and this one is absolute.** The population sets its own objectives; the
  architect makes one move before launch and thereafter can only watch and kill. **A Class 2
  factory is not an acceptable recommendation, at any level of performance.** The architect is
  not interested in a machine that pursues an objective he supplied, however well it pursues
  it, however profitable, however much easier it would be to launch. Any proposal that smuggles
  an objective in as physics must be named as such and will be rejected, even if it would work
  — especially if it would work, because that is the version that gets adopted by accident.
  If your honest judgment is that Class 3 is unreachable in this world, do not hand back a
  well-run Class 2 as consolation: say plainly that this world cannot host it, and say what
  world could, at what cost.
- **The kernel's hard casts.** Money is conserved. Nothing returns before it is paid for. An
  unaffordable action is infeasible. Death at the floor is final. Every sampled action is
  addressable and its propensity logged. Novelty is reserved. Nothing judges its own output.
  Population code has no network. The diary is sealed until death.
- **The norms**, as the architect's single move, read-only to the population.
- **The venue**: Hyperliquid, with spot, perpetuals, public data and HyperEVM. Say what the
  population should reach there that it cannot today.

These are commitments, not things beyond argument. If one of them is what makes a Class 3
factory impossible here, say which and why. Changing a kernel invariant means killing this
factory and starting another at v0 — a real option with a real cost, which the essay itself
describes. Price it and recommend it if that is your judgment.

Everything else — the nine seats, the roles, the judges, the metas, the antagonist, the
charter's cards, the commissions, the registration grammar, the prompt — you may delete.

## 5. How to run this

Convene a committee with distinct briefs rather than one brief read seven times. Astra
orchestrates. Use Opus, Fable, Sol, Grok and GLM as subagents, and any other model that earns
its seat. Suggested seats: the ontologist (Class 3 fidelity; what here is Class 2 wearing a
charter); the mechanism designer (the reward line, scoring rules, markets, futarchy for λ and
metric proxies, and what pays for exploration before consequences close); the political
theorist in Bratton's register (address, interface, users, what governance means for a
population with no stable membership, and whether this factory has an outside at all); the
empiricist (what populations of language models actually do, and what the rehearsal evidence
shows about attention, memory and refusal); the economist (capital, rent, bankruptcy,
reproduction, and what makes a small closed economy produce rather than hoard); the adversary,
whose only job is to explain why the proposed world will produce theatre — activity that looks
like a society and is not — and what measurement would expose it; and the measurer, who owns
darkness as a number and the early-warning signals for the four pathologies.

**Disagreement is settled by prediction, not consensus.** Where two seats disagree, each
states what a short run would show, and the run decides later. A minority report is required:
if a seat thinks the committee is wrong, its dissent ships with the conclusion.

## 6. Experiments to design, not to run

A 30-minute run on the existing testnet, at a short tick with seats waking in parallel, now
gives hundreds of real decisions. Consequence horizons are counted in ticks, so things resolve
inside a run. Design the experiments that would settle your disagreements: each changing one
thing, each with what it would show if you are right and if you are wrong.

A starting matrix, to improve or replace: the repaired build as baseline; a prompt cut to
about 8,000 characters; address and delivery between seats; a cost for existing; producers
paid by realized profit and loss instead of verdicts.

**One experiment the architect wants designed properly.** In a rehearsal, give every seat
somewhere to say what it would want the kernel or the world to be different. Not a survey the
architect writes, and never present in a launched world, where it would become a steering
channel and a Class 2 demand for self-disclosure (essay L193). It is diagnostic: what a
population reaches for, when it can finally reach for anything, is evidence about what it
lacks. Note that it is the address problem pointed at the architect — a seat cannot tell us
for the same reason it cannot tell another seat. Design its shape, its cost, who may read it,
when it exists, and what result would show the communication hypothesis is wrong.

## 7. Rules

1. **Read-only.** No code, no patches, no branches. Run the offline scripted world or read the
   evidence if it helps you think; change nothing.
2. **One round.** There is no second reading of your output. This bounds the process, not the
   ambition: it exists so a strong answer gets built instead of reviewed again.
3. **No bug lists.** The code is repaired. If you find a defect, one line at the end.
4. **Every institution you add deletes one.** The world only gets smaller.
5. Say what you read and what you ran. No mainnet, no fund movement, no manifest named
   `funded`, never read a `*.key`.

## 8. What to return

1. **The diagnosis, at most 1,000 words.** Why this population does nothing: the pressures,
   the behaviour they select, and the evidence that favours your account over the others.
2. **What would change it, at most 3,000 words**, as contracts rather than code: what exists,
   what each inhabitant may do, what it costs, what it earns, how it dies. Answer on two
   tracks, and say which you believe: the smallest intervention that could work on what
   exists, roughly two weeks of building; and the world you would actually build if that
   cannot produce a Class 3 factory, at whatever ambition the material deserves, priced in
   weeks and dollars, with the cheapest experiment that would tell the architect within a day
   whether it is right. If the first track is theatre and you are only writing it because it
   was asked for, say so.
3. **The deletion list**, and what is lost with each.
4. **The experiment set**, with sealed predictions per seat, including the wish channel.
5. **The darkness measurement**: the number, its formula over the diary, and the value that
   counts as success.
6. **The minority report.**
7. **The launch call.** With your changes made, would the architect launch on $500 — and if
   not on $500, on what? What would they expect to read in the diary after a week if it works,
   and after a week if it does not.

The standard is the essay's own: if the architect can predict what the factory becomes, it is
not the factory the essay describes.
