# The Superdark Factory — reading record and project implications

Read on 10 September 2026: the complete abstract and Chapters I, II and III, including their inline footnotes and four embedded formulas. This record distinguishes the authors’ proposal from the experiment we are designing. It is not a verification of every historical, economic or technical claim in the essay.

The subsequent detailed Chapter II audit found that v0.1 omitted central mechanisms despite this reading. Use `chapter2-audit.md` for the coverage ledger and the current `project-plan.md` v0.3 for the corrected design. The independent CLI review is preserved in `fable-cli-review.md`, with decisions in `fable-refinements.md`; prior plan versions remain available for comparison. Reading the source did not make the first implementation proposal faithful to it.

## What each chapter contributes

**Abstract.** The proposed factory chooses and revises its own purposes. Its architect seeds the conditions for that autonomy and subsequently participates through a limited governance interface. The proposal has four parts: primitives, behavioral versioning, evaluations and a charter. [Abstract](https://superdark.antikythera.org/#abstract)

**Chapter I — Darkness.** The classification depends on the boundary between supplied inputs and automated activity: execution, planning, then objective formation. Merely generating subgoals or rewriting code does not establish the third class. The chapter treats darkness as the loss of practical utility of internal descriptions, rather than secrecy. It contrasts formally supplied possibilities with empirical inputs from the world and frames the architect’s initial commitment as consequential. These are the authors’ definitions and arguments; they should not be mistaken for an empirical classification test. [Chapter I](https://superdark.antikythera.org/chapter-i-darkness)

**Chapter II — The Dark Stack.** The design proposal combines composable components, persistent learning, addressable delayed feedback, differentiated information access, behavioral observations, evaluators accountable to consequences, and a charter the population helps revise. It distinguishes unchangeable operating conditions from negotiable criteria. Its timing argument matters as much as its population argument: feedback delivered after the relevant configuration disappears can destabilize the system. The chapter also explicitly permits temporary initial orchestration. Its four named pathologies are stable failure, overfitting, learning death and thrash. [Chapter II](https://superdark.antikythera.org/chapter-ii-the-dark-stack)

**Chapter III — The Anything Factory.** The scale expands from one factory to an ecology of interdependent factories. Shared infrastructure and synchronization can create correlated failures. The authors speculate about reproduction, machine demand and an economy whose production need not terminate in human consumption. For our project, this motivates attention to shared dependencies and limits of a single-population experiment; it does not establish that these larger economic outcomes will occur. [Chapter III](https://superdark.antikythera.org/chapter-iii-the-anything-factory)

## Questions the project must make concrete

- What changes when a population revises its criteria, beyond the language it uses to describe itself?
- Which observations come from independent outcomes, and which are opinions produced by another model?
- Can discoveries persist long enough to receive delayed feedback without letting obsolete feedback steer a successor indiscriminately?
- Which mechanisms are genuinely learned, which are deterministic constraints, and which are temporary starting arrangements?
- What would a useful negative result look like?

## Technical boundaries checked separately

Deng, Schneider and Sivan study repeated games against specified learning algorithms. Their results distinguish mean-based and no-swap-regret learners under mathematical assumptions. They do not establish that an LLM population with particular prompts will become an open-ended factory, nor that exceeding a trading baseline proves the essay’s broader bridge between utility and unreadable solutions. [Research paper](https://arxiv.org/abs/1909.13861)

Blum and Mansour provide an algorithmic reduction from external to internal/swap regret, including a partial-information setting. Implementing and checking such an algorithm is different from calling one agent a careful retainer. Merely possessing action probabilities does not provide observed outcomes for actions that were not taken. [JMLR paper](https://www.jmlr.org/papers/v8/blum07a.html)

Our proposed first experiment should therefore use precise labels: measured adaptation, criterion revision, forecast calibration, resource allocation and behavior under interventions. “Class 3,” “outside readable space” and large-scale economic consequences remain interpretive hypotheses.
