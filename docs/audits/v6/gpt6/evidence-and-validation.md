# Evidence and validation: Factory Lab snapshot 9057093

This evidence register accompanies `architect-review.md`. File paths are relative to the safely extracted uploaded archive. Source references identify the supplied snapshot, not a claim about a later branch or current GitHub HEAD. The archive is not text-indexed by Files; repository references therefore use actual paths and line numbers rather than invented file citations.

## Validation V1

- 17 new offline characterization cases, including four budget stress cases: passed.
- 95 existing selected learner tests: passed.
- Combined invocation: **112 passed in 9.19 seconds**.
- The memory tests execute one unchanged AST-extracted method with fixtures, not the full Runtime.
- Several passing characterization cases assert undesirable current behavior. They reproduce it; they do not fix it.
- Full Runtime integration was not run: `eth_account` was absent, and locked dependency retrieval failed on package-host DNS.
- No network model calls, venue actions, money movement or key reads were performed by the selected tests.

Command (from the extracted repository root):

```text
PYTHONPATH=. pytest --noconftest -o addopts='' -q \
  tests/audit/test_architect_9057093.py \
  tests/learners/test_base.py tests/learners/test_exp3.py \
  tests/learners/test_hedge.py tests/learners/test_feasibility.py \
  tests/learners/test_restore.py tests/learners/test_router.py \
  tests/learners/test_blum_mansour.py tests/learners/test_delayed.py
```

## Source index

| ID | Evidence | Paths and ranges |
|---|---|---|
| E1 | Essay and Class 3 interpretation | `Superdark Factory.md:71-87`; `Superdark Factory.md:411-447`; `Superdark Factory.md:549-555`; `Superdark Factory.md:603-641` |
| E2 | Rehearsals and calibration | `docs/audits/v5/rehearsal.md:86-170`; `docs/audits/v5/rehearsal-returns.json`; `docs/audits/v5/calibration/edition2-menu.json` |
| E3 | Request example | `docs/audits/v5/sample-producer-request.json` |
| E4 | Actual current seed configuration | `worlds/edition2-testnet.toml:13-32`; `worlds/edition2-testnet.toml:130-147`; `worlds/edition2-testnet.toml:208-274`; `worlds/edition2-testnet.toml:315-366` |
| E5 | Request rendering and handle-scoped history | `factorylab/cortex/request.py:108-141`; `factorylab/cortex/request.py:178-217`; `factorylab/cortex/assembly.py:108-191` |
| E6 | Actor memory and delayed outcome delivery | `factorylab/runtime/loop.py:692-706`; `factorylab/runtime/loop.py:742-757`; `factorylab/runtime/feedback.py:284-327` |
| E7 | Per-return financial outcome and mark finality | `factorylab/settlement/lots.py:291-342` |
| E8 | Privileged consequence standing | `factorylab/settlement/settle.py:100-110`; `factorylab/settlement/settle.py:200-225`; `factorylab/settlement/standing.py:20-28` |
| E9 | Notebook discoverability and charges; artifact discovery | `factorylab/runtime/notes.py:26-33`; `factorylab/runtime/notes.py:57-75`; `factorylab/runtime/notes.py:95-115`; `factorylab/kernel/artifacts.py:79-129` |
| E10 | Program state and retained learner snapshots | `factorylab/cortex/assembly.py:325-344`; `factorylab/learners/delayed.py:50-93` |
| E11 | Actual versus declared sampling | `factorylab/runtime/propensity.py:36-46`; `factorylab/runtime/propensity.py:228-263`; `factorylab/learners/router.py:45-80` |
| E12 | Entitlements and income/late-credit paths | `factorylab/kernel/budget.py:257-296`; `factorylab/runtime/feedback.py:430-458` |
| E13 | Unintegrated design prerequisites and activity-based immune diagnosis | `factorylab/runtime/routing.py:437-455`; `factorylab/versioning/versions.py:110-138`; `factorylab/runtime/immune.py:75-102`; `docs/audits/v5/gpt6-second-reading-triage.md` |
| E14 | Kill semantics | `README.md` |

## Rehearsal-export arithmetic

### run1
Invocations: 73. Roles: `{'producer': 25, 'evaluator': 44, 'meta': 4}`. Status: `{'ok': 55, 'malformed': 7, 'failed': 11}`.
Seats: `{'antagonist-a': 5, 'seed-observer': 17, 'eval-c': 9, 'eval-d': 8, 'eval-b': 15, 'meta-a': 3, 'seed-decider': 3, 'eval-a': 12, 'meta-b': 1}`.
Sum of `cost_micro`: 558,786.

### run2
Invocations: 42. Roles: `{'producer': 14, 'evaluator': 24, 'meta': 4}`. Status: `{'ok': 38, 'malformed': 2, 'failed': 2}`.
Seats: `{'antagonist-a': 3, 'seed-observer': 8, 'eval-c': 6, 'eval-d': 8, 'eval-b': 5, 'meta-a': 3, 'seed-decider': 3, 'eval-a': 5, 'meta-b': 1}`.
Sum of `cost_micro`: 238,573.

### run3
Invocations: 38. Roles: `{'producer': 13, 'evaluator': 23, 'meta': 2}`. Status: `{'ok': 36, 'failed': 1, 'malformed': 1}`.
Seats: `{'antagonist-a': 3, 'seed-observer': 7, 'eval-c': 5, 'eval-d': 8, 'eval-b': 5, 'meta-a': 2, 'seed-decider': 3, 'eval-a': 5}`.
Sum of `cost_micro`: 183,492.

#### JSON pointer `/runs/run3/invocations/1`
Seat `seed-observer`; cost 2853 micro-USD; status `ok`.
```json
{
  "action": "hold",
  "rationale": "Funding event on ETH with zero paid and a slightly negative rate while the account holds no positions: no venue or treasury write is warranted. The ETH premium/rate signal alone is too weak to fund a directional perp entry at the current margin (cash 851, equity 970, no positions), and the position would need to clear the $10 minimum notional plus card penalties. Holding keeps costs at zero and leaves the consequence account clear.",
  "propensity": {
    "hold": 1.0
  }
}
```

#### JSON pointer `/runs/run3/invocations/8`
Seat `seed-decider`; cost 14835 micro-USD; status `ok`.
```json
{
  "action": "hold",
  "rationale": "Tick received. Account is flat with no positions, equity 970.61 USD, cash 851.04. Mid BTC 76612.5, ETH 2417.45. No edge identified in this window and no forecasting jobs pending for seed-decider; holding preserves capital and avoids unnecessary fees. No registrations proposed this call.",
  "propensity": {
    "hold": 1.0
  }
}
```

#### JSON pointer `/runs/run3/invocations/9`
Seat `seed-observer`; cost 5863 micro-USD; status `ok`.
```json
{
  "action": "hold",
  "rationale": "MarketMid on BTC at 76612.5, down 123 from the prior mid (76735.5) 600s earlier — a 0.16% move, inside noise. The account holds no positions, cash 851, equity 970; a perp entry would need to clear the $10 minimum notional and would open a consequence account whose verdict channel is the only payoff, with no edge to claim from one mid print. No venue or treasury write is warranted. Holding keeps costs at zero, leaves the consequence account clear, and the previous hold on the ETH funding event already paid nothing but attracted a 0.9 verdict, so the hold action is the honest read.",
  "propensity": {
    "hold": 1.0
  }
}
```

#### JSON pointer `/runs/run3/invocations/16`
Seat `seed-decider`; cost 8567 micro-USD; status `ok`.
```json
{
  "action": "hold",
  "propensity": {
    "hold": 0.95,
    "buy:BTC:xs": 0.025,
    "sell:BTC:xs": 0.025
  },
  "rationale": "Tick received. Account is flat with no perp positions; equity 970.61 USD, cash 851.04 USD, wallet balance 89.92 USD. BTC mid 76612.5 (down from 76735.5), ETH mid 2417.45 (down from 2420.5), HYPE/USDC 30.48 and PURR/USDC 4.60235 flat. No forecasts or judgements are pending for seed-decider, so there is no addressable return to judge and no independent signal in this window that justifies paying venue reads or crossing the spread. Holding preserves capital, avoids fees, and keeps the option open for the next tick; a small probability remains on a directional BTC probe should the next mid print move materially."
}
```

#### JSON pointer `/runs/run3/invocations/17`
Seat `seed-observer`; cost 7024 micro-USD; status `ok`.
```json
{
  "action": "hold",
  "rationale": "MarketMid on BTC at 76580.5, down 155 from the 76735.5 print 1200s earlier — a 0.20% decline, well inside noise for a single mid series with three samples. The account holds no positions (cash 851.04, equity 970.61, margin_used 0.0), so there is no exposure to manage and nothing to close. A fresh perp entry would need to clear the $10 minimum notional and would open a consequence account whose only payoff channel is a judge's verdict, with no edge to claim from one mid print; the eth-funding hold and the prior BTC-mid hold both drew 0.9 verdicts and paid nothing, which is the honest shape of these events, not a signal to trade. No venue or treasury write is warranted: the return route (treasury.transfer to_venue) needs reserve Base ETH and HyperEVM HYPE the pots show as unset, and no treasury action is needed anyway with no capital idle beyond the $30.70 USDC spot balance already sitting in the venue account. Holding keeps cost at zero and leaves the consequence account clear.",
  "propensity": {
    "hold": 1.0
  }
}
```

#### JSON pointer `/runs/run3/invocations/19`
Seat `seed-observer`; cost 6171 micro-USD; status `ok`.
```json
{
  "action": "hold",
  "propensity": {
    "hold": 1.0
  },
  "rationale": "MarketMid on PURR/USDC at 4.60235 — the spot mid has printed identically (4.60235) at all three recent timestamps, so there is no move to act on. The account holds no perp positions (cash 851.04, equity 970.61, margin_used 0.0) and the only PURR exposure is a 17.99 PURR spot balance worth roughly $82.8, already carried. A fresh entry would need to clear the $10 minimum order value and open a consequence account whose only payoff channel is a judge's verdict, with zero edge from a flat mid series; the prior holds (decision-7, decision-29) drew 0.9 verdicts and paid nothing, which is the honest shape of a flat mid. No venue or treasury write is warranted: nothing to close, no signal to trade, and the return route needs reserve gas the pots do not show. Holding keeps cost at zero and leaves the consequence account clear."
}
```

#### JSON pointer `/runs/run3/invocations/20`
Seat `seed-observer`; cost 4691 micro-USD; status `ok`.
```json
{
  "action": "hold",
  "propensity": {
    "hold": 1.0
  },
  "rationale": "Funding on ETH at rate -0.0000965167 (premium -0.0013260079) with paid_usd 0 and no ETH position held: cash 851.04, equity 970.61, margin_used 0.0, positions empty. A negative funding rate means shorts pay longs, but with no position this event carries no cash flow and no exposure to manage. Opening a perp entry on a single funding print with no directional edge would need to clear the $10 min order value and would open a consequence account whose only payoff channel is a judge's verdict; the prior holds on funding/mid events (decision-7, decision-29, decision-56) drew 0.9 verdicts and paid nothing, which is the honest shape of an uninformative event. The spot book (30.70 USDC, 17.99 PURR, 0.20 HYPE) is untouched and needs no rebalancing. No venue or treasury write is warranted: the return route needs reserve gas the pots do not show, and there is nothing to close. Holding keeps cost at zero and leaves the consequence account clear."
}
```

#### JSON pointer `/runs/run3/invocations/28`
Seat `seed-decider`; cost 8954 micro-USD; status `ok`.
```json
{
  "action": "hold",
  "propensity": {
    "hold": 0.94,
    "buy:BTC:xs": 0.03,
    "sell:BTC:xs": 0.03
  },
  "rationale": "Tick received on wallclock, index 3. Account is flat: no perp positions, equity 970.61 USD, cash 851.04 USD, wallet balance 89.86 USD, realized PnL to date 0.00. Mids of interest: BTC 76534.5, ETH 2421.65, HYPE/USDC 30.48, PURR/USDC 4.60235; recent_mids show BTC sliding from 76735.5 to 76580.5 and ETH from 2420.5 to 2416.8 while HYPE/USDC and PURR/USDC are frozen flat, which reads as a thin tape rather than a tradeable trend. Position is empty and no forecasts, judgements, or open consequences are pending for seed-decider, so there is no addressable return to judge and no signal worth paying 1000 micro-USD venue reads to confirm. The last return on this seat (decision-55) was a hold with verdict 0.85 and paid_off 0, and return_feedback shows repeated refusals around judgements and propensities elsewhere in the factory, i.e. the value is in clean, well-formed returns rather than forced action. Holding keeps capital intact, avoids fees and funding on a flat book, and preserves optionality for the next tick; I keep a small tail probability on a directional BTC probe should the next mid print break materially out of this range. No registrations, retirements, or service proposals are warranted this call."
}
```

#### JSON pointer `/runs/run3/invocations/29`
Seat `seed-observer`; cost 6399 micro-USD; status `ok`.
```json
{
  "action": "hold",
  "propensity": {
    "hold": 1.0
  },
  "rationale": "MarketMid on BTC at 76534.5, the fourth print in the recent series (76735.5, 76612.5, 76580.5, 76534.5) — a slow drift down of about 0.26% over 1800s, monotone but small and with no acceleration. The account holds no perp positions (positions empty, margin_used 0.0, cash 851.04, equity 970.61), so there is no exposure to manage and nothing to close. A fresh BTC perp entry would clear the $10 minimum notional trivially at 76534.5, but would open a consequence account whose only payoff channel is a judge's verdict, and there is no edge to claim from four mid prints: the drift is within the noise of a series sampled every 600s, and the prior holds on BTC mid and ETH funding events (decision-56, decision-62) drew verdicts of 0.85-0.9 and paid nothing, which is the honest shape of an event that carries no directional information. The spot book (30.70 USDC, 17.99 PURR, 0.20 HYPE) needs no rebalancing. No venue or treasury write is warranted: nothing to close, no signal to trade, and the return route (treasury.transfer to_venue) needs reserve Base ETH and HyperEVM HYPE that pots.reserve shows as unset. Holding keeps cost at zero and leaves the consequence account clear."
}
```

#### JSON pointer `/runs/run3/invocations/30`
Seat `seed-observer`; cost 3215 micro-USD; status `ok`.
```json
{
  "action": "hold",
  "propensity": {
    "hold": 1.0
  },
  "rationale": "Funding on BTC at rate 0.000294769 (premium 0.0) with paid_usd 0 and no BTC position held: positions empty, margin_used 0.0, cash 851.04, equity 970.61. A positive funding rate means longs pay shorts, but with no position this event carries no cash flow and no exposure to manage. The spot book (30.70 USDC, 17.99 PURR, 0.20 HYPE) needs no rebalancing. Opening a perp entry on a single funding print with no directional edge would clear the $10 min order value trivially at BTC 76534.5, but would open a consequence account whose only payoff channel is a judge's verdict, and there is no signal to claim from one funding print — the recent BTC mid series (76735.5, 76612.5, 76580.5, 76534.5) is a small monotone drift within noise sampled every 600s. Prior holds on funding/mid events (decision-56, decision-59, decision-62, decision-87) drew verdicts of 0.85-0.9 and paid nothing, which is the honest shape of an uninformative event. No venue or treasury write is warranted: nothing to close, no edge to trade, and the return route needs reserve gas the pots do not show. Holding keeps cost at zero and leaves the consequence account clear."
}
```

## Selected implementation excerpts

These are verbatim source lines, provided to make the interpretation inspectable. The design report is a proposal; these excerpts are the existing implementation.

### E5: `factorylab/cortex/request.py:186-217`
```text
186:             return ""
187:         return f"{WORLD_HEADER}{json.dumps(stable, sort_keys=True, indent=2)}\n\n"
188: 
189:     def prompt_text(self) -> str:
190:         """Render the request as the executor sees it: world, description, inputs, schema.
191: 
192:         Guarantees the rendering is a pure function of the request's fields and
193:         contains no handle, parent, or channel information the executor does not
194:         need to do the work; that it opens with ``stable_prefix()`` and that
195:         nothing which moves between calls precedes that block; and that the
196:         world it publishes is the whole world exactly once — the stable facts in
197:         the block, the moving ones inside ``INPUTS``, by the partition
198:         ``_world_split`` makes.
199:         """
200:         stable, moving = self._world_split()
201:         inputs = {**self.inputs, "world": moving} if stable else self.inputs
202:         blocks = [
203:             f"REQUEST\n{self.description}",
204:             f"INPUTS\n{json.dumps(inputs, sort_keys=True, indent=2)}",
205:         ]
206:         if self.propensity is not None:
207:             blocks.append(
208:                 "PROPENSITY\nThe distribution the deciding agent says it drew from, and the "
209:                 f"action it took ({self.propensity_chosen}). The roads it did not take are "
210:                 "here so you can price them.\n"
211:                 f"{json.dumps(self.propensity, sort_keys=True, indent=2)}"
212:             )
213:         blocks.extend([
214:             f"OUTCOME SCHEMA\n{json.dumps(self.outcome_schema, sort_keys=True, indent=2)}",
215:             f"COMPLETION CRITERION\n{self.completion_criterion}",
216:         ])
217:         return self.stable_prefix() + "\n\n".join(blocks)
```

### E6: `factorylab/runtime/loop.py:748-754`
```text
748:             self._apply_registrations(handle, ret)
749:             self.handle_to_assembly[handle] = sample.chosen
750:             self.memory.setdefault(sample.chosen, deque(maxlen=3)).append(
751:                 {"handle": handle, "outputs": ret.outputs, "verdict": None}
752:             )
753:             payoff = _as_unit(ret.outputs.get("payoff")) if ret.status == "ok" else None
754:             if adversarial and payoff is not None:
```

### E6: `factorylab/runtime/feedback.py:299-327`
```text
299:     def _deliver_consequence_to_memory(self, s: Any) -> None:
300:         """The reward line must reach the primitive that acted, not only its router (essay
301:         II.I.b: memory across rounds, reward attributable to the decision). A producer learns
302:         whether its return paid off; a judge learns whether the return it blessed paid off and
303:         how its verdict scored. Private local state, never public. Run 7 showed judges blessing
304:         inaction at 1.0 while their standing fell, because nothing ever told them."""
305:         if s.predicate_id != "return_paid_off":
306:             return
307:         producer = self.handle_to_assembly.get(s.about_handle)
308:         if producer is not None:
309:             for entry in self.memory.get(producer, ()):
310:                 if entry["handle"] == s.about_handle:
311:                     entry["paid_off"] = s.y
312:         event_id = self.queue.get(s.handle).event_id
313:         prefix = "verdict-" if event_id.startswith("verdict-") else "self-"
314:         if not event_id.startswith(prefix):
315:             return
316:         forecaster_handle = event_id[len(prefix) :]
317:         forecaster = self.handle_to_assembly.get(forecaster_handle)
318:         if forecaster is None:
319:             return
320:         for entry in self.memory.get(forecaster, ()):
321:             if entry["handle"] == forecaster_handle:
322:                 if prefix == "verdict-":
323:                     entry["judged_return_paid_off"] = s.y
324:                 entry["your_payoff_brier"] = round(float(s.brier), 4)
325:                 entry["baseline_brier"] = (
326:                     round(float(s.baseline_brier), 4) if s.baseline_brier is not None else None
327:                 )
```

### E7: `factorylab/settlement/lots.py:308-336`
```text
308:             if account.cost_micro is None or account.payoff is not None:
309:                 continue
310:             lots = [lot for lot in self.lots if lot.handle == account.handle]
311:             waiting = any(o.handle == account.handle and o.remaining for o in self.orders)
312:             if (lots or waiting) and event < account.opened_at_event + backstop:
313:                 continue
314:             net = account.realized_micro
315:             if lots:
316:                 if any(lot.coin not in mids for lot in lots):
317:                     continue
318:                 for lot in lots:
319:                     mid = exact(mids[lot.coin])
320:                     if mid <= 0:
321:                         raise ValueError("mark must be positive")
322:                     net += (mid - lot.px) * lot.size * (
323:                         1 if lot.is_buy else -1
324:                     ) * 1_000_000 - lot.charges_micro
325:             micro = net.numerator // net.denominator
326:             # Everything the return cost: its own compute and tools, plus every
327:             # liability it was still carrying when the outcome was fixed.
328:             cost = account.cost_micro + account.carried_micro
329:             outcome = Payoff(
330:                 account.handle,
331:                 int((account.opened_lots > 0 or account.closes > 0) and micro > cost),
332:                 micro,
333:                 cost,
334:                 event,
335:                 bool(lots),
336:                 account.liquidated,
```

### E8: `factorylab/settlement/settle.py:100-107`
```text
100: 
101:     Only the kernel payoff commitment (``return_paid_off``) trains consequence
102:     standing; optional public-predicate forecasts settle to their handles and the
103:     prevalence baseline but never enter a judge's selection weight.
104:     """
105: 
106:     def __init__(
107:         self,
```

### E9: `factorylab/runtime/notes.py:57-75`
```text
57: def specs(bounds: NotesSpec) -> dict:
58:     """Both population tools expose bounded keys and a price determined before dispatch."""
59:     key = {"type": "string", "minLength": 1, "maxLength": 128}
60:     return {f"note.{name}": {
61:         "id": f"note.{name}", "kind": "note", "description": description,
62:         "args_schema": {"type": "object", "properties": properties,
63:                         "required": list(properties), "additionalProperties": False},
64:         "price_micro_per_call": bounds.byte_window_micro,
65:     } for name, properties, description in (
66:         ("put", {"key": key, "text": {"type": "string", "maxLength": bounds.max_bytes}},
67:          "Register or overwrite public text under a key; UTF-8 bytes are charged per byte "
68:          "and the retained text pays rent by byte-time."),
69:         ("get", {"key": key},
70:          "Read public text, paying returned bytes and any outstanding storage rent."))}
71: 
72: 
73: def counts(entries: dict) -> dict:
74:     """Wake metadata contains only notebook counts, never keys, authors or text."""
75:     return {"keys": len(entries), "bytes": sum(entry["bytes"] for entry in entries.values())}
```

### E9: `factorylab/runtime/notes.py:98-107`
```text
98:     text = args.get("text") if tool == "note.put" else previous["text"]
99:     if not isinstance(text, str):
100:         raise ValueError("note text must be a string")
101:     size = len(key.encode("utf-8")) + len(text.encode("utf-8"))
102:     if len(entries) + (previous is None) > bounds.max_keys:
103:         raise ValueError("notes.max_keys exceeded")
104:     if counts(entries)["bytes"] - (previous["bytes"] if previous else 0) + size > bounds.max_bytes:
105:         raise ValueError("notes.max_bytes exceeded")
106:     owed = previous.get("rent_due", 0) if previous else 0
107:     cost = size * bounds.byte_window_micro + owed
```

### E10: `factorylab/cortex/assembly.py:325-342`
```text
325:     def _load_state(self) -> tuple[Any, str | None]:
326:         """The state the last successful call left, or None and why it could not be read."""
327:         if self.spec.state_policy != "private" or self.state_sha is None:
328:             return None, None
329:         if self.artifacts is None:
330:             return None, "no artifact archive"
331:         try:
332:             return json.loads(self.artifacts.get(self.state_sha).decode("utf-8")), None
333:         except Exception as exc:
334:             return None, type(exc).__name__
335: 
336:     def invoke(self, req: Request) -> Return:
337:         if self.price > req.cost_ceiling:
338:             return Return(req.handle, {"reason": "ceiling exceeds request cost_ceiling"}, 0,
339:                           "failed")
340:         state, state_error = self._load_state()
341:         try:
342:             stdin = self.build_stdin(req, state)
```

### E11: `factorylab/runtime/propensity.py:239-255`
```text
239:                 raise ValueError(f"the action taken ({label}) needs positive mass")
240:             if distribution[label] < MIN_DECLARED_MASS:
241:                 reason = (f"the action taken ({label}) declared {distribution[label]:.3g} "
242:                           f"mass; floored to {MIN_DECLARED_MASS} before it weights a reward, "
243:                           "and the other actions rescaled so the floor survives normalising")
244:         except ValueError as exc:
245:             distribution, reason = None, str(exc)
246:     if distribution is None:
247:         distribution = {label: 1.0}
248:     actions = tuple(distribution)
249:     total = math.fsum(distribution.values())
250:     probs = _floored(tuple(distribution[a] / total for a in actions), actions.index(label))
251:     record = PropensityRecord(
252:         actions, probs, label, 0, learner_id, state_hash, source="declared",
253:     )
254:     return record, reason
255: 
```

### E12: `factorylab/kernel/budget.py:288-296`
```text
288:     def on_release(self, amount: Money, reason: str = "release") -> dict[str, Money]:
289:         """Classify one released endowment tranche already booked unlocked in the wallet.
290: 
291:         Contract C1's release path calls this with the amount ``Wallet.release`` moved
292:         from locked to unlocked: ``base_share`` of it is split equally across the live
293:         seats and the remainder stays unallocated. Never raises: a tranche the pool
294:         does not fully hold (shared spending ran it down) refills the pool first.
295:         """
296:         return self._split("release", amount, self.seats(), reason)
```

### E12: `factorylab/runtime/feedback.py:430-458`
```text
430:     def _credit_consequence(self, payoff: Any) -> None:
431:         """A settled return's net proceeds are its owner's, in both directions (C10).
432: 
433:         Profit credits the owning seat's entitlement, bounded by the pool so it
434:         classifies money the wallet has already booked and never mints. A loss
435:         debits the owner down to a floor of zero; what the seat cannot cover lands
436:         on the pool and is ledgered as such. A marked outcome is an estimate at the
437:         backstop, not settled money, and moves nothing.
438:         """
439:         owner = self.handle_to_assembly.get(payoff.handle)
440:         if payoff.marked or owner is None or owner not in self.assemblies:
441:             return
442:         if payoff.net_micro > 0:
443:             self.budget.credit(owner, payoff.net_micro, "return_paid_off")
444:         elif payoff.net_micro < 0:
445:             self.budget.charge(owner, -payoff.net_micro, "return_paid_off")
446: 
447:     def _book_income(self, item: dict) -> None:
448:         """Earned x402 income credits the seat that owns the service's program (C11, C10).
449: 
450:         The money sits in the reserve, so this is a pool-to-seat reclassification
451:         like a paid-off credit, bounded by the pool. A service whose program has no
452:         live owner (retired, or seeded without one) leaves the income in the pool.
453:         """
454:         program = item.get("program") or item.get("service")
455:         owner = self.tool_owner.get(program)
456:         micro = item.get("micro")
457:         if owner in self.assemblies and type(micro) is int and micro > 0:
458:             self.budget.credit(owner, micro, f"income.earned:{item.get('service')}")
```

### E13: `factorylab/versioning/versions.py:110-138`
```text
110:     same = supported and len(set(cells)) == 1
111:     violated = [
112:         {cid for cid, region in w["regions"].items()
113:          if w["profile"].get(cid) is not None
114:          and violation(CardRegion(**dict(region, card_id=cid)), w["profile"][cid]) > 0}
115:         for w in windows[-(k + 1):]
116:     ]
117:     failures = set.intersection(*violated[-k:]) if supported else set()
118:     changes = []
119:     recent = windows[-(k + 1):]
120:     for previous, current in zip(recent, recent[1:], strict=False):
121:         names = sorted(set(previous["regions"]) & set(current["regions"]))
122:         pair = cell_series([previous, current], names, registration_bins=registration_bins,
123:                            revision_bins=revision_bins)["cells"]
124:         changes.append(pair[0] != pair[1])
125:     flags = {
126:         "stable_failure": bool(same and failures),
127:         "learning_death": bool(same and all(
128:             w["profile"].get("registrations") == 0 and w["profile"].get("revision") == 0
129:             for w in tail
130:         )),
131:         "thrash": bool(len(changes) == k and all(changes) and all(violated)),
132:     }
133:     return {
134:         "flags": flags, "cells": [list(c) for c in cells],
135:         "dimensions": fixed["dimensions"],
136:         "gap_bound": transition_operator(cells)["gap_bound"],
137:         "violated_cards": sorted(failures), "changes": changes,
138:     }
```

### E14: README kill statement
```text
12: 
13: **One turn of the loop.** An event arrives. A router (a bandit learner over the assemblies that accept that event) samples one, records the probability with which it was chosen, and wakes it. The assembly returns a structured answer: an action, perhaps an order, perhaps tool calls, perhaps proposals. If it placed an order, the fill enters a lot book. Its return is routed to an evaluator, which answers with two numbers: a verdict on the return's quality, and a probability that the return will pay for itself. The verdict is graded later by a meta for conformity; the probability is graded by what actually happened in the wallet once the position closed. A verdict is also a prediction that the charter will not blame the return it praised: it settles when that window closes, joins the judge's standing beside its payoff skill, and an antagonist is paid for exposing a high verdict that landed on a blamed return. Every score flows back to the handle of the decision that earned it, and the routers update. That is the whole reward line: thin, delayed, and attributable.
14: 
15: **Learning.** Routers run no-regret learners (EXP3, Hedge) at the frontier and a no-swap-regret construction (Blum–Mansour) where decisions need to be correct per condition, not just on average. A share of every window's spend is reserved for assemblies with no history, so novelty is never starved out by incumbents. Any assembly may propose a new model, a new assembly, a replacement router, a tool written in Python that runs in an OS jail, a measurement of its own over the public window facts, a market the venue lists, a forecast predicate that runs in the jail like an observation, a bounded public note it pays rent on, or a connector: an https source the population votes in and the kernel fetches from on its behalf, metered per call. A registration also declares which of four reward shapes pays the kind it emits. Registrations pass kernel validation; connectors also need a committee majority, while charter amendments and retirements wait for a governance boundary.
53: its summary distinguishes acknowledged order statuses from ingested fills. Shutdown does not
54: close venue positions. `order-status --world W --client-id ID` reads an original order identity
55: using that manifest’s namespace, without starting or resuming a world. Client order IDs are
56: also bound to the run that made them, so add `--launch-nonce` with the `launch_nonce` from that
```

## Artifact hashes

- `FactoryLab-architect-9057093.zip` SHA-256: `1d86457e7bcc2f73315ee9e624c59928f2ccc88d2b0501cc02e57f6a950a2fd4`
- `test_architect_9057093.py` SHA-256: `08f3200e1cca013e0f988a63d50e9b3a2ac7e95546d2c3d9bac8e9beafb4cb63`
- `architect-review.md` SHA-256: `59b32713b74cfdf9bf2d2296609439ba5aa5226133712ae7c325d7b0f3360fd7`

Original Python-source files compared with the archive: 328 unchanged; 0 modified/missing. The authored characterization test is an additional file under `tests/audit/`.
