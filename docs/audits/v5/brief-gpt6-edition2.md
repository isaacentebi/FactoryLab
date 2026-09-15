# Cold review brief for edition 2 (for GPT-6 Pro, after wave two merges)

Paste everything below the line into the reviewer with the repository zip at the commit named
in "Scope", `Superdark Factory.md` as the essay, and your own prior deliverables
(`docs/audits/v4/gpt6/`) so it can hold itself to them.

---

You audited Factory Lab at `3a27fa4` and said do not launch. We built the edition you proposed,
narrowed to what one night of work could carry. This is the second reading. You are still the
cold reader: judge whether what was built answers what you found, not whether it was hard.

## What changed since your audit

Read `docs/audits/v4/gpt6-triage.md` (our triage of your report, with which findings we
verified and what we decided) and `docs/plans/edition2.md` (the frozen contracts C1 to C13
every workstream built against). In short: programs can be seats; each seat thinks from its own
entitlement and its children cost it; a card can be challenged by a replacement measured
alongside it; the endowment is locked and released on a committed schedule with a dormant
state before death; rent is byte-time; the release digest is ledgered and resume refuses a
different one; Venice confirms on debit evidence; the verdict baseline, cost card, tool card,
lot credit and blame floor are fixed; the population can sell a service over x402; the wake
shows earned, subsidy and principal-conversion separately and the architect can watch without
pushing; the charter is four read-only norms and eight cards, no quotas.

## Scope

The whole repository at the commit named here, with the diff from `3a27fa4` as the primary
object. `docs/manifest.md` is the reference for how the code is meant to behave; where it and
the code disagree, that is a finding.

## Questions, in order

1. **Did each of your findings close?** For F1 through F8 and the rent trap: closed, narrowed,
   or open, with `file:line` and the test that proves it if one exists. If a fix introduces a
   new seam, name it.
2. **Is a program seat a seat?** Trace one program from registration through routing,
   judging, standing, retirement and resume. Say where it is treated differently from a model
   seat and whether any difference is a leak (a way to act unjudged, unpaid, or unrecorded).
3. **Is the entitlement conserved?** State the invariant the code keeps between the wallet,
   the locked backing, the entitlements and the unallocated pool, and try to break it: release,
   debit, credit, child registration, retirement, dormancy, crash and resume.
4. **Can a bad card block its own replacement now?** Walk the challenge route. Say whether the
   incumbent still has a veto anywhere, and whether the trial can be gamed by the proposer.
5. **Is the architect still able to steer?** Repeat your outside-in analysis against the new
   deploy, backup, witness, wake and seller paths.
6. **Redo the prognosis** for this edition with these seats, this charter, this schedule, and
   say which of your seven trajectories moved and why.
7. **Forensics last**, ranked, `file:line`, reproduce under `tests/audit/` where you can.

## Rules you must keep

Do not run anything on mainnet, move funds, or create a manifest named `funded`. Do not read,
print or copy any `*.key` file. If you write code, only tests under `tests/audit/`. Separate
what you ran from what you read from what you infer, with confidence. End with one paragraph:
launch, do not launch, or launch with these things changed, in priority order.
