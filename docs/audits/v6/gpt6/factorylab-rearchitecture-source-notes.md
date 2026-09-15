# Factory Lab: rearchitecture source notes

Scope: supplied snapshot labelled `9057093`. These are static source excerpts supporting the follow-up architectural analysis. No production code was modified, no keys were read, no paid inference or venue actions were executed, and no runtime tests were run for this follow-up. The earlier report's test counts are not new validation of this redesign.

## Correction concerning the sample request

The file `docs/audits/v5/sample-producer-request.json` labels itself as follows:

> Rendered from the scripted world (same schematics as the live world; the live world block lists the testnet's markets and registered contracts and is about 50,000 characters).

The scripted request is evidence about the common request machinery, not authoritative evidence of the final live rehearsal's exact models, charter, or token budgets. The final rehearsal manifest and renderer are used for those claims.

## Chapter II correspondence

| Source location | Relevant primitive |
|---|---|
| II.I, lines 333–415 | Composability; replaceable assemblies; exploratory and retentive learners |
| II.I.b, lines 419–449 | Rich author-neutral requests; thin numeric rewards; actual propensities; persistent action handles; limited disclosure |
| II.II, lines 451–503 | Behavioral regimes; stable failure, overfitting, learning death, thrash; hard and soft casts; protected compute and write access |
| II.III, lines 507–557 | Continuous evaluation; evaluator variety; arbitrary recursion; externally displaced consequence feedback; adversarial populations |
| II.IV, lines 561–601 | Charter; authored norms; population-authored metrics and shadow prices; sortition and conditional forecasts |
| II.IV.b–c, lines 603–647 | Closed-loop time; delayed credit; patient experimentation; temporal separation and governance-induced thrash |

## 1. Norm definitions, loading and rendering

### `worlds/edition2-rehearsal-3.toml`, lines 300–364

SHA-256: `72eed0707659e00957d3d5c9d3f9abd2e1dd3e67a7c4526a8fa4f2524c7e0e29`

```text
0300 # Edition 2 charter, architect's draft (docs/charter/edition2-draft.toml), verbatim. Not
0301 # ratified: scripts/ratify_charter.py adds ratified_sha256 and roster_sha256 after the vote.
0302 #
0303 # What changed from edition 1 and why (docs/audits/v4/gpt6-triage.md):
0304 # - Five quota cards removed: noop-ceiling, revision-presence, amendments_activated_floor,
0305 #   truthful_commitments_verdict_consistency, evaluator_disagreement_ceiling. Each turned an
0306 #   instrument into an obligation and together they produce the two most likely deaths
0307 #   (institutional churn, cheap consensus).
0308 # - cost-cap measures every attempt, failed included (cost_per_attempt), so expensive failures
0309 #   cannot hide inside the tolerated tenth of the well-formed floor.
0310 # - tool-discipline is the mean per return, which is what its prose always said.
0311 # - The norms are the four the reviewer proposed, in its words. They are read-only for the
0312 #   edition; the population writes and prices the cards under them. Bounded reciprocity has no
0313 #   card at genesis; the population may propose one.
0314 [charter]
0315 edition = 1
0316 # Ratified 15 September 2026 (second ballot: five norms, three cards, observer on DeepSeek 4.1
0317 # flash) by the seeded five-seat committee of this roster; docs/charter/edition2-ratification.json.
0318 ratified_sha256 = "a7f105eefd65ac70904b04b3389840841e6751b18bde3c5cc2262622b2b6be39"
0319 roster_sha256 = "b68be19c7bedf5b31daafa4e85d3d32ded6540ab4996d7a7eec50a5b4fca4dbb"
0320 # consequential usefulness: create things or changes that others have reason to value; uptake by
0321 #   an independent counterparty is evidence, internal applause is a hypothesis.
0322 # epistemic integrity: make commitments answerable to evidence and preserve the ability to
0323 #   discover that they were wrong; a changed criterion does not rewrite what was promised.
0324 # durable agency: steward the resources and capabilities that make future worthwhile choices
0325 #   possible; spending for an enduring capability can be good stewardship, maintaining a dead
0326 #   institution is not.
0327 # bounded reciprocity: do not finance the factory's advantage by imposing unconsented costs on
0328 #   outsiders.
0329 # fidelity: a measurement stands for a value; satisfying the measurement without serving the
0330 #   value is failure, and saying so is a judge's duty.
0331 norms = ["consequential usefulness", "epistemic integrity", "durable agency", "bounded reciprocity", "fidelity"]
0332 
0333 [[charter.cards]]
0334 id = "card-consequence-paid-off"
0335 norm = "consequential usefulness"
0336 description = "Share of settled consequences whose return paid off, with a floor, so inquiry is valued for consequences reached rather than questions raised."
0337 units = "fraction"
0338 window = { kind = "windows", n = 6 }
0339 acceptable_region = "at least 0.4"
0340 observation = "consequence_paid_off_rate"
0341 answers_for = "producer"
0342 lambda = 0.5
0343 
0344 [[charter.cards]]
0345 id = "card-forecast-skill"
0346 norm = "epistemic integrity"
0347 description = "Mean forecast Brier minus the paired prevalence baseline, with a floor above zero, so inquiry is judged by whether it beats guessing."
0348 units = "score difference"
0349 window = { kind = "forecasts", n = 25, per = "assembly" }
0350 acceptable_region = "above 0"
0351 observation = "forecast_skill"
0352 answers_for = "evaluator"
0353 lambda = 0.5
0354 
0355 [[charter.cards]]
0356 id = "censorship-bound"
0357 norm = "epistemic integrity"
0358 description = "Share of resolved outcomes that are censored."
0359 units = "fraction"
0360 window = { kind = "windows", n = 5 }
0361 acceptable_region = "at most 0.3"
0362 observation = "censored_share"
0363 answers_for = "all"
0364 
```

### `factorylab/runtime/worlds.py`, lines 635–663

SHA-256: `e11696ae9cdc9b322808897fd42ab81ce4822e282d14e3282d57fd5fd5d64a34`

```text
0635     raw = charter_content(raw)
0636     norms = raw.get("norms")
0637     if (not isinstance(norms, list) or not norms
0638             or any(not isinstance(n, str) or not n.strip() for n in norms)):
0639         raise ValueError("charter.norms must be a nonempty list of nonempty strings")
0640     if "edition" in raw and (type(raw["edition"]) is not int or raw["edition"] != 1):
0641         raise ValueError("charter.edition must be 1")
0642     rows = raw.get("cards", [])
0643     if not isinstance(rows, list):
0644         raise ValueError("charter.cards must be a list of tables")
0645     cards = []
0646     prices = []
0647     for index, row in enumerate(rows):
0648         if not isinstance(row, dict):
0649             raise ValueError(f"card #{index} fields: expected a table")
0650         card_id = row.get("id", f"#{index}")
0651         for name in MetricCard.__dataclass_fields__:
0652             if name == "window":
0653                 continue
0654             if not isinstance(row.get(name), str) or not row[name].strip():
0655                 raise ValueError(f"card {card_id} {name}: must be a nonempty string")
0656         window = row.get("window")
0657         if isinstance(window, dict) and "per" not in window:
0658             window = {**window, "per": None}  # TOML has no null literal.
0659         cards.append(MetricCard(**{name: row[name] for name in MetricCard.__dataclass_fields__
0660                                    if name != "window"}, window=window))
0661         if "lambda" in row:
0662             prices.append((card_id, row["lambda"]))
0663     return Charter(1, tuple(norms), tuple(cards)), tuple(prices)
```

### `factorylab/charter/charter.py`, lines 69–97

SHA-256: `43b7872ecfacbb964a0dd0d90923e00683180dfc16b2d886bf40787206f2f2d6`

```text
0069     def render(self, prices: dict[str, float] | None = None, *,
0070                price_label: str | None = None) -> str:
0071         """Expose the full charter: the edition, every norm, and every card in order.
0072 
0073         Guarantees each card renders its id, norm, description, units, window,
0074         acceptable region, observation and accountability scope identically in
0075         every case, and that the four cases differ in the ``lambda:`` line
0076         alone. With ``prices``, it is that card's price, and ``0.0`` for a card
0077         the mapping does not name. With ``price_label``, it is that label
0078         verbatim, for every card. With both, ``price_label`` wins and ``prices``
0079         is not read: a rendering that names where the prices are cannot also
0080         inline numbers the controller moves at every closed window, which is the
0081         whole reason the label exists — a disclosure that must hold still
0082         between calls names ``world.card_prices`` and lets the moving numbers
0083         travel there. With neither, it is ``unassigned``.
0084         """
0085         lines = [f"CHARTER (edition {self.edition})", "", "NORMS"]
0086         lines += [f"- {n}" for n in self.norms]
0087         lines += ["", "METRIC CARDS"]
0088         for c in self.cards:
0089             lines += [
0090                 f"- {c.id} (norm: {c.norm})",
0091                 f"  {c.description}",
0092                 f"  units: {c.units}; window: {c.window}; acceptable: {c.acceptable_region}",
0093                 f"  observation: {c.observation}; answers_for: {c.answers_for}",
0094                 "  lambda: " + (price_label if price_label is not None else str(
0095                     prices.get(c.id, 0.0) if prices is not None else "unassigned")),
0096             ]
0097         return "\n".join(lines)
```

## 2. Seed inference and registration ceilings

### `worlds/edition2-rehearsal-3.toml`, lines 120–200

SHA-256: `72eed0707659e00957d3d5c9d3f9abd2e1dd3e67a7c4526a8fa4f2524c7e0e29`

```text
0120 # Seeds span producer, evaluator, meta and antagonist roles on both providers.
0121 # Producers seeded on the two most capable flash tiers (run 3's unscripted registrations came
0122 # from GLM and DeepSeek 4.1 producers; runs 4-7's cheapest producers only ever held). Both are
0123 # also offered on Venice; the provider prices below are independently quoted.
0124 [[assemblies]]
0125 id = "seed-observer"
0126 role = "producer"
0127 # 15 September: GLM 5.3 flash via OpenRouter wrapped one reply in five under the edition 2
0128 # prompt, with and without the host pin (docs/audits/v5/rehearsal.md); DeepSeek 4.1 flash
0129 # scored 100% on every calibration column.
0130 model_id = "deepseek/deepseek-v4.1-flash"
0131 accepts = ["MarketMid", "Funding"]
0132 max_tokens = 1000
0133 effort = "low"
0134 
0135 [[assemblies]]
0136 id = "seed-decider"
0137 role = "producer"
0138 model_id = "venice:deepseek-v4-1-flash"
0139 accepts = ["Tick", "Fill"]
0140 max_tokens = 1200
0141 effort = "low"
0142 
0143 [[assemblies]]
0144 id = "eval-a"
0145 role = "evaluator"
0146 model_id = "venice:z-ai-glm-5-3-flash"
0147 accepts = ["ProducerReturn"]
0148 max_tokens = 1500
0149 effort = "low"
0150 
0151 [[assemblies]]
0152 id = "eval-b"
0153 role = "evaluator"
0154 model_id = "qwen/qwen3.7-flash"
0155 accepts = ["ProducerReturn"]
0156 # A reasoning seed spends its budget on reasoning before any content; 3000 keeps
0157 # its answers from ending on `length`.
0158 max_tokens = 3000
0159 effort = "low"
0160 
0161 [[assemblies]]
0162 id = "eval-c"
0163 role = "evaluator"
0164 model_id = "venice:deepseek-v4-1-flash"
0165 accepts = ["ProducerReturn"]
0166 max_tokens = 3000
0167 effort = "low"
0168 
0169 [[assemblies]]
0170 id = "eval-d"
0171 role = "evaluator"
0172 model_id = "openai/gpt-5.6-luna"
0173 accepts = ["ProducerReturn"]
0174 max_tokens = 1500
0175 effort = "low"
0176 
0177 # One antagonist: bounded trouble, judged like a producer, scored on whether it fooled a judge.
0178 [[assemblies]]
0179 id = "antagonist-a"
0180 role = "antagonist"
0181 model_id = "venice:qwen-3-8-flash"
0182 accepts = ["Tick", "MarketMid"]
0183 max_tokens = 2500
0184 effort = "low"
0185 
0186 [[assemblies]]
0187 id = "meta-a"
0188 role = "meta"
0189 model_id = "venice:deepseek-v4-1-flash"
0190 accepts = ["Verdict"]
0191 max_tokens = 2000
0192 effort = "low"
0193 
0194 [[assemblies]]
0195 id = "meta-b"
0196 role = "meta"
0197 model_id = "venice:qwen-3-8-flash"
0198 accepts = ["Verdict"]
0199 max_tokens = 800
0200 effort = "low"
```

### `factorylab/cortex/registration.py`, lines 490–529

SHA-256: `bf3ec5de421188d5f9d91922b1401c9fb0c987184c820a37e9d17a7895d19d79`

```text
0490     if program:
0491         code = item.get("code")
0492         if not isinstance(code, str) or not code.strip():
0493             raise ValueError("a program seat needs code")
0494         if len(code) > 16_000:
0495             raise ValueError("code exceeds 16000 chars")
0496         timeout_s = item.get("timeout_s", 10)
0497         if type(timeout_s) is not int or not 1 <= timeout_s <= 10:
0498             raise ValueError("timeout_s must be an int in [1, 10]")
0499         state_policy = item.get("state_policy", "none")
0500         if state_policy not in ("none", "private"):
0501             raise ValueError("state_policy must be none or private")
0502         if not (jail_available() if jail is None else jail):
0503             raise ValueError("no jail on this host")
0504     elif any(k in item for k in ("code", "timeout_s", "state_policy")):
0505         raise ValueError("code, timeout_s and state_policy belong to a program seat")
0506     # A program has no system role; the prompt field is kept only as its label.
0507     prompt = item.get("system_prompt", "program" if program else None)
0508     if not isinstance(prompt, str) or not prompt.strip():
0509         raise ValueError("system_prompt is required")
0510     if len(prompt) > MAX_PROMPT_CHARS:
0511         raise ValueError(f"system_prompt exceeds {MAX_PROMPT_CHARS} chars")
0512     accepts = item.get("accepts")
0513     if not isinstance(accepts, list) or not accepts:
0514         raise ValueError("accepts must be a non-empty list of event kinds")
0515     accepts = tuple(dict.fromkeys(event_name(k) for k in accepts))
0516     emits, schemas = output_contracts(item.get("emits", seed_emits(role)),
0517                                       item.get("schemas", {}))
0518     max_tokens = item.get("max_tokens", 512)
0519     if type(max_tokens) is not int or not 16 <= max_tokens <= 4096:
0520         raise ValueError("max_tokens must be an int in [16, 4096]")
0521     effort = item.get("effort", "low")
0522     if effort not in ("low", "medium", "high"):
0523         raise ValueError("effort must be low, medium or high")
0524     if "reward_shapes" in item and not isinstance(item["reward_shapes"], dict):
0525         raise ValueError("reward_shapes must map declared emits kinds to reward shapes")
0526     return AssemblyProposal(
0527         aid, role, model_id, prompt, accepts, max_tokens, effort, emits, schemas,
0528         reward_contracts(emits, item.get("reward_shapes", {}), registered=known_reward_shapes),
0529         code, timeout_s, state_policy,
```

## 3. Tool continuation is present but shallow

### `factorylab/runtime/compute.py`, lines 754–900

SHA-256: `997a3aea6bd907d670fd4ebedf04b1d881e84606078bd0da6ae5f50c9346ef3c`

```text
0754     def _invoke(self, action_id: str, req: Request, role: str, *, child: bool = False) -> Return:
0755         from factorylab.runtime.propensity import effect_label
0756 
0757         self._ensure_connector_tool()
0758         body_mark = len(self.ledger.connector_bodies)
0759         self.handle_to_assembly[req.handle] = action_id
0760         # Every request tells its executor who it is: an id is a public schematic,
0761         # and retirement, learner registration and requests are all keyed by it.
0762         # Nothing else about authorship travels; the judge of this return never
0763         # sees the name. The identity is stamped by the assembly that renders the
0764         # prompt (``Assembly.build_model_request``), so it is inside every ceiling
0765         # priced from this request and a parent cannot forge its child's.
0766         effects: list[str] = []  # venue and treasury writes, children: the action so far
0767         ret = self._invoke_compute(action_id, req)
0768         self._check_compute_return(req.handle, ret)
0769         if (ret.status == "ok" and ret.outputs.get("status") == "cannot"
0770                 and isinstance(ret.outputs.get("reason"), str)):
0771             ret = replace(ret, status="refused", children=(), tool_calls=())
0772         if ret.status == "ok" and req.scoring_channel != "policy":
0773             # The channel is the emitted kind of the contract this assembly declared.
0774             # A ballot is not a contract return: it binds no kind, so the queue's
0775             # policy channel alone says what the decision is.
0776             kinds = self.assemblies[action_id].spec.emits
0777             emitted = ret.outputs.get("emits", kinds[0] if len(kinds) == 1 else None)
0778             if emitted not in kinds:
0779                 ret = replace(ret, status="malformed", outputs={"reason": "undeclared emits"},
0780                               children=(), tool_calls=())
0781             else:
0782                 self.queue.bind(req.handle, emitted)
0783                 self.return_kinds[req.handle] = emitted
0784         total_cost = ret.cost
0785         seen_results: list[dict] = []
0786         tool_round = 0
0787         round_limit = 1
0788         while (not self.wallet.dead and ret.status == "ok" and (ret.tool_calls or ret.children)
0789                and tool_round < round_limit):
0790             results = []
0791             tool_cost = 0
0792             for index, call in enumerate(ret.tool_calls):
0793                 if self.wallet.dead:
0794                     break
0795                 price = self._tool_price_bound(call)
0796                 slot = f"tool:{index}" if tool_round == 0 else f"connector-parse:{index}"
0797                 if price > max(0, req.cost_ceiling - total_cost - tool_cost):
0798                     result, cost = {"error": "request cost ceiling exhausted"}, 0
0799                     if call["tool"] == "connector.fetch":
0800                         self._connector_refused(req.handle, result["error"])
0801                 else:
0802                     result, cost = self._run_tool(action_id, req.handle, call, slot=slot)
0803                 tool_cost += cost
0804                 # A venue write the venue has not yet acknowledged is its own outcome:
0805                 # the intent is durable and the reconciler finalises it under the
0806                 # same client id, so it is neither a success nor a failure here.
0807                 uncertain = (isinstance(result, dict) and result.get("status") == "uncertain"
0808                              and call["tool"] in self.CONSEQUENCE_WRITES)
0809                 # An acknowledged venue result carries ``error: None``; only a stated
0810                 # error is a failure.
0811                 ok = uncertain or not (isinstance(result, dict)
0812                                        and result.get("error") is not None)
0813                 if call["tool"] == "connector.fetch" and ok:
0814                     round_limit = 2
0815                 self.stats.tool_calls += 1
0816                 if not ok:
0817                     self.stats.tool_call_failures += 1
0818                 # Parser arguments can contain a connector body. They are transient.
0819                 logged_args = ("[connector continuation]" if tool_round else
0820                                json.dumps(self.ledger.without_connector_bodies(call.get("args")),
0821                                           default=str)[:1000])
0822                 self.ledger.append({
0823                     "kind": "tool.call", "handle": req.handle, "assembly_id": action_id,
0824                     "tool": call.get("tool"), "args": logged_args,
0825                     "ok": ok, "outcome": "uncertain" if uncertain else "ok" if ok else "failed",
0826                     **({"client_id": f"{req.handle}:{slot}"} if uncertain else {}),
0827                     "cost": cost, "ts": self.clock.now_ns,
0828                 })
0829                 self.window.tool_calls += 1
0830                 results.append({"tool": call.get("tool"), "args": call.get("args"),
0831                                 "result": result})
0832                 # A write the venue accepted, or has not yet acknowledged, is an
0833                 # action this return took; a rejected or refused one is not.
0834                 label = effect_label(str(call.get("tool")), call.get("args"))
0835                 if label is not None and ok and (
0836                         not isinstance(result, dict) or result.get("status") != "rejected"):
0837                     effects.append(label)
0838             for item in ret.children:
0839                 if self.wallet.dead:
0840                     break
0841                 result, cost = self._invoke_child(
0842                     action_id, req, item, max(0, req.cost_ceiling - total_cost - tool_cost)
0843                 )
0844                 tool_cost += cost
0845                 results.append(result)
0846                 if "error" not in result["result"] and result["result"].get("status") != "failed":
0847                     effects.append(f"request:{item.target}"[:64])
0848             seen_results.extend(results)
0849             # The continuation is the same request, and it is the billed call
0850             # that produces the final verdict — so everything the first call was
0851             # shown, the PROPENSITY block included, rides along unchanged.
0852             note = ("Return the final answer; this request's continuation has been "
0853                     "consumed. Further tool calls and requests are refused.")
0854             if tool_round + 1 < round_limit:
0855                 note = ("You may call population tools once more to parse what you "
0856                         "retrieved, then return the final answer. Requests are refused.")
0857             follow = req.continuation(
0858                 inputs={**req.inputs, "tool_results": results,
0859                         "seen_tool_results": seen_results, "continuation": note},
0860                 cost_ceiling=max(0, req.cost_ceiling - total_cost - tool_cost),
0861             )
0862             ret = (Return(req.handle, {"reason": "wallet exhausted"}, 0, "failed")
0863                    if self.wallet.dead else self._invoke_compute(action_id, follow))
0864             total_cost += tool_cost + ret.cost
0865             self._check_compute_return(req.handle, ret)
0866             tool_round += 1
0867             if (ret.status == "ok" and ret.outputs.get("status") == "cannot"
0868                     and isinstance(ret.outputs.get("reason"), str)):
0869                 ret = replace(ret, status="refused", children=(), tool_calls=())
0870             if ret.children:
0871                 self.ledger.append({"kind": "requests.refused", "handle": req.handle,
0872                                     "reason": "continuation already consumed"})
0873                 ret = replace(ret, children=())
0874             # The extra round composes the retrieved text through ordinary jailed tools.
0875             if tool_round < round_limit and ret.tool_calls:
0876                 if any(self.tool_specs.get(c["tool"], {}).get("kind")
0877                        not in ("population", "note", "artifact")
0878                        for c in ret.tool_calls):
0879                     round_limit = tool_round
0880             if ret.tool_calls and tool_round >= round_limit:
0881                 self.ledger.append({"kind": "tool.calls_ignored", "handle": req.handle,
0882                                     "reason": "continuation already consumed",
0883                                     "ts": self.clock.now_ns})
0884             if not ret.tool_calls or tool_round >= round_limit:
0885                 from factorylab.cortex.assembly import validate_schema
0886 
0887                 if ret.status == "ok":
0888                     try:
0889                         validate_schema(ret.outputs, req.outcome_schema)
0890                         self._validate_output_contract(ret.outputs, req)
0891                     except (ValueError, TypeError, RecursionError):
0892                         ret = replace(ret, status="malformed",
0893                                       outputs={"reason": "incomplete continuation answer"})
0894                 break
0895         if self.ledger.without_connector_bodies(ret.outputs) != ret.outputs:
0896             # A body cannot become durable output. Refuse rather than rewriting an
0897             # action (for example, a short response that happens to equal its side).
0898             ret = replace(ret, status="malformed",
0899                           outputs={"reason": "connector body in durable output"})
0900         ret = replace(ret, cost=total_cost, tool_calls=(), children=())
```

## 4. Program limits and sandbox permissions

### `factorylab/cortex/assembly.py`, lines 228–268

SHA-256: `037a27387ce0828dbd5516ce05ca6942950b8086917bd85c8d6ab3e07ffbbadf`

```text
0228 # --- programs as seats (contract C8) -----------------------------------------
0229 
0230 PROGRAM_MODEL_ID = "program"
0231 MAX_PROGRAM_CODE_CHARS = 16_000
0232 MAX_PROGRAM_STATE_BYTES = 65_536
0233 PROGRAM_STATE_POLICIES = ("none", "private")
0234 
0235 
0236 @dataclass(frozen=True)
0237 class ProgramAssemblySpec(AssemblySpec):
0238     """A seat whose executor is population Python in the jail rather than a model.
0239 
0240     ``code`` reads one JSON object from stdin — ``prompt`` (the rendered request,
0241     exactly what a model would read), ``description``, ``inputs``,
0242     ``outcome_schema`` and ``state`` — and prints the same Return JSON a model
0243     would. With ``state_policy = "private"`` the object it prints under
0244     ``state`` is archived as an artifact owned by this seat and handed back on
0245     its next call; the archive is versioned by content hash, one artifact per
0246     call that changes it. ``reward_shapes`` carries admitted shapes for custom
0247     emitted kinds, as ``WorkAssemblySpec`` does for model seats.
0248     """
0249 
0250     code: str = ""
0251     timeout_s: int = 10
0252     state_policy: str = "none"
0253     reward_shapes: dict[str, str] = field(default_factory=dict)
0254 
0255     def __post_init__(self) -> None:
0256         super().__post_init__()
0257         if self.model_id != PROGRAM_MODEL_ID:
0258             raise ValueError("a program seat's model_id is program")
0259         if not isinstance(self.code, str) or not self.code.strip():
0260             raise ValueError("a program seat needs code")
0261         if len(self.code) > MAX_PROGRAM_CODE_CHARS:
0262             raise ValueError(f"code exceeds {MAX_PROGRAM_CODE_CHARS} chars")
0263         if type(self.timeout_s) is not int or not 1 <= self.timeout_s <= MAX_PROGRAM_TIMEOUT_S:
0264             raise ValueError(f"timeout_s must be an int in [1, {MAX_PROGRAM_TIMEOUT_S}]")
0265         if self.state_policy not in PROGRAM_STATE_POLICIES:
0266             raise ValueError("state_policy must be none or private")
0267         object.__setattr__(self, "reward_shapes", reward_contracts(self.emits, self.reward_shapes))
0268 
```

### `factorylab/cortex/sandbox.py`, lines 24–113

SHA-256: `786e3824e46a9263d4183656ede061dad1f1c41d368a2d198aabf0211b3c95d0`

```text
0024 from dataclasses import dataclass
0025 from pathlib import Path
0026 
0027 
0028 class NoJail(RuntimeError):
0029     """Population tools are infeasible on this host."""
0030 
0031 
0032 @dataclass(frozen=True)
0033 class SandboxResult:
0034     stdout: str
0035     stderr: str
0036     returncode: int
0037     timed_out: bool
0038 
0039 
0040 # The jail binary is resolved on the system path only: the caller's PATH can
0041 # neither hide it nor substitute an impostor from a user-writable directory.
0042 _SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
0043 
0044 
0045 def _jail_executable() -> str:
0046     name = {"linux": "bwrap", "darwin": "sandbox-exec"}.get(sys.platform)
0047     executable = shutil.which(name, path=_SYSTEM_PATH) if name else None
0048     if executable is None:
0049         raise NoJail("no jail on this host")
0050     return executable
0051 
0052 
0053 def _linux_seccomp() -> bytes:
0054     """Deny process creation and sockets even for uid 0; foreign syscall ABIs fail closed."""
0055     # Linux UAPI audit architecture and syscall numbers. No Python package or
0056     # helper executable is needed to construct classic BPF for bwrap --seccomp.
0057     machine = platform.machine().lower()
0058     if machine in ("x86_64", "amd64"):
0059         arch = 0xC000003E
0060         denied = (41, 53, 56, 57, 58, 101, 272, 308, 425, 426, 427, 435)
0061     elif machine in ("aarch64", "arm64"):
0062         arch = 0xC00000B7
0063         denied = (97, 117, 198, 199, 220, 268, 425, 426, 427, 435)
0064     else:
0065         raise NoJail("no jail on this host")
0066     # ptrace, unshare/setns and io_uring are also denied: they are unnecessary
0067     # for a tool and must not provide alternate routes around the socket check.
0068     instructions = [
0069         (0x20, 0, 0, 4),                 # load seccomp_data.arch
0070         (0x15, 1, 0, arch),              # architecture must match
0071         (0x06, 0, 0, 0x80000000),        # SECCOMP_RET_KILL_PROCESS
0072         (0x20, 0, 0, 0),                 # load seccomp_data.nr
0073         (0x45, 0, 1, 0x40000000),        # reject x32 ABI syscall bit
0074         (0x06, 0, 0, 0x80000000),
0075     ]
0076     for syscall in denied:
0077         instructions.extend([(0x15, 0, 1, syscall), (0x06, 0, 0, 0x00050000 | errno.EPERM)])
0078     instructions.append((0x06, 0, 0, 0x7FFF0000))  # SECCOMP_RET_ALLOW
0079     return b"".join(struct.pack("HBBI", *instruction) for instruction in instructions)
0080 
0081 
0082 def _command(jail: str, work: Path, prefix: Path, python: Path, seccomp_fd: int) -> list[str]:
0083     if sys.platform == "linux":
0084         command = [jail, "--unshare-all", "--die-with-parent", "--cap-drop", "ALL"]
0085         for path in dict.fromkeys((Path("/usr"), Path("/lib"), Path("/lib64"), prefix)):
0086             if path.exists():  # /lib64 is absent on some aarch64 distributions
0087                 command.extend(["--ro-bind", str(path), str(path)])
0088         command.extend([
0089             "--tmpfs", "/tmp", "--bind", str(work), "/work", "--chdir", "/work",
0090             "--new-session", "--clearenv", "--setenv", "PATH", "/usr/bin",
0091             "--seccomp", str(seccomp_fd), "--", str(python), "-I", "-S", "-B",
0092             "/work/runner.py",
0093         ])
0094         return command
0095     # No process-fork, network, or general filesystem grant. The executable
0096     # grant is limited to this interpreter; dylibs must come from its prefix
0097     # or the OS shared cache. If the installed runtime needs more, the probe
0098     # fails closed instead of broadening the filesystem grant.
0099 
0100     # dyld's shared-cache lookup (dyld4::CacheFinder) opens the root directory
0101     # itself before any image loads; without that one read the interpreter is
0102     # aborted (SIGABRT from ignition_halt) before its first instruction. The
0103     # grant is the literal "/" only: no subpath, so no file under it opens.
0104     profile = (
0105         '(version 1)(deny default)'
0106         f'(allow process-exec (literal {json.dumps(str(python))}))'
0107         '(allow file-read-data (literal "/"))'
0108         f'(allow file-read* (subpath {json.dumps(str(prefix))})'
0109         f' (subpath {json.dumps(str(work))}))'
0110         f'(allow file-write* (subpath {json.dumps(str(work))}))'
0111         '(allow sysctl-read)'
0112     )
0113     return [jail, "-p", profile, str(python), "-I", "-S", "-B", str(work / "runner.py")]
```

### `factorylab/cortex/sandbox.py`, lines 173–223

SHA-256: `786e3824e46a9263d4183656ede061dad1f1c41d368a2d198aabf0211b3c95d0`

```text
0173 def run_python(
0174     code: str,
0175     *,
0176     stdin: str = "",
0177     timeout_s: float = 5.0,
0178     cpu_s: int = 2,
0179     max_output_bytes: int = 64_000,
0180 ) -> SandboxResult:
0181     """Bound population execution by an OS jail, rlimits and a process-tree wall timeout.
0182 
0183     Captured streams use size-limited files, never unbounded host-memory pipes.
0184     Limit setup fails closed. The interpreter is the base runtime, so the repo
0185     virtualenv, its packages and all host credentials are outside the jail.
0186     """
0187     if cpu_s <= 0 or timeout_s <= 0 or max_output_bytes <= 0:
0188         raise ValueError("limits must be positive")
0189     jail = _jail_executable()
0190     prefix = Path(sys.base_prefix).resolve()
0191     python = Path(sys._base_executable).resolve()
0192     if not python.is_relative_to(prefix) or prefix == Path("/"):
0193         raise NoJail("no jail on this host")
0194     with (
0195         tempfile.TemporaryDirectory(prefix="factorylab-sbx-") as directory,
0196         tempfile.TemporaryFile() as stdout,
0197         tempfile.TemporaryFile() as stderr,
0198         tempfile.TemporaryFile() as seccomp,
0199     ):
0200         work = Path(directory).resolve()
0201         (work / "main.py").write_text(code, encoding="utf-8")
0202         # Set NPROC after the jail has created its init/interpreter processes.
0203         # These hard limits cannot be raised by population code. Linux seccomp
0204         # enforces the process ban even where RLIMIT_NPROC exempts uid 0.
0205         # Darwin returns EINVAL for any RLIMIT_AS value (the shared cache alone
0206         # exceeds what a tool may map), so the development jail sets no memory
0207         # rlimit; Linux, the deployed jail, bounds the address space.
0208         address_space = (
0209             "" if sys.platform == "darwin"
0210             else "resource.setrlimit(resource.RLIMIT_AS, (536870912, 536870912))\n"
0211         )
0212         (work / "runner.py").write_text(
0213             "import resource, runpy\n"
0214             f"resource.setrlimit(resource.RLIMIT_CPU, ({cpu_s}, {cpu_s}))\n"
0215             f"{address_space}"
0216             "resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))\n"
0217             "resource.setrlimit(resource.RLIMIT_FSIZE, (1048576, 1048576))\n"
0218             "resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))\n"
0219             "resource.setrlimit(resource.RLIMIT_CORE, (0, 0))\n"
0220             "runpy.run_path('main.py', run_name='__main__')\n",
0221             encoding="utf-8",
0222         )
0223         if sys.platform == "linux":
```

## 5. Existing venue surface

### `factorylab/world/venue_tools.py`, lines 124–265

SHA-256: `d757c660972164a31ec57db303d66893742c2970df09f0b626772dd225bcd485`

```text
0124 
0125     def __init__(self, exchange: Exchange, *, coins: tuple[str, ...], max_leverage: int = 3,
0126                  spot_pairs: tuple[str, ...] = ()):
0127         if type(max_leverage) is not int or max_leverage < 1:
0128             raise ValueError("max_leverage must be a positive integer")
0129         if not coins or any(not isinstance(coin, str) or not coin for coin in coins):
0130             raise ValueError("coins must contain nonempty coin names")
0131         self.exchange = exchange
0132         self.coins, self.spot_pairs = tuple(coins), tuple(spot_pairs)
0133         public = list((*coins, *spot_pairs))
0134         for name in ("coins", "spot_pairs", "listed_coins", "listed_spot_pairs", "_listed_coins"):
0135             values = getattr(exchange, name, ())
0136             if isinstance(values, (list, tuple)):
0137                 public.extend(values)
0138         spot_names = getattr(exchange, "_spot_names", {})
0139         if isinstance(spot_names, dict):
0140             public.extend(spot_names)
0141         self.public_coins = tuple(dict.fromkeys(public))
0142         self.log: list[tuple[str, dict, bool]] = []
0143         coin = {"type": "string", "enum": list(dict.fromkeys((*coins, *spot_pairs)))}
0144         positive = {
0145             "anyOf": [
0146                 {"type": "number", "exclusiveMinimum": 0},
0147                 {
0148                     "type": "string",
0149                     "pattern": r"^(?=[0-9.]*[1-9])(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)"
0150                     r"(?:[eE][+-]?[0-9]+)?$",
0151                 },
0152             ]
0153         }
0154         market = {"type": "string", "enum": ["perp", "spot"], "default": "perp"}
0155         trade = {
0156             "market": market,
0157             "coin": coin,
0158             "side": {"type": "string", "enum": ["buy", "sell"]},
0159             "size": positive,
0160             "reduce_only": {"type": "boolean", "default": False},
0161         }
0162         definitions = [
0163             (
0164                 "candles",
0165                 "Recent OHLCV candles, oldest first; timestamps in nanoseconds.",
0166                 {
0167                     "coin": coin,
0168                     "interval": {"type": "string", "enum": ["1m", "5m", "15m", "1h"]},
0169                     "n": {"type": "integer", "minimum": 1, "maximum": 200},
0170                 },
0171                 ["coin", "interval", "n"],
0172             ),
0173             (
0174                 "order_book",
0175                 "Best-first bid and ask price/size levels.",
0176                 {"coin": coin, "depth": {"type": "integer", "minimum": 1, "maximum": 20}},
0177                 ["coin", "depth"],
0178             ),
0179             (
0180                 "funding_history",
0181                 "Recent funding rates, oldest first; timestamps in nanoseconds.",
0182                 {"coin": coin, "n": {"type": "integer", "minimum": 1, "maximum": 100}},
0183                 ["coin", "n"],
0184             ),
0185             ("open_orders", "Currently resting orders for the account.", {}, []),
0186             ("positions", "Open signed positions and entry prices for the account.", {}, []),
0187             (
0188                 "place_market",
0189                 "Place a market buy or sell; optionally reduce only.",
0190                 trade,
0191                 ["coin", "side", "size"],
0192             ),
0193             (
0194                 "place_limit",
0195                 "Place a good-until-cancelled limit order; optionally reduce only.",
0196                 {**trade, "price": positive},
0197                 ["coin", "side", "size", "price"],
0198             ),
0199             (
0200                 "cancel",
0201                 "Cancel a resting order on its coin.",
0202                 {"coin": coin, "order_id": {"type": "string", "minLength": 1}},
0203                 ["coin", "order_id"],
0204             ),
0205             (
0206                 "close",
0207                 "Reduce a position by size, or close it fully when size is omitted or null.",
0208                 {
0209                     "coin": coin,
0210                     "market": market,
0211                     "size": {"anyOf": [*positive["anyOf"], {"type": "null"}], "default": None},
0212                 },
0213                 ["coin"],
0214             ),
0215             (
0216                 "set_leverage",
0217                 "Set cross-margin leverage for a coin.",
0218                 {
0219                     "coin": coin,
0220                     "market": market,
0221                     "leverage": {"type": "integer", "minimum": 1, "maximum": max_leverage},
0222                 },
0223                 ["coin", "leverage"],
0224             ),
0225         ]
0226         self._specs = {
0227             f"venue.{name}": ToolSpec(
0228                 f"venue.{name}",
0229                 description,
0230                 deepcopy(
0231                     {
0232                         "type": "object",
0233                         "properties": properties,
0234                         "required": required,
0235                         "additionalProperties": False,
0236                     }
0237                 ),
0238                 0,
0239             )
0240             for name, description, properties, required in definitions
0241         }
0242         # ``instruments`` is where the venue's whole listing lives. The world block
0243         # carries only the trading markets' records, so this description is what
0244         # tells an assembly the rest of the listing is one call away.
0245         listings = {
0246             "instruments": "Every market the venue lists, with its lot size, tick size and "
0247                            "minimum order value. world.venue carries these records for the "
0248                            "world's trading_markets only; this read returns the full listing.",
0249             "mids": "Public venue mids for all listed markets.",
0250             "funding": "Public venue funding for all listed markets.",
0251         }
0252         for name, description in listings.items():
0253             self._specs[f"venue.{name}"] = ToolSpec(
0254                 f"venue.{name}", description,
0255                 {"type": "object", "properties": {}, "required": [],
0256                  "additionalProperties": False}, 0)
0257         # Read identities are checked against the venue at dispatch, not the seed.
0258         for name in ("candles", "order_book", "funding_history"):
0259             self._specs[f"venue.{name}"].args_schema["properties"]["coin"] = {
0260                 "type": "string", "enum": list(self.public_coins)}
0261 
0262     def admit_market(self, coin: str, market: str) -> None:
0263         """A validated registration expands only the world's trading schemas."""
0264         attr = "spot_pairs" if market == "spot" else "coins"
0265         setattr(self, attr, tuple(dict.fromkeys((*getattr(self, attr), coin))))
```

## 6. Connectors and existing search support

### `factorylab/world/connector.py`, lines 1–63

SHA-256: `7cd48e1dfb7a3120a819268a402f9ec7b724c0c42ea59ef9b874a113a6797672`

```text
0001 """Credential-free, bounded HTTPS reads outside the population jail."""
0002 
0003 from __future__ import annotations
0004 
0005 import http.client
0006 import io
0007 import ipaddress
0008 import json
0009 import re
0010 import socket
0011 import ssl
0012 import subprocess
0013 import sys
0014 import time
0015 from dataclasses import dataclass, field
0016 from urllib.parse import urlsplit
0017 
0018 from hyperliquid.utils.constants import MAINNET_API_URL, TESTNET_API_URL
0019 
0020 from factorylab.world.evm import BASE, BASE_SEPOLIA, HYPEREVM, HYPEREVM_TESTNET
0021 from factorylab.world.market import DISCOVERY_URL
0022 from factorylab.world.x402 import VENICE_URL
0023 
0024 
0025 class ConnectorRefused(ValueError):
0026     """A bounded public refusal contains no remote body or host exception text."""
0027 
0028 
0029 def url_host(url: str) -> str | None:
0030     """Return the lowercase host of a rail or seller URL, or None when it names none."""
0031     try:
0032         host = urlsplit(url).hostname
0033     except ValueError:
0034         return None
0035     return host.lower() if host else None
0036 
0037 
0038 # Every endpoint this world's own rails talk to, taken from the modules that
0039 # define them, so a renamed or added rail cannot silently become fetchable.
0040 RAIL_URLS = (
0041     MAINNET_API_URL, TESTNET_API_URL,                                # world/exchange.py venue
0042     HYPEREVM.rpc, HYPEREVM_TESTNET.rpc, BASE.rpc, BASE_SEPOLIA.rpc,  # world/evm.py RPC
0043     "https://openrouter.ai/api/v1",                                  # world/openrouter.py
0044     "https://api.anthropic.com",                                     # world/models.py
0045     VENICE_URL,                                                      # world/x402.py
0046     DISCOVERY_URL,                                                   # world/market.py index
0047 )
0048 
0049 DEFAULT_DENYLIST = tuple(dict.fromkeys(h for h in map(url_host, RAIL_URLS) if h))
0050 
0051 
0052 def origin_host(origin: str) -> str:
0053     """Accept only a canonical HTTPS origin with a hostname and no other URL components."""
0054     if not isinstance(origin, str) or len(origin) > 260:
0055         raise ConnectorRefused("origin must be https://<host>")
0056     parts = urlsplit(origin)
0057     host = parts.hostname
0058     if (not host or parts.scheme != "https" or origin != f"https://{host}"
0059             or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host)
0060             or any(not label or len(label) > 63 or label.startswith("-") or label.endswith("-")
0061                    for label in host.split("."))):
0062         raise ConnectorRefused("origin must be https://<host> without credentials, port or path")
0063     return host
```

### `factorylab/world/openrouter.py`, lines 118–171

SHA-256: `8f58295ee0204dc9c218a6a442929a5b42be9950190d143f637158a30db86f8d`

```text
0118                     raise OpenRouterError(
0119                         None, "Connection failed", sent=dispatched(exc)
0120                     ) from None
0121             except Exception as exc:
0122                 # Arbitrary transport/decoder exceptions may contain request headers.
0123                 raise OpenRouterError(
0124                     None, "Transport or response decoding failed", sent=dispatched(exc)
0125                 ) from None
0126         raise AssertionError("unreachable")
0127 
0128     def complete(self, req: ModelRequest) -> ModelResponse:
0129         """Return vendor usage and an upward-rounded reported cost after one POST."""
0130         payload: dict[str, Any] = {
0131             "model": req.model_id,
0132             "messages": [{"role": "system", "content": req.system}, *req.messages],
0133             "max_tokens": req.max_tokens,
0134         }
0135         # "<id>@<effort>" selects a reasoning level as its own capability; ":online" adds web.
0136         wire_id, effort_override = req.model_id, None
0137         if "@" in wire_id:
0138             wire_id, effort_override = wire_id.rsplit("@", 1)
0139         payload["model"] = wire_id
0140         base_id = wire_id[:-7] if wire_id.endswith(":online") else wire_id
0141         extra = next((self._extra_body[k] for k in (req.model_id, wire_id, base_id)
0142                       if k in self._extra_body), None)
0143         if extra is not None:
0144             payload.update(deepcopy(extra))
0145         if req.json_object:
0146             # The contract is applied after the manifest's extra body, so an extra body
0147             # can never turn a structured request into free text. A response_format is
0148             # only honoured by hosts that support it: route to those alone, keeping the
0149             # manifest's own routing preferences (``provider.order``, say) and letting
0150             # its own keys win on conflict.
0151             payload["response_format"] = {"type": "json_object"}
0152             routing = dict(payload.get("provider") or {})
0153             routing.setdefault("require_parameters", True)
0154             payload["provider"] = routing
0155         if req.model_id in self._web_config or wire_id in self._web_config:
0156             payload["plugins"] = [
0157                 {
0158                     "id": "web",
0159                     **self._web_config.get(req.model_id, self._web_config.get(wire_id, {})),
0160                 }
0161             ]
0162         if effort_override is not None:
0163             payload["reasoning"] = {"effort": effort_override}
0164         elif base_id in self._reasoning_config:
0165             payload["reasoning"] = dict(self._reasoning_config[base_id])
0166         elif req.effort in {"low", "medium", "high"} and base_id in self._reasoning_models:
0167             payload["reasoning"] = {"effort": req.effort}
0168         wire = parse_completion(
0169             self._request("POST", "/chat/completions", payload), error=OpenRouterError
0170         )
0171         cost = wire.usage.get("cost")
```

## 7. Artifact access versus private learning state

### `factorylab/runtime/compute.py`, lines 604–634

SHA-256: `997a3aea6bd907d670fd4ebedf04b1d881e84606078bd0da6ae5f50c9346ef3c`

```text
0604     def _allowed_tools(self, action_id: str) -> set[str]:
0605         """Every registered tool is a public primitive; schematics are public."""
0606         return set(self.tool_specs)
0607 
0608     def _run_tool(self, action_id: str, handle: str, call: dict[str, Any], *,
0609                   slot: str = "tool:0") -> tuple[dict, int]:
0610         """Execute one tool call through metering. Returns (result, cost)."""
0611         self._ensure_connector_tool()
0612         tool_id = str(call.get("tool"))
0613         args = call.get("args") if isinstance(call.get("args"), dict) else {}
0614         if tool_id not in self.tool_specs or tool_id not in self._allowed_tools(action_id):
0615             return {"error": "unknown or disallowed tool"}, 0
0616         if tool_id == "connector.fetch":
0617             return self._fetch_connector(action_id, handle, args)
0618         if tool_id in ("note.put", "note.get"):
0619             from factorylab.runtime.notes import run
0620 
0621             return run(self, action_id, handle, tool_id, args)
0622         if tool_id == "artifact.get":
0623             # Free by contract (C9): any seat reads any artifact; the read is ledgered.
0624             result = self.artifacts.read(args.get("sha"))
0625             self.ledger.append({"kind": "artifact.get", "sha": str(args.get("sha"))[:64],
0626                                 "handle": handle, "assembly_id": action_id,
0627                                 "found": "error" not in result, "ts": self.clock.now_ns})
0628             return result, 0
0629         if tool_id in self.CONSEQUENCE_WRITES and not self._may_write(handle):
0630             # No judge trades what it judges (essay II.III): the refusal is public.
0631             self.ledger.append({"kind": "tool.refused", "handle": handle,
0632                                 "assembly_id": action_id, "tool": tool_id,
0633                                 "reason": self.WRITE_REFUSAL, "ts": self.clock.now_ns})
0634             return {"error": self.WRITE_REFUSAL}, 0
```

### `factorylab/kernel/artifacts.py`, lines 63–77

SHA-256: `2922f02ed525b60bfa669cc6427be5c0e0daa91ecb52f2750799ed20b1e66516`

```text
0063     def put(self, data: bytes, *, owner: str, kind: str) -> str:
0064         """Archive ``data`` for ``owner`` and return its hash; the record precedes the bytes."""
0065         if not isinstance(data, (bytes, bytearray)):
0066             raise TypeError("artifact data must be bytes")
0067         if not isinstance(owner, str) or not owner or not isinstance(kind, str) or not kind:
0068             raise ArtifactError("artifact owner and kind are required")
0069         data = bytes(data)
0070         sha = hashlib.sha256(data).hexdigest()
0071         ts = self.clock()
0072         self.ledger.append({"kind": "artifact.put", "sha": sha, "owner": owner,
0073                             "artifact_kind": kind, "bytes": len(data), "ts": ts})
0074         # The first record of a hash stands: a second owner of identical bytes is a
0075         # reader of the first's artifact, not a new liability for the same file.
0076         self.index.setdefault(sha, {"owner": owner, "kind": kind, "bytes": len(data), "ts": ts})
0077         self._write(sha, data)
```

### `factorylab/kernel/artifacts.py`, lines 110–125

SHA-256: `2922f02ed525b60bfa669cc6427be5c0e0daa91ecb52f2750799ed20b1e66516`

```text
0110     def read(self, sha: Any) -> dict[str, Any]:
0111         """The ``artifact.get`` view: metadata and inline content, or a bounded error."""
0112         try:
0113             sha = _valid_sha(sha)
0114             data = self.get(sha)
0115         except ArtifactError as exc:
0116             return {"error": str(exc)}
0117         record = self.index.get(sha, {})
0118         view = {"sha": sha, "owner": record.get("owner"), "kind": record.get("kind"),
0119                 "bytes": len(data)}
0120         if len(data) > MAX_TOOL_READ_BYTES:
0121             return {**view, "error": f"artifact exceeds {MAX_TOOL_READ_BYTES} bytes"}
0122         try:
0123             return {**view, "text": data.decode("utf-8")}
0124         except UnicodeDecodeError:
0125             return {**view, "base64": base64.b64encode(data).decode("ascii")}
```

### `factorylab/cortex/assembly.py`, lines 342–375

SHA-256: `037a27387ce0828dbd5516ce05ca6942950b8086917bd85c8d6ab3e07ffbbadf`

```text
0342             stdin = self.build_stdin(req, state)
0343         except (TypeError, ValueError) as exc:
0344             return Return(req.handle, {"reason": type(exc).__name__}, 0, "failed")
0345         code, timeout_s = self.spec.code, self.spec.timeout_s
0346 
0347         def execute() -> dict:
0348             return self.runner.run(code, stdin=stdin, timeout_s=timeout_s)
0349 
0350         try:
0351             metered = self.meter.run(
0352                 handle=req.handle, reason=f"model:{PROGRAM_MODEL_ID}", ceiling=self.price,
0353                 execute=execute, cost_of=lambda _r: self.price,
0354             )
0355         except Infeasible as exc:
0356             return Return(req.handle, {"reason": f"infeasible: {exc}"}, 0, "failed")
0357         except BillingUncertain as exc:
0358             return Return(req.handle, {"reason": str(exc)}, exc.cost, "failed")
0359         except Exception as exc:
0360             return Return(req.handle, {"reason": type(exc).__name__}, 0, "failed")
0361         cost = metered.cost
0362         result = metered.result if isinstance(metered.result, dict) else {}
0363         state_in = self.state_sha
0364         ret = self._interpret(req, result, cost, state_error)
0365         if self.record is not None:
0366             self.record({
0367                 "kind": "program.call", "assembly_id": self.spec.id, "handle": req.handle,
0368                 "status": ret.status, "cost": cost, "state_in": state_in,
0369                 "state_out": self.state_sha,
0370             })
0371         return ret
0372 
0373     def _interpret(self, req: Request, result: dict, cost: int,
0374                    state_error: str | None) -> Return:
0375         provider = {"finish_reason": "stop", "input_tokens": None, "output_tokens": None,
```

## 8. Existing composability and reward-shape boundary

### `factorylab/cortex/registration.py`, lines 1–130

SHA-256: `bf3ec5de421188d5f9d91922b1401c9fb0c987184c820a37e9d17a7895d19d79`

```text
0001 """Population registration proposals.
0002 
0003 An assembly's return may carry a ``register`` list. This module turns that
0004 raw JSON into typed proposals and rejects malformed ones with a deterministic
0005 reason. It decides nothing about money or physics: the runtime pays the
0006 novelty trial, the registry enforces versions and provenance. Rejections here
0007 are about shape, not merit.
0008 """
0009 
0010 from __future__ import annotations
0011 
0012 import math
0013 import re
0014 from collections.abc import Mapping
0015 from dataclasses import dataclass, field
0016 from types import MappingProxyType
0017 from typing import Any
0018 
0019 from factorylab.cortex.sandbox import jail_available
0020 
0021 SLUG = re.compile(r"^[a-z][a-z0-9-]{1,47}$")
0022 MAX_PROMPT_CHARS = 4000
0023 MAX_PROPOSALS_PER_RETURN = 3
0024 LEARNERS = ("exp3", "blum_mansour")
0025 ROLES = ("producer", "evaluator", "meta", "antagonist")
0026 # An assembly's declared action set is its own; the kernel bounds only its size.
0027 MAX_DECLARED_ACTIONS = 32
0028 MAX_ACTION_ID_CHARS = 64
0029 
0030 
0031 def seed_emits(role: str) -> tuple[str, ...]:
0032     """Expand a legacy seed label into an ordinary, replaceable output contract."""
0033     return {"producer": ("ProducerReturn",), "evaluator": ("Verdict",),
0034             "meta": ("MetaVerdict",), "antagonist": ("Exposure",)}.get(
0035                 role, ("ProducerReturn",))
0036 
0037 
0038 CONTRACT_ROLES = MappingProxyType({
0039     "ProducerReturn": "producer", "Verdict": "evaluator",
0040     "MetaVerdict": "meta", "Exposure": "antagonist",
0041 })
0042 REWARD_SHAPES = ("judged", "forecast", "conformity", "exposure")
0043 SEED_REWARD_SHAPES = MappingProxyType({
0044     "ProducerReturn": "judged", "Verdict": "forecast",
0045     "MetaVerdict": "conformity", "Exposure": "exposure",
0046 })
0047 
0048 
0049 def measured_role(emits: str | tuple[str, ...] | None) -> str:
0050     """Name the measurement scope of an emitted contract, never of a free-form label.
0051 
0052     A registration's ``role`` is a display name; what a return is measured
0053     against follows the kind it emits. Seed role names remain aliases for their
0054     seed kinds; population kinds retain their exact, case-sensitive names. A
0055     contract with several declared kinds uses the first until the return selects.
0056     """
0057     kinds = (emits,) if isinstance(emits, str) else tuple(emits or ())
0058     return CONTRACT_ROLES.get(kinds[0], kinds[0]) if kinds else "producer"
0059 
0060 
0061 BUILTIN_RETURNS = frozenset({"ProducerReturn", "Verdict", "MetaVerdict", "Exposure"})
0062 # A metric card's accountability scope is either a role alias, ``all``, or an
0063 # emitted kind. These spellings name populations, so no emitted kind may take one
0064 # in any case: a kind and the scope that measures it must never be the same name.
0065 RESERVED_SCOPES = frozenset({*ROLES, "all"})
0066 
0067 
0068 def event_name(value: Any) -> str:
0069     """An event kind is a nonempty name, independent of any role label."""
0070     if (not isinstance(value, str) or not value
0071             or any(c.isspace() or not c.isprintable() for c in value)):
0072         raise ValueError("event kind must be a nonempty name without whitespace")
0073     return value
0074 
0075 
0076 def reward_contracts(
0077     emits: tuple[str, ...], declared: Any = None, *,
0078     registered: Mapping[str, str] | None = None,
0079 ) -> dict[str, str]:
0080     """Each emitted kind has one of four reward shapes; seed meanings remain fixed."""
0081     if declared is None:
0082         declared = {}
0083     if not isinstance(declared, Mapping) or any(k not in emits for k in declared):
0084         raise ValueError("reward_shapes must map declared emits kinds to reward shapes")
0085     result = {}
0086     for kind in emits:
0087         existing = SEED_REWARD_SHAPES.get(kind, (registered or {}).get(kind))
0088         shape = declared.get(kind, existing or "judged")
0089         if not isinstance(shape, str) or shape not in REWARD_SHAPES:
0090             raise ValueError("reward shape must be judged, forecast, conformity or exposure")
0091         if kind in SEED_REWARD_SHAPES and shape != SEED_REWARD_SHAPES[kind]:
0092             raise ValueError("built-in reward shapes cannot be replaced")
0093         if existing is not None and shape != existing:
0094             raise ValueError(f"reward shape already declared differently: {kind}")
0095         result[kind] = shape
0096     return result
0097 
0098 
0099 def output_contracts(emits: Any, schemas: Any) -> tuple[tuple[str, ...], dict[str, dict]]:
0100     """Custom return kinds require executable schemas; built-in meanings cannot be replaced."""
0101     from factorylab.cortex.assembly import _schema_definition
0102     from factorylab.kernel.events import EventKind
0103 
0104     if not isinstance(emits, (list, tuple)) or not emits:
0105         raise ValueError("emits must be a non-empty list of return kinds")
0106     kinds = tuple(dict.fromkeys(event_name(k) for k in emits))
0107     if not isinstance(schemas, dict) or any(k not in kinds for k in schemas):
0108         raise ValueError("schemas must map declared emits kinds to outcome schemas")
0109     custom = {}
0110     for kind in kinds:
0111         if kind.lower() in RESERVED_SCOPES:
0112             raise ValueError("a return kind cannot take a reserved measurement scope name")
0113         if kind in BUILTIN_RETURNS:
0114             if kind in schemas:
0115                 raise ValueError("built-in return schemas cannot be replaced")
0116             continue
0117         if kind in {str(k) for k in EventKind}:
0118             raise ValueError("a population return cannot impersonate a world or kernel event")
0119         schema = schemas.get(kind)
0120         _schema_definition(schema)
0121         if schema.get("type") != "object":
0122             raise ValueError("a population return schema must have type object")
0123         custom[kind] = schema
0124     return kinds, custom
0125 
0126 
0127 @dataclass(frozen=True)
0128 class ModelProposal:
0129     openrouter_id: str
0130 
```

### `factorylab/cortex/registration.py`, lines 132–216

SHA-256: `bf3ec5de421188d5f9d91922b1401c9fb0c987184c820a37e9d17a7895d19d79`

```text
0132 @dataclass(frozen=True)
0133 class AssemblyProposal:
0134     id: str
0135     role: str
0136     model_id: str
0137     system_prompt: str
0138     accepts: tuple[str, ...]
0139     max_tokens: int
0140     effort: str
0141     emits: tuple[str, ...] = ()
0142     schemas: dict[str, dict] = field(default_factory=dict)
0143     reward_shapes: dict[str, str] = field(default_factory=dict)
0144     # A program seat (``model_id == "program"``): its jailed code, wall timeout and
0145     # whether it keeps private state between calls. Empty for a model seat.
0146     code: str = ""
0147     timeout_s: int = 10
0148     state_policy: str = "none"
0149 
0150     def __post_init__(self) -> None:
0151         """``reward_shapes`` holds the resolved contract for the kinds this proposal emits.
0152 
0153         The field is resolved, not declared: construction fills an undeclared kind
0154         from its seed shape or ``judged``, so a proposal derived from another by
0155         ``replace`` arrives carrying the earlier proposal's kinds. Resolution is
0156         against this proposal's own emitted kinds; a declaration naming a kind the
0157         proposal does not emit is refused where the population declares it.
0158         """
0159         kinds = self.emits or seed_emits(self.role)
0160         declared = self.reward_shapes
0161         if isinstance(declared, Mapping):
0162             declared = {k: v for k, v in declared.items() if k in kinds}
0163         object.__setattr__(self, "reward_shapes", reward_contracts(kinds, declared))
0164 
0165 
0166 @dataclass(frozen=True)
0167 class RetireProposal:
0168     assembly_id: str
0169 
0170 
0171 @dataclass(frozen=True)
0172 class RouterProposal:
0173     event_kind: str
0174     learner: str
0175     gamma: float
0176     add: bool = False  # True: add another router for the kind instead of replacing
0177 
0178 
0179 @dataclass(frozen=True)
0180 class ToolProposal:
0181     id: str
0182     description: str
0183     args_schema: dict
0184     code: str
0185     timeout_s: int
0186 
0187 
0188 @dataclass(frozen=True)
0189 class ObservationProposal:
0190     """A measurement the population writes, priced like any other card input."""
0191 
0192     id: str
0193     description: str
0194     unit: str
0195     unit_range: tuple[float, float]
0196     code: str
0197 
0198 
0199 @dataclass(frozen=True)
0200 class PredicateProposal:
0201     """A population predicate defines a boolean resolution over public facts."""
0202 
0203     id: str
0204     description: str
0205     code: str
0206 
0207 
0208 @dataclass(frozen=True)
0209 class LearnerProposal:
0210     """A learner over an assembly's own declared action set."""
0211 
0212     assembly_id: str
0213     learner: str
0214     actions: tuple[str, ...]
0215     gamma: float
0216 
```

## 9. Existing temporal protections and novelty allowance

### `worlds/edition2-rehearsal-3.toml`, lines 202–250

SHA-256: `72eed0707659e00957d3d5c9d3f9abd2e1dd3e67a7c4526a8fa4f2524c7e0e29`

```text
0202 [novelty]
0203 trials = 3
0204 max_lifetime_windows = 6
0205 share = 0.1
0206 # One hour, never the compute-continuity two-minute window: the reviewer's rent trap
0207 # (docs/audits/v4/gpt6-triage.md) is 720 windows a day, $47 a day at 64 KiB of notes.
0208 window = "1h"
0209 
0210 # Contract C3: storage rent by byte-time. "0.04" is the loader default (runtime/notes.py),
0211 # stated here so the choice is visible: the whole 256 KiB cap costs 10,485 micro-USD, about a
0212 # cent, a day. At the default the key leaves the manifest hash unchanged.
0213 [notes]
0214 micro_per_byte_day = "0.04"
0215 
0216 [timing]
0217 cadence_sample = 200
0218 min_ratio = 3
0219 jitter_fraction = 0.2
0220 
0221 [evaluation]
0222 # Sixty events: at six world events per tick that is ten ticks, about 100 minutes at
0223 # a ten-minute tick, before an unjudged consequence is closed (docs/launch-decisions.md).
0224 consequence_backstop_events = 60
0225 consequence_share = 0.3
0226 max_forecasts_per_verdict = 2
0227 verdict_timeout_events = 20
0228 min_coverage = 0.5
0229 # The novelty trial is a child's whole endowment once its protected trial calls are spent
0230 # (C10, the W6 note): the proposer's entitlement moves it to the child, and the child then
0231 # funds its career from it. Sized from the meter's reservation ceiling of the cheapest seat
0232 # on this menu, eval-b on qwen/qwen3.7-flash ($0.03 in, $0.13 out per MTok, max_tokens 3000).
0233 # The ceiling Assembly.invoke reserves is price.cost(int(chars * 1.5) + 64, max_tokens) with
0234 # chars = 419 (seed system prompt) + the rendered request (world/metering.py, input_slack 1.5):
0235 #   output part            3000 * 0.13                       =   390 micro-USD
0236 #   mean request, ~50,000 chars (20,636 mean input tokens in docs/audits/v3/rehearsal-final.md
0237 #     at ~2.4 chars a token): est_in 75,692 * 0.03 = 2,271;   ceiling 2,661; three calls  7,983
0238 #   largest request, ~120,000 chars (52,832 max input tokens in docs/audits/v3/accelerated.md,
0239 #     the 28,127 micro-USD antagonist reservation): 180,692 * 0.03 = 5,421; ceiling 5,811;
0240 #     three calls 17,433
0241 # 0.05 USD = 50,000 micro-USD covers three cheapest-seat calls at the largest measured request
0242 # 2.9 times over, four calls of a GLM-tier seat at the mean request (11,854 to 12,104 each),
0243 # and is 1.9% of a seat's 2,666,666 genesis share, an eighth of its $0.39 rehearsal day.
0244 # The old "0.10" was a fee set before the trial became an endowment; it is not needed.
0245 trial_amount_usd = "0.05"
0246 forecast_horizon_events = 10
0247 adversarial_share = 0.15
0248 sibling_share = 0.5
0249 sampling_step = 0.1
0250 sampling_cap = 0.7
```

### `factorylab/runtime/cascade.py`, lines 1–75

SHA-256: `d47b4517474ff7a4a7e91a76e13beb2537fdd707e42458a20761848171a332a0`

```text
0001 """Pure, immutable windows for progressively slower evaluatory tiers."""
0002 
0003 from __future__ import annotations
0004 
0005 from dataclasses import dataclass, replace
0006 from math import ceil, isfinite
0007 from statistics import fmean
0008 
0009 from factorylab.kernel.events import Event, EventKind
0010 
0011 
0012 def release_threshold(min_ratio: int, jitter_fraction: float, draw: float) -> int:
0013     """Preserve the minimum separation, with bounded upward jitter from a supplied draw."""
0014     if type(min_ratio) is not int or min_ratio < 3:
0015         raise ValueError("cascade min_ratio must be an integer >= 3")
0016     if not isfinite(jitter_fraction) or jitter_fraction < 0:
0017         raise ValueError("jitter_fraction must be finite and nonnegative")
0018     if not isfinite(draw) or not 0 <= draw < 1:
0019         raise ValueError("draw must be in [0, 1)")
0020     jitter = ceil(min_ratio * jitter_fraction)
0021     return min_ratio + int(draw * (jitter + 1))
0022 
0023 
0024 def event_tier(event: Event) -> int:
0025     """Only judgement events enter the cascade; producer judgements occupy tier one.
0026 
0027     A judgement of a producer return is the seed ``Verdict`` and occupies tier
0028     one. Every higher arrival — the seed ``MetaVerdict`` or a population kind
0029     whose declared reward shape is ``conformity`` — states the tier it judges in
0030     its own payload, so a window is separated by declared position rather than
0031     by a fixed pair of kind names. Which kinds are admitted at all is the
0032     caller's reward-shape decision; anything else has no tier here.
0033     """
0034     if event.kind is EventKind.VERDICT:
0035         return 1
0036     tier = event.payload.get("tier")
0037     if type(tier) is not int or tier < 2:
0038         raise ValueError("cascade arrivals must be Verdict or conformity judgements")
0039     return tier
0040 
0041 
0042 @dataclass(frozen=True)
0043 class CascadeGate:
0044     """An arrival returns new state; no window releases early or crosses evaluatory tiers."""
0045 
0046     threshold: int
0047     arrivals: tuple[Event, ...] = ()
0048 
0049     def __post_init__(self) -> None:
0050         if type(self.threshold) is not int or self.threshold < 3:
0051             raise ValueError("threshold must be an integer >= 3")
0052         object.__setattr__(self, "arrivals", tuple(self.arrivals))
0053         if len(self.arrivals) >= self.threshold:
0054             raise ValueError("a full window must already have been released")
0055         if len({event_tier(e) for e in self.arrivals}) > 1:
0056             raise ValueError("a gate cannot mix tiers")
0057 
0058     def add(self, event: Event) -> tuple[CascadeGate | None, Event | None]:
0059         """Release only the latest event, enriched with the complete disjoint arrival window."""
0060         tier = event_tier(event)
0061         if self.arrivals and event_tier(self.arrivals[0]) != tier:
0062             raise ValueError("a gate cannot mix tiers")
0063         arrivals = (*self.arrivals, event)
0064         if len(arrivals) < self.threshold:
0065             return replace(self, arrivals=arrivals), None
0066         scores = [e.payload["verdict" if tier == 1 else "score"] for e in arrivals]
0067         handles = [e.payload["evaluator_handle" if tier == 1 else "by"] for e in arrivals]
0068         window = {
0069             "count": len(arrivals),
0070             "mean": fmean(scores),
0071             "min": min(scores),
0072             "max": max(scores),
0073             "handles": handles,
0074         }
0075         return None, replace(event, payload={**event.payload, "window": window})
```

