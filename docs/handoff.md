# State on main and what remains before launch

## 15 September, early morning: edition 2 is on main

The outside review (GPT-6 Pro, `docs/audits/v4/gpt6/`, triaged in
`docs/audits/v4/gpt6-triage.md`) said do not launch `3a27fa4`, and proposed a different edition.
The experimenter chose to build it. `docs/plans/edition2.md` froze thirteen contracts; nine
workstreams and three repair passes (pull requests #69 to #82) built them overnight, each
reviewed on its diff and merged after the full gate. Main now holds, with the reference in
`docs/manifest.md` updated to match:

- **Programs as seats** (C8, C9): a seat can be jailed code, woken, routed, judged, paid and
  retired like a model, with private state in a content-addressed artifact archive that outlives
  its author; `artifact.get` is a seeded free tool.
- **Per-seat entitlement** (C10): one conserved wallet, classified into a locked endowment, an
  unallocated pool and one entitlement per seat; every call is metered against its seat, a
  child's trial comes from its proposer, settled gains credit and losses debit the owner to a
  floor of zero, x402 income credits the service's owner; routing prices the real rendered
  request and bridges a stale estimate from the pool rather than failing a return.
- **Endowment schedule and dormancy** (C1, C2): locked backing released on a committed schedule
  from the ledgered Launch; when the unlocked money cannot buy the cheapest seat and a release is
  still due, the world is `budget_dormant`: no paid cognition, maintenance continues, ballots wait.
- **Rent by byte-time** (C3): `notes.micro_per_byte_day`, default four hundredths; the two-minute
  window no longer drains the wallet.
- **Hard casts** (C4, C5): the release digest is in the Launch event and resume refuses another;
  every kill writes a witness line beside the runs directory and POSTs it when a URL is set, and
  resume refuses a killed identity from a restored copy or an old checkpoint; Venice confirms a
  purchase on the on-chain debit with the balance as advice.
- **Grading** (C6): judges scored against a baseline on the same fractional target;
  `cost_per_attempt` counts failures; tool discipline is per return; a lot's P&L is credited once;
  generic blame has a floor.
- **Metric challenge** (C7): a card can be challenged by a replacement measured beside it for a
  trial; the ballot sees the evidence and both series; the incumbent cannot veto its challenger.
- **Service seller** (C11): a registered tool can be sold over x402 from the droplet; income is
  ledgered `income.earned`; the wake shows earned, subsidy and principal-conversion apart, plus
  money by class, deliveries, open commitments, cells, liveness and entitlements: the architect
  can watch without pushing.
- **Charter edition 2** (C12): five norms (the reviewer's four plus fidelity) and three cards
  (consequence paid off, forecast skill, censorship bound); the quota, frugality and concentration
  cards gone, re-ratified by the seeded committee of the testnet roster on 15 September
  (`docs/charter/edition2-ratification.json`; the eight-card ballot is under `docs/charter/history/`).
- **Seat calibration** (C13): `scripts/calibrate_seats.py` runs candidates through the real
  contracts under a hard budget.
- **The twelve-step slice** (`tests/audit/test_edition2_slice.py`): the reviewer's vertical slice
  end to end, 21 steps green, including crash and resume at three points and kill finality.

The gate on main is green: `uv run pytest` 2,916 passed (`af103a0`; one witness test fails only
when HEAD moves during the run). The reviewer zip for the second reading is
`~/Downloads/FactoryLab-edition2-af103a0.zip` with `docs/audits/v5/brief-gpt6-edition2.md`.
Two testnet rehearsals at the ten-minute tick and the paid menu calibration are in
`docs/audits/v5/rehearsal.md`. The edition 2 testnet manifest is `worlds/edition2-testnet.toml`
(`docs/launch-decisions.md`, "Edition 2"); the funded manifest derives from it.

## What is merged

Everything from three audit rounds. Round three (`docs/audits/v3/`) closed 57 of its 60 triage rows across twenty pull requests (#47 to #66), four found by the live rehearsal (`docs/audits/v3/rehearsal.md`) and four by the cold review of the unreviewed pull requests (`docs/audits/v3/review-58-64.md`) and built the six decisions in `docs/audits/v3/fix-plan.md`: a kill command; a two-minute tick with a 60-event consequence horizon; judges answer for their verdicts against the charter's own blame; every proposal names the card it promises to improve and its voters are liable for it; the population may register new kinds of work, its own forecast predicates and one of four reward shapes; the population may read the venue's public data for any listed coin, register markets, pay for data through x402, and keep a metered public notebook. Two rows are known by decision (testnet rehearses on the seed charter; the crash world's overshoot is its demonstration) and one is open: T47, the launch roster's well-formed rate. The first live re-check (`docs/audits/v3/recheck.md`) measured it at 0.87 against the charter's 0.9 floor, with 160k input tokens and $0.025 per call because the whole venue listing was in every prompt. PR #66 took the listing out (reachable through `venue.instruments`) and opened every prompt with a byte-stable block so provider prefix caching hits per assembly; `tencent/hy3` left the roster and eval-c sits on DeepSeek 4.1 flash. The second re-check (`docs/audits/v3/recheck2.md`) measured 21,825 input tokens and $0.0035 per call (7.2 times cheaper, about $21 a day at the declared tick), a well-formed rate of 0.94 over 63 calls, cache hits on 30% of calls (DeepSeek and Qwen 3.8 hit; GLM rarely; GPT 5.6 luna and Qwen 3.7 never), zero length stops and zero provider faults; no price window closed in 14 minutes so T47's card value is still unmeasured, and tick timing was contaminated by the concurrent gate. The well-formed and cost cards use a 100-returns-per-role window, so they price roughly three hours behind the observation. `docs/audits/v3/closure.md` is the cold closure review; `docs/manifest.md` describes the code as it is.

## The gate

```
uv run pytest
2916 passed (12:35, main af103a0)
uv run pytest -m slow -o addopts="" tests/runtime/test_resume.py
30 passed (2:13)
```

Only the experimenter runs the gate; agents run targeted files. Pull requests #58 to #64 were merged on real-jail verification without bot reviews; Codex then reviewed them cold and its four findings were fixed in #65 (seven Codex review rounds, mostly on note rent entering cost measurement without ever counting as a response) and #66 (three rounds, one of which caught population-authored text reaching the system role and was reverted). `tests/cache_contract_check.py`, which failed on main and was never collected, has been deleted.

## Secrets and money

`openrouter.key`, `hyperliquid.key` and `reserve.key` sit at the repository root, mode 0600, gitignored; only the CLI loads them. Never print or commit a key. In place: $100 USDC on Hyperliquid perps (classic account mode), about $100 of OpenRouter credit after the audits and rehearsals, a Base reserve of about 4.97 USDC and 4.98 Venice credit. The testnet manifest is the shipped `worlds/testnet.toml`: `PURR/USDC` spot, the real reserve address, a 120 s tick, a 60-event backstop.

## What remains, in order

Edition 3 is on main (`docs/plans/edition3.md`; PRs #90 to #94; gate 3071 passed): a seat
keeps a working state and receives its outcomes addressed to the decision that caused them;
it owns when it thinks (coalesced world update, subscriptions, defer, watchers); one card, the
norms carry their definitions, the hidden paid-off objective is gone from judge standing;
the seat sees itself (entitlement, runway, provider inventory, open commitments, directory);
GPT-6's nine seats and seed lenses; $300 backing released 120/60/60/60 and $120 of trading
principal; kill cancels, closes and sells before the world is declared dead. The charter is
ratified on this roster (`docs/charter/edition3-ratified.toml`), the manifest carries it
(`worlds/edition3-testnet.toml`, hash `781584ce…`), and rehearsal 1 ran clean
(`docs/audits/v6/rehearsal.md`).

1. **Roster.** The calibration case gate (`docs/audits/v6/calibration.md`) fails DeepSeek 4.1
   flash and Qwen 3.8 flash on funding arithmetic and fails Sol and Luna on the refusal
   shape only. Decide the roster (recommendation in the report: GLM on Venice, Luna and Sol
   on OpenRouter, four seats per provider), make the refusal contract salient in the seed
   prompt, re-screen the changed routes, re-ratify on the new roster hash
   (`scripts/ratify_charter.py`), re-pin the manifest hash.
2. **24-hour testnet rehearsal** on the final roster (`scripts/rehearsal.py preflight`, then
   `factorylab run --duration 24h --kill-at-end` on a fresh-namespace copy under `runs/`),
   read for: a seat writing its own `working_state` and finding it next wake; an outcome
   acknowledged; a subscription change or watcher; the constructor's wakes and their cost;
   burn per day. Report under `docs/audits/v6/`.
3. Fresh read-only balance read; `worlds/funded.toml` from `worlds/edition3-testnet.toml`
   with `name = "funded"`, `mainnet = true`, a client namespace, and the fresh starting
   accounting; hash recorded.
4. Droplet: `deploy/install.sh` records the release; the experimenter places the three keys,
   sets `FACTORYLAB_WITNESS_URL` (with a receiver configured a resume that cannot get its
   verdict refuses) and, if a service is to be sold, `FACTORYLAB_INCOME_SPOOL` and
   `deploy/serve.py`.
5. Launch. Then nothing changes but the one control: kill, which winds the venue down.

Deferred by decision: project funding contracts and commissioned judges (GPT-6 §8).
