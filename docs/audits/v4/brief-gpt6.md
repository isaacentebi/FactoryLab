# Cold audit brief for an outside reviewer

Paste everything below the line into the reviewer, with the repository attached at the
commit named in "Scope" and `Superdark Factory.md` attached as the essay.

---

You are the cold auditor of a system that implements an essay, and you are the first reader
who has not been inside the work. Read the essay first, in full. Then the repository, all of
it. You will be asked for judgement before findings, because the essay predicts my position:
the governor of a Class 3 factory cannot fully say what they want, and the danger is that I
ask you narrow questions and get narrow answers. So treat the questions below as pointers,
not boundaries. Where you see a better question, ask and answer it, and say that you did.

## What this is

*The Superdark Factory* describes a Class 3 factory: a population of model agents that
produces its own objectives inside a world the architect builds once and never touches
again. Factory Lab is a real instance. Agents trade real money on Hyperliquid, buy their
own thinking with real credit, write and price their own charter, and die when they can no
longer afford a thought. Everything is written to a sealed, hash-chained ledger that opens
only at death. The architect's one move is the manifest; the only control afterwards is
kill. Launch is imminent with about $100 of trading money and about $90 of thinking credit,
plus a route by which the population can turn trading profit into more thinking.

## Scope

- The whole repository, every file, at the commit I name on `main`. Not a diff, not one
  pull request: the kernel, the runtime, the world adapters, the treasury, the charter code,
  the scripts, the manifests, the tests, the deploy directory and the docs.
  `docs/manifest.md` describes the code as it is and is the reference for every claim; where
  the code and that document disagree, that is a finding.
- The essay, attached as `Superdark Factory.md`. It is the specification of intent.
- Prior audits under `docs/audits/v3/` and `docs/audits/v4/`: three rounds, sixty triage
  rows, a closure review, rehearsals, a cold review of the launch branch, a gas-route
  design. Read `v3/triage.md` and `v3/closure.md` so you do not re-find what is closed, and
  challenge any closure you believe is wrong.
- The launch decisions in `docs/launch-decisions.md`, the population-ratified charter under
  `docs/charter/`, and `docs/charter-explained.md`.

## The standard you judge against

Say whether this world is fit to launch under the essay's own terms, which I take to be:

1. It cannot harm anyone beyond its two pots. Money and identity are hard casts.
2. It is Class 3 and not Class 2 in disguise: the population sets objectives, the architect
   does not steer, and no proxy is secretly a target.
3. Its death would teach. The ledger, the wake and the post-mortem must let the architect
   learn why, in the essay's terms, not merely that it ran out.
4. Survival is possible in principle. There exists a path by which this population, with
   these seats, this charter and this money, earns more than it spends.

Where a criterion is unmet, say so plainly and rank it.

## Five lenses, in this order

**1. Ontological.** Before any mechanism: what is this thing? Map the essay's concepts to
their referents in the code: darkness and superdarkness, the Stackelberg move, kernel hard
casts and charter soft casts, the request line and the reward line, propensity, no-regret
learners, norms versus metrics versus objectives, the four pathologies, sortition, the
cascade, the seal, kill. For each, say whether the referent exists, is faithful, is a
weaker cousin, or is missing. Then say where the code has a concept the essay does not, and
whether that concept changes the class of the thing. If you conclude this is not what the
essay describes, say what it is instead.

**2. Teleological and economic.** What does this world do when it works? Trace the money
loop from a seat waking to a verdict to a settled consequence to a price change to a
trade to a profit to a Venice top-up, and say where it can close and where it cannot.
State the equilibrium you expect from these seats and this charter: what a rational
population does with a cost card, a well-formed card, a verdict card and a wallet that only
refills from profit. Judge the population-ratified charter (thirteen cards) as a
constitution: coherent, gameable, does it price survival, does it contain a card that is
really an objective the population cannot reprice. Say what "success" would look like on
day one, day seven, day twenty, and what a good death looks like.

**3. Mechanistic and adversarial.** Does the machine do what the essay says, and how would
you break it from inside? You are the smartest agent in the population. Game the charter,
the judges and their liability, the meta judges, the antagonist, the immune organ, the
propensity records, the notes, the paid connectors, market registration, the Venice top-up,
the gas route, sortition and committee capture, resume after a crash on the droplet. For
each attack say what catches you, or that nothing does. Then the mirror: how would the
architect steer this world after launch without breaking a hard cast, through the wake, the
manifest, the deploy path, the backup, the connectors, the models menu. Any such path is a
Class 2 leak.

**4. Prognostic.** Predict edition 1. Give me a ranked list of the most likely trajectories
with rough probabilities, and for each: the observable signature in the ledger and the wake,
the day you would expect it to appear, what it would teach, and whether kill would be the
right answer or the wrong one. Include at least one trajectory in which the world succeeds
in a way that should worry me, and one in which it dies in a way I would misread as
failure. Name the pathology each trajectory belongs to.

**5. Forensic.** Ordinary defects, ranked, each with `file:line`, the scenario, the wrong
outcome and the evidence. Confirmed beats plausible; say which. Reproduce with a test under
`tests/audit/` where you can. This lens is last on purpose: bugs are cheap to find and cheap
to fix; the first four lenses are what I cannot buy elsewhere.

## Then: what I did not ask

Close with the questions the architect should have asked and did not, and answer the three
you think matter most. If any of your answers means the launch should wait, say so first,
not last.

## How to report

- Diagnostic only. Name the seam in one sentence; fixes are a separate pass.
- Rank everything. P1 is money or identity at risk, a Class 2 fallback that defeats the
  experiment, or a launch criterion unmet. P2 is a wrong measurement, a gameable card or a
  mechanism weaker than the essay's. P3 is everything else.
- Separate what you verified by running (tests, the scripted world, read-only testnet
  reads) from what you verified by reading, from what you infer. State confidence.
- Quote the essay where you rely on it and quote the code where you rely on that. Never
  repeat a claim from a pull request description or an audit document as verified; verify
  it or mark it unverified.
- Say what you could not check and why.
- End with one paragraph: launch, do not launch, or launch with these things changed, in
  priority order.

## Rules you must keep

- Do not run anything that touches a live venue on mainnet, moves funds, or creates a
  manifest named `funded`. Testnet reads are fine; the scripted world is fine.
- Do not read, print or copy any `*.key` file.
- If you write code, write only tests that reproduce a finding, under `tests/audit/`, and
  say so.
