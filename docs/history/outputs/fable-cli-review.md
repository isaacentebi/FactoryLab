# Fable 5.1 — independent Claude Code CLI review

10 September 2026 · Completed review of Factory Lab v0.2

The CLI confirmed `claude-fable-5-1` for the request and response. It received the complete abstract and Chapters I–III (46,214 words), all four embedded formula definitions, the exact v0.2 plan, and the prior audit: 54,746 words in total. Tools and MCP access were disabled. This was a separate CLI session from the earlier Claude browser conversation.

The response below is preserved as returned. It reviewed supplied material and did not independently retrieve the cited research papers. Some recommendations overstate what the article requires; see `fable-refinements.md` for the checked disposition and `project-plan.md` v0.3 for adopted changes.

---

# Independent review of Factory Lab v0.2 against The Superdark Factory

## Overall judgment

v0.2 is a serious and mostly source-faithful design. It repairs the large v0.1 omissions and is honest about what a bounded run can show. It is not yet buildable as written. Four problems would make the first run either uninformative or unfaithful to Chapter II. The seed learner serialization in section 5 either blocks every learner on the slowest score channel or quietly reintroduces the delayed feedback it claims to defer. The consequence channel in section 7 lets a judge author the events it forecasts and lets it hedge its forecast independently of its verdict. The population-owned service in section 3 can sit on the trusted settlement path. The paper-trading horizon makes the headline charter-revision test in section 1 unlikely to complete within a limited-cost run. Below those, the committee in section 10 is a plenary rather than a sortition at the proposed population size, multi-hop credit across assemblies is unspecified, and feasibility-filtered sampling silently breaks the comparator the reference algorithms assume. None of these fixes requires real money, a larger trusted mechanism, or a standing governor. The verdict is keep the architecture, change seven clauses, defer three mechanisms.

Category labels used below: (A) explicit article requirement, (B) illustrative article mechanism, (C) unproved claim or ambiguity in the article, (D) engineering choice in the plan.

## Material findings

**1. One outstanding decision per learner starves the fast channel or manufactures learning death.**
- Severity: High.
- Plan: section 5, seed scheduling paragraph. Also section 7 and section 8.
- Source: II:072 (A), II:169 (A), II:172 (A with B remedies), II:131 (A).
- Failure scenario: a learner has at least two score channels, a within-round evaluator verdict and a consequence settlement that closes after a service or market horizon. If the declared bundle includes the slow channel, the learner makes one decision per horizon. Exploratory learners then get fewer decisions than the lifetime of anything they discover, which is exactly the learning-death condition in II:172, now caused by the schedule rather than by the population. If the bundle includes only the fast channel, the slow score lands after later decisions were made, so the "delayed-feedback variant" the plan postpones is already live. Evaluators blocked on consequence would also invert II:131, which wants evaluation to be the continuous majority of activity.
- Article position: II:072 treats delay of many rounds as the normal case and mandates a queue of addressable outstanding decisions. Delay is not a later variant. It is the baseline.
- Minimal remedy: each learner declares which single channel closes its round. In the seed that is the fastest declared channel. Slower channels update standing and allocation asynchronously by decision handle and never gate the next decision. Serial blocking on any channel slower than the round channel is forbidden for exploratory learners. Use an existing bounded-delay bandit result for the reference frontier instead of serialization, and name the delay bound in the manifest.
- Status: the queue and delay handling are source-mandated. The channel-closes-round rule is my engineering proposal.

**2. The consequence channel preserves externality only partially: predicate authorship and the verdict-forecast hedge.**
- Severity: High.
- Plan: section 2 consequence paragraph, section 7 first four paragraphs.
- Source: II:136 (A), II:139 (A), II:150 (A), II:138 (A, with C on what counts as real), II:162 and II:164 (B).
- What is preserved: the observation source, the sealed-before-outcome rule, and the proper score sit outside the charter. That is the right shape. A settled market price or a trusted instrumentation reading is genuinely not the factory's input.
- What leaks: section 7 allows new event contracts to be registered within the fixed rules but does not say who registers them. If a judge or committee can author the predicate it then forecasts, the definition of consequence has moved into the input. II:139 requires that signal to sit entirely outside the input and be fixed at the first move. Second, the plan makes the forecast a separate output from the verdict. II:136 scores whether the verdict itself predicted the outcome. A judge can therefore issue a charter-conforming verdict, forecast the prevalence rate, and keep a clean consequence record while its verdicts are useless. Third, action-conditional forecasts let a judge condition on actions unlikely to be taken, so most forecasts settle as inapplicable. Coverage is reported but does not touch standing.
- Minimal remedy: fix at launch a predicate vocabulary computable only from kernel-trusted instrumentation and settled external observations. Population registration may parameterize window and threshold within that vocabulary, and a judge may never forecast a contract from a lineage that registered it. Bind forecasts to verdict classes: a verdict of a given class carries a forecast vector over the finite candidate action set, the taken action settles, and the rest are recorded as unsettled but not judge-censored. This is the conditional-market structure of II:162 applied without any market. Make coverage part of consequence standing so an uncovered record scores as no better than baseline.
- Status: predicate externality and verdict binding are source-mandated. Vector forecasts and coverage-in-standing are my engineering proposals.

**3. The population-owned service can corrupt the trusted settlement path.**
- Severity: High.
- Plan: section 3 owned service paragraph, section 2 consequence settlement row.
- Source: II:139 (A), II:137 (A), II:186 (A).
- Failure scenario: the plan's example services include forecast ingestion and artifact validation. An antagonist with fault rights over ingestion can delay or malform the very messages that establish whether a forecast was sealed before its outcome. The consequence channel then depends on a surface the population may break. Section 2 says settlement provenance is fixed and instrumentation is outside worker write access, so the plan contradicts itself when the service is any part of that path.
- Minimal remedy: the owned service must be off the trusted path. Choose a service whose failure consumes the population's own time and compute, such as a cache, a report generator, or a tool runner, and keep sealing, observation, and settlement in kernel-owned code the population cannot write to. State this as a kernel invariant.
- Status: engineering proposal, directly required by II:139's nonfungibility.

**4. The run length cannot deliver the headline evidence if paper trading is on the critical path.**
- Severity: High for feasibility.
- Plan: section 1, section 3 market observations, section 8 amendment timing, section 12 cost.
- Source: II:177 to II:180 (A on ordering, B on ratios), II:153 (C), II:172 (A).
- Failure scenario: section 1 demands a full chain from experience through proposal, enactment, altered learning, and later observation. Section 8 requires an amendment to wait for the prior amendment's response at the slowest consequential loop. If the slowest loop is a forward market horizon of days, one governance cycle is weeks, and a limited-cost serial run yields at most one cycle. Section 8 admits this in one sentence but the plan still treats trading as a seed opportunity feeding the headline test.
- Minimal remedy: designate the owned service as the fast-consequence niche whose loops close in minutes or hours, and run the headline charter-revision test against it. Keep sealed market forecasts as a slow channel that informs versioning and audit but is not required to close before an amendment. Declare in the manifest the minimum number of full governance cycles the budget must afford, and report the niche as unsupported if it cannot.
- Status: engineering proposal, consistent with II:180's viability logic.

**5. Responsibility across assemblies is not credited anywhere.**
- Severity: Medium.
- Plan: section 4 request paragraph, section 8 leaf settlement, section 9 late outcomes.
- Source: II:059 (A), II:072 (A), II:068 (A), II:131 (B), II:135 (A).
- Failure scenario: a producer delegates to an assembly, which picks a tool, which fails a day later. The plan says leaf outcomes settle to their rightful learner, singular. Three decision handles exist in the lineage. If the terminal score is broadcast backward to all of them, learners receive a second reward with different semantics than their local evaluator score, and named channels no longer mean one thing. If it settles only to the last hop, upstream delegation choices never learn.
- Article position: II:131 has each recursive level sense, decide, produce, and reward within its own scope. II:135 has producers learn only from evaluators, and evaluators learn from meta-evaluators and consequence. II:068 forbids routing by author. Together these imply per-hop credit through the requesting side's evaluation, never a global backward broadcast.
- Minimal remedy: every sub-request is scored by an evaluation issued at its own hop and returned to its own handle. Consequence settlement attaches only to sealed forecasts, so producers receive consequence only through evaluator standing. A terminal outcome never propagates across hops except through meta-evaluation of the evaluators that judged each hop. Private lineage in the ledger is for rights and antagonist accounting only.
- Status: engineering proposal with direct support from II:131 and II:135.

**6. Upward buffering into distributions conflicts with per-decision attribution for evaluator learners.**
- Severity: Medium-High.
- Plan: section 8 first two paragraphs, section 7 meta-evaluation.
- Source: II:186 (A), II:072 (A), II:059 (A). The conflict is a genuine (C) ambiguity in the article.
- Failure scenario: II:186 withholds verdicts from the next tier until they settle into a distribution. II:072 requires every score to return to the exact decision and propensity. An evaluator is a learner. If its meta-evaluator only ever sees a histogram of verdicts, it can only score the batch, and a batch score assigned uniformly to every handle destroys the within-batch information that swap-regret machinery needs. The plan's retentive evaluators then cannot learn as designed, while the plan believes they can.
- Minimal remedy: separate aggregation for regulation from aggregation for attribution. The buffer delays and jitters release but retains handles. On release the meta-tier receives the distribution for any regulating decision and a precommitted random sample of addressable items for judging. Meta-verdicts settle per handle to the evaluator's decision. Consequence returns to an evaluator are not buffered at all. State that the distribution rule governs what upper tiers may act on, not what they may score.
- Status: engineering proposal resolving an article ambiguity. The delay, jitter, and distribution rule remain source-mandated.

**7. Sampling after feasibility filtering breaks the comparator and hides a gate.**
- Severity: Medium.
- Plan: section 5 declaration paragraph, section 2 broker sentence, section 4 sampling record.
- Source: II:105 (A), II:110 (A), II:059 (A).
- Failure scenario: the reference algorithms assume a fixed action set. Once resource feasibility removes actions, the best fixed action in hindsight may not have been feasible in many rounds, so external and swap regret against it are undefined. Importance-weighted estimates over the post-filter distribution are unbiased for feasible actions, but infeasible actions receive no estimate and return with stale weights. The Blum-Mansour reduction assumes one copy per always-available action. Worse, if the broker filters the menu before the learner samples, and the filter reflects anything beyond published resource physics, it is an unlogged central choice over what the learner may do.
- Minimal remedy: in the finite laboratory keep menus fixed, as the plan does. In the open world adopt an availability-aware comparator, of the sleeping-experts kind, and say the theorem scope ends there. Log every feasibility removal as a kernel event with a reason code, so it is world physics in the sense of II:105 and not policy. The learner's sealed propensity is over the post-filter menu, and the filter never orders by score.
- Status: engineering proposal. Logging the filter as physics is required by II:105.

**8. New-action adoption still passes through semantic gates in the broker and lacks a per-learner rule.**
- Severity: Medium.
- Plan: section 6, section 2 broker sentence, section 5 menu-change paragraph.
- Source: II:110 (A), II:021 (A), II:028 (B), II:157 (B).
- Failure scenario: section 6 asks to detect materially equivalent resubmissions. Equivalence is a semantic judgment. If the broker makes it, an unaccountable component decides what counts as new. Separately, the plan opens a comparator epoch when a menu changes but never says how a retentive learner's menu grows. If the broker injects new actions into menus, adoption is centralized. If nobody does, discoveries never reach ordinary use, which is the retention-boundary failure the prior audit warned about.
- Minimal remedy: equivalence classification is issued by learning evaluators and is itself consequence-scorable. The kernel only enforces the novelty reserve's budget. Menu growth is an action: any learner may query the public registry, and adding an action is a choice sampled from a per-learner unhistoried share, so II:110's niche exists inside each retentive learner, not only globally.
- Status: engineering proposal grounded in II:110 and II:028.

**9. The committee is a plenary at the proposed population size, and the anonymity claim is unsupported.**
- Severity: Medium.
- Plan: section 5 seed, section 10 steps 3 and 4.
- Source: II:160 (B), II:148 (A on committee including factory delegation), II:162 (B).

| Seed instances | Count |
|---|---|
| Producers | 3 |
| Evaluators | 6 |
| Committee seats per round | 5 |

- Failure scenario: five seats drawn from nine lineages seat most of the factory every round. Rotation barely changes membership. Round aliases cannot hide identity when only three producers exist and a delegate discusses trading. The plan claims stratified sortition with anonymity, which the article proposes as a mechanism, but the numbers deliver a whole-population vote with a rename. Coverage of both learning types among producers is also unspecified, since three producers cannot supply replicated instances of each.
- Minimal remedy: seed three seats with a two-of-three rule, coverage of at least one producer and one evaluator lineage, and report anonymity as absent. Declare in the manifest a population size at which alias anonymity becomes meaningful, without pretending the seed reaches it. Make the delegate a separate invocation carrying the lineage's memory, so deliberation does not consume the running learner's round.
- Status: engineering proposal. Sortition and anonymity are illustrative in II:160, not required.

**10. Internal credits must buy real allocation or λ is not a price.**
- Severity: Medium-Low.
- Plan: section 2 final sentence, section 9 soft rewards, section 11B consequential allocation.
- Source: II:150 (A), II:158 (A with C).
- Failure scenario: keeping units distinct is correct for safety. But II:158 defines λ as the marginal worth of a constraint where the factory stands, and II:150 wants the correspondence between norm pricing and material cost understood at charter time. If credits never move real compute, prices are decorative.
- Minimal remedy: a kernel-fixed, non-inflatable map from credits and consequence standing to shares of the approved budget. Credits move allocation within the budget and cannot expand it.
- Status: engineering proposal implementing an explicit requirement.

**11. The price controller can chase its own measurement lag.**
- Severity: Low.
- Plan: section 9 controller rule.
- Source: II:174 (A), II:176 and II:177 (A on ordering, B on ratios), II:108 (B).
- Remedy: compute the violation error only on settled windows and set the controller period to at least the seed ratio times the settlement lag of that violation measure. The plan already declares rate limits; it should tie them to section 8's clocks.

## Prior audit, open questions, verdict, and limitations

**Disagreements with the prior audit.** The prior audit's core corrections stand, and its warning against reading the repeated-game results as engineering laws is right. Five points need revision.
- Item P10 marked post-filter propensity logging as keep. It should be partial for the reasons in finding 7.
- Items T05 and T06 are correct but did not notice that the plan's serialization seed, which they helped motivate, produces the learning death they aimed to prevent. See finding 1.
- Item E06 and failure 3 treat registration of new event contracts as safe. Predicate authorship is the remaining leak. See finding 2.
- Item C04's stratified sortition is sound in principle and oversized at the seed scale. See finding 9.
- The cross-check correctly withdrew the claim that shared broker code creates a hidden governor. It should have added that the consequence-selection share is a deliberate kernel-owned scoring rule, and that the plan should label it as such rather than treat the broker as scoring-free.

**Unresolved questions, not findings.**
- The charter has no death condition. II:144 and II:149 make survival the stake that gives soft casts force, while II:149 also wants the charter permissive. The plan's only death is budget exhaustion. Decide whether a kernel-checkable survival predicate beyond budget belongs in the first world, and note that a committee able to loosen any acceptable region would empty it.
- Whether faults an antagonist injects into an owned service are real in the sense of II:138 is unresolved in the article itself. Label the residual gap and do not treat real money as the answer.
- Whether swap-regret learning is the right operationalization of retention is a (C) claim. The plan's adoption path in section 6 does the retaining. Say so.
- Section 1's distinction between criterion revision and sophisticated fixed-objective adaptation remains open, as the plan states. No universal Class 3 criterion exists to import.

**Verdict.**
- Keep: the four-authority split, distinct units, the bounded proper score, the deterministic substrate first, protected trials, the seed cascade ratio labeled uncalibrated, exclusion of real money, and the refusal to certify Class 3.
- Change: section 5 seed scheduling, section 7 predicate and forecast binding, section 3 service placement, sections 1 and 3 headline niche, section 10 committee size, sections 4 and 8 hop credit and buffer semantics, section 5 feasibility logging.
- Defer: transfer-operator versioning, futarchic λ, alias anonymity, and PID, all as the plan already intends.

**Replacement clauses.**

Section 5, scheduling paragraph:
```
Each learner declares exactly one round-closing channel, which in the seed is
its fastest declared channel. Slower channels settle by decision handle and
update standing or allocation asynchronously; they never gate the next
decision. The reference frontier uses a bounded-delay bandit variant with the
delay bound stated in the launch manifest. Serial blocking of an exploratory
learner on any channel slower than its round channel is a kernel violation.
```

Section 7, event contracts:
```
Event predicates are drawn from a launch-fixed vocabulary computable only from
kernel-trusted instrumentation or settled external observations. Registration
may set window and threshold within that vocabulary. No lineage forecasts a
contract it registered. A verdict of a declared class carries a forecast
vector over the finite candidate action set; the taken action settles, the
others are recorded as unsettled. Coverage enters consequence standing.
```

Section 3, owned service:
```
The owned service is off the trusted settlement path. Sealing, observation,
and settlement run in kernel-owned code outside population write access. The
service's failures consume the population's reserved time and compute only.
```

Section 8, buffering:
```
Upward buffers delay and jitter release but retain decision handles. Upper
tiers act on the released distribution and judge a precommitted random sample
of addressable items. Meta-verdicts and consequence settlements return per
handle and are never buffered.
```

Section 10, committee:
```
Seed three seats by sortition with a two-of-three rule, covering at least one
producer and one evaluator lineage. Anonymity is reported as absent below the
population size declared in the manifest. A delegate is a separate invocation
carrying its lineage's memory.
```

**Limitations of this review.** No implementation exists, so every failure scenario is a design projection. I read the article as supplied, including the formula definitions, and did not verify the cited papers beyond the packet; in particular I have not checked the exact conditions of any bounded-delay or sleeping-experts result, and the plan should cite specific ones before relying on them. I did not assess provider costs, model availability, or isolation technology. Findings 5, 6, and 7 resolve ambiguities the article leaves open, so a different faithful resolution is possible.
