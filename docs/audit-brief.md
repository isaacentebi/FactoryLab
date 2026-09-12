# Cold audit brief

Five independent auditors, none of whom has seen this code before, in separate worktrees, no contact with each other, no contact with the build history beyond what the repository says. The point of five is diversity of blind spots.

- Three **fidelity auditors** answer all of questions A–E: Codex (`gpt-6-astra`, xhigh), a Claude Fable subagent, and a Claude Opus subagent.
- Two **defect auditors** answer only question C, in depth: a Claude Fable subagent and a Claude Opus subagent. (Codex was tried twice for this seat and was blocked both times by its provider's cybersecurity classifier once it read the sandbox and key-handling code; recorded here so nobody retries it.) They still read the essay first, because "will it run" includes running as the essay intends, but they do not opine on Class 3 or fidelity. They read every test as well as every module and try to break the world: fuzz the return shapes, malformed provider replies, venue failures mid-fill, a kill during every ledger write, concurrent resume, wallet arithmetic at the boundaries, the sandbox from a hostile tool, the x402 quote parser on adversarial headers.

## Required reading, in this order

1. The essay, in full: `~/Downloads/Superdark Factory.md` (a copy is placed in the auditor's worktree as `docs/essay.md`). An audit that has not read it cannot do this job.
2. `docs/design-audit-v2.md`, `docs/build-spec-v0.4.md`, `v0.5-phase2.md`, `v0.6-phase3.md`, `v0.7-phase4.md`, `docs/build-log.md`, `docs/handoff.md`.
3. The code: `factorylab/` and `tests/`, all of it. Run the scripted worlds. Read a dead world's diary (`runs/testnet-sixth.jsonl` with its key).

## The questions

**A. Is this a Class 3 factory?** By the essay's own definitions (Chapter I: classes, darkness, the neurotic governor; Chapter II: primitives, versioning, evaluations, charter), where does this system still take an objective from the architect rather than deriving one from norms and constraints? Where is the architect still inside the loop? What would demote it to Class 2 the moment it is switched on? What is missing for it to be superdark rather than merely automated? Be specific: file, function, contract.

**B. The four convergence pathologies** (Chapter II.II.a and II.IV.b: stable failure, overfitting, learning death, thrash). For each: does the design incentivise it? Through which reward, price, timing or disclosure? What would the first sign look like in the ledger, and would our versioning module see it? Which of the essay's remedies (priced duration, unhistoried compute share, sampling rate, cascade ratio, jitter, the adversarial minority) is present, absent, or present but toothless?

**C. Will it run?** Bugs, races, crashes, unhandled venue and provider failures, money paths that can lose or double-count a micro-dollar, the ledger's seal and hash chain, key handling, resume, the tool sandbox on Linux, the x402 signing path, the reconciler's identity check. Anything that could stop the world for a reason that is not the factory's own doing.

**D. Contracts.** Every contract the population sees (the world block, proposal shapes, return shapes, tool schemas, the charter render): is it thin enough to compose against and rich enough to act on (the essay's thick/thin tension)? Where does semantic coupling hide? Where is a rule dressed as a schematic? Where is disclosure more than minimal sufficient, or less?

**E. Reward hacking and collusion**, beyond `factorylab/settlement/REWARD_HACKING.md`: ways producers, evaluators, metas, antagonists or a committee could game verdicts, forecasts, the exposure channel, prices, amendments, the clock, the novelty reserve, registrations, or the market.

## What to deliver

`docs/audits/<auditor>.md` (fidelity-codex, fidelity-fable, fidelity-opus, defects-fable, defects-opus): findings ranked by severity, each with file and line, the essay passage it answers to (quote under 15 words with the section), the failure scenario, and a proposed fix or the statement that no fix is possible without demoting the factory. Separate "this makes it not Class 3" from "this will break" from "this is unclean". No fixes applied; findings only. Under 3,000 words.

## What is out of scope

Style, formatting, performance unless it threatens liveness, and anything the essay itself leaves open (the auditor may note it as open).
