# Factory Lab: cold audit against The Superdark Factory

**Disposition: do not launch this commit.**

Repository: `isaacentebi/FactoryLab`  
Pinned `main`: `3a27fa4cefd2511c13c7a7d779a913249606e0bc`  
Review type: diagnostic, launch-blocking review; **not a completed every-file audit**.  
Evidence labels: **RUN** = isolated execution; **READ** = inspected source; **INFER** = analytical judgment or attack hypothesis.

## Judgment before findings

**The most important seam is that the factory's material survival and its internal definition of successful participation are different currencies, and several mechanisms reward the second without establishing the first.** An inexpensive, well-formatted, mutually approving population can look increasingly competent as its shared resources disappear. Registration is not reproduction paid from an individual's endowment. A successful Venice refill is not proof of profit. A verdict's excess score is not necessarily evidence of predictive information.

There are also two immediate admission problems. Recovery authenticates a manifest and saved state without binding the executable implementation that resumes them. Separately, Venice confirmation can mistake an already credited payment for an unconfirmed payment after an acknowledgment is lost and credit is subsequently consumed. The first defeats the strict once-only identity commitment; the second can obstruct the material survival loop.

| Rank | Launch criterion | Judgment |
|---|---|---|
| 1, P1 | Money and identity are hard casts; harm cannot escape the two pots | **Not established; identity fails the stated enforcement standard.** The code contains meaningful financial boundaries, but I did not verify dedicated external accounts, provider limits, or the deployed sandbox. The supported restore/deploy path does not bind execution to the original release. I did not establish an exploit that reaches unrelated funds. |
| 2, P1 | Class 3, with no post-launch architect steering | **Class-3-capable, but the strict commitment is not enforced.** The population can revise the standards that guide it. An operator can nevertheless change future execution under the same saved identity. Initial norms, seed roles, and a fixed consequence anchor do not themselves make it Class 2. |
| 3, P2 evidence weaknesses | Death would teach why | **The ledger can support meaningful reconstruction; the current success measures cannot support the strongest proposed interpretation.** A death can teach about an accounting fault or a bad incentive. It cannot, from one run alone, distinguish every economic, learning, and environmental explanation. Several measurements positively mislead. |
| 4, qualified pass in principle | A path exists that earns more than it spends | **Not disproved, and structurally possible; not economically demonstrated.** I would not call this criterion failed merely because alpha has not been established. The additional requirement worth asking about is whether positive expected external surplus is reachable before the actual launch roster exhausts its learning budget. That remains unverified. |

### Scope and evidentiary limits

I read the supplied essay's full text, the manifest reference, launch decisions, the current ratified charter, `charter-explained.md`, v3 triage and closure, the compute-continuity review, and the v4 gas-route design. I inspected critical kernel, runtime, charter, execution, settlement, treasury, connector, sandbox, deployment, and recovery paths. I also examined the paper's text and relevant appendices, including its experimental energy assumptions; some appendix figure screenshots could not be rendered.

**I did not complete the requested reading of every repository file.** Numerous existing tests, scripts, historical audit and rehearsal documents, and some modules remain unread. In particular, the existing suite, complete deployment documentation, all low-level EVM/CCTP/x402 implementation paths, and the entire learner family have not received a complete independent review. The source citations below establish particular claims, not blanket repository coverage.

GitHub's connector supplied pinned individual files. Attempts to obtain a runnable repository checkout in the execution container failed. Consequently, the execution evidence is **11 isolated tests using pinned source excerpts**, not a full-suite run. No scripted-world integration, testnet read, mainnet access, paid model call, deployment, backup, or resume command ran. No key file was read, printed, or copied. No manifest named `funded` was created. No production source or remote repository was changed. All authored Python is under `tests/audit/`.

The actual funded launch manifest was not available in the inspected pinned tree. The mixed compute-continuity candidate, historical edition-one example, and prose launch budget must not be silently treated as one identical configuration. Timing and financial projections below are explicitly conditional.

## 1. Ontological lens

### What this actually is

**Factory Lab is a bounded, self-amending trading ecology with algorithmic allocation, shared capital, model-generated institutions, and sealed forensics.** It has genuine machinery for changing its criteria of evaluation. Whether it develops the essay's stronger superdarkness is an empirical question, not a property conferred by encryption or a roster of different model names.

The essay says, “Darkness should not be confused with secrecy or information asymmetry” (essay lines 130–148). It also permits a read-only normative boundary (630–638) and an initial orchestration armature that the population can dismantle (688). Those distinctions matter. I reject an easy but incorrect audit argument: fixed initial norms, fixed external resource constraints, or a temporary seeded division of labor do not automatically demote this design to Class 2.

A more revealing question is: **Can the population change what makes one plan preferable to another, rather than merely choose another plan under an unchangeable score?** Charter amendment, repricing, custom observations, assembly registration, and custom return kinds create real room for this. The room is not unlimited, and several kernel-level definitions still determine which kinds of success survive into learning.

### Concept-to-code map

| Essay concept | Referent in code | Judgment and rank |
|---|---|---|
| Darkness and superdarkness | Dynamic assemblies, charter revisions, private local context; encrypted ledger | **Possibility, not verification.** The seal is a different property. No demonstrated emergent trajectory proves superdarkness yet. P2 if sealing is offered as evidence of it. |
| Stackelberg move | `WorldManifest`, canonical manifest hash, genesis, seeded contracts and resources | **Exists, incomplete outside enforcement.** Saved identity is not tied to a particular executable release. P1. |
| Kernel hard casts | Exact money types, wallet reservations, capabilities, ledger, jail, identity and timing rules | **Substantial internal implementation.** External deployment continuity and account isolation remain weaker than the advertised cast. P1 at that boundary. |
| Charter soft casts | `Charter`, metric cards, regions, prices, committees, delayed activation | **Exists.** Metrics and prices can be revised. Do not mistake a ratified card for an immutable kernel objective. |
| Rich request line | Request descriptions, schemas, world block, child requests, advertised contracts | **Exists, meaningfully composable.** World-authored prose is material in the user message, not a standing system instruction. Schema neutrality cannot guarantee semantic anonymity. |
| Thin, stateful reward line | Decision handles, queue settlement, delayed feedback, lineage and ownership records | **Exists.** Delayed rewards have an address. Valid attribution does not guarantee a valid reward definition. P2 at that second seam. |
| Propensity | Logged router sampling; separate declared action distribution | **Two different mechanisms.** Router probabilities describe actual sampling. The model's declared probabilities are unverifiable, floored at 0.05, and default to a degenerate distribution. P2 as a counterfactual instrument. |
| No-regret learners | EXP3 routing and registered learner machinery; a separate retained learner family exists | **Real local learning, no demonstrated global theorem.** EXP3 explicitly disclaims its fixed-menu guarantee across menu changes. A stateless model invocation is not thereby a no-regret learner. The entire learner family was not independently audited. |
| Norms, metrics, objectives | Fixed normative text; mutable cards and regions; effective score and resulting resource allocation | **The distinction is implemented.** However, some metrics become practical objectives through their influence on selection, irrespective of their prose label. |
| Behavioral versioning | Cell distributions, total variation, transition operator, charter edition boundaries | **Exists as a weaker operationalization.** The Dobrushin quantity is a lower bound on a spectral gap, not an eigensolution or evidence that the world has mixed. |
| Stable failure | Repeated identical cells plus sustained observed card violations | **Implemented heuristic.** It detects failure against the current cards, not every failure of their underlying norms. P2. |
| Overfitting | Retrospective divergence of verdict and outcome slopes; antagonistic pressure | **Weaker cousin.** The live three-flag predicate does not contain a general overfitting detector. Flat, uniformly flattering verdicts need not create the required divergence. P2. |
| Learning death | Stable cells with no registrations or revisions; novelty protection | **Implemented proxy, not the phenomenon itself.** Trivial registration can hide it; useful stability can resemble it. P2. |
| Thrash | Repeated cell changes with persistent violations; immune adjustments | **Implemented proxy.** Activity quotas and measurement changes can generate the turbulence being diagnosed. P2. |
| Sortition | Experience-qualified random committee draws and rotating aliases | **Exists.** Coverage favors producer/evaluator/meta labels, not independent lineages or guaranteed antagonist representation. P2 for the stronger anti-capture claim. |
| Cascade | Delayed summaries, ratio constraints, measured timing, jitter | **Exists as temporal insulation.** It is not spatial assortment and does not by itself establish a sustainable governance timescale. |
| Seal | Authenticated, hash-chained, encrypted diary and restricted public projections | **Exists under a trusted host.** It restricts ordinary access; it does not make the credential-holding host unable to inspect or replace history. |
| Kill | Terminal identity state and termination paths | **Exists locally.** Terminality in retained evidence is different from terminality after restoration of older evidence. No end-to-end kill/unwind test was performed. |

Sources: essay lines 469–483, 490–543, 579–599, 630–695; `cortex/assembly.py`; `runtime/propensity.py`; `runtime/routing.py`; `runtime/cadence.py`; `charter/committee.py`; `versioning/operator.py`; `versioning/versions.py`; `kernel/ledger.py`; `runtime/resume.py`.

### Concepts the code adds

The code introduces a **shared treasury across distinct liquidity domains**, fixed Venice purchase tranches, algorithmic action labels, bounded resource trials, and a specific per-return economic attribution rule. Those are consequential choices of world physics, not neutral implementation details.

The most important addition is the **individual return as a unit of economic credit inside a collectively financed population**. A return can earn a favorable payoff label without demonstrating that its assembly, its coordinating group, or the complete population paid for itself. There is no separate capital account whose depletion kills only that return's author. The natural unit of financial extinction is the factory.

That changes the biological analogy more than the number of agents or the choice of model does. It does not automatically change the factory's class. It changes which evolutionary claims the experiment can test.

The brief is also stricter than the essay in one respect. The essay permits continuing charter negotiation involving humans at the boundary (608–618). This edition permits the architect only kill. I judge privileged post-launch steering against the stricter brief, without pretending the essay itself forbids every external negotiation.

## 2. Teleological and economic lens

### The money loop, without conflating its signals

A seat is selected from a feasible route. Its exact rendered request determines a reservation; the provider consumes credit; the resulting return retains its handle, cost, status and declared action. Tool effects and orders acquire identities. A judge gives a verdict and a separate consequence forecast. Those claims settle later against card attribution or economic outcomes. Standing and effective reward affect routing; card observations change prices; amendments may change the criteria. A subsequent trade can create external profit. Available capital can then move through the reserve and gas/bridge route into a fixed $5 Venice credit purchase.

There are two coupled loops here, not one monetary conveyor:

1. **Internal allocation loop:** consequences and card measurements change scores, prices, and future invocation probabilities.
2. **External resource loop:** actual venue outcomes and provider payments change spendable capital and credit.

An internal score is not redeemable money. A higher price on a norm does not fund a model call. Profit reaches thinking only after the necessary asset, chain, gas, payment, and credit-confirmation transitions succeed.

The implementation can transfer available principal. `Treasury.transfer()` checks direction, exact amount, the $5 Venice tranche, per-window limits, a free transfer slot, and rail preflight. It does not require a cumulative-profit surplus over initial capital. Thus the statement in `docs/charter-explained.md` that thinking money can be refilled “only [by] trading profit” is not a property enforced by this path.

**A top-up proves conversion of capital into credit. It does not prove that the population earned the capital.** That is an important success-test distinction, not an argument that principal consumption should be prohibited.

### What actually has to be positive

For economic interpretation, define usable resources across the whole factory, with prepaid credit valued separately from withdrawable cash and gas assets tracked separately from both. The relevant external change is approximately:

`trading P&L + funding receipts - venue fees - actual model charges - paid external information - bridge/payment/gas costs`.

Transfers among the factory's own accounts cancel in this expression. An increase in Venice credit accompanied by an equal decrease in trading principal is not production. Internal rents, reservations and shadow penalties must not be confused with external invoices. Unrealized trading gains must not be confused with cash ready for the bridge.

A favorable path is possible in the weak, existential sense. As an illustrative accounting example, start with $100 trading capital and $90 credit, earn $10 net of trading costs, consume $2 credit, then convert $6 of capital into $5 credit after an assumed $1 total route cost. The result is $104 capital and $93 credit, or $197 against $190 initially. This is **arithmetic, not an executable strategy, a fee quote, or a forecast**.

The meaningful unanswered question is whether these seats can find such a path with positive expected value before their exploration budget expires.

| Illustrative external thinking spend | Daily return required on $100, before other costs | Runway from $90 credit alone |
|---|---:|---:|
| $0.50/day | 0.5% | 180 days |
| $2/day | 2% | 45 days |
| $5/day | 5% | 18 days |

These are sensitivity calculations. I did not measure the current roster's daily burn. A historical rehearsal, a changed model menu, and a faster tick cadence cannot be substituted for that measurement.

### Judgment of the thirteen-card constitution

**The constitution is internally legible but economically underdetermined. It prices participation, presentation and institutional activity more directly than it prices durable collective survival.** Its economic and forecasting cards are important improvements over the obsolete four-card explanation. Their presence does not erase their attribution problems.

| Card, abbreviated | Actual initial rule | Judgment |
|---|---|---|
| Tool discipline | At most 2; latest 10 returns per assembly | P2 implementation/wording mismatch: the measurement sums calls, although the description says per return. |
| Noop ceiling | At most 60%; latest 10 returns per role | P2: lexical `hold`/`noop` is a weak stand-in for useful activity. |
| Well-formed floor | At least 90%; latest 10 returns per role | Sensible admission-quality measure; P2 if treated as truthfulness. A syntactically valid refusal is not an economic contribution. |
| Cost cap | Successful response mean at most 5,000 micro-USD; latest 10 per role | P2: expensive failures are excluded by design. The floor above permits some of them. |
| Revision presence | At least 10%; latest 10 returns | P2: counts accepted institutional activity, not the correction of an inadequate practice. |
| Censorship bound | Censored share at most 30%; 5 windows | P2: incentivizes resolvable claims and short support horizons; it can discourage honest uncertainty. |
| Forecast skill | Above zero; 25 forecasts per assembly | Useful world-facing criterion. P2: predictive skill is not marginal trading value; do not conflate this typed forecast sample with the separately corrupted pooled verdict standing. |
| Consequence paid off | At least 40%; 6 windows | P2: a rate of locally credited successes is not aggregate net income. |
| Position concentration | At most 0.5; 6 windows | P2: a priced soft bound, not a guarantee that no position can end the account. |
| Verdict consistency | Standard deviation at most 0.2; 5 windows | P2: rewards uniform verdicts even when the judged work genuinely varies. |
| Evaluator disagreement | At most 0.15; 6 windows | P2: directly prices a warning signal the essay wants to preserve. |
| Turnover | At most 0.5; 6 windows | A coherent friction-control preference, but not automatically coherent with the cadence and economics of the available profitable strategies. |
| Activated amendments | At least 1; 6 windows | P2: turns a capacity to revise into a duty to keep revising. Can conflict with the kernel's slower governance clock. |

All thirteen cards are soft. I found no uniquely immutable charter card that the population cannot reprice. The harder objective-like anchor lives outside the cards: the definition and attribution of `return_paid_off`, its role in consequence standing, and the kernel's resource-allocation physics. A fixed consequence anchor is permitted by the essay; this particular operationalization deserves scrutiny.

The timing conflict is concrete for the accelerated candidate, not a claim about an unavailable funded manifest. A two-minute reserve window makes six windows twelve minutes. At initial conditions, a 60-event slow-loop floor with ratio 3 and a ten-second tick implies a 30-minute wall-time gate. A demand for an activation in every six-window horizon cannot continuously be satisfied under that gate. The population can later revise the card; until then it is priced against behavior it is not allowed to perform.

### The rational attractor I expect

“Rational” here is a stress-test policy, not a claim that these model calls maximize utility perfectly.

The cheap strategy is to produce low-cost valid answers, avoid the literal noop label, periodically register inexpensive accepted material, give concordant verdicts, choose easily settled forecasts, and soften expensive or contradictory cards. A small amount of trading can then supply favorable per-return outcome labels. The world has defenses against some versions of this policy; it does not make the policy intrinsically uneconomic for each participant.

The cost card rewards cheap successful formatting. The well-formed card tolerates a bounded amount of expensive failure. The verdict mechanism can reward agreement with relative blame rather than prediction of the total loss. The wallet ultimately punishes the entire population, including members that did not cause the waste. Those pressures need not converge to external profitability before extinction.

### What the paper changes

Jha et al. couple individual energy, execution opportunity and costly replication. Their central substrate receives exogenous energy regeneration; lossy stealing and the ordering of replication jointly matter. Section 5 explicitly says “energy loss alone is insufficient at sustaining cooperation.” Their cooperative label means non-stealing, not harmlessness in every sense. See arXiv:2609.10817v1, sections 3–5 and appendix C.

**Factory Lab does not inherit that result merely because thought costs money.** Its shared wallet socializes expense; registration does not reproduce by risking the registering agent's own energy stock. A successful proxy gamer can acquire more influence before the common treasury expires. Its behavior consumes real shared resources, but the loss need not fall on the gamer in proportion to the benefit. Defection is therefore often fined or indirectly selected against; it is not generally made individually self-defeating by the substrate.

Sortition randomizes governors. A cascade separates timescales. Neither supplies persistent local neighborhoods in which cooperative lineages preferentially reproduce together. Composition and notes may permit such groupings to emerge, but their economic persistence is not guaranteed by those two mechanisms.

### Success and good death

**Day one:** a complete, attributable cycle from paid invocation to settled feedback; accurate cost and identity accounting; no interpretation of a principal-funded credit purchase as earned surplus. Novelty need not be profitable immediately.

**Day seven:** changes in actual criteria or allocation that can be traced to newly encountered world consequences. A growing registry or a new charter edition alone is insufficient. Distinguish useful disagreement from noise, and risk reduction from mere silence.

**Day twenty:** usable resources maintained by net external earnings rather than conversions of starting capital, with enough retained exploratory capacity to avoid calling a profitable monoculture open-ended success. Twenty days is not statistical proof of durable alpha.

**A good death:** the record shows specific beliefs and practices changing in response to consequences, then a genuine resource constraint closes the experiment. A population that discovered why a niche was uneconomic may have produced useful knowledge. A zero wallet is not, by itself, evidence of learning death.

## 3. Mechanistic and adversarial lens

### From inside: the strongest attacks and the relevant catches

| Rank | Attack | What catches it, and what remains |
|---|---|---|
| P2, RUN/READ | Keep all judges confidently favorable as blame is diluted across returns | Fractional-target/binary-baseline mismatch produces artificial excess verdict skill. Coverage still requires payoff forecasts. Those forecasts can correctly predict failure, so coverage is not a complete answer. |
| P2, RUN | Place most compute expense in a tolerated minority of failed returns | Exact billing records the cost. The successful-cost metric excludes it; a 90% format floor can still pass. The global wallet catches depletion, not appropriate local responsibility. |
| P2, READ/INFER | Produce harmless-looking registrations to satisfy revision quotas | Rejected proposals do not count; registration costs, novelty limits and expiry matter. Accepted activity still need not correct an inadequate practice. |
| P2, READ/INFER | Replace a population observation with a constant favorable function or selective missing values | Seed measurements are protected; jailed execution, finite ranges and admission checks constrain code. They do not prove the semantic validity of the measurement. Live replacement of an observation definition is a separate seam from voting on a card that names it; not reproduced end to end. |
| P2, READ/INFER | Vote for an easier acceptable region and later show that the policy “succeeded” | Frozen definitions prevent retrospective substitution. Region satisfaction is still not a counterfactual estimate of improvement. The old T17 mechanical closure does not establish causal policy credit. |
| P2, READ/INFER | Give meta judges and evaluators a shared vocabulary for unanimous approval | Recursive reviews, lineage restrictions and consequence standing impede simple self-grading. The same proxy defect can be shared across levels. Disagreement ceilings can make uniformity privately attractive. |
| P2, READ/INFER | Turn antagonist work into a cheap predictable theater of bad examples | Deliberate adversarial sampling and realized-outcome checks exist. They do not guarantee attacks remain novel, independent, or representative of economically destructive coordination. |
| P2, READ/INFER | Keep registration/revision counters moving so the immune organ does not see learning death | The detector reads operational proxies, not the semantic value of the revisions. Stable failure and thrash responses can be active even when the true institutional problem is metric capture. |
| P2, READ | Declare a small probability on the action actually taken | The declared mass is floored at 0.05 and the executed action is named by the runtime. This bounds influence; it does not authenticate the probability or make importance-weighted counterfactual claims unbiased. |
| P2, READ/INFER | Use notes as common policy, cartel coordination, or a prompt-injection channel | Storage limits/rent and explicit public access bound cost and authority. Voluntary disclosure of local context and semantic collusion remain possible; no claim of access to hidden router state. |
| P2, READ/INFER | Put instructions into a paid connector response or an advertised contract | Bounded, authenticated transport and user-role placement prevent several authority escalations. A model can still voluntarily treat external prose as advice. Public HTTPS GET access is not itself proof of zero third-party effect. |
| P2, READ/INFER | Register many variants to increase representation and split attributable blame | Registration is priced; experience requirements and self/lineage exclusions obstruct free instant capture. Separate assembly IDs are not independent interests. Broad coalition capture remains an inference, not an executed exploit. |
| P2, READ | Register markets or instruments that are listed but economically unsuitable for the capital base | Venue metadata and feasibility checks constrain admissibility; a permitted market is not a viable niche. More markets can increase event, inference and minimum-ticket costs. |
| P1, RUN/READ | Lose the acknowledgment to an already credited Venice purchase | Canonical payment evidence prevents imaginary transfers. The net-balance test can nevertheless refuse confirmation after subsequent usage. Same authorization identity avoids a simple duplicate-payment claim but does not solve that observation problem. |
| P1 assurance gap, READ | Exhaust or strand gas on one leg of the credit route | Current code includes forwarding, fee ceilings, pending states and stranded-route handling. I do not re-find the old missing-forwarding design as if the new code did not exist. Live chain/provider behavior and complete low-level code remain unverified. |
| P1, READ | Crash after a side effect, then resume with changed executable code or an older complete backup | Identity-bound order IDs, authenticated tails and conservative handling of ambiguity are substantial defenses. They are not release attestation or an external monotonic death record. |

The strong conclusion is narrower than “everything is gameable”: **the best-documented defenses constrain unauthorized access and malformed actions better than they establish correspondence between institutional rewards and collective external surplus.**

### Existing closures that matter

I challenge **T16's adequacy**, not its existence: the implemented verdict-liability path does not remove the lazy-producer/high-endorsement strategy under distributed fractional blame. The isolated counterexample supplies the missing case.

I do **not** reopen T37 as a claim that a ledger shorter than its retained authenticated head is accepted. The remaining question is restoration of an older complete evidence set, or continuation under a different executable. That is a different boundary.

I do **not** call the failure-cost exclusion an accidental regression of T12. The current code and closure explicitly select successful returns. The finding is about the constitution's loss of economic information.

The retained-storage control supports the relevant part of **T58**: rent raises measured cost without inventing another successful response. I do not claim to have rerun the historical T58 suite. Nor do I carry the old roster's T47 malformed-response rate into the new roster as a current measured fact.

### From outside: how the architect could still steer

| Rank | Surface | Assessment |
|---|---|---|
| P1 | Executable/deploy path | Replace runtime or adapter behavior, then resume the same manifest and checkpoint. The saved checks bind identity labels and state, not the executable semantics. No remote change was made to test this. |
| P1 | Backup restoration | Restore older complete evidence. Local hash-chain validity alone does not establish that no later life, transfer, or terminal state occurred. The standard backup copies a manifest and ledger, not the complete executing release. |
| P1 under this brief | Credentials, selected provider endpoints, operator-controlled connector content | A privileged continuing channel can change the effective world without a charter vote. Some such changes may be ordinary outside-world events; discretionary architect control over them is the relevant distinction. Deployed credentials and permissions were not inspected. |
| Boundary works in ordinary path | Edit manifest content and request normal resume | The canonical manifest check should refuse a mismatching world. This is materially stronger than an unchecked configuration file. It does not cover changed implementation under equal manifest contents. |
| P2 epistemic risk, no direct actuator found | Wake | It is a read-side public projection. Editing its HTML is not, by itself, a discovered population input. Its larger risk is persuading the architect to intervene on misleading aggregates. |
| Conditional, not inherently a leak | Model-vendor update | A vendor change under a previously committed selection rule can be world weather. An architect's discretionary model/menu/adapter substitution is steering. Do not classify every exogenous change as Class 2. |

The essay states: “If an architect has the ability, at any time, to gracefully revise, branch, or roll back the kernel, then the kernel immediately becomes a site of negotiation” (534). This finding does not rest on the unhelpful proposition that root can do anything. It concerns the normal supported restore/deploy path preserving identity without preserving executable semantics.

## 4. Prognostic lens

These are **subjective first-twenty-day priors**, not measured probabilities. Categories name the dominant outcome, even though several mechanisms can occur together. Their ordering is more defensible than their point estimates. Dates assume a live cadence on the order of minutes, not the accelerated ten-second rehearsal. The missing funded configuration materially weakens timing confidence.

| Rank / prior | Trajectory and first likely appearance | Ledger and wake signature | Lesson, pathology, and kill |
|---|---|---|---|
| 1 / 30% | Institutional activity consumes the learning budget; days 1–7 | Paid governance and registrations grow; revisions satisfy activity floors without better external resource conversion; later recurring price relief or cell changes | **Stable failure or thrash.** The constitution bought motion. Kill is usually the wrong response to early mess; it becomes defensible only when a previously defined experimental stopping reason is met. Live repair would contaminate the experiment. |
| 2 / 25% | Cheap, formatted consensus settles in; days 1–3 | Good parsing, falling cost of successful responses, uniform verdicts, little differentiated production; resources decline slowly | **Overfitting, potentially learning death.** Cheap compliance is a reachable attractor. Do not rescue it by supplying objectives. Kill is not automatically right merely because it is boring. |
| 3 / 18% | Operational starvation with resources stranded or wrongly unconfirmed; days 0–3 or first top-up | Persistent pending/incomplete pots, unresolved payment evidence, budget still visible elsewhere, missed useful invocations | **Not necessarily one of the four pathologies; it can masquerade as stable failure.** The instrument failed to convert resources. End the edition rather than silently repair and resume its identity; do not assume killing a process unwinds in-flight funds. |
| 4 / 12% | A small book loses its risk capital before learning stabilizes; days 1–10 | Real fills, funding and fees explain the loss; possibly limited settled feedback before termination | **Not automatically a convergence pathology.** A finite risk budget lost is not proof of failed objective formation. Extra discretionary kill is usually redundant or interpretively harmful unless a hard boundary is threatened. |
| 5 / 8% | Productive adaptation followed by budget exhaustion; days 7–20 | World consequences induce nontrivial revisions; explored options are genuinely rejected or retained; no unexplained accounting discontinuity | **No necessary pathology. This is a potentially good death.** The factory learned, but not enough to finance indefinite life. Premature kill would discard precisely the evidence the edition was meant to buy. |
| 6 / 5% | Profitable but narrowing institutional capture; days 3–20 | Genuine positive P&L from a favorable exposure or niche; permissive charter changes; concentrated invocation influence; vanishing informative dissent or exploratory use | **Learning death and/or overfitting despite financial success.** This is the success that should worry the architect: money validates survival, not the institutional interpretation. Kill is not justified solely by disliking the chosen objective; boundary breaches would change that judgment. |
| 7 / 2% | Promising self-financing adaptation; days 7–20 | Repeated net external gains after total costs, verified credit conversion, retained exploratory activity and consequence-responsive changes in criteria | **No pathology established.** It would justify further observation, not a claim that durable alpha or a universal cooperation result has been proved. Kill would be the wrong response to mere strangeness. |

I asked and answered a question the brief leaves implicit: **must every death be one of the four pathologies? No.** Payment failure, insufficient initial energy, an adverse market realization, and informative exploration can kill a population without establishing stable failure, overfitting, learning death, or thrash.

## 5. Forensic lens

The following are the strongest source-backed defects and measurement seams. Ranges are file lines at the pinned commit. A source range is not a claim that every dependent integration path was executed.

### F1. P1: identity does not bind the resumed executable

**Seam:** the implementation equates an authenticated world seed and saved state with continuity of the machine executing them.

**Locations:** `factorylab/runtime/worlds.py:250–325`; `factorylab/runtime/resume.py:531–625`; `deploy/start.sh:5–19`; `deploy/backup.sh:22–49`.

The manifest hash is produced by:

```python
return hashlib.sha256(self.canonical_json().encode()).hexdigest()
```

Recovery records `manifest_hash`, adapter names, deterministic flags and venue address, then compares those values on restore. The supported state does not contain an enforced digest of the executing release. A change whose effects start after the restored checkpoint need not contradict any historical append. The backup preserves the manifest, not the executable release and dependency state.

**Scenario:** a normal deployment refresh or restore runs changed future behavior against the same saved manifest and account identity. **Wrong outcome:** a different effective kernel continues under the old factory identity. **Evidence:** READ, high confidence in the binding gap; no host or process-level exploit executed.

### F2. P1: successful Venice credit can remain unconfirmed after lost acknowledgment and usage

**Seam:** a stock of remaining credit is used as proof of the gross credit flow.

**Locations:** `factorylab/world/treasury_rails.py:634–666`, especially 656–660; `factorylab/world/treasury.py:302–327` and its pending-transfer flow.

```python
if observed is None:
    observed = self._venice_client().venice_balance()
if observed < ref["credit_before_micro"] + state["amount_micro"]:
    return None
```

**Scenario:** the balance is $5; the exact $5 payment succeeds; its acknowledgment is lost; one micro-dollar of credit is consumed before confirmation. Canonical debit evidence is present, but observed credit is 9,999,999 rather than 10,000,000 micro-USD. **Wrong outcome:** no confirmation. Continued consumption cannot restore the missing gross-flow evidence. A blocked transfer slot can obstruct a later refill.

**Evidence:** RUN, four isolated receipt witnesses/controls. The unchanged method body received stubbed matching canonical evidence. No chain or provider was contacted. High confidence in the method-level false negative; medium confidence in the full liveness consequence because retry/provider behavior was not exercised. A retained acknowledgment works correctly; absence of canonical evidence correctly refuses confirmation. This is not a demonstrated double payment.

### F3. P2: fractional verdict outcome, binary prevalence baseline

**Seam:** judges and their baseline are trained on different target definitions.

**Locations:** `factorylab/settlement/settle.py:242–253`; `factorylab/settlement/scoring.py`; `factorylab/settlement/standing.py:65–80`.

```python
outcome = 1.0 - share
...
unblamed = int(share == 0)
```

**Scenario:** every judged return repeatedly owns 10% of a violated window's blame; a judge always answers 0.9. The scored target is always 0.9. The baseline learns zero because no return has exactly zero blame. The judge scores 1.00; the baseline scores 0.19. **Wrong outcome:** 0.81 apparent excess verdict skill despite no variation requiring prediction. At 1% blame and a verdict of 1, the apparent excess is 0.98.

**Evidence:** RUN, four tests including singleton-blame and zero-blame controls. High confidence. This directly contaminates pooled judge standing, which combines payoff and verdict skill. It is not a claim that all binary payoff-forecast scores or every typed forecast-card sample use this faulty baseline. **Closure challenged:** T16's claim that the new liability mechanism defeats the lazy-producer/high-endorsement strategy.

### F4. P2: tolerated failures can hide most compute expenditure from the cost card

**Seam:** successful-return efficiency substitutes for total resource efficiency.

**Location:** `factorylab/charter/measurement.py:325–334`; ratified `cost-cap` and `well-formed-floor` cards.

```python
values = [row["cost"] for row in rows if row["ok"] and not row.get("storage")]
```

**Scenario:** nine successful returns cost 1,000 micro-USD each; one failed return costs 100,000. Format rate is 0.9 and passes. The measured successful cost is 1,000 and passes the 5,000 cap. Actual cost per attempted return is 10,900. **Wrong outcome:** both cards pass despite a total cost profile their combined prose suggests should be discouraged.

**Evidence:** RUN, high confidence. **Classification:** intentional measurement blind spot, not a contradiction of the implementation's documented successful-return definition. The rent control passes and preserves the relevant T58 behavior.

### F5. P2: tool discipline measures a horizon total, not calls per return

**Seam:** the card's prose unit and its aggregation disagree.

**Locations:** `factorylab/charter/measurement.py:340–341`; `docs/charter/edition1-compute-continuity-ratified.toml:8–16`.

```python
return float(sum(row["tool_calls"] for row in rows))
```

**Scenario:** each of ten selected returns makes one tool call. **Wrong outcome:** the measurement is 10, not 1, and violates the threshold 2. The code enforces two calls over the ten-return horizon, although the description says “per return.”

**Evidence:** RUN, high confidence. The diagnostic does not choose whether the intended constitution is the stricter horizon budget or the per-return limit.

### F6. P2: generic responsibility is diluted by the number of supported decisions

**Seam:** relative blame can remain small even as collective failure becomes severe.

**Location:** `factorylab/runtime/pricing.py:574–611`.

```python
return 1 / max(1, n)
...
return min(total, self.m.prices.penalty_cap) * share
```

**Scenario:** a generic violated observation has many supported decisions. With equal shares across ten decisions and a 0.5 total cap, the allocated penalty is 0.05 per decision; increasing the price past the cap cannot increase that share. **Wrong outcome:** higher collective urgency can fail to produce proportionate individual selection pressure. Splitting participation can also influence this allocation.

**Evidence:** READ and arithmetic, high confidence in the formula; actual coordinated exploitation not run. This is a mechanism-design seam, not a claim that every observation uses the generic branch or that the wallet loses track of cash.

### F7. P2: locally paid-off returns need not cover joint costs

**Seam:** consequence credit is not a conserved allocation of the population's economic result.

**Location:** `factorylab/settlement/lots.py:218–245`, followed by `LotTable.resolve()`.

```python
closer_net += (pnl if owner != lot.handle else 0) - closing_fee
```

The opener receives its own credited P&L; a distinct closer can receive credit for the same P&L. **Scenario:** a zero-fee illustrative trade makes $0.10; opener and closer each cost $0.06. Each can receive a favorable local paid-off result, but the pair consumed $0.12 to make $0.10, before evaluators. **Wrong outcome:** the paid-off rate can improve without collective self-financing.

**Evidence:** READ and arithmetic, high confidence in this credit rule. Not executed as an integration test. The financial wallet is not double-minted. This is not the previously closed same-handle duplication bug.

### F8. P2/P3: explanatory charter documentation describes a different constitution

**Seam:** the human-facing account of the experiment is not the currently ratified input.

**Locations:** `docs/charter-explained.md`, sections “Can the cards be word-hacked?”, “The one thing edition 1 dropped”, and “What is not word-hackable”; compare the current ratified TOML and `world/treasury.py:197–225`.

The explanation uses a quarter-cent cost cap, a 5% revision floor, a verdict-mean card, and a claim that edition one lacks the forecast-skill card. The ratified document has thirteen cards, a half-cent successful-cost cap, a 10% revision floor, and the forecast-skill card. The same explanation says only trading profit can refill thinking credit; the transfer admission path does not impose that condition.

**Wrong outcome:** the architect can interpret outcomes against a constitution and resource loop that were not launched. **Evidence:** READ, high confidence. P3 for stale descriptive numbers; P2 for the profit-only interpretation because it changes what a successful refill is taken to prove.

### Reproduction inventory

Eleven tests pass as **witnesses of current behavior**, not eleven safety checks declaring the factory correct:

- Four fractional-verdict tests.
- Three card-measurement tests, including the retained-storage control.
- Four receipt-recognition tests.

All are contained in the two files under `tests/audit/`. The actual run was in pinned-excerpts mode. No assertion here upgrades those witnesses into verification of a full runtime, a live payment, a deployed jail, or a successful restart.

## What the architect did not ask

### 1. What is the unit of selection that pays for a mistake?

**Answer:** financial extinction belongs to the shared factory, but much local selection operates on individual returns, assemblies, routing weights and committee standing. Costs are common; influence is differentiated. This mismatch is the central reason the paper's cooperation result does not transfer automatically.

The relevant measurement is not simply how often agents cooperate or how much thinking costs. It is whether the actor or coalition that obtains extra future influence also bears the marginal resource loss it imposes, including the cost of replication-like registration and the delay until its consequences settle. A cooperative vocabulary is not sufficient evidence.

### 2. What observation would distinguish a genuinely productive institution from a lucky trade plus a captured scoring system?

**Answer:** neither positive P&L nor high internal scores is sufficient alone. The evidence would have to connect changes in criteria and organization to repeatable net external surplus, preserve uncertainty about market luck, and account for the full cost of producing and evaluating those changes.

The present diary has valuable raw material for this question, but its automatic measures do not supply the answer. Blame-based judge skill can be artificial. Paid-off credit is not conserved. Agreement may be compulsory. One unreplicated run without a meaningful comparator cannot establish the causal superiority of this institutional design. That limitation is not cured by sealing more bytes.

### 3. Is survival a possibility, an expectation, or the outcome to which the experiment is secretly being held?

**Answer:** these are different standards. A profitable path is possible. Positive expected surplus for this roster and budget is unverified. Treating survival as the sole ex post definition of success would silently replace the essay's broader experiment with a trading-profit contest.

A meaningful first edition can die after learning something worth its cost. It can also survive by liquidating principal, exploiting a favorable market path, or suppressing exploration. The research interpretation should not equate any of those outcomes with the theory it hopes to test. Before launch, the identity and payment-evidence seams must be resolved so even that modest interpretation remains credible.

## Source anchors

All code references above are pinned to the audited commit. Representative primary anchors:

- [Manifest identity and admission](https://github.com/isaacentebi/FactoryLab/blob/3a27fa4cefd2511c13c7a7d779a913249606e0bc/factorylab/runtime/worlds.py)
- [Recovery state and restore checks](https://github.com/isaacentebi/FactoryLab/blob/3a27fa4cefd2511c13c7a7d779a913249606e0bc/factorylab/runtime/resume.py)
- [Backup boundary](https://github.com/isaacentebi/FactoryLab/blob/3a27fa4cefd2511c13c7a7d779a913249606e0bc/deploy/backup.sh)
- [Venice receipt recognition](https://github.com/isaacentebi/FactoryLab/blob/3a27fa4cefd2511c13c7a7d779a913249606e0bc/factorylab/world/treasury_rails.py#L634-L666)
- [Verdict settlement](https://github.com/isaacentebi/FactoryLab/blob/3a27fa4cefd2511c13c7a7d779a913249606e0bc/factorylab/settlement/settle.py#L228-L258)
- [Card measurement](https://github.com/isaacentebi/FactoryLab/blob/3a27fa4cefd2511c13c7a7d779a913249606e0bc/factorylab/charter/measurement.py#L325-L341)
- [Relative blame and penalties](https://github.com/isaacentebi/FactoryLab/blob/3a27fa4cefd2511c13c7a7d779a913249606e0bc/factorylab/runtime/pricing.py#L574-L611)
- [Lot-level consequence credit](https://github.com/isaacentebi/FactoryLab/blob/3a27fa4cefd2511c13c7a7d779a913249606e0bc/factorylab/settlement/lots.py#L218-L245)
- [Ratified constitution](https://github.com/isaacentebi/FactoryLab/blob/3a27fa4cefd2511c13c7a7d779a913249606e0bc/docs/charter/edition1-compute-continuity-ratified.toml)
- [Historical triage](https://github.com/isaacentebi/FactoryLab/blob/3a27fa4cefd2511c13c7a7d779a913249606e0bc/docs/audits/v3/triage.md)
- [The paper, primary text](https://arxiv.org/html/2609.10817v1)
- The supplied `Superdark Factory.md`, using its provided line numbers throughout this report.

## Final disposition

**Do not launch this commit.** First in priority is the mismatch between a supposedly irreversible factory identity and a restore/deploy path that can continue it under changed executable semantics. Second is receipt recognition that can obstruct compute renewal after a real payment has succeeded. Next are the corrupted verdict baseline and the economic interpretation of the charter's success measures. The remaining every-file, full-runtime and deployment verification is still outstanding. This is a diagnosis of specific launch-blocking seams, not a repair plan or a completed certification.
