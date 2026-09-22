# Factory Lab — implementer conventions

You are building a superdark factory: a Class 3 factory, one that produces its own
objectives by negotiating them with its world. The design authority is **Chapter II
of *The Superdark Factory*** (`docs/essay.md`, the chapter headed "THE DARK STACK"),
and Chapter I's class definitions:

- **Class 1** automates executions and takes a *plan* as input.
- **Class 2** automates plan-making and takes an *objective* as input.
- **Class 3** automates objectives.

Anything we write that hands the factory a plan or an objective it should own makes
it Class 1 or Class 2, however clever the rest is.

**Order of authority:** Chapter II, then the code, then every other document
(`docs/build-spec-*`, `docs/manifest.md`, audits, plans). Where the code disagrees
with Chapter II, the code is wrong. Where a spec disagrees with Chapter II, the spec
is wrong. `README.md` says what the kernel enforces today. `docs/manifest.md`
documents every manifest key, its default, and whether it is fixed for the world's
life.

## Chapter II, as design rules

Before you build anything, name the Chapter II passage it implements. A mechanism no
passage calls for is not built, and an existing one is a deletion candidate. The one
exception is the world itself: a venue, a market or a data source is the world, not
architecture.

1. **Surfaces, not strategies (§I, §I.a).**
   - We expose tools, prices, custody and limits.
   - No prompt, tool description, default or refusal text tells a seat what to do,
     what is good, or how cautious to be.
   - The architect does not prescribe the orchestration layer. A scaffold is allowed
     only if the factory can tear it down.
   - "A hard-coded pipeline of agents is literally just a waterfall."
2. **Robust simplicity (§I.a, Carroll).**
   - The less we know, the less structure we impose.
   - No carve-outs or prompt patches for a model's past mistakes.
   - Never justify a change by a behaviour-mix delta ("they hold too much"). That is
     the architect optimizing toward its own "better", which is Class 2.
3. **Physics is enforced, not announced (§II.b).** The kernel is the hard cast. Never
   describe kernel rules in prompt text.
4. **Two channels only (§I.b).**
   - A *rich* request channel: self-describing and author-neutral.
   - A *thin* reward channel: a score plus the propensity, delivered to a persistent
     handle through the stateful queue.
   - No third channel between seats (no direct messages, no side chats). Seats
     compose through contracts.
5. **Minimal sufficient disclosure (§I.b).**
   - Schematics are public.
   - Local state is private: scores, history, learner state.
   - The propensity travels forward with the request.
   - Judges get a clean context: request, output, executed operations, propensity.
     Never the author's identity, and never text that reveals who wrote it.
6. **The reward chain (§III.b).**
   - Producers learn from judges' verdicts.
   - Evaluators are graded from above, tier by tier, for compliance with the charter.
   - Evaluators are also graded by **realized consequence**: a fact the world
     measures (settled PnL, the priced road not taken, a resolved forecast). It is
     never another model's reading, and it is prebaked at the Stackelberg move.
   - The signal that grades an evaluator sits outside the loop it judges.
7. **Evaluations (§III).**
   - Online and continuous, never offline gates or predefined rubrics.
   - Evaluators are the majority of the population and of compute.
   - Most evaluators are mean-based learners.
   - Evaluations are recursive (evaluations of evaluations).
   - Model families are heterogeneous: a shared foundation model is a forcing
     function.
   - Early warning comes from variance, autocorrelation and ensemble disagreement.
   - The adversarial layer includes producers and evaluators, internal and external.
8. **Learners (§I.a).**
   - Mean-based no-regret learners at the frontier, no-swap-regret learners at the
     core (Blum–Mansour).
   - Learning death is prevented as a fact about the world: a share of compute and
     write access usable only by unhistoried actions. The kernel never chooses a
     seat's action for it.
9. **Versioning is behavioural (§II).**
   - A version is a metastable input–output distribution (the transfer operator and
     its spectral gap), not a configuration or a hash.
   - The factory never rewinds.
   - A change to the kernel is lethal: a new world, starting again from v0.
10. **Pathologies are priced, live (§II.b, §IV.b).**
    - Stable failure: its duration ratchets the gain.
    - Thrash: its volatility is priced.
    - Overfitting: answered by evaluations and a higher sampling rate.
    - λ comes from a PID controller.
11. **The charter is co-written (§IV, §IV.a).**
    - Governance acts only through the charter, never inside the factory.
    - The metrics layer is ceded: the factory proposes metrics for norms and holdout
      criteria, and posts λ as a shadow price.
    - The factory joins governance by sortition, rotated and anonymous, on the
      charter's revision cadence.
    - Norms may sit behind a read-only wall. Constraints and λ suit conditional
      markets (futarchy).
    - Speed is a constraint: speed is cash burn.
12. **Time (§IV.b, §IV.c).**
    - Loop periods are ratios, not absolute constants.
    - An inner loop settles at least 3× faster than the outer loop that commands it.
    - Verdicts rise a tier only after settling, as distributions, with jitter.
    - An explorer is compensated sooner than the lifetime of what it found
      (anticipatory settlement, or guaranteed patience).
    - Neither the factory nor its control apparatus may be slower than its
      environment.

## Engineering rules

- Python 3.13. `uv` manages the environment: `uv sync`, `uv run pytest`,
  `uv run ruff check .`. Do not add tooling.
- Allowed third-party packages: `hyperliquid-python-sdk`, `cryptography`,
  `pytest`, `ruff`. Anything else needs a written reason.
- Money is integer micro-USD. Never a float.
- No global mutable state. No background threads.
- `factorylab.kernel` imports nothing from `cortex`, `world`, or `runtime`.
- Every kernel invariant gets at least one test that attempts to violate
  it and asserts failure.
- Docstrings state what a function guarantees, not what it does. Comments explain
  why, and cite the Chapter II passage a mechanism implements.
- Money-path code (orders, treasury, custody, conversions) gets a cold,
  adversarial review before it merges.
- Never read, print or commit key files (`*.key`) or `*_PRIVATE_KEY` values.
- Stay inside the files named in your task. Do not refactor neighbours, do
  not reformat unrelated files, do not commit.

## Verify gate

```
uv run ruff check . && uv run pytest
uv run pytest -m gate -n 2 <the gate test files you touched or that cover your change>
```

A bare `uv run pytest` runs the `check` tier (no world runs, 2 workers). Run it, plus
only the specific `gate` files for what you changed; do not run the whole gate
(`uv run pytest -m "check or gate" -n 4`) unless your task says so, and never with
`-n auto`. A new test that runs a world is `gate`: the conftest usually detects it,
and a `check` test over 2 s fails with a message telling you to mark it
`@pytest.mark.gate`. Run it before you return. Return the list of changed files, the gate output
verbatim, and any decision you made that the task did not specify.
