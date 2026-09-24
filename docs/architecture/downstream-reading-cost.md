# Downstream reading cost: should a return's author pay for what its readers read?

Design note, wave 7. Nothing here is built. It asks for a ruling.

*Wave 11 note.* Retained-storage rent, the precedent this note leans on, was removed
in Wave 11: the bytes sit on the world's own fixed-price disk, so the rent paid no
one, and the engineering rule is now that the wallet moves only when money moves.
Option B below (a debit to the commons that credits nobody) is excluded by that
rule; a reading cost is a constraint, priced by the charter's λ on reward through
the `downstream_read_bytes` observation, or a limit.

## 1. The question

In a live run the median rendered prompt grew from 21k to 29k characters in 100
ticks. Some judges were rendered INPUTS sections of 42k characters, because the
return they judged was that large. Today the author of a return pays nothing for
the context its return puts in front of its readers. Each reader pays for its own
call through its own seat meter (`_liable_seat`, `_seat_meter`).

The proposal: the author pays for the context its return imposes on the seats that
read it downstream. It has been framed as the bounded-reciprocity norm made
physical.

Wave 7 has already built one precondition: the fact is now published. Four seed
observations exist: `prompt_bytes`, `you_bytes`, `inputs_bytes` and
`downstream_read_bytes` (see `docs/manifest.md`, "Exact measurement").
`downstream_read_bytes` files each routed reader's INPUTS bytes under the author of
the return it was commissioned on, per role and per assembly. So the charter can
price reading cost today, with no new mechanism, through a card and its λ. The rest
of this note is about whether the kernel should also enforce a charge.

## 2. What the reading flow looks like today

The seats that read a return because it was published:

- Judges (`_evaluator_step`). The subject's `outputs`, the `inputs` of the event the
  author answered, and the kernel's `executed_operations`, inside INPUTS. Beside
  them in INPUTS: the charter text, the predicate catalogue, the early-warning
  view and the commission.
- Metas (`_meta_step`). The verdict and its rationale, written by the judge. Also
  the producer's `producer_outputs`, written by the producer, so one reading
  covers two authors.
- Adversarial judges (`_counter_step`). The verdict and the producer's outputs.
- Any population contract routed on the return's kind (`decision_subjects`).

Children are not in this list. A child's request is written by its parent, and the
child's call already spends the parent's entitlement (`_liable_seat`). Composition
already makes the author of a request pay for its reading. Only routed readers are
unpaid by the author.

Artifacts are not a side door today. `artifact.get` refuses another seat's
artifact (`artifact_private`), so a pointer in a return is something the judge
cannot open, and it reads as an unverifiable claim.

## 3. Where the charge would land

| Option | What moves | Assessment |
|---|---|---|
| A. Charter only | Nothing new. A card over `downstream_read_bytes` per assembly, and the λ penalty on the author's settled score through per-scope attribution | Available now. Chapter II's own route (§IV.a, §II.b). The population decides whether the quantity is a pathology. |
| B. Debit to commons | The author's seat meter pays a reading charge (`tool:return.read`, as rent is `tool:state.storage`). The charge goes to the decision's cost contribution. Nobody is credited. | The least-bad physical form (§9). |
| C. Transfer to the reader | The author's entitlement is debited and the reader's is credited | Rejected. Reading becomes income, so readers are paid to seek large returns: judges would inflate, and author and reader could collude. A credit that names or implies its source is a value channel between seats keyed on content, and AGENTS.md rule 4 allows no third channel. |
| D. Publication fee | The author pays per byte at publication, whether or not anyone reads the return | Simple and split-neutral. It charges for readings that never happen and ignores routing, so it is a price on writing, not on imposition. |

## 4. How it would be metered

| Basis | Honest? | Gameable? |
|---|---|---|
| The reader's reported input cost | Not computable from the ledger today (§8) | Yes, by the reader's model choice and continuation rounds. It also leaks the reader's price, and so its family, to the author (§5.4). |
| The reader's whole INPUTS bytes (what `downstream_read_bytes` publishes) | Yes, as a fact about the reader's context | An upper bound on the author's share. It includes the charter, predicates, early warning and commission, which are the same for every author. Right for an observation, too coarse for a charge. |
| Bytes the author wrote, as rendered in each reader's request (`outputs` minus `UNJUDGED_OUTPUT_FIELDS`, plus the published propensity), times one published rate | Yes, once the kernel counts them where it builds the reader's request (§8) | Readers cannot inflate it, and splitting a return does not reduce it (§6). |

If a charge is built, it should use the third basis: author-written bytes, times the
number of readings, times one rate fixed for the world's life and published in the
schematics, as `storage.micro_per_byte_day` is. The kernel-generated
`executed_operations` stay out of it. They record the author's acts, not its
prose, and charging for them would be a tax on acting.

## 5. Interactions

### 5.1 Rent on working state

Rent prices bytes the author keeps (byte-time, `continuity.charge_window`). A
reading charge would price bytes the author sends (bytes times readers). Without
the second, returns are a medium others pay to read while state is rented, so there
is an asymmetry that favours carrying memory in returns. A reading charge should
follow rent's accounting exactly:

- through the author seat's own meter;
- into the author decision's cost contribution in the window the charge lands, so
  cost cards see it;
- an unaffordable charge stays due, as `state.rent_due` does. It never blocks or
  delays a reading, and never delays the verdict.

It should differ from rent in one respect (§5.2).

### 5.2 The reward chain (§III.b)

Rent is carried into the author's open consequence outcome (`consequences.carry`),
so `return_paid_off` cannot settle on a margin that storage has already consumed. A
reading charge must not be carried there.

The consequence of a producer return is the signal that grades its judges (ruling
R1). How many judges read a return is set by routing and `multi_judge_share`, not
by the author. If reading cost entered that consequence, judges would be graded
partly on their own activity. That breaks Chapter II's fourth design principle
for evaluations (§III): "the signal that grades an evaluator must sit outside the
loop that evaluator judges".

The charge therefore belongs in cost contribution and entitlement, never in the
realized consequence. Producers keep learning from verdicts. The charge reaches
them only as spend and through any card over it.

### 5.3 The niche for unhistoried actions (§II; ruling R5)

The niche exists to prevent learning death, and exploratory returns are the ones
most likely to be unusual in size. A reading charge on a decision whose action the
niche covered should be paid from the niche, within that seat's
`novelty.seat_share`, as the call itself was. Otherwise the charge becomes a tax on
exploration, which is exactly the pressure the niche is built to offset.

### 5.4 The clean context (§I.b; AGENTS.md rule 5)

Three conditions keep the charge from leaking identity:

- **The reader's request is untouched.** The charge is computed after the reading,
  from the kernel's own attribution (`decision_subjects`, `handle_to_assembly`),
  as `downstream_read_bytes` already is. Nothing about the author enters the
  reader's context.
- **The debit names no reader.** The author learns only "return h was read for N
  bytes this window", collected at the window boundary as rent is, so timing does
  not single out a reader. The number of readings is a routing fact already fixed
  by public parameters (`multi_judge_share`, `multi_judge_count`).
- **The rate is uniform.** A debit priced at the reader's own model price would
  tell the author which family judged it. That is an identity leak in the other
  direction: an author could fit itself to particular judges. A published per-byte
  rate carries nothing.

## 6. Failure modes

- **Splitting.** Under author-bytes times readings, one return of 5x bytes read
  twice costs what five returns of x bytes read twice cost, so splitting gains
  nothing. Under the whole-INPUTS basis, splitting costs more, because every reader
  also carries the fixed charter overhead. The pressure then runs the other way,
  toward consolidation: one very large return gets fewer readers per byte. This is
  one more reason the observation's basis is wrong for a charge.
- **Pointers.** Moving content into an artifact takes it out of the reader's INPUTS.
  Today the judge cannot open another seat's artifact, so the author trades bytes
  for an unverifiable claim, and that is the judge's to price. If artifacts ever
  become readable across seats, the reader's `artifact.get` of the author's blob is
  reading the author imposed. It would then have to be metered too, by the same
  basis.
- **Readers inflating.** Under option C, readers are paid to read. Under B with the
  reader's own price, readers can inflate by model choice and extra rounds. Under B
  with author bytes and a fixed rate, readers have no lever: they neither gain from
  the charge nor set its size. What remains is a hostile population contract
  registered to accept a kind and read everything. It raises the author's charge
  only by paying for its own whole call, a cost to the attacker above the harm it
  does.
- **Governance capture.** Authors who pay per reading gain an interest in fewer
  readings: they could vote down `multi_judge_share`, or retire judges. Chapter
  II's answer to overfitting is a higher sampling rate (§IV.b). A charge that makes
  producers a constituency against evaluation cuts against it. The fixed rate
  should be small, and the charge should be reviewed against the sampling rate the
  charter sets.
- **Metas read two authors.** A meta's reading of a verdict holds the judge's
  rationale and the producer's outputs. `downstream_read_bytes` files it all under
  the verdict's author. A charge would have to split it by author-written bytes.

## 7. Does any Chapter II passage argue against it?

Yes, four:

1. **§I.a, robust simplicity.** "The less we know, the less structure we impose."
   We do not know that long returns are bad. A long return may be the most useful
   one. A kernel toll is structure. The only justification that survives AGENTS.md
   rule 2 is cost incidence: who bears a real cost they caused. "Prompts grow, make
   them shrink" is a behaviour-mix delta, and building to it is the architect
   optimizing toward its own "better".
2. **§IV.c, on upper tiers starved of variety.** Upper tiers are starved by the
   cascade: settling, distributions and jitter. Their penalty for collapsing
   downward is "wasted compute and degraded scores", natural consequences and not
   a toll. A meta today reads the producer's raw `producer_outputs`. That is
   downstream variety reaching tier two unfiltered. Chapter II's own answer is what
   a tier is shown (information design, §I.b and §IV.c), not a price on the author.
   This may be the real finding under the 42k INPUTS, and it deserves its own
   ruling.
3. **§III, on evaluator compute.** Evaluators are "likely" to consume more compute
   than producers. That is expected, not a pathology. Moving evaluation's input
   cost onto producers reallocates a cost the chapter treats as the price of
   evaluating.
4. **The bounded-reciprocity norm.** It reads "Do not finance the factory's
   advantage by imposing unconsented costs on outsiders." Its subject is outsiders
   to the factory. A judge is not an outsider, so citing the norm seat-to-seat is an
   analogy, not the norm. It is also charter text, and the charter is co-written
   (§IV.a). Making a norm physical in the kernel takes it out of the charter's
   hands.

In its favour:

- §II.b: pathologies are priced live.
- §IV: "speed is categorically indistinguishable from a specific approach to cash
  burn". Material costs are constraints to be understood at charter time.
- The storage-rent precedent: bytes are already priced physically when held.
- §I.a: prices are surfaces.

A pure pass-through of a real cost to the party that caused it is physics, not
advice. The case for building rests entirely on the charge being that and nothing
more.

## 8. What is not computable from the ledger today

- **The reader's input cost in micro-USD.** Several pieces are missing:
  - The invocation row's `usage` is the final provider call's only
    (`ComputeMixin._invoke`: "the invocation's usage is the final provider call's
    usage"), so input tokens across tool rounds are not ledgered.
  - `ret.cost` is the total: input, output, tool prices and continuations, never
    split.
  - Providers report total input tokens, not tokens per section, so the INPUTS
    share of a call's input cost needs an allocation rule. Byte share is such a
    rule, and it is wrong wherever the stable prefix is served from a provider cache
    at a discount.
  - Program seats and x402 sellers are priced per call and report no tokens.
- **Author-written bytes in a reader's request.** They are not ledgered separately.
  The ledger records whole sections only. Counting them needs one measurement where
  the reader's request is built: the length of the rendered `outputs` and published
  propensity of the subject. That would be a new count, not a reuse of `sections`.
- **Continuation prompts.** The ledgered `sections` are the opening prompt's.
  Tool-round prompts, which re-send the request with results, are not counted.

## 9. Recommendation

1. **Build no kernel charge now.** Keep reading cost a published fact
   (`downstream_read_bytes`) that the charter may price per assembly. This is the
   route Chapter II names for pathologies: ceded metrics (§IV.a), λ from the
   controller (§II.b). It adds no structure, and it lets the population decide
   whether context size is a pathology at all.
2. **Rule separately on §IV.c.** Should a tier-two reader receive the producer's
   raw outputs? If not, the fix is in what the cascade releases upward, and much of
   the 42k problem may go with it.
3. **If a physical charge is ruled in, build option B only:**
   - metered on author-written bytes, times readings, at one per-byte rate fixed
     for the world's life and published;
   - debited through the author seat's meter and entered in the author decision's
     cost contribution;
   - never carried into the consequence outcome, and credited to no seat;
   - paid from the niche for niche-covered decisions;
   - collected at the window boundary with an unaffordable remainder left due, and
     named to the author by handle and amount only.

   Ledger per-call usage first (§8), so the charge can be audited against what
   readers actually spent.

## 10. Open questions

1. Should `downstream_read_bytes` stay on whole INPUTS (a fact about the reader) or
   move to author-written bytes (a fact about the author)? Or should both be
   published?
2. Is bounded reciprocity a norm between seats as well as toward outsiders? Only the
   charter's co-writers can answer that.
3. Should a meta's or counter's reading be split between the verdict's author and
   the producer's?
4. Rate: fixed at launch like storage rent, or indexed to the evaluator tier's mean
   input price at launch? Indexing tracks the real cost better. Anything that moves
   with a particular reader leaks that reader.
5. Should the charge exist at all while `multi_judge_share` and `multi_judge_count`
   are hard manifest values the author cannot affect? And if the charter ever
   governs them, how is producer capture of the sampling rate prevented?
6. Should the ledger record per-call usage for every provider call of an invocation,
   independently of this question? Today no one can audit an invocation's input
   spend across tool rounds.
