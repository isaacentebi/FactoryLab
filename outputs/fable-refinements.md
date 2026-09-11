# Fable review — checked refinements

10 September 2026 · Disposition of the independent Fable 5.1 Claude Code CLI review

**The review found real remaining gaps.** Its most useful contribution was following the proposed dataflow through concrete failures: slow outcomes blocking fast learning, an editable service undermining evidence, verdicts decoupled from forecasts, and feedback losing its owner inside an assembly.

The complete response is preserved in `fable-cli-review.md`. The revised specification is `project-plan.md` v0.3, with v0.2 preserved. The review ran successfully on the requested `claude-fable-5-1`, receiving the full article, formula definitions, plan and prior audit in one CLI session. It had no tools and did not independently verify cited research papers. Its claims below were checked against the supplied Chapter II passages; design remedies remain untested.

## What changed

| Fable finding | Disposition | v0.3 change |
|---|---|---|
| 1. A complete score bundle stalls fast learning | Adopt | Keep serialization in the finite reference harness. The operational queue supports outstanding decisions and separate local and consequence clocks; declare and implement any delayed-feedback algorithm before claiming its guarantees. |
| 2. Consequence targets and verdicts can be gamed | Adopt with qualification | Use launch-declared observable event types, an admissibility process for new types, assigned exposures, verdict-linked forecasts, consequential coverage and matched baseline comparisons. Predictive standing applies only to the claim class actually tested. |
| 3. The editable service can sit on the trusted evidence path | Adopt | Use a derived-report generator or cache. Keep authoritative sealing, observation ingestion, addressing and settlement outside that mutable path. |
| 4. Market horizons may make the experiment unaffordable | Adopt as a feasibility condition | Profile complete cycles and budget a minimum pilot opportunity. A service-only test is an available narrower experiment, not permission to ignore relevant slow market feedback. |
| 5. Assembly credit is unspecified | Adopt with qualification | Declare a scoring contract at each request hop. Evaluate both child selection and outer assembly selection in their own scopes; never broadcast a terminal score identically to all ancestors. |
| 6. Aggregation destroys individual attribution | Adopt as an explicit interpretation | Preserve handles; release aggregate regulation evidence and separately scoped sampled meta-evaluation requests. Record both action and evaluation-sampling probabilities. Keep item-level evidence out of the regulator's live context. |
| 7. Feasibility filtering undermines reference algorithms | Adopt | Log every exclusion and its capability/resource reason. Declare an availability-aware comparator for operation. No post-hoc clipping of a reference swap-regret distribution while claiming its original invariant. |
| 8. Novelty and adoption conceal central decisions | Adopt the failure, adjust the remedy | Keep hard hashing/quotas in the kernel and semantic novelty judgments in accountable evaluation. Make discovery, trial and local menu adoption explicit population actions, with access to retention. |
| 9. Committee size and anonymity are questionable | Partly adopt | Retain five proposed seats and explicit coverage of cross-cutting roles. Enumerate eligibility and sampling before launch. Describe aliases as pseudonymity, not anonymity; limit delegate inputs and avoid blocking reward delivery. |
| 10. Credits may have no material effect | Adopt the requirement, adjust the remedy | Require a traceable mapping to approved compute allocation. The kernel conserves resources and protects rights; ordinary conversion/allocation policies remain charter-revisable. |
| 11. Prices may chase delayed measurements | Adopt | Put price controllers in the timing registry and update on eligible settled windows. Measure overshoot, saturation and actual budget effects. |

The revised plan also makes survival/termination semantics an explicit launch decision and separates the practical retention/adoption mechanism from any theoretical claim about swap regret.

## Where I did not follow the review literally

**A fixed consequence channel does not prove that every possible event predicate must be enumerated forever.** Chapter II distinguishes the external consequence signal from the mutable input, while allowing the factory to encounter and formalize new conditions. A bounded initial event vocabulary is a useful engineering choice. Generalizing it without surrendering externality remains a design question. Likewise, a categorical ban on judging any event type one's lineage proposed is not stated by the source and would not, by itself, prevent cross-lineage collusion. We retain provenance, conflicts and independent admissibility instead. [Overfitting and Adversarial Populations](https://superdark.antikythera.org/chapter-ii-the-dark-stack#overfitting-and-adversarial-populations)

**Five of nine is not a plenary.** It creates substantial overlap and weak practical anonymity, but it is still a sample. The article presents sortition and representative variety more directly than the review's “merely illustrative” label suggests. A three-seat committee is not automatically more faithful. We retain the proposed coverage while making the small-population limitation explicit. Copying a delegate's entire private learner memory, as the review suggested, would also weaken our information boundary; the revision uses a restricted governance view and a constituency submission. [Self-Writing](https://superdark.antikythera.org/chapter-ii-the-dark-stack#self-writing)

**Slow market feedback cannot simply be removed from governance.** Trading does not inherently require a days-long horizon. Conversely, making market outcomes “audit-only” does not make them irrelevant to a decision that changes trading. The right check is the dependency of the actual amendment on the actual measured loop. We retain the market activity and require evidence that the selected pilot horizon is viable. [Clocking the Factory](https://superdark.antikythera.org/chapter-ii-the-dark-stack#clocking-the-factory)

**No need to freeze every credit-conversion policy or install an exploration floor in every retentive learner.** Those remedies add permanent structure beyond the underlying requirements. We preserve an operative relationship between prices and resource use, a protected frontier and a real route into retention, while leaving local adoption and ordinary allocation rules revisable. The exact implementation still needs validation. [Commitment](https://superdark.antikythera.org/chapter-ii-the-dark-stack#versioning-commitment-and-the-first-commit), [The Charter and the Loop](https://superdark.antikythera.org/chapter-ii-the-dark-stack#iv-the-charter-and-the-loop)

**Attribution is not the same as causal proof.** Per-hop feedback prevents lost or duplicated learning returns. It does not prove which component caused a global outcome. We did not adopt the stronger claim that no producer can ever receive an outcome-based score through an evaluator, or that all cross-level evidence must move only through meta-evaluator standing. Scope, channel and meaning must remain explicit. [Evaluations](https://superdark.antikythera.org/chapter-ii-the-dark-stack#iii-evaluations)

## What this review establishes

It establishes that the requested independent review was completed and used to refine the design. It does not establish that the resulting system learns, remains stable, preserves all theoretical guarantees, or exhibits Class 3 behavior. Delayed-feedback and changing-action implementations, event admissibility, aggregation/attribution, anonymity and calibrated timing remain substantive implementation or experimental questions.

The proposed next work is the deterministic substrate with these interfaces made explicit. No code, funded experiment or live trading was launched during this review.
