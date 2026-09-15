# Factory Lab: deeper rearchitecture evidence

Source: the user-supplied FactoryLab-architect-9057093.zip, extracted as FactoryLab-9057093.

This is a static architectural reading, not a claim that the snapshot is the current remote repository or that a full runtime test suite passed. No source was modified, no paid inference was invoked, no venue action was performed, and no key file was read. All code below is quoted with its original file line numbers. Proposed changes in the accompanying answer are not existing behavior.

Chapter II of Superdark Factory.md was read in full separately through the Files reader; its four sections are The Primitive, Versioning, Evaluations, and The Charter and the Loop. File citations in the answer point to that reading.

## 1. Norm semantics are in comments; the stored charter carries labels

The launch manifest writes its substantive normative clauses as TOML comments. The loader retains the norms list, and Charter.render prints those strings. This is a static-path finding; a separate custom prompt could add prose, but the charter object does not preserve these definitions.

### `worlds/edition2-rehearsal-3.toml`: 312-363

SHA-256: `72eed0707659e00957d3d5c9d3f9abd2e1dd3e67a7c4526a8fa4f2524c7e0e29`

```text
0312  #   edition; the population writes and prices the cards under them. Bounded reciprocity has no
0313  #   card at genesis; the population may propose one.
0314  [charter]
0315  edition = 1
0316  # Ratified 15 September 2026 (second ballot: five norms, three cards, observer on DeepSeek 4.1
0317  # flash) by the seeded five-seat committee of this roster; docs/charter/edition2-ratification.json.
0318  ratified_sha256 = "a7f105eefd65ac70904b04b3389840841e6751b18bde3c5cc2262622b2b6be39"
0319  roster_sha256 = "b68be19c7bedf5b31daafa4e85d3d32ded6540ab4996d7a7eec50a5b4fca4dbb"
0320  # consequential usefulness: create things or changes that others have reason to value; uptake by
0321  #   an independent counterparty is evidence, internal applause is a hypothesis.
0322  # epistemic integrity: make commitments answerable to evidence and preserve the ability to
0323  #   discover that they were wrong; a changed criterion does not rewrite what was promised.
0324  # durable agency: steward the resources and capabilities that make future worthwhile choices
0325  #   possible; spending for an enduring capability can be good stewardship, maintaining a dead
0326  #   institution is not.
0327  # bounded reciprocity: do not finance the factory's advantage by imposing unconsented costs on
0328  #   outsiders.
0329  # fidelity: a measurement stands for a value; satisfying the measurement without serving the
0330  #   value is failure, and saying so is a judge's duty.
0331  norms = ["consequential usefulness", "epistemic integrity", "durable agency", "bounded reciprocity", "fidelity"]
0332  
0333  [[charter.cards]]
0334  id = "card-consequence-paid-off"
0335  norm = "consequential usefulness"
0336  description = "Share of settled consequences whose return paid off, with a floor, so inquiry is valued for consequences reached rather than questions raised."
0337  units = "fraction"
0338  window = { kind = "windows", n = 6 }
0339  acceptable_region = "at least 0.4"
0340  observation = "consequence_paid_off_rate"
0341  answers_for = "producer"
0342  lambda = 0.5
0343  
0344  [[charter.cards]]
0345  id = "card-forecast-skill"
0346  norm = "epistemic integrity"
0347  description = "Mean forecast Brier minus the paired prevalence baseline, with a floor above zero, so inquiry is judged by whether it beats guessing."
0348  units = "score difference"
0349  window = { kind = "forecasts", n = 25, per = "assembly" }
0350  acceptable_region = "above 0"
0351  observation = "forecast_skill"
0352  answers_for = "evaluator"
0353  lambda = 0.5
0354  
0355  [[charter.cards]]
0356  id = "censorship-bound"
0357  norm = "epistemic integrity"
0358  description = "Share of resolved outcomes that are censored."
0359  units = "fraction"
0360  window = { kind = "windows", n = 5 }
0361  acceptable_region = "at most 0.3"
0362  observation = "censored_share"
0363  answers_for = "all"
```

### `factorylab/runtime/worlds.py`: 631-665

SHA-256: `e11696ae9cdc9b322808897fd42ab81ce4822e282d14e3282d57fd5fd5d64a34`

```text
0631          value = raw.get(name)
0632          if value is not None and (not isinstance(value, str)
0633                                    or not re.fullmatch(r"[0-9a-f]{64}", value)):
0634              raise ValueError(f"charter.{name} must be 64 lowercase hex characters")
0635      raw = charter_content(raw)
0636      norms = raw.get("norms")
0637      if (not isinstance(norms, list) or not norms
0638              or any(not isinstance(n, str) or not n.strip() for n in norms)):
0639          raise ValueError("charter.norms must be a nonempty list of nonempty strings")
0640      if "edition" in raw and (type(raw["edition"]) is not int or raw["edition"] != 1):
0641          raise ValueError("charter.edition must be 1")
0642      rows = raw.get("cards", [])
0643      if not isinstance(rows, list):
0644          raise ValueError("charter.cards must be a list of tables")
0645      cards = []
0646      prices = []
0647      for index, row in enumerate(rows):
0648          if not isinstance(row, dict):
0649              raise ValueError(f"card #{index} fields: expected a table")
0650          card_id = row.get("id", f"#{index}")
0651          for name in MetricCard.__dataclass_fields__:
0652              if name == "window":
0653                  continue
0654              if not isinstance(row.get(name), str) or not row[name].strip():
0655                  raise ValueError(f"card {card_id} {name}: must be a nonempty string")
0656          window = row.get("window")
0657          if isinstance(window, dict) and "per" not in window:
0658              window = {**window, "per": None}  # TOML has no null literal.
0659          cards.append(MetricCard(**{name: row[name] for name in MetricCard.__dataclass_fields__
0660                                     if name != "window"}, window=window))
0661          if "lambda" in row:
0662              prices.append((card_id, row["lambda"]))
0663      return Charter(1, tuple(norms), tuple(cards)), tuple(prices)
0664  
0665  
```

### `factorylab/charter/charter.py`: 48-98

SHA-256: `43b7872ecfacbb964a0dd0d90923e00683180dfc16b2d886bf40787206f2f2d6`

```text
0048  @dataclass(frozen=True)
0049  class Charter:
0050      """Each edition retains its exact norms and checked cards."""
0051  
0052      edition: int
0053      norms: tuple[str, ...]
0054      cards: tuple[MetricCard, ...]
0055  
0056      def __post_init__(self) -> None:
0057          if self.edition < 1:
0058              raise ValueError("charter edition starts at 1")
0059          if not self.norms:
0060              raise ValueError("a charter needs at least one norm")
0061          ids = [c.id for c in self.cards]
0062          if len(set(ids)) != len(ids):
0063              duplicate = next(card_id for card_id in ids if ids.count(card_id) > 1)
0064              raise ValueError(f"card {duplicate} id: metric card ids must be unique")
0065          for c in self.cards:
0066              if c.norm not in self.norms:
0067                  raise ValueError(f"card {c.id} references an unknown norm")
0068  
0069      def render(self, prices: dict[str, float] | None = None, *,
0070                 price_label: str | None = None) -> str:
0071          """Expose the full charter: the edition, every norm, and every card in order.
0072  
0073          Guarantees each card renders its id, norm, description, units, window,
0074          acceptable region, observation and accountability scope identically in
0075          every case, and that the four cases differ in the ``lambda:`` line
0076          alone. With ``prices``, it is that card's price, and ``0.0`` for a card
0077          the mapping does not name. With ``price_label``, it is that label
0078          verbatim, for every card. With both, ``price_label`` wins and ``prices``
0079          is not read: a rendering that names where the prices are cannot also
0080          inline numbers the controller moves at every closed window, which is the
0081          whole reason the label exists — a disclosure that must hold still
0082          between calls names ``world.card_prices`` and lets the moving numbers
0083          travel there. With neither, it is ``unassigned``.
0084          """
0085          lines = [f"CHARTER (edition {self.edition})", "", "NORMS"]
0086          lines += [f"- {n}" for n in self.norms]
0087          lines += ["", "METRIC CARDS"]
0088          for c in self.cards:
0089              lines += [
0090                  f"- {c.id} (norm: {c.norm})",
0091                  f"  {c.description}",
0092                  f"  units: {c.units}; window: {c.window}; acceptable: {c.acceptable_region}",
0093                  f"  observation: {c.observation}; answers_for: {c.answers_for}",
0094                  "  lambda: " + (price_label if price_label is not None else str(
0095                      prices.get(c.id, 0.0) if prices is not None else "unassigned")),
0096              ]
0097          return "\n".join(lines)
0098  
```

## 2. The ordinary invocation has one tool/child round and a narrow connector exception

The general continuation is forced to a final answer after a single batch. A successful connector fetch permits one further parsing round with restricted tool kinds; it does not provide a general research/build/debug session.

### `factorylab/runtime/compute.py`: 784-888

SHA-256: `997a3aea6bd907d670fd4ebedf04b1d881e84606078bd0da6ae5f50c9346ef3c`

```text
0784          total_cost = ret.cost
0785          seen_results: list[dict] = []
0786          tool_round = 0
0787          round_limit = 1
0788          while (not self.wallet.dead and ret.status == "ok" and (ret.tool_calls or ret.children)
0789                 and tool_round < round_limit):
0790              results = []
0791              tool_cost = 0
0792              for index, call in enumerate(ret.tool_calls):
0793                  if self.wallet.dead:
0794                      break
0795                  price = self._tool_price_bound(call)
0796                  slot = f"tool:{index}" if tool_round == 0 else f"connector-parse:{index}"
0797                  if price > max(0, req.cost_ceiling - total_cost - tool_cost):
0798                      result, cost = {"error": "request cost ceiling exhausted"}, 0
0799                      if call["tool"] == "connector.fetch":
0800                          self._connector_refused(req.handle, result["error"])
0801                  else:
0802                      result, cost = self._run_tool(action_id, req.handle, call, slot=slot)
0803                  tool_cost += cost
0804                  # A venue write the venue has not yet acknowledged is its own outcome:
0805                  # the intent is durable and the reconciler finalises it under the
0806                  # same client id, so it is neither a success nor a failure here.
0807                  uncertain = (isinstance(result, dict) and result.get("status") == "uncertain"
0808                               and call["tool"] in self.CONSEQUENCE_WRITES)
0809                  # An acknowledged venue result carries ``error: None``; only a stated
0810                  # error is a failure.
0811                  ok = uncertain or not (isinstance(result, dict)
0812                                         and result.get("error") is not None)
0813                  if call["tool"] == "connector.fetch" and ok:
0814                      round_limit = 2
0815                  self.stats.tool_calls += 1
0816                  if not ok:
0817                      self.stats.tool_call_failures += 1
0818                  # Parser arguments can contain a connector body. They are transient.
0819                  logged_args = ("[connector continuation]" if tool_round else
0820                                 json.dumps(self.ledger.without_connector_bodies(call.get("args")),
0821                                            default=str)[:1000])
0822                  self.ledger.append({
0823                      "kind": "tool.call", "handle": req.handle, "assembly_id": action_id,
0824                      "tool": call.get("tool"), "args": logged_args,
0825                      "ok": ok, "outcome": "uncertain" if uncertain else "ok" if ok else "failed",
0826                      **({"client_id": f"{req.handle}:{slot}"} if uncertain else {}),
0827                      "cost": cost, "ts": self.clock.now_ns,
0828                  })
0829                  self.window.tool_calls += 1
0830                  results.append({"tool": call.get("tool"), "args": call.get("args"),
0831                                  "result": result})
0832                  # A write the venue accepted, or has not yet acknowledged, is an
0833                  # action this return took; a rejected or refused one is not.
0834                  label = effect_label(str(call.get("tool")), call.get("args"))
0835                  if label is not None and ok and (
0836                          not isinstance(result, dict) or result.get("status") != "rejected"):
0837                      effects.append(label)
0838              for item in ret.children:
0839                  if self.wallet.dead:
0840                      break
0841                  result, cost = self._invoke_child(
0842                      action_id, req, item, max(0, req.cost_ceiling - total_cost - tool_cost)
0843                  )
0844                  tool_cost += cost
0845                  results.append(result)
0846                  if "error" not in result["result"] and result["result"].get("status") != "failed":
0847                      effects.append(f"request:{item.target}"[:64])
0848              seen_results.extend(results)
0849              # The continuation is the same request, and it is the billed call
0850              # that produces the final verdict — so everything the first call was
0851              # shown, the PROPENSITY block included, rides along unchanged.
0852              note = ("Return the final answer; this request's continuation has been "
0853                      "consumed. Further tool calls and requests are refused.")
0854              if tool_round + 1 < round_limit:
0855                  note = ("You may call population tools once more to parse what you "
0856                          "retrieved, then return the final answer. Requests are refused.")
0857              follow = req.continuation(
0858                  inputs={**req.inputs, "tool_results": results,
0859                          "seen_tool_results": seen_results, "continuation": note},
0860                  cost_ceiling=max(0, req.cost_ceiling - total_cost - tool_cost),
0861              )
0862              ret = (Return(req.handle, {"reason": "wallet exhausted"}, 0, "failed")
0863                     if self.wallet.dead else self._invoke_compute(action_id, follow))
0864              total_cost += tool_cost + ret.cost
0865              self._check_compute_return(req.handle, ret)
0866              tool_round += 1
0867              if (ret.status == "ok" and ret.outputs.get("status") == "cannot"
0868                      and isinstance(ret.outputs.get("reason"), str)):
0869                  ret = replace(ret, status="refused", children=(), tool_calls=())
0870              if ret.children:
0871                  self.ledger.append({"kind": "requests.refused", "handle": req.handle,
0872                                      "reason": "continuation already consumed"})
0873                  ret = replace(ret, children=())
0874              # The extra round composes the retrieved text through ordinary jailed tools.
0875              if tool_round < round_limit and ret.tool_calls:
0876                  if any(self.tool_specs.get(c["tool"], {}).get("kind")
0877                         not in ("population", "note", "artifact")
0878                         for c in ret.tool_calls):
0879                      round_limit = tool_round
0880              if ret.tool_calls and tool_round >= round_limit:
0881                  self.ledger.append({"kind": "tool.calls_ignored", "handle": req.handle,
0882                                      "reason": "continuation already consumed",
0883                                      "ts": self.clock.now_ns})
0884              if not ret.tool_calls or tool_round >= round_limit:
0885                  from factorylab.cortex.assembly import validate_schema
0886  
0887                  if ret.status == "ok":
0888                      try:
```

## 3. Population-created model assemblies are capped at 4096 output tokens

This ceiling is independent of the request dollar budget. The seed rehearsal producers use 1000 and 1200 output tokens. Token limits alone do not establish the actual amount of latent reasoning; the recorded configuration is the evidence.

### `factorylab/cortex/registration.py`: 475-529

SHA-256: `bf3ec5de421188d5f9d91922b1401c9fb0c987184c820a37e9d17a7895d19d79`

```text
0475      aid = item.get("id")
0476      if not isinstance(aid, str) or not SLUG.match(aid):
0477          raise ValueError("id must be a slug of 2-48 chars")
0478      if aid in known_assemblies or aid == "NOOP":
0479          raise ValueError("id already registered")
0480      # A display name only: measurement and settlement both follow ``emits``.
0481      role = item.get("role", "producer")
0482      if not isinstance(role, str) or not SLUG.fullmatch(role):
0483          raise ValueError("role must be a descriptive slug")
0484      model_id = item.get("model_id")
0485      # ``program`` is not a registered model: the seat's executor is its own code.
0486      program = model_id == "program"
0487      if not isinstance(model_id, str) or (model_id not in known_models and not program):
0488          raise ValueError("model_id must name a registered model")
0489      code, timeout_s, state_policy = "", 10, "none"
0490      if program:
0491          code = item.get("code")
0492          if not isinstance(code, str) or not code.strip():
0493              raise ValueError("a program seat needs code")
0494          if len(code) > 16_000:
0495              raise ValueError("code exceeds 16000 chars")
0496          timeout_s = item.get("timeout_s", 10)
0497          if type(timeout_s) is not int or not 1 <= timeout_s <= 10:
0498              raise ValueError("timeout_s must be an int in [1, 10]")
0499          state_policy = item.get("state_policy", "none")
0500          if state_policy not in ("none", "private"):
0501              raise ValueError("state_policy must be none or private")
0502          if not (jail_available() if jail is None else jail):
0503              raise ValueError("no jail on this host")
0504      elif any(k in item for k in ("code", "timeout_s", "state_policy")):
0505          raise ValueError("code, timeout_s and state_policy belong to a program seat")
0506      # A program has no system role; the prompt field is kept only as its label.
0507      prompt = item.get("system_prompt", "program" if program else None)
0508      if not isinstance(prompt, str) or not prompt.strip():
0509          raise ValueError("system_prompt is required")
0510      if len(prompt) > MAX_PROMPT_CHARS:
0511          raise ValueError(f"system_prompt exceeds {MAX_PROMPT_CHARS} chars")
0512      accepts = item.get("accepts")
0513      if not isinstance(accepts, list) or not accepts:
0514          raise ValueError("accepts must be a non-empty list of event kinds")
0515      accepts = tuple(dict.fromkeys(event_name(k) for k in accepts))
0516      emits, schemas = output_contracts(item.get("emits", seed_emits(role)),
0517                                        item.get("schemas", {}))
0518      max_tokens = item.get("max_tokens", 512)
0519      if type(max_tokens) is not int or not 16 <= max_tokens <= 4096:
0520          raise ValueError("max_tokens must be an int in [16, 4096]")
0521      effort = item.get("effort", "low")
0522      if effort not in ("low", "medium", "high"):
0523          raise ValueError("effort must be low, medium or high")
0524      if "reward_shapes" in item and not isinstance(item["reward_shapes"], dict):
0525          raise ValueError("reward_shapes must map declared emits kinds to reward shapes")
0526      return AssemblyProposal(
0527          aid, role, model_id, prompt, accepts, max_tokens, effort, emits, schemas,
0528          reward_contracts(emits, item.get("reward_shapes", {}), registered=known_reward_shapes),
0529          code, timeout_s, state_policy,
```

### `worlds/edition2-rehearsal-3.toml`: 124-200

SHA-256: `72eed0707659e00957d3d5c9d3f9abd2e1dd3e67a7c4526a8fa4f2524c7e0e29`

```text
0124  [[assemblies]]
0125  id = "seed-observer"
0126  role = "producer"
0127  # 15 September: GLM 5.3 flash via OpenRouter wrapped one reply in five under the edition 2
0128  # prompt, with and without the host pin (docs/audits/v5/rehearsal.md); DeepSeek 4.1 flash
0129  # scored 100% on every calibration column.
0130  model_id = "deepseek/deepseek-v4.1-flash"
0131  accepts = ["MarketMid", "Funding"]
0132  max_tokens = 1000
0133  effort = "low"
0134  
0135  [[assemblies]]
0136  id = "seed-decider"
0137  role = "producer"
0138  model_id = "venice:deepseek-v4-1-flash"
0139  accepts = ["Tick", "Fill"]
0140  max_tokens = 1200
0141  effort = "low"
0142  
0143  [[assemblies]]
0144  id = "eval-a"
0145  role = "evaluator"
0146  model_id = "venice:z-ai-glm-5-3-flash"
0147  accepts = ["ProducerReturn"]
0148  max_tokens = 1500
0149  effort = "low"
0150  
0151  [[assemblies]]
0152  id = "eval-b"
0153  role = "evaluator"
0154  model_id = "qwen/qwen3.7-flash"
0155  accepts = ["ProducerReturn"]
0156  # A reasoning seed spends its budget on reasoning before any content; 3000 keeps
0157  # its answers from ending on `length`.
0158  max_tokens = 3000
0159  effort = "low"
0160  
0161  [[assemblies]]
0162  id = "eval-c"
0163  role = "evaluator"
0164  model_id = "venice:deepseek-v4-1-flash"
0165  accepts = ["ProducerReturn"]
0166  max_tokens = 3000
0167  effort = "low"
0168  
0169  [[assemblies]]
0170  id = "eval-d"
0171  role = "evaluator"
0172  model_id = "openai/gpt-5.6-luna"
0173  accepts = ["ProducerReturn"]
0174  max_tokens = 1500
0175  effort = "low"
0176  
0177  # One antagonist: bounded trouble, judged like a producer, scored on whether it fooled a judge.
0178  [[assemblies]]
0179  id = "antagonist-a"
0180  role = "antagonist"
0181  model_id = "venice:qwen-3-8-flash"
0182  accepts = ["Tick", "MarketMid"]
0183  max_tokens = 2500
0184  effort = "low"
0185  
0186  [[assemblies]]
0187  id = "meta-a"
0188  role = "meta"
0189  model_id = "venice:deepseek-v4-1-flash"
0190  accepts = ["Verdict"]
0191  max_tokens = 2000
0192  effort = "low"
0193  
0194  [[assemblies]]
0195  id = "meta-b"
0196  role = "meta"
0197  model_id = "venice:qwen-3-8-flash"
0198  accepts = ["Verdict"]
0199  max_tokens = 800
0200  effort = "low"
```

## 4. Program seats support useful but tightly bounded stateful computation

Program source is limited to 16000 characters, state to 65536 bytes, and each program invocation to at most 10 seconds. The state survives through content-addressed artifacts. Programs can return mediated tool requests; lack of in-process network access is not lack of all external capabilities.

### `factorylab/cortex/assembly.py`: 228-267

SHA-256: `037a27387ce0828dbd5516ce05ca6942950b8086917bd85c8d6ab3e07ffbbadf`

```text
0228  # --- programs as seats (contract C8) -----------------------------------------
0229  
0230  PROGRAM_MODEL_ID = "program"
0231  MAX_PROGRAM_CODE_CHARS = 16_000
0232  MAX_PROGRAM_STATE_BYTES = 65_536
0233  PROGRAM_STATE_POLICIES = ("none", "private")
0234  
0235  
0236  @dataclass(frozen=True)
0237  class ProgramAssemblySpec(AssemblySpec):
0238      """A seat whose executor is population Python in the jail rather than a model.
0239  
0240      ``code`` reads one JSON object from stdin — ``prompt`` (the rendered request,
0241      exactly what a model would read), ``description``, ``inputs``,
0242      ``outcome_schema`` and ``state`` — and prints the same Return JSON a model
0243      would. With ``state_policy = "private"`` the object it prints under
0244      ``state`` is archived as an artifact owned by this seat and handed back on
0245      its next call; the archive is versioned by content hash, one artifact per
0246      call that changes it. ``reward_shapes`` carries admitted shapes for custom
0247      emitted kinds, as ``WorkAssemblySpec`` does for model seats.
0248      """
0249  
0250      code: str = ""
0251      timeout_s: int = 10
0252      state_policy: str = "none"
0253      reward_shapes: dict[str, str] = field(default_factory=dict)
0254  
0255      def __post_init__(self) -> None:
0256          super().__post_init__()
0257          if self.model_id != PROGRAM_MODEL_ID:
0258              raise ValueError("a program seat's model_id is program")
0259          if not isinstance(self.code, str) or not self.code.strip():
0260              raise ValueError("a program seat needs code")
0261          if len(self.code) > MAX_PROGRAM_CODE_CHARS:
0262              raise ValueError(f"code exceeds {MAX_PROGRAM_CODE_CHARS} chars")
0263          if type(self.timeout_s) is not int or not 1 <= self.timeout_s <= MAX_PROGRAM_TIMEOUT_S:
0264              raise ValueError(f"timeout_s must be an int in [1, {MAX_PROGRAM_TIMEOUT_S}]")
0265          if self.state_policy not in PROGRAM_STATE_POLICIES:
0266              raise ValueError("state_policy must be none or private")
0267          object.__setattr__(self, "reward_shapes", reward_contracts(self.emits, self.reward_shapes))
```

### `factorylab/cortex/assembly.py`: 314-349

SHA-256: `037a27387ce0828dbd5516ce05ca6942950b8086917bd85c8d6ab3e07ffbbadf`

```text
0314      def build_stdin(self, req: Request, state: Any) -> str:
0315          """Render what the program reads: the request as a model would see it, plus state."""
0316          req = replace(req, inputs={**req.inputs, "you": self.spec.id})
0317          return json.dumps({
0318              "prompt": req.prompt_text(),
0319              "description": req.description,
0320              "inputs": req.inputs,
0321              "outcome_schema": req.outcome_schema,
0322              "state": state,
0323          }, sort_keys=True, ensure_ascii=False)
0324  
0325      def _load_state(self) -> tuple[Any, str | None]:
0326          """The state the last successful call left, or None and why it could not be read."""
0327          if self.spec.state_policy != "private" or self.state_sha is None:
0328              return None, None
0329          if self.artifacts is None:
0330              return None, "no artifact archive"
0331          try:
0332              return json.loads(self.artifacts.get(self.state_sha).decode("utf-8")), None
0333          except Exception as exc:
0334              return None, type(exc).__name__
0335  
0336      def invoke(self, req: Request) -> Return:
0337          if self.price > req.cost_ceiling:
0338              return Return(req.handle, {"reason": "ceiling exceeds request cost_ceiling"}, 0,
0339                            "failed")
0340          state, state_error = self._load_state()
0341          try:
0342              stdin = self.build_stdin(req, state)
0343          except (TypeError, ValueError) as exc:
0344              return Return(req.handle, {"reason": type(exc).__name__}, 0, "failed")
0345          code, timeout_s = self.spec.code, self.spec.timeout_s
0346  
0347          def execute() -> dict:
0348              return self.runner.run(code, stdin=stdin, timeout_s=timeout_s)
0349  
```

## 5. The jail is deliberately not a general persistent workstation

Linux namespace/seccomp isolation denies direct networking and child processes. The base Python runtime is exposed rather than the repository environment and credentials. Preserve this boundary; add separate funded workspace/job capabilities instead of removing it.

### `factorylab/cortex/sandbox.py`: 1-9

SHA-256: `786e3824e46a9263d4183656ede061dad1f1c41d368a2d198aabf0211b3c95d0`

```text
0001  """Population Python runs only inside an OS jail, with bounded resources and output.
0002  
0003  Linux uses bubblewrap namespaces and seccomp: no host tree outside the Python
0004  runtime/system libraries and the tool directory, no sockets or child processes.
0005  macOS sandbox-exec is development-only: a deny-default profile grants access
0006  only to the Python prefix and the tool directory, and the kernel refuses every
0007  RLIMIT_AS/RLIMIT_DATA value (EINVAL, confined or not), so on macOS the memory
0008  bound is only the wall and CPU timeouts. Hosts unable to launch the jail cannot
0009  execute population code. No source-text filtering is a boundary.
```

### `factorylab/cortex/sandbox.py`: 54-96

SHA-256: `786e3824e46a9263d4183656ede061dad1f1c41d368a2d198aabf0211b3c95d0`

```text
0054      """Deny process creation and sockets even for uid 0; foreign syscall ABIs fail closed."""
0055      # Linux UAPI audit architecture and syscall numbers. No Python package or
0056      # helper executable is needed to construct classic BPF for bwrap --seccomp.
0057      machine = platform.machine().lower()
0058      if machine in ("x86_64", "amd64"):
0059          arch = 0xC000003E
0060          denied = (41, 53, 56, 57, 58, 101, 272, 308, 425, 426, 427, 435)
0061      elif machine in ("aarch64", "arm64"):
0062          arch = 0xC00000B7
0063          denied = (97, 117, 198, 199, 220, 268, 425, 426, 427, 435)
0064      else:
0065          raise NoJail("no jail on this host")
0066      # ptrace, unshare/setns and io_uring are also denied: they are unnecessary
0067      # for a tool and must not provide alternate routes around the socket check.
0068      instructions = [
0069          (0x20, 0, 0, 4),                 # load seccomp_data.arch
0070          (0x15, 1, 0, arch),              # architecture must match
0071          (0x06, 0, 0, 0x80000000),        # SECCOMP_RET_KILL_PROCESS
0072          (0x20, 0, 0, 0),                 # load seccomp_data.nr
0073          (0x45, 0, 1, 0x40000000),        # reject x32 ABI syscall bit
0074          (0x06, 0, 0, 0x80000000),
0075      ]
0076      for syscall in denied:
0077          instructions.extend([(0x15, 0, 1, syscall), (0x06, 0, 0, 0x00050000 | errno.EPERM)])
0078      instructions.append((0x06, 0, 0, 0x7FFF0000))  # SECCOMP_RET_ALLOW
0079      return b"".join(struct.pack("HBBI", *instruction) for instruction in instructions)
0080  
0081  
0082  def _command(jail: str, work: Path, prefix: Path, python: Path, seccomp_fd: int) -> list[str]:
0083      if sys.platform == "linux":
0084          command = [jail, "--unshare-all", "--die-with-parent", "--cap-drop", "ALL"]
0085          for path in dict.fromkeys((Path("/usr"), Path("/lib"), Path("/lib64"), prefix)):
0086              if path.exists():  # /lib64 is absent on some aarch64 distributions
0087                  command.extend(["--ro-bind", str(path), str(path)])
0088          command.extend([
0089              "--tmpfs", "/tmp", "--bind", str(work), "/work", "--chdir", "/work",
0090              "--new-session", "--clearenv", "--setenv", "PATH", "/usr/bin",
0091              "--seccomp", str(seccomp_fd), "--", str(python), "-I", "-S", "-B",
0092              "/work/runner.py",
0093          ])
0094          return command
0095      # No process-fork, network, or general filesystem grant. The executable
0096      # grant is limited to this interpreter; dylibs must come from its prefix
```

### `factorylab/cortex/sandbox.py`: 172-205

SHA-256: `786e3824e46a9263d4183656ede061dad1f1c41d368a2d198aabf0211b3c95d0`

```text
0172  
0173  def run_python(
0174      code: str,
0175      *,
0176      stdin: str = "",
0177      timeout_s: float = 5.0,
0178      cpu_s: int = 2,
0179      max_output_bytes: int = 64_000,
0180  ) -> SandboxResult:
0181      """Bound population execution by an OS jail, rlimits and a process-tree wall timeout.
0182  
0183      Captured streams use size-limited files, never unbounded host-memory pipes.
0184      Limit setup fails closed. The interpreter is the base runtime, so the repo
0185      virtualenv, its packages and all host credentials are outside the jail.
0186      """
0187      if cpu_s <= 0 or timeout_s <= 0 or max_output_bytes <= 0:
0188          raise ValueError("limits must be positive")
0189      jail = _jail_executable()
0190      prefix = Path(sys.base_prefix).resolve()
0191      python = Path(sys._base_executable).resolve()
0192      if not python.is_relative_to(prefix) or prefix == Path("/"):
0193          raise NoJail("no jail on this host")
0194      with (
0195          tempfile.TemporaryDirectory(prefix="factorylab-sbx-") as directory,
0196          tempfile.TemporaryFile() as stdout,
0197          tempfile.TemporaryFile() as stderr,
0198          tempfile.TemporaryFile() as seccomp,
0199      ):
0200          work = Path(directory).resolve()
0201          (work / "main.py").write_text(code, encoding="utf-8")
0202          # Set NPROC after the jail has created its init/interpreter processes.
0203          # These hard limits cannot be raised by population code. Linux seccomp
0204          # enforces the process ban even where RLIMIT_NPROC exempts uid 0.
0205          # Darwin returns EINVAL for any RLIMIT_AS value (the shared cache alone
```

## 6. Artifact hashes currently grant read access across seats

The artifact.get branch explicitly lets any seat read any artifact. A program state called private is therefore not protected by per-seat authorization once its hash is known. Public content addressing and private content authorization are separate concerns.

### `factorylab/runtime/compute.py`: 603-632

SHA-256: `997a3aea6bd907d670fd4ebedf04b1d881e84606078bd0da6ae5f50c9346ef3c`

```text
0603  
0604      def _allowed_tools(self, action_id: str) -> set[str]:
0605          """Every registered tool is a public primitive; schematics are public."""
0606          return set(self.tool_specs)
0607  
0608      def _run_tool(self, action_id: str, handle: str, call: dict[str, Any], *,
0609                    slot: str = "tool:0") -> tuple[dict, int]:
0610          """Execute one tool call through metering. Returns (result, cost)."""
0611          self._ensure_connector_tool()
0612          tool_id = str(call.get("tool"))
0613          args = call.get("args") if isinstance(call.get("args"), dict) else {}
0614          if tool_id not in self.tool_specs or tool_id not in self._allowed_tools(action_id):
0615              return {"error": "unknown or disallowed tool"}, 0
0616          if tool_id == "connector.fetch":
0617              return self._fetch_connector(action_id, handle, args)
0618          if tool_id in ("note.put", "note.get"):
0619              from factorylab.runtime.notes import run
0620  
0621              return run(self, action_id, handle, tool_id, args)
0622          if tool_id == "artifact.get":
0623              # Free by contract (C9): any seat reads any artifact; the read is ledgered.
0624              result = self.artifacts.read(args.get("sha"))
0625              self.ledger.append({"kind": "artifact.get", "sha": str(args.get("sha"))[:64],
0626                                  "handle": handle, "assembly_id": action_id,
0627                                  "found": "error" not in result, "ts": self.clock.now_ns})
0628              return result, 0
0629          if tool_id in self.CONSEQUENCE_WRITES and not self._may_write(handle):
0630              # No judge trades what it judges (essay II.III): the refusal is public.
0631              self.ledger.append({"kind": "tool.refused", "handle": handle,
0632                                  "assembly_id": action_id, "tool": tool_id,
```

### `factorylab/cortex/assembly.py`: 238-247

SHA-256: `037a27387ce0828dbd5516ce05ca6942950b8086917bd85c8d6ab3e07ffbbadf`

```text
0238      """A seat whose executor is population Python in the jail rather than a model.
0239  
0240      ``code`` reads one JSON object from stdin — ``prompt`` (the rendered request,
0241      exactly what a model would read), ``description``, ``inputs``,
0242      ``outcome_schema`` and ``state`` — and prints the same Return JSON a model
0243      would. With ``state_policy = "private"`` the object it prints under
0244      ``state`` is archived as an artifact owned by this seat and handed back on
0245      its next call; the archive is versioned by content hash, one artifact per
0246      call that changes it. ``reward_shapes`` carries admitted shapes for custom
0247      emitted kinds, as ``WorkAssemblySpec`` does for model seats.
```

### `factorylab/cortex/assembly.py`: 355-380

SHA-256: `037a27387ce0828dbd5516ce05ca6942950b8086917bd85c8d6ab3e07ffbbadf`

```text
0355          except Infeasible as exc:
0356              return Return(req.handle, {"reason": f"infeasible: {exc}"}, 0, "failed")
0357          except BillingUncertain as exc:
0358              return Return(req.handle, {"reason": str(exc)}, exc.cost, "failed")
0359          except Exception as exc:
0360              return Return(req.handle, {"reason": type(exc).__name__}, 0, "failed")
0361          cost = metered.cost
0362          result = metered.result if isinstance(metered.result, dict) else {}
0363          state_in = self.state_sha
0364          ret = self._interpret(req, result, cost, state_error)
0365          if self.record is not None:
0366              self.record({
0367                  "kind": "program.call", "assembly_id": self.spec.id, "handle": req.handle,
0368                  "status": ret.status, "cost": cost, "state_in": state_in,
0369                  "state_out": self.state_sha,
0370              })
0371          return ret
0372  
0373      def _interpret(self, req: Request, result: dict, cost: int,
0374                     state_error: str | None) -> Return:
0375          provider = {"finish_reason": "stop", "input_tokens": None, "output_tokens": None,
0376                      "reasoning_tokens": None, "cached_tokens": None,
0377                      "max_tokens": self.spec.max_tokens, "state_sha": self.state_sha}
0378          if state_error is not None:
0379              provider["state_error"] = state_error
0380  
```

## 7. Market information and trading tools already exist

The repository already exposes candles, order books, funding history and basic spot/perpetual execution. The rehearsal includes spot pairs; it would be incorrect to describe this snapshot as perps-only. Recommending richer capabilities is not a finding that these existing tools are absent.

### `factorylab/world/venue_tools.py`: 119-245

SHA-256: `d757c660972164a31ec57db303d66893742c2970df09f0b626772dd225bcd485`

```text
0119  class VenueTools:
0120      """Only schema-valid requests reach the exchange; every attempt has an audit entry."""
0121  
0122      PUBLIC_READS = frozenset({"venue.instruments", "venue.mids", "venue.funding",
0123                                "venue.candles", "venue.order_book", "venue.funding_history"})
0124  
0125      def __init__(self, exchange: Exchange, *, coins: tuple[str, ...], max_leverage: int = 3,
0126                   spot_pairs: tuple[str, ...] = ()):
0127          if type(max_leverage) is not int or max_leverage < 1:
0128              raise ValueError("max_leverage must be a positive integer")
0129          if not coins or any(not isinstance(coin, str) or not coin for coin in coins):
0130              raise ValueError("coins must contain nonempty coin names")
0131          self.exchange = exchange
0132          self.coins, self.spot_pairs = tuple(coins), tuple(spot_pairs)
0133          public = list((*coins, *spot_pairs))
0134          for name in ("coins", "spot_pairs", "listed_coins", "listed_spot_pairs", "_listed_coins"):
0135              values = getattr(exchange, name, ())
0136              if isinstance(values, (list, tuple)):
0137                  public.extend(values)
0138          spot_names = getattr(exchange, "_spot_names", {})
0139          if isinstance(spot_names, dict):
0140              public.extend(spot_names)
0141          self.public_coins = tuple(dict.fromkeys(public))
0142          self.log: list[tuple[str, dict, bool]] = []
0143          coin = {"type": "string", "enum": list(dict.fromkeys((*coins, *spot_pairs)))}
0144          positive = {
0145              "anyOf": [
0146                  {"type": "number", "exclusiveMinimum": 0},
0147                  {
0148                      "type": "string",
0149                      "pattern": r"^(?=[0-9.]*[1-9])(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)"
0150                      r"(?:[eE][+-]?[0-9]+)?$",
0151                  },
0152              ]
0153          }
0154          market = {"type": "string", "enum": ["perp", "spot"], "default": "perp"}
0155          trade = {
0156              "market": market,
0157              "coin": coin,
0158              "side": {"type": "string", "enum": ["buy", "sell"]},
0159              "size": positive,
0160              "reduce_only": {"type": "boolean", "default": False},
0161          }
0162          definitions = [
0163              (
0164                  "candles",
0165                  "Recent OHLCV candles, oldest first; timestamps in nanoseconds.",
0166                  {
0167                      "coin": coin,
0168                      "interval": {"type": "string", "enum": ["1m", "5m", "15m", "1h"]},
0169                      "n": {"type": "integer", "minimum": 1, "maximum": 200},
0170                  },
0171                  ["coin", "interval", "n"],
0172              ),
0173              (
0174                  "order_book",
0175                  "Best-first bid and ask price/size levels.",
0176                  {"coin": coin, "depth": {"type": "integer", "minimum": 1, "maximum": 20}},
0177                  ["coin", "depth"],
0178              ),
0179              (
0180                  "funding_history",
0181                  "Recent funding rates, oldest first; timestamps in nanoseconds.",
0182                  {"coin": coin, "n": {"type": "integer", "minimum": 1, "maximum": 100}},
0183                  ["coin", "n"],
0184              ),
0185              ("open_orders", "Currently resting orders for the account.", {}, []),
0186              ("positions", "Open signed positions and entry prices for the account.", {}, []),
0187              (
0188                  "place_market",
0189                  "Place a market buy or sell; optionally reduce only.",
0190                  trade,
0191                  ["coin", "side", "size"],
0192              ),
0193              (
0194                  "place_limit",
0195                  "Place a good-until-cancelled limit order; optionally reduce only.",
0196                  {**trade, "price": positive},
0197                  ["coin", "side", "size", "price"],
0198              ),
0199              (
0200                  "cancel",
0201                  "Cancel a resting order on its coin.",
0202                  {"coin": coin, "order_id": {"type": "string", "minLength": 1}},
0203                  ["coin", "order_id"],
0204              ),
0205              (
0206                  "close",
0207                  "Reduce a position by size, or close it fully when size is omitted or null.",
0208                  {
0209                      "coin": coin,
0210                      "market": market,
0211                      "size": {"anyOf": [*positive["anyOf"], {"type": "null"}], "default": None},
0212                  },
0213                  ["coin"],
0214              ),
0215              (
0216                  "set_leverage",
0217                  "Set cross-margin leverage for a coin.",
0218                  {
0219                      "coin": coin,
0220                      "market": market,
0221                      "leverage": {"type": "integer", "minimum": 1, "maximum": max_leverage},
0222                  },
0223                  ["coin", "leverage"],
0224              ),
0225          ]
0226          self._specs = {
0227              f"venue.{name}": ToolSpec(
0228                  f"venue.{name}",
0229                  description,
0230                  deepcopy(
0231                      {
0232                          "type": "object",
0233                          "properties": properties,
0234                          "required": required,
0235                          "additionalProperties": False,
0236                      }
0237                  ),
0238                  0,
0239              )
0240              for name, description, properties, required in definitions
0241          }
0242          # ``instruments`` is where the venue's whole listing lives. The world block
0243          # carries only the trading markets' records, so this description is what
0244          # tells an assembly the rest of the listing is one call away.
0245          listings = {
```

### `worlds/edition2-rehearsal-3.toml`: 292-298

SHA-256: `72eed0707659e00957d3d5c9d3f9abd2e1dd3e67a7c4526a8fa4f2524c7e0e29`

```text
0292  # Testnet spot: PURR/USDC is the only pair on testnet with a canonical name and a
0293  # rehearsable lot (szDecimals 0, mid ~$4.6). BTC/USDC exists there but szDecimals 0
0294  # at a ~$6,100 mid puts one lot far beyond the spot balance a rehearsal holds, and
0295  # ETH/USDC does not exist on testnet at all. HYPE/USDC (@1035 on testnet) is the
0296  # physics of the exit route: the spot HYPE that pays the population's own Core gas
0297  # (docs/launch-decisions.md, "Self-serve gas"), one more MarketMid per tick.
0298  spot_pairs = ["PURR/USDC", "HYPE/USDC"]
```

## 8. Web connectors are bounded GET, not a generic blockchain RPC client

The connector reserves internal rail hosts from general access and issues GET requests. General read-only JSON-RPC and read-only POST /info require an explicit mediated capability; removing the rail denylist would be the wrong remedy.

### `factorylab/world/connector.py`: 33-63

SHA-256: `7cd48e1dfb7a3120a819268a402f9ec7b724c0c42ea59ef9b874a113a6797672`

```text
0033      except ValueError:
0034          return None
0035      return host.lower() if host else None
0036  
0037  
0038  # Every endpoint this world's own rails talk to, taken from the modules that
0039  # define them, so a renamed or added rail cannot silently become fetchable.
0040  RAIL_URLS = (
0041      MAINNET_API_URL, TESTNET_API_URL,                                # world/exchange.py venue
0042      HYPEREVM.rpc, HYPEREVM_TESTNET.rpc, BASE.rpc, BASE_SEPOLIA.rpc,  # world/evm.py RPC
0043      "https://openrouter.ai/api/v1",                                  # world/openrouter.py
0044      "https://api.anthropic.com",                                     # world/models.py
0045      VENICE_URL,                                                      # world/x402.py
0046      DISCOVERY_URL,                                                   # world/market.py index
0047  )
0048  
0049  DEFAULT_DENYLIST = tuple(dict.fromkeys(h for h in map(url_host, RAIL_URLS) if h))
0050  
0051  
0052  def origin_host(origin: str) -> str:
0053      """Accept only a canonical HTTPS origin with a hostname and no other URL components."""
0054      if not isinstance(origin, str) or len(origin) > 260:
0055          raise ConnectorRefused("origin must be https://<host>")
0056      parts = urlsplit(origin)
0057      host = parts.hostname
0058      if (not host or parts.scheme != "https" or origin != f"https://{host}"
0059              or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host)
0060              or any(not label or len(label) > 63 or label.startswith("-") or label.endswith("-")
0061                     for label in host.split("."))):
0062          raise ConnectorRefused("origin must be https://<host> without credentials, port or path")
0063      return host
```

### `factorylab/world/connector.py`: 211-244

SHA-256: `7cd48e1dfb7a3120a819268a402f9ec7b724c0c42ea59ef9b874a113a6797672`

```text
0211                  raise TimeoutError
0212              raw = socket.create_connection((addresses[0], 443), timeout=remaining)
0213              try:
0214                  context = ssl.create_default_context()
0215                  remaining = deadline - time.monotonic()
0216                  if remaining <= 0:
0217                      raise TimeoutError
0218                  raw.settimeout(remaining)
0219                  secured = context.wrap_socket(raw, server_hostname=host)
0220              except BaseException:
0221                  raw.close()
0222                  raise
0223              connection.sock = _DeadlineSocket(
0224                  secured, deadline, max_bytes + HEADER_ALLOWANCE_BYTES)
0225              # Host is required HTTP framing. These are the only application headers.
0226              connection.putrequest("GET", path, skip_accept_encoding=True)
0227              connection.putheader("Accept", "*/*")
0228              connection.putheader("User-Agent", "FactoryLab-Connector/1")
0229              if payment_signature is not None:
0230                  connection.putheader("PAYMENT-SIGNATURE", payment_signature)
0231              connection.endheaders()
0232              response = connection.getresponse()
0233              try:
0234                  chunks = bytearray()
0235                  while len(chunks) <= max_bytes:
0236                      if time.monotonic() >= deadline:
0237                          raise TimeoutError
0238                      chunk = response.read1(min(65536, max_bytes + 1 - len(chunks)))
0239                      if not chunk:
0240                          break
0241                      chunks.extend(chunk)
0242                  return ConnectorResponse(response.status, bytes(chunks), {
0243                      k: v for k, v in response.getheaders()
0244                      if k.lower() in ("payment-required", "payment-response", "x-payment-response")})
```

## 9. One profit predicate retains privileged consequence selection

The Settler explicitly limits consequence standing updates to the kernel return_paid_off commitment. Other predicates can settle, but their forecasts do not enter a judge selection weight through this path. Removing the metric card alone does not remove this objective privilege.

### `factorylab/settlement/settle.py`: 97-157

SHA-256: `b3d286b093e7c13ca6c17117c06bc4dc6db556493b3a99ba2abfea4cc7854f70`

```text
0097  
0098  class Settler:
0099      """Each due forecast is scored at most once and missing facts never become performance.
0100  
0101      Only the kernel payoff commitment (``return_paid_off``) trains consequence
0102      standing; optional public-predicate forecasts settle to their handles and the
0103      prevalence baseline but never enter a judge's selection weight.
0104      """
0105  
0106      def __init__(
0107          self,
0108          book: ForecastBook,
0109          queue: DecisionQueue,
0110          standing: ConsequenceStanding,
0111          baseline: PrevalenceBaseline,
0112          observer: Observer,
0113      ) -> None:
0114          self.__book = book
0115          self.__queue = queue
0116          self.__standing = standing
0117          self.__baseline = baseline
0118          self.__observer = observer
0119          # about_handle -> baseline q before that return's outcome entered the base rate
0120          self.__snapshots: dict[str, float] = {}
0121          # about_handle -> the outcome already counted in the base rate, once per return
0122          # (a binary payoff, or a verdict key's fractional unblamed target)
0123          self.__recorded: dict[str, float] = {}
0124  
0125      def settle_due(
0126          self, n: int, facts_for: Callable[[Forecast], WindowFacts | None]
0127      ) -> list[Settled]:
0128          """Return due outcomes in seal order; only accepted observed settlements train history."""
0129          results = []
0130          for forecast in self.__book.due(n):
0131              if forecast.predicate_id == RETURN_PAID_OFF.id:
0132                  continue
0133              facts = facts_for(forecast)
0134              y = score = baseline_score = None
0135              status = SettleStatus.CENSORED
0136              if facts is not None:
0137                  if isinstance(forecast, PredicateForecast):
0138                      y = self.__observer.observe(forecast.predicate_id, forecast.params, facts,
0139                                                  version=forecast.predicate.version)
0140                  else:
0141                      y = self.__observer.observe(forecast.predicate_id, forecast.params, facts)
0142              if y is not None:
0143                  baseline_score = self.__baseline.baseline_brier(baseline_key(forecast), y)
0144                  score = brier(forecast.q, y)
0145                  status = SettleStatus.SETTLED
0146              # A rejected queue/ledger write must not contaminate history on a later retry.
0147              self.__queue.settle(
0148                  forecast.handle,
0149                  channel="consequence",
0150                  score=0.0 if score is None else score,
0151                  status=status,
0152                  definition_version="brier-v1",
0153                  sampling_ref=None,
0154              )
0155              if y is not None:
0156                  self.__baseline.record(baseline_key(forecast), y)
0157              self.__book.mark_settled(forecast.handle)
```

## 10. Meaningful temporal machinery already exists and should be retained

Governance cadence uses outcome latencies and outstanding forecast ages; kernel timing supports closure counts and jittered aggregate returns. The runtime cascade gate also batches judgment arrivals. A redesign must preserve the former and test that each evaluative layer is tied to the correct completed evidence, rather than claiming timing has not been built.

### `factorylab/runtime/cadence.py`: 74-139

SHA-256: `25562ed404ec97433dbd93a1a1945104f5050821bcb6a20596a526e5fb31eb41`

```text
0074      def record(
0075          self, *, handle: str, predicate_id: str, opened_event: int, settled_event: int,
0076          opened_ns: int, settled_ns: int, status: str,
0077      ) -> None:
0078          """Retain event latency and close its outstanding age after recording both time units."""
0079          if settled_ns < opened_ns or settled_event < opened_event:
0080              raise ValueError("settlement must not precede opening")
0081          latency_ns = settled_ns - opened_ns
0082          self._ledger.append({
0083              "kind": "cadence.settlement", "handle": handle, "predicate_id": predicate_id,
0084              "opened_event": opened_event, "settled_event": settled_event,
0085              "opened_ns": opened_ns, "settled_ns": settled_ns,
0086              "latency_events": settled_event - opened_event, "latency_ns": latency_ns,
0087              "status": status,
0088          })
0089          self._latencies.append(settled_event - opened_event)
0090          self._outstanding.pop(handle, None)
0091          self._current_event = max(self._current_event, settled_event)
0092  
0093      def slowest_period_events(self) -> int:
0094          """Keep the committed consequence horizon beneath p90 and outstanding forecast age.
0095  
0096          A pooled sample of quick completions cannot disprove an unfinished slow
0097          loop. The backstop remains the conservative floor even after warm-up.
0098          """
0099          estimate = self._backstop
0100          if len(self._latencies) >= self._min_support:
0101              ordered = sorted(self._latencies)
0102              estimate = max(estimate, ordered[(9 * len(ordered) + 9) // 10 - 1])
0103          oldest = max((self._current_event - opened for opened in self._outstanding.values()),
0104                       default=0)
0105          return max(estimate, oldest)
0106  
0107      def slowest_period_ns(self, tick_interval_ns: int | TickClock) -> int:
0108          """Convert at the slower of the delivered gap and the interval now declared.
0109  
0110          A gap sample is evidence that the loop ran slowly, never evidence that
0111          it may run faster than the charter currently says. An amendment that
0112          lengthens the tick therefore takes effect immediately, and measurement
0113          may only push the priced period further out.
0114          """
0115          interval = tick_interval_ns
0116          if not isinstance(interval, int):
0117              declared = interval.interval_ns
0118              measured = getattr(interval, "measured_interval_ns", None)
0119              interval = max(measured(), declared) if measured is not None else declared
0120          return self.slowest_period_events() * interval
0121  
0122      def earliest_event(self) -> int:
0123          """Require fresh event evidence since the most recent activation."""
0124          return self._last_activation_event + self._min_ratio * self.slowest_period_events()
0125  
0126      def earliest_ns(self, tick_interval_ns: int | TickClock) -> int:
0127          """Return the inclusive activation threshold, recomputed from current observations."""
0128          return self._last_activation_ns + self._min_ratio * self.slowest_period_ns(tick_interval_ns)
0129  
0130      def approve(self, amendment_id: str) -> None:
0131          """Keep approved candidates in approval order until their activation is recorded."""
0132          if amendment_id in self._waiting:
0133              return
0134          self._ledger.append({"kind": "charter.approved", "amendment_id": amendment_id})
0135          self._waiting[amendment_id] = None
0136  
0137      def ready(self, *, now_ns: int, tick_interval_ns: int | TickClock, window: int) -> bool:
0138          """Block early activation and emit at most one deferral per waiting candidate per window."""
0139          earliest = self.earliest_ns(tick_interval_ns)
```

### `factorylab/kernel/timing.py`: 57-132

SHA-256: `949131e94f65a44889d3f7ec5422da1c0766fb15d71c6aad1156217f9c76f591`

```text
0057  
0058      count: int
0059      mean: float | None
0060      variance: float | None
0061      missing: int
0062      oldest_ts: int
0063      newest_ts: int
0064  
0065  
0066  class UpwardBuffer:
0067      """No report escapes before every governed loop meets a seeded minimum closure ratio."""
0068  
0069      def __init__(
0070          self,
0071          registry: TimingRegistry,
0072          governing_loop: str,
0073          *,
0074          min_ratio: int = 3,
0075          seed: int = 0,
0076          jitter: int = 1,
0077      ) -> None:
0078          if type(min_ratio) is not int or min_ratio < 1:
0079              raise ValueError("minimum ratio must be a positive integer")
0080          if type(jitter) is not int or jitter < 0 or type(seed) is not int:
0081              raise ValueError("jitter must be nonnegative and seed must be an integer")
0082          self.__registry = registry
0083          self.__governed = registry.governed(governing_loop)
0084          if not self.__governed:
0085              raise ValueError("upward reports require a loop that governs lower loops")
0086          self.__min_ratio = min_ratio
0087          self.__jitter = jitter
0088          self.__rng = random.Random(seed)
0089          self.__items: list[tuple[LearningReturn, int]] = []
0090          self.__baseline: dict[str, int] = {}
0091          self.__thresholds: dict[str, int] = {}
0092          self._reset_window()
0093  
0094      def _reset_window(self) -> None:
0095          self.__baseline = {loop: self.__registry.closure_count(loop) for loop in self.__governed}
0096          self.__thresholds = {
0097              loop: self.__min_ratio + self.__rng.randint(0, self.__jitter)
0098              for loop in self.__governed
0099          }
0100  
0101      def add(self, feedback: LearningReturn, ts_ns: int) -> None:
0102          """Retain thin attribution internally until an eligible aggregate release."""
0103          if not isinstance(feedback, LearningReturn):
0104              raise TypeError("only thin LearningReturn values may enter an upward buffer")
0105          if type(ts_ns) is not int or ts_ns < 0:
0106              raise ValueError("ts_ns must be nonnegative integer nanoseconds")
0107          self.__items.append((feedback, ts_ns))
0108  
0109      def release(self) -> DistributionSummary | None:
0110          """Return one summary when all closure thresholds hold; polling never redraws jitter."""
0111          if not self.__items or any(
0112              self.__registry.closure_count(loop) - self.__baseline[loop] < self.__thresholds[loop]
0113              for loop in self.__governed
0114          ):
0115              return None
0116          scores = [item.score for item, _ in self.__items if item.status == SettleStatus.SETTLED]
0117          timestamps = [ts for _, ts in self.__items]
0118          result = DistributionSummary(
0119              len(self.__items),
0120              fmean(scores) if scores else None,
0121              pvariance(scores) if scores else None,
0122              len(self.__items) - len(scores),
0123              min(timestamps),
0124              max(timestamps),
0125          )
0126          self.__items.clear()
0127          self._reset_window()
0128          return result
0129  
0130      def state(self) -> dict:
0131          """Retain pending reports, drawn thresholds, baselines and the exact jitter RNG."""
0132          return {
```

### `factorylab/runtime/cascade.py`: 12-31

SHA-256: `d47b4517474ff7a4a7e91a76e13beb2537fdd707e42458a20761848171a332a0`

```text
0012  def release_threshold(min_ratio: int, jitter_fraction: float, draw: float) -> int:
0013      """Preserve the minimum separation, with bounded upward jitter from a supplied draw."""
0014      if type(min_ratio) is not int or min_ratio < 3:
0015          raise ValueError("cascade min_ratio must be an integer >= 3")
0016      if not isfinite(jitter_fraction) or jitter_fraction < 0:
0017          raise ValueError("jitter_fraction must be finite and nonnegative")
0018      if not isfinite(draw) or not 0 <= draw < 1:
0019          raise ValueError("draw must be in [0, 1)")
0020      jitter = ceil(min_ratio * jitter_fraction)
0021      return min_ratio + int(draw * (jitter + 1))
0022  
0023  
0024  def event_tier(event: Event) -> int:
0025      """Only judgement events enter the cascade; producer judgements occupy tier one.
0026  
0027      A judgement of a producer return is the seed ``Verdict`` and occupies tier
0028      one. Every higher arrival — the seed ``MetaVerdict`` or a population kind
0029      whose declared reward shape is ``conformity`` — states the tier it judges in
0030      its own payload, so a window is separated by declared position rather than
0031      by a fixed pair of kind names. Which kinds are admitted at all is the
```

### `factorylab/runtime/cascade.py`: 43-75

SHA-256: `d47b4517474ff7a4a7e91a76e13beb2537fdd707e42458a20761848171a332a0`

```text
0043  class CascadeGate:
0044      """An arrival returns new state; no window releases early or crosses evaluatory tiers."""
0045  
0046      threshold: int
0047      arrivals: tuple[Event, ...] = ()
0048  
0049      def __post_init__(self) -> None:
0050          if type(self.threshold) is not int or self.threshold < 3:
0051              raise ValueError("threshold must be an integer >= 3")
0052          object.__setattr__(self, "arrivals", tuple(self.arrivals))
0053          if len(self.arrivals) >= self.threshold:
0054              raise ValueError("a full window must already have been released")
0055          if len({event_tier(e) for e in self.arrivals}) > 1:
0056              raise ValueError("a gate cannot mix tiers")
0057  
0058      def add(self, event: Event) -> tuple[CascadeGate | None, Event | None]:
0059          """Release only the latest event, enriched with the complete disjoint arrival window."""
0060          tier = event_tier(event)
0061          if self.arrivals and event_tier(self.arrivals[0]) != tier:
0062              raise ValueError("a gate cannot mix tiers")
0063          arrivals = (*self.arrivals, event)
0064          if len(arrivals) < self.threshold:
0065              return replace(self, arrivals=arrivals), None
0066          scores = [e.payload["verdict" if tier == 1 else "score"] for e in arrivals]
0067          handles = [e.payload["evaluator_handle" if tier == 1 else "by"] for e in arrivals]
0068          window = {
0069              "count": len(arrivals),
0070              "mean": fmean(scores),
0071              "min": min(scores),
0072              "max": max(scores),
0073              "handles": handles,
0074          }
0075          return None, replace(event, payload={**event.payload, "window": window})
```

## 11. Protected novelty exists, but its viability needs end-to-end testing

The rehearsal grants three trials and a six-window lifetime, with hourly novelty windows and a 10% share. The proposal is to fund complete investigative episodes, not to impose a registration or trading quota.

### `worlds/edition2-rehearsal-3.toml`: 202-219

SHA-256: `72eed0707659e00957d3d5c9d3f9abd2e1dd3e67a7c4526a8fa4f2524c7e0e29`

```text
0202  [novelty]
0203  trials = 3
0204  max_lifetime_windows = 6
0205  share = 0.1
0206  # One hour, never the compute-continuity two-minute window: the reviewer's rent trap
0207  # (docs/audits/v4/gpt6-triage.md) is 720 windows a day, $47 a day at 64 KiB of notes.
0208  window = "1h"
0209  
0210  # Contract C3: storage rent by byte-time. "0.04" is the loader default (runtime/notes.py),
0211  # stated here so the choice is visible: the whole 256 KiB cap costs 10,485 micro-USD, about a
0212  # cent, a day. At the default the key leaves the manifest hash unchanged.
0213  [notes]
0214  micro_per_byte_day = "0.04"
0215  
0216  [timing]
0217  cadence_sample = 200
0218  min_ratio = 3
0219  jitter_fraction = 0.2
```

### `worlds/edition2-rehearsal-3.toml`: 234-248

SHA-256: `72eed0707659e00957d3d5c9d3f9abd2e1dd3e67a7c4526a8fa4f2524c7e0e29`

```text
0234  # chars = 419 (seed system prompt) + the rendered request (world/metering.py, input_slack 1.5):
0235  #   output part            3000 * 0.13                       =   390 micro-USD
0236  #   mean request, ~50,000 chars (20,636 mean input tokens in docs/audits/v3/rehearsal-final.md
0237  #     at ~2.4 chars a token): est_in 75,692 * 0.03 = 2,271;   ceiling 2,661; three calls  7,983
0238  #   largest request, ~120,000 chars (52,832 max input tokens in docs/audits/v3/accelerated.md,
0239  #     the 28,127 micro-USD antagonist reservation): 180,692 * 0.03 = 5,421; ceiling 5,811;
0240  #     three calls 17,433
0241  # 0.05 USD = 50,000 micro-USD covers three cheapest-seat calls at the largest measured request
0242  # 2.9 times over, four calls of a GLM-tier seat at the mean request (11,854 to 12,104 each),
0243  # and is 1.9% of a seat's 2,666,666 genesis share, an eighth of its $0.39 rehearsal day.
0244  # The old "0.10" was a fee set before the trial became an endowment; it is not needed.
0245  trial_amount_usd = "0.05"
0246  forecast_horizon_events = 10
0247  adversarial_share = 0.15
0248  sibling_share = 0.5
```

## Proposed engineering order

1. Preserve full norm text and introduce actual state access control.
2. Add resumable, budgeted sessions with durable workspaces and attributed outcome delivery.
3. Broaden observable consequences without making peer approval or self-authored predicates cash.
4. Add research, replay, data collection, and typed chain-read capabilities.
5. Fund complete frontier episodes; retain replaceable evaluation scaffolding and mature-feedback timing.
6. Demonstrate end-to-end capability and authority boundaries before funding an autonomous live run.

No trading frequency, profit target, registration count, or novelty quota is a launch success criterion.
