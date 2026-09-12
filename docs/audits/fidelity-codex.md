# Fidelity findings

Numbers give overall severity order; categories distinguish architectural limits, defects, and incentive problems.

## Will break

### 1. Critical — The population runs with the host's filesystem authority

**Location:** `factorylab/cortex/sandbox.py:60`, `factorylab/cortex/registration.py:206` (`run_python`, `_tool`). **Essay:** “The kernel is inviolable” — II.II.b.

The subprocess has the parent's user identity, an empty environment and a temporary working directory; neither filesystem nor network access is isolated, on Linux or macOS. A registered tool using only built-in `open` can bypass the supposed kernel boundary. A benign probe read and overwrote an audit-created `/tmp` file outside its working directory. The substring import blacklist does not constrain Python authority. Captured stdout is truncated only after collection; memory and descendant processes are also unbounded. Population execution can therefore corrupt the host or exhaust the runtime independently of legitimate factory economics.

**Fix:** Enforce filesystem, network and process isolation outside Python; remove access to host credentials and writable runtime state, bound memory/output while streaming, and kill the entire process group. Do not expose this capability before that boundary exists.

### 3. Critical — Accepted orders become “rejected” when acknowledgements disappear

**Location:** `factorylab/world/exchange.py:812` (`HyperliquidExchange.place`), `factorylab/settlement/consequence.py:39` (`order_result`). **Essay:** “thin but stateful reward line” — II.I.b.

Every submission exception becomes a rejection without an order ID. `Order.client_id` is not passed to either submission method. An accepted order followed by a timeout consequently loses its originating handle, and a population retry can duplicate exposure. A fake transport accepting before raising produced two accepted orders and two reported rejections when given the same client ID twice. Later account reconciliation cannot restore missing intent identity; `order_result` ignores rejected responses.

**Fix:** Persist an intent and stable venue client ID before submission. Represent uncertain outcomes explicitly, query by that identity before resubmission, and retain original-handle attribution through recovery. Apply the same treatment to close/cancel ambiguity.

### 5. High — Legal router changes crash delayed Blum–Mansour feedback

**Location:** `factorylab/runtime/loop.py:1122`, `:2860`, `:3111`; `factorylab/learners/blum_mansour.py:177`. **Essay:** “Reward is always delayed” — II.I.b.

The learner snapshots its distribution before `_mix_with_standing` changes the executed probabilities. Its update requires the saved probability, while `_deliver_returns` supplies the correctly logged executed probability. An evaluator-router probe raises `ValueError: feedback must carry the saved round's executed propensity`. Separately, `_open_epoch` replaces snapshot learners under the same identity and discards outstanding rounds; registering another evaluator then updating an existing round raises `KeyError`. Both operations are advertised population choices, and neither exception is contained by delivery.

**Fix:** Make sampling mixtures part of the learner's explicitly supported propensity calculation and validate the resulting estimator. Keep old epochs addressable until their decisions settle; assign distinct replacement identities and explicit successor rules.

### 6. High — Local compute conservation can conceal external liabilities

**Location:** `factorylab/world/metering.py:78`, `factorylab/cortex/assembly.py:97` (`Meter.run`, `Assembly.invoke`). **Essay:** “a token budget of $0” — II.IV.

An over-ceiling bill commits only the ceiling and returns `overrun`; the assembly discards that field, and the runtime never settles the promised debt. A 25-micro fixture bill committed 10 and lost 15 beyond the return boundary. Provider failures also release reservations even when a malformed or lost reply may already have been billed. Conversely, `cost_of` runs outside the release guard: a throwing cost parser left a 20-micro hold stranded. The wallet can pass its internal conservation check while misstating spend or becoming spuriously infeasible.

**Fix:** Carry actual cost, debt and uncertain billing through durable accounting and reconciliation. Ensure every reservation reaches an explicit terminal or uncertain state, including cost-decoding failures; release only known unspent funds.

### 7. High — Model-output validation is neither safe nor schema-faithful

**Location:** `factorylab/runtime/loop.py:2273`, `factorylab/cortex/assembly.py:152` (`_open_forecasts`, `_parse_json_object`). **Essay:** “structured output and its expected structured input” — II.I.b.

A JSON forecast containing `"predicate": []` reaches dictionary membership before type validation and raises an uncaught `TypeError`. This is a malformed model reply that should become an addressable failure. Conversely, valid JSON containing a closing brace inside a quoted rationale is rejected because brace counting ignores strings. Any parsed object is otherwise marked `ok` without validating its declared outcome schema; malformed semantics can earn well-formedness credit.

**Fix:** Use a real JSON decoder with finite-value checks, validate the supported outcome schema before effects, and normalize all malformed fields to deterministic failed returns. Test nested wrong types and punctuation inside strings at the complete invocation boundary.

## Not Class 3

### 2. Critical fidelity gap — The temporary assembly scaffold cannot be dismantled

**Location:** `factorylab/runtime/loop.py:850`, `:918`, `:1838`; `factorylab/cortex/assembly.py:141` (`_instantiate`, proposal shapes, `_role_for_kind`, `_children`). **Essay:** “A hard-coded pipeline of agents is literally just a waterfall” — II.I.

This is not yet a Class 3 factory under the essay's compositional requirement. The architect fixes event meanings, producer/evaluator/meta dispatch, the available learner algorithms and the action grammar. The population can add assemblies, routers and tools, but cannot replace this dispatch algebra or retire its assembly graph. The seed prompt advertises recursive child requests, yet no `child_factory` is wired and returned children are never dispatched. A discovered research→tool-builder→customer workflow must fit the existing return pipeline or await a human code change.

`factorylab/settlement/lots.py:235` further makes useful inquiry answer to its own return's profitable fills: an enabling tool cannot receive downstream economic credit. A fixed external consequence is explicitly permitted by the essay; the problem is the hidden commitment to a trading-shaped unit of usefulness, combined with no operative delegation contract.

**Fix:** Make requests, continuations, scoped liabilities and graph replacement operative population primitives. Keep constitutional resource limits. Requiring an operator to inspect and approve each new objective or topology would instead cement Class 2; encrypting its diary does not supply superdarkness.

### 4. High fidelity gap — Charter self-writing stops at architect-owned metric identifiers

**Location:** `factorylab/runtime/loop.py:116`, `:1588`; `factorylab/runtime/cards.py:64`; `factorylab/runtime/observations.py:50`. **Essay:** “The quantization of a norm or a constraint” — I.I.

Amendments can add and price cards, but only fixed `PRODUCER_CARDS`/`EVALUATOR_CARDS` identifiers enter settlement. A new `custom-turnover` card using the existing turnover observation, price 1 and violation 9 produced controller penalty 9 but actual role penalties 0. Its price is decorative. New observations also require editing the architect's catalogue. Thus negotiable prose does not imply negotiable operational quantization: renaming an otherwise identical card changes whether it matters. Charter-governed objective derivation is only partial.

**Fix:** Let every admitted card declare a checked observation contract, accountable scope and settlement binding. Permit population-supplied measurement capabilities under isolated execution and independent evaluation. Reject unsupported proposals before voting instead of displaying fictitious prices. Read-only norms and kernel bounds themselves are compatible with Class 3.

## Unclean

### 8. High — Consequence feedback misses its judge, and delayed discoveries disappear

**Location:** `factorylab/runtime/loop.py:2971`, `:2090`, `:2189`; `factorylab/settlement/consequence.py:105`. **Essay:** “memory that persists across rounds” — II.I.a.

`_deliver_consequence_to_memory` expects a `verdict-` prefix on the forecast handle, but that prefix belongs to its event ID; queue handles are `decision-N`. The probe left a present judge-memory entry untouched. In the 260-event scripted run, 3,499 forecasts settled and no retained judge entry carried `your_consequence_brier`. Aggregate standing still updates, concealing the broken personal feedback. Three-entry histories also evict producers before long consequences arrive; metas accumulate histories they are never shown.

**Fix:** Address feedback through the queue's parent handle and retain compact pending-decision memory until delivery/acknowledgement, including metas. Ledger evidence should distinguish settled scores from acknowledged learning. Current versioning observes settlement, not this loss of learning.

### 9. High — The novelty reserve protects registration tickets, not exploratory survival

**Location:** `factorylab/kernel/reserve.py:55`, `:85`; `factorylab/runtime/loop.py:1107`, `:2521`. **Essay:** “Some share of compute and write access” — II.II.b.

The reserve is an entitlement counter independent of wallet holds. Consuming a trial ticket registers a contract but buys no invocation or protected lifetime; incumbents can spend the wallet first. Eligibility checks history by new contract ID, so renamed clones also consume the niche. This invites **learning death**: look for `novelty.reserve`/`Registered` followed by no novel invocations or delayed reward after abandonment. `versioning/versions.py:172` only notices same-cell runs with zero registrations, so clone churn hides the pathology.

**Fix:** Reserve actual compute and scheduling opportunity for unhistoried decisions through an explicit trial horizon. Track inherited reward/propensity history across descendants, preserve a mean-based frontier, and measure its rewarded invocation share. Ticket issuance alone is a toothless implementation of guaranteed patience.

### 10. High — Common, saturated penalties can lock in stable failure

**Location:** `factorylab/runtime/loop.py:1588`, `:1611`; `factorylab/charter/controller.py:218`. **Essay:** “price the duration of failure” — II.II.b.

Every return in a role pays the latest closed window's aggregate violation, regardless of which decision caused or corrected it. Large violations clip all rewards to zero, eliminating the contrast needed to learn an escape. The scripted tests explicitly expect turnover saturation and zero producer rewards. First signs of **stable failure** are persistent `price.update` saturation, repeated zero effective settlements and unchanged behavioral occupancy. Price ramping, decay and damping exist, but no attractor-duration signal reaches the controller. `versioning/versions.py:114` can diagnose a supported violating attractor after diary analysis; it cannot actuate relief and misses failures outside measured cards.

**Fix:** Price accountable damage and persistent regime failure separately, preserve learning contrast and protected exploration, and couple duration-sensitive gain to measured settling rather than blindly increasing a common clipped tax.

### 11. High — Evaluators can intervene in the consequences that grade them

**Location:** `factorylab/runtime/loop.py:1846`, `:2048`, `:2135`, `:2999`. **Essay:** “the signal that grades an evaluator must sit outside the loop” — II.III.a.

Every role receives every tool, including venue writes and treasury transfers. Only producer steps open/finalize economic return accounts. A fixture evaluator successfully bought ETH without acquiring a corresponding consequence account. A judge can therefore close the very position it grades, or use unscored side effects to make its forecasts come true. This is stronger than the documented producer-to-producer closing externality. A cooperating judge can also deliberately underperform the baseline on an antagonist's return to award exposure; that antagonist need not discover an actual evaluator weakness.

**Fix:** Preserve capability composability while separating evaluated evidence from interventions: account for every role's effects, commit forecasts before intervention, and use independent or counterfactual settlement. Reward reproducible exposure against independently selected judges, not a single cooperating loser.

### 12. High — Cascade buffering launders individual credit; top metas need only valid syntax

**Location:** `factorylab/runtime/cascade.py:49`, `factorylab/runtime/loop.py:2355`, `:2413`. **Essay:** “a reward that can be attributed back to the decision that earned it” — II.I.a.

The gate releases the last verdict plus count/mean/min/max. A meta sees that representative's rationale, not the other judgments' adequacy; its score then settles every sibling handle, including unrelated judges. Bad judges can receive a good neighbor's conformity or poison its window. Sampling has no representative-selection correction. Meanwhile the highest meta receives 1 for any numeric conformity, whether its judgment predicts anything useful. A purchasable model seller returning canned valid conformity can satisfy this demand without evaluating anything. Adding another meta merely relocates this ungraded terminus.

**Fix:** Make the aggregate itself an explicitly accountable decision, or sample evidence with recorded inclusion probabilities and individual attribution. Give every terminal evaluator displaced, delayed consequence feedback. Retain the implemented minimum-count ratio and jitter without treating them as evidence that all siblings earned the same score.

### 13. High — The clock measures completed traffic, not the slowest controlled loop

**Location:** `factorylab/runtime/cadence.py:29`, `:46`; `factorylab/runtime/loop.py:1190`, `:1297`; `factorylab/settlement/lots.py:209`. **Essay:** “the periodicity of the factory’s slowest loops” — II.IV.c.

Governance uses capped p90 completed-forecast latency, ignoring outstanding slow decisions and post-perturbation settling. Cheap immediate outcomes can drive that estimate toward zero. Router multiplication also produces internal events that advance forecast/backstop horizons before another market observation. Thus count-based cascade separation can coexist with economic loops that have not closed. First **thrash** signs are charter changes during unsettled cohorts, rapidly expiring decisions and alternating profiles. `versioning/versions.py:194` misses ordinary two-cell alternation at defaults: adjacent three-window block distance is 1/3, below 0.5. Its settling report is not wired into governance.

**Fix:** Register actual loop/cohort lifetimes, include pending evidence and perturbation settling, distinguish economic time from endogenous traffic, and enforce relative cadence across the whole cascade. Detect periodic revisits and price their duration; retain jitter.

### 14. Medium — Committee identity and voting cost are exploitable

**Location:** `factorylab/charter/committee.py:62`; `factorylab/runtime/loop.py:2448`, `:2736`; `factorylab/charter/book.py:113`. **Essay:** “a site of negotiation” — II.IV.

Sortition distinguishes assembly IDs, not inherited policies: a lineage can register producer, evaluator and meta clones to capture role coverage. Fresh aliases do not create independent interests. Amendments are removed before the ordinary proposal cap, and each admitted one immediately invokes the committee at the shared wallet's expense. Ballots receive no subsequent outcome reward. Finally, approved patches apply to the latest charter even when voted against an older base, enabling conflicting approvals to restore removed constraints without a fresh vote.

**Fix:** Sample across accountable lineages, price/bound total voting work against the proposing decision, and settle voters against delayed effects. Revalidate changed amendment meaning against its approved base, with renewed voting where necessary.

### 15. Medium — Public contracts disclose machinery while omitting actionable semantics

**Location:** `factorylab/runtime/loop.py:985`, `:2023`; `factorylab/cortex/request.py:46`; `factorylab/cortex/tools.py:84`; `factorylab/charter/charter.py:53`. **Essay:** “minimal sufficient disclosure” — II.I.b.

The world block exposes the entire active assembly roster and router menus, encouraging dependencies on incidental topology. Yet requests omit their deadline/budget and directed propensity information; `capability_versions` is always empty. Proposal shapes advertise extensibility with the limits in findings 2/4. Return shapes are not enforced (7), and second-round tool requests are silently ignored. Tool schemas resemble JSON Schema but population tools ignore nested constraints, bounds and enums. Charter rendering omits the observation binding and promises rolling 100-return/50-forecast windows while measurement uses reserve windows and cumulative evaluator skill.

**Fix:** Publish versioned capability contracts and exact supported schema semantics, provide recipient-specific resource/selection information, and expose effective observation/window bindings. Minimize ambient topology and enforce declared return/continuation behavior. This preserves rich requests without publishing private learner state.

### 16. Medium — Activity proxies reward cosmetic revision and obscure overfitting

**Location:** `factorylab/runtime/loop.py:1916`, `:2003`, `:2094`; `factorylab/runtime/observations.py:75`; `factorylab/versioning/versions.py:149`. **Essay:** “optimizing for the latter instead of the former” — II.II.a.

Any proposal object, including a rejected one, or any tool call counts as revision. An arbitrary non-`noop` action string conceals inaction; parsed but schema-invalid objects count as well formed. Repeated useless registrations/tool errors can therefore improve norm proxies. First **overfitting** signs are rising revision/well-formedness and falling reported noop share alongside rejected proposals, unchanged economic behavior or deteriorating outcomes. The detector demands rising verdicts and a falling outcome series, prioritizing forecast skill whenever any sample exists; accurate predictions of failure or flat high verdicts defeat it. Antagonists exist, but findings 4/11 leave their corrective force weak.

**Fix:** Increase measurement variety and temporal resolution, distinguish attempted from effective changes, and compare calibration with realized behavior independently. Let competing observation contracts test these proxies rather than declaring every registration to be revision.
