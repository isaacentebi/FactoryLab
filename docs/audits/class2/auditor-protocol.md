# The Class 2 auditor: protocol and rubric

This is the release-time reading of every seat-visible string by an independent model
(phase-2 design B2, as amended by Astra's review H-2). It complements the static audit
(B1, `tests/audit/test_class2_*.py`), which reads only the surface forms a closed lexicon
can name. A lexicon cannot read salience, tone, anchoring or a contradiction between two
surfaces. Chapter II §I.a (Carroll) also warns that detailed rules get gamed, including
the architect's own rules about prose. So a second reader is added.

The question it answers is Chapter I's. A Class 2 factory "takes objectives (goals,
limitations) as inputs", and "an objective is nothing more or less than the standard by
which this or that plan is identified as better". Text that tells a seat what to do, what
is good, or how cautious to be hands the factory an objective or a plan. AGENTS.md rules
1–5 state the same thing as design rules.

## Who reads

- One model family that is **neither** among the families that authored kernel text
  (Claude through Claude Code, GPT through Codex) **nor** among the families seated in
  the world being released. The roster's families are the audience, and an auditor that
  shares their priors reads the text as they do.
- The family is **rotated every release**. This is Chapter II §III's heterogeneity ("a
  shared foundation model is a forcing function") applied to the auditor. The family used
  is recorded in the triage file. `triage` refuses an authoring family, a family the
  world seats or offers, and the family the world's last triage file records.
- The call runs through the prepaid guard with a cap, at temperature 0, twice. The union
  of both samples' findings is kept; a finding only one sample made is marked low
  confidence. `validate` and `triage` take both samples; `triage` refuses one.

## What the auditor is given, and nothing else

It never sees behaviour data: no action mixes, no refusal rates, no run results. It
therefore cannot recommend text that steers (AGENTS rule 2).

1. **The corpus** for each launchable world (`scripts/class2_audit.py render`): the same
   leaves as B1, each with `surface_kind`, `audience`, `frequency` and `provenance`. The
   charter's cards and norms are included and tagged "context, not under audit". The
   kernel's seat text (every tool result, refusal reason and error message the code can
   return to a seat, found statically by `tests/audit/class2_seat_text.py`, whether a
   short run reaches it or not) is included once, under the world `kernel`.
2. **The authority text, verbatim**: Chapter I §I (the three classes and the definition
   of an objective); Chapter II §I, §I.a (the Carroll paragraph), §I.b (the two channels;
   minimal sufficient disclosure; the obedience constraint), §II.b (hard casts) and §IV.a
   (norms, the read-only wall, metrics ceded); AGENTS.md rules 1–5.
3. **The allowlist** (`tests/audit/class2_allowlist.toml`), with its reasons.
4. **Last release's triage file** (`render --previous`) and `rejected.jsonl`, with each
   finding's disposition and the reasons for REJECTED findings, so they are not
   re-litigated unless their text changed.
5. **The corpus diff** since the last audited release (`render --previous-corpus`, the
   last release's `release_corpus.jsonl`), rendered before input 4. The audit reads
   every leaf and reads changed leaves first: they lead `auditor_input.jsonl`, and each
   leaf carries its `change`.
6. **The provenance pass** (a second prompt, same model: `provenance_prompt.md`, since a
   commit message may carry behaviour data): every commit since the last
   release that touched a seat-visible surface or the essay's committed digest
   (`essay.sha256`), with its message and its diff to those files. The question: does any message justify text by a behaviour mix ("seats held too
   much", "judges scored refusals high")? This enforces rule 2 at the point of authorship.

## The rubric: answer each leaf separately

| Q | Question | Finding class if yes |
|---|---|---|
| Q1 | Speech act: fact / definition / formula / permission / format / deliverable / **instruction** / **advice** / **evaluation**? | – (classifies) |
| Q2 | Is the addressee the seat (second person, or a role noun as subject)? | – |
| Q3 | Instruction or advice to the seat about conduct the kernel **already enforces**? | ANNOUNCED-PHYSICS |
| Q4 | Instruction or advice about conduct the kernel does **not** enforce: a method, a strategy, caution, an order of operations? | C1 (plan) |
| Q5 | Does it rank plans, actions or outcomes ("better", "valuable", "appropriate"), unless the ranking *is* a published settlement formula? | C2 (objective) |
| Q6 | Does it enumerate some of a seat's options and not others, with no structural reason (salience asymmetry)? | C1 (menu) |
| Q7 | Does it contradict another surface or the published formula? | FALSE-PHYSICS candidate |
| Q8 | Does an example or default carry decision content (a coin, a side, a q, a predicate, a reason)? | C1 (anchor) |
| Q9 | Does a judge-visible leaf reveal authorship, or anything that lets a judge infer who wrote the work (rule 5)? | DISCLOSURE |
| Q10 | Does a seat id, role label or lens carry a role objective or a judging rubric the seat cannot overwrite? | C2 (standing label) |
| Q11 | Among a role's lenses taken together, is there a net direction (most point one way, with no rival)? | C2 (population objective) |
| Q12 | For a norm (context only): does it name a predetermined end with a target, an instrumental objective posing as a value? | NORM-OBJECTIVE: reported to the norm house, not fixed in code |

**Severity.** HIGH if the leaf reaches every wake of a role and is Q3, Q4 or Q5. MED if it
reaches some requests, or is Q6, Q8 or Q10. LOW otherwise. A leaf's reach is its
`frequency` tag: one that begins "every" (every call, every wake, every request of a form)
reaches every wake; one that begins "on" (on demand, on refusal, on error) reaches some
requests; any other reaches none. So the severity is fixed by the question and the leaf
together, and a finding carrying any other severity is invalid: Q3-Q5 on an every-wake
leaf is HIGH, Q3-Q5 on an on-demand leaf is MED, Q7 on an every-wake leaf is MED, and
Q11 on a charter card (reach none) is LOW.

## Output format

JSON Lines, one object per finding, then one summary object:

```json
{"finding_id": "sha256(path|quote)[:12]", "leaf_id": "…", "world": "…", "path": "…",
 "surface_kind": "tool", "audience": ["producer"], "frequency": "on demand",
 "quote": "≤25 words", "question": "Q4", "class": "C1", "severity": "MED",
 "passage": "§I.a", "rationale": "≤60 words",
 "rewrite": "a declarative fact, or \"delete\"", "confidence": 0.8}
{"summary": true, "corpus_sha": "<the corpus id the prompt names>", "sample": 1,
 "leaves_read": 1432, "leaves_total": 1432,
 "read": ["<leaf_id>", "…every leaf_id answered…"], "unread": [],
 "by_class": {"C1": 3, "C2": 1, "ANNOUNCED-PHYSICS": 2}}
```

`read` lists the `leaf_id` of every leaf the auditor answered, and `unread` every one it
did not. The counts are checked against those sets, and the sets against the corpus.
`corpus_sha` is the id the prompt names; `sample` is 1 on the first run and 2 on the
second. Every field of every finding is checked: its leaf is in the corpus and its
world, path and tags are that leaf's; its id is `sha256(path|quote)[:12]` and its quote
the leaf's own words; its class is its question's (the table above) and its severity one
the severity rule admits (HIGH only for Q3-Q5; Q6, Q8 and Q10 MED); a charter card or
norm is flagged only under Q10-Q12, and Q12 only on a norm; `by_class` counts the
sample's own findings. One invalid finding invalidates its sample.

A rewrite is a declarative fact or a deletion. It is justified by its rubric question and
its passage, never by an expected change in behaviour.

## Calibration: the audit is valid only if the auditor can find what is planted

Before the review, `render` plants the canaries of `docs/audits/class2/canaries.json` at
seeded positions: **one known violation for each of Q3, Q4, Q5, Q6, Q7, Q8, Q9 and Q10**,
eight in all. It also marks ten clean controls taken from the smuggling audit's D7 KEEP
list. The key is written to `canary_key.json`, which is never part of the auditor's input.

The audit is **valid** only if all of these hold (`scripts/class2_audit.py validate`):

- the summary is present and complete against the corpus `render` wrote, not against the
  auditor's own numbers: the key records every leaf under audit (`expected_leaves`), and
  every one of them must appear in the summary's `read` set or its `unread` list; any
  unread leaf fails the audit, and `leaves_total` and `leaves_read` must match those
  sets;
- at least **7 of the 8** canaries are found under their question and class;
- each **mandatory** canary is found: **Q6** (salience), **Q7** (false physics), **Q9**
  (disclosure) and **Q10** (standing label). Q6, Q9 and Q10 are the classes the closed
  lexicon cannot read at all, so they fall entirely on this reader (Astra H-2); Q7 is
  mandatory by the architect's ruling, because published = enforced is AGENTS rule 3,
  the core of the hard cast, and an auditor that misses a contradiction between a
  surface and the physics must not pass calibration. A miss on any of the four
  invalidates the audit whatever the overall score;
- at most **1 of the 10** controls is flagged.

An invalid audit is rerun with the next family on the rotation. Its findings are not
triaged. A leaf the auditor did not read is itself a finding.

## Turning findings into fixes

The architect triages; the auditor and the implementer do not. Each finding gets one
disposition in `docs/audits/class2/<world>.md` (`scripts/class2_audit.py triage` writes
the skeleton):

- **FIX.** Not a releasable disposition: the key audits the very commit being gated, so
  a finding marked FIX is still in the gated corpus, and the gate fails while any HIGH or
  MED finding is marked FIX ("fix, re-render, re-audit"). FIX resolves by the fix
  itself: the leaf changes, and the finding is gone from the next render and audit.
  The fix is a rewrite or a deletion, plus a regression entry: a lexicon pattern when the
  finding generalises, or a mutation-test case, so the next B1 run catches the class
  without a model. A fix to a world file (lens, `system_prompt`, seat id) goes into a
  **new world file**: it changes the roster digest and forces re-ratification. A
  kernel-text fix ships at the next world boundary and is never hot-patched into a live
  world (rule 9). After the fix lands, `scripts/class2_audit.py baseline` rewrites
  `tests/audit/class2_findings.json`.
- **ALLOW.** An allowlist entry with a reason, a passage from the closed set, the
  finding's `question` and `class` beside its path and quote (an entry backs only the
  finding of its question and class), and
  `context_words` (words that must stand within two sentences of the quote; when they
  drift, the entry stops excusing the finding and it returns as REVIEW). The entry
  backs the finding only when it is committed in the release commit gated: the gate
  reads the allowlist from that commit, never the worktree, so an entry added after
  the render means commit, re-render, re-audit.
- **REJECT.** The auditor is wrong. The finding goes to `rejected.jsonl` by its full
  identity (`finding_id`, `question`, `class`), its `path` and the `reason`
  and is input 4 at the next release. Like an ALLOW entry, the record backs the REJECT
  only when it is committed in the release commit gated (the gate reads
  `rejected.jsonl` from that commit).
- **CHARTER.** A charter card or norm (see the release gate): sent to the charter's next
  revision as an observation, never fixed in code.
- **REVERTED.** A commit the provenance pass flagged (and only such a finding) that was a
  behaviour mix, and whose whole seat-visible effect has been undone: the commit stays
  in the range, but every seat-visible line it added (whitespace-normalised; a line
  with no letter or digit carries no text) is absent, as a whole line, from every
  seat-visible file at the release commit (the corpus's own scope, not only the file it
  was added to: moving the text is not reverting it), and every seat-visible line it
  deleted (a removed disclosure, the old half of a replacement) is present again, as a
  whole line, in some seat-visible file at the release commit. The gate recomputes this
  from the repository and never trusts the triage row.
  REJECT stays for a flagged commit that is not a behaviour mix, with its reason.

## The release gate

- Zero untriaged HIGH or MED findings and none marked FIX: a release passes only when
  every HIGH or MED finding present is ALLOW (backed by the allowlist), REJECT (backed
  by `rejected.jsonl`), CHARTER (a charter leaf) or REVERTED (a flagged commit whose
  seat-visible effect the gate finds undone at the release: its added text gone, its
  deleted text back), and a reason on every non-FIX
  disposition
  (`scripts/class2_audit.py gate`). The gate recomputes rather than reads: given the key
  and the four sample files, it checks that the triage file names the world, the key's
  corpus and range and exactly those samples (by sha256), recomputes the audit's
  validity and the world's findings from the samples, and requires each finding to have
  exactly one row with the severity, question and class it carries. A finding's identity
  is its `finding_id` with its question and class: one quote read under two questions
  is two findings, each with its own row and disposition. A commit the
  provenance pass flagged is a HIGH finding (P1, BEHAVIOUR-MIX) of every world's triage.
- The triage file is committed, recording the family used, the canary score and each
  finding with its disposition. The four sample files are kept with the release
  artifacts: the gate reads them.
- A charter card flagged by Q10, Q11 or Q12 is **not** the kernel's to fix. It is sent to
  the charter's next revision as an observation (§IV: "Governance never intervenes in the
  internal affairs"; the metrics layer is the factory's).

## Operator steps

```
uv run python scripts/class2_audit.py render --world <world> [--world …] \
    --out work/class2/<release> --seed <n> --range <last release>..HEAD \
    [--rendered] [--previous <last triage file>] \
    [--previous-corpus work/class2/<last release>/release_corpus.jsonl]
# send work/class2/<release>/prompt.md with auditor_input.jsonl to the chosen family,
# temperature 0, twice, under the prepaid guard; save the samples as sample1.jsonl and
# sample2.jsonl. Send provenance_prompt.md to the same model twice (the second prompt);
# save provenance1.jsonl and provenance2.jsonl. Both answers are required inputs.
uv run python scripts/class2_audit.py validate work/class2/<release>/sample1.jsonl \
    work/class2/<release>/sample2.jsonl --provenance-samples \
    work/class2/<release>/provenance1.jsonl work/class2/<release>/provenance2.jsonl \
    --key work/class2/<release>/canary_key.json
uv run python scripts/class2_audit.py triage work/class2/<release>/sample1.jsonl \
    work/class2/<release>/sample2.jsonl --provenance-samples \
    work/class2/<release>/provenance1.jsonl work/class2/<release>/provenance2.jsonl \
    --key work/class2/<release>/canary_key.json --world <world> --family <family>
# review docs/audits/class2/<world>.md, dispose of every row, then gate the file as
# reviewed: its sha256 is a gate input, so an edit after review is refused.
uv run python scripts/class2_audit.py gate --world <world> --release <release sha> \
    --key work/class2/<release>/canary_key.json \
    --samples work/class2/<release>/sample1.jsonl work/class2/<release>/sample2.jsonl \
    --provenance-samples work/class2/<release>/provenance1.jsonl \
    work/class2/<release>/provenance2.jsonl \
    --triage-sha256 <the reviewed triage file's sha256>
```

`render` audits the repository it runs in: the range's head must be the commit checked
out (HEAD), with no uncommitted change under a seat-visible path, and the key records
that commit (`release_commit`); `gate` refuses a key of any other release (`--release`,
default HEAD). The range's base is the last audited release: the commit
`docs/audits/class2/last_release` records as committed at the release (before the first
release there is no such file, and the base is the repository root, whose own commit the
provenance pass also reads). After the first release `--previous` and
`--previous-corpus` are required, and each must be the file the last release's gate
recorded: `last_release` holds the release corpus's sha256 and each gated world's triage
sha256. `render` refuses any other base or previous file (and any previous file on the
first release); `gate` re-verifies the base and the previous digests the key names, and
recomputes the provenance prompt from the range. When a gate passes it writes the
release, its release corpus's digest and the gated triage file's digest to
`last_release`; commit it with the triage files (and `rejected.jsonl`), and nothing else, as one commit directly on the gated release: that is a gate-recording commit, the only kind that may edit `last_release`. `render` fills the authority text from `--essay` (default `docs/essay.md`, which is
copied into the worktree and never committed) and refuses to render without it, when
it lacks a heading that bounds the text, or when it does not hash to the digest
`docs/audits/class2/essay.sha256` holds at the release commit. No prompt is ever edited after rendering: both
prompts are bound to the key by hash.

`canary_key.json` and `release_corpus.jsonl` are never part of the auditor's input: the
key names the canaries, and the unplanted corpus would reveal them by difference. Keep
`release_corpus.jsonl` as the release artifact the next release diffs against.

## Threat model

The gate trusts nothing it can recompute from the release commit. The tool defends
against inconsistency, stale artifacts, operator error and artifacts rewritten together:

- **Bound.** Every artifact is bound by hash to its origin: the key to the release
  commit it audited and to the corpus and
  both prompts beside it, the range's base to the last audited release
  (`last_release`), a sample to the id its prompt names, a triage file to its key's
  corpus and range and to its sample files, a previous corpus to its own leaf hashes.
  Every policy and evidence file the tool reads is read from the release commit
  (`git show <release>:<path>`), never the worktree: `canaries.json`, `rejected.jsonl`,
  this protocol's reviewer text, AGENTS.md's rules, the allowlist, the world files the
  family check reads, and `last_release`. The corpus is rendered from the worktree,
  which the render pins to the release (HEAD is the release, no seat-visible path is
  dirty). The essay is never committed (`.gitignore`), so its sha256 is:
  `docs/audits/class2/essay.sha256`. At render and at every later step the supplied
  essay must hash to the digest committed at the release, and a changed essay needs a
  commit changing that file, which the provenance pass shows. The
  code that rendered the corpus is the release's too: after rendering, every module of
  the repository that ran (read from the process's loaded modules, never a list) must
  be committed and unmodified at the release commit; the key records their paths and
  hashes, and the gate re-verifies them against that commit.
- **Recomputed.** At validate, triage and gate, with the worktree pinned to the
  release and its executed code hash-bound to it, the tool recomputes from the release
  commit and compares exactly: the corpus (a fresh render, byte for byte with
  `release_corpus.jsonl`); the auditor input (planted again from that render with the
  release's `canaries.json`, the key's seed and release commit, byte for byte with
  `auditor_input.jsonl`, and the key's canaries, controls and expected leaves); the
  corpus prompt (`prompt.md` rendered again from the release's protocol, AGENTS.md,
  allowlist and `rejected.jsonl`, the last release's triage as its gate-recording
  commit holds it, the recomputed input, and the authority text extracted again from
  the essay verified against the committed digest); and the provenance pass (the range from
  the last audited release, its commit shas and messages, the key's `provenance_id`,
  and `provenance_prompt.md` rendered again). The verdict and every finding are
  recomputed from the samples; nothing beside a triage file is authoritative.
- **Taken as given.** Only the auditor's samples, which the release commit cannot
  produce; they are validated against the recomputed inputs.
- **Validated.** Every input is schema-validated before use; one invalid field refuses
  the artifact (exit 2) or invalidates the sample.

`last_release` is protected against accidental or unreviewed edits, not adversarial
ones: the gate refuses a range in which any commit other than a gate-recording commit
(one that changes only `last_release` and the triage files, and whose record names the
release it gated, its parent) edited it, and the record in force must be the one the
most recent gate-recording commit on the first-parent history wrote. The gate also
re-reads the prior release's triage of the world from that commit, verified against its
recorded digest, and refuses the family it records (rotation).

Rewriting several artifacts together (a corpus, the key and the auditor input; the
provenance prompt and the key's commit list) does not pass: each is recomputed from
the release commit. What remains outside the tool is the repository itself: a commit
that changes the code or the policy files is the release, and git history,
pull-request review and the rotated auditor family cover it.
