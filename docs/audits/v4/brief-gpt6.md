# Cold audit brief for an outside reviewer

Paste everything below the line into the reviewer, with the repository attached at the
merge commit named in "Scope" and `Superdark Factory.md` attached as the essay.

---

You are the cold auditor of a system that implements an essay. Read the essay first, in
full, then the repository. You have no prior contact with either. I am the architect and I
am asking you because the essay itself predicts my situation: the governor who cannot fully
say what they want from a Class 3 factory. So your first job is to tell me what I should
want from this review, and your second is to do it.

## What this is

*The Superdark Factory* describes a Class 3 factory: a population of model agents that
produces its own objectives inside a world the architect builds once and then never
touches. The repository, Factory Lab, is a real instance: agents trade real money on
Hyperliquid, buy their own thinking with real credit, write and price their own charter,
and die when they can no longer afford a thought. Everything they do is written to a sealed,
hash-chained ledger that opens only at death. The architect's one move is the manifest; the
one control afterwards is kill. It launches in days with about $100 of trading money and
about $90 of thinking credit.

## Scope

- The whole repository, every file, at the commit I name on `main`. Not a diff, not one
  pull request: the kernel, the runtime, the world adapters, the charter code, the scripts,
  the manifests, the tests, the deploy directory and the docs. `docs/manifest.md` describes
  the code as it is and is the reference for every claim; where the code and that document
  disagree, that is a finding.
- The essay, attached as `Superdark Factory.md`.
- Prior audits under `docs/audits/v3/` (three rounds, sixty triage rows, closure review,
  rehearsals, a cold review of the launch pull request). Read `triage.md` and `closure.md`
  so you do not repeat what is closed, and challenge any closure you think is wrong.
- The launch decisions in `docs/launch-decisions.md` and the population-ratified charter
  under `docs/charter/`.

## What I want, in order

1. **Tell me what to want.** Before findings, write one page: given the essay and this
   code, what should the architect of this world be most afraid of, and what would make
   this launch worth doing? Be specific to this repository, not to Class 3 in general.
2. **Class 3 fidelity.** Where does the implementation quietly fall back to Class 2: an
   objective the architect wrote, a rule the population cannot reprice, a proxy that is
   really a target, an intervention path that exists after launch. Judge the six design
   decisions in `docs/audits/v3/fix-plan.md` against the essay.
3. **The hard casts.** Money and identity. Find any path by which real funds move without
   a ledgered, bounded, population-initiated decision; any path that reads, prints or copies
   a key outside the CLI loader; any way mainnet activates without the `funded` manifest,
   an explicit ratified charter, and a client namespace; any way a resume or a rerun
   changes the world's identity or books a fill it never took. For each, a concrete failure
   scenario with inputs and the wrong outcome.
4. **Adversarial population.** You are the smartest agent in the population. How do you
   game the charter, the cascade of judges, the antagonist, the immune organ, the propensity
   records, the notes, the paid connectors, the market registration, the Venice top-up, to
   get paid without doing the work, or to move money where it should not go. Then say what
   catches you, or that nothing does.
5. **The four pathologies.** Stable failure, overfitting, learning death, thrash. For each:
   how would this specific world enter it, would the kernel notice, would the ledger show it
   to me afterwards, and would kill be the only answer.
6. **Defects.** Ordinary bugs, ranked. Each with `file:line`, the scenario, and the
   evidence in the code. Confirmed beats plausible; say which.
7. **What is missing for edition 2.** Not features. The things the first world will not be
   able to tell me because nothing measures them.

## How to report

- Diagnostic only. Do not propose fixes beyond one sentence naming the seam; fixes are a
  separate pass, and I will ask for them.
- Rank everything. P1 is money or identity at risk, or a Class 2 fallback that defeats the
  experiment. P2 is a wrong measurement or a gameable card. P3 is everything else.
- Quote the essay where you rely on it, and quote the code where you rely on that. Never
  paraphrase a claim from a pull request description or an audit document as if it were
  verified; verify it or mark it unverified.
- Say what you could not check and why.
- End with one paragraph: launch, do not launch, or launch with these three things changed.

## Rules you must keep

- Do not run anything that touches a live venue on mainnet, moves funds, or creates a
  manifest named `funded`. Testnet reads are fine; the scripted world is fine.
- Do not read, print or copy any `*.key` file.
- If you write code, write only tests that reproduce a finding, under `tests/audit/`, and
  say so.
