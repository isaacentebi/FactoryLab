# State on main and what remains before launch

## What is merged

Everything from three audit rounds. Round three (`docs/audits/v3/`) closed 57 of its 60 triage rows across twenty pull requests (#47 to #66), four found by the live rehearsal (`docs/audits/v3/rehearsal.md`) and four by the cold review of the unreviewed pull requests (`docs/audits/v3/review-58-64.md`) and built the six decisions in `docs/audits/v3/fix-plan.md`: a kill command; a two-minute tick with a 60-event consequence horizon; judges answer for their verdicts against the charter's own blame; every proposal names the card it promises to improve and its voters are liable for it; the population may register new kinds of work, its own forecast predicates and one of four reward shapes; the population may read the venue's public data for any listed coin, register markets, pay for data through x402, and keep a metered public notebook. Two rows are known by decision (testnet rehearses on the seed charter; the crash world's overshoot is its demonstration) and one is open: T47, the launch roster's well-formed rate. The first live re-check (`docs/audits/v3/recheck.md`) measured it at 0.87 against the charter's 0.9 floor, with 160k input tokens and $0.025 per call because the whole venue listing was in every prompt. PR #66 took the listing out (reachable through `venue.instruments`) and opened every prompt with a byte-stable block so provider prefix caching hits per assembly; `tencent/hy3` left the roster and eval-c sits on DeepSeek 4.1 flash. The second re-check (`docs/audits/v3/recheck2.md`) measured 21,825 input tokens and $0.0035 per call (7.2 times cheaper, about $21 a day at the declared tick), a well-formed rate of 0.94 over 63 calls, cache hits on 30% of calls (DeepSeek and Qwen 3.8 hit; GLM rarely; GPT 5.6 luna and Qwen 3.7 never), zero length stops and zero provider faults; no price window closed in 14 minutes so T47's card value is still unmeasured, and tick timing was contaminated by the concurrent gate. The well-formed and cost cards use a 100-returns-per-role window, so they price roughly three hours behind the observation. `docs/audits/v3/closure.md` is the cold closure review; `docs/manifest.md` describes the code as it is.

## The gate

```
uv run pytest
2601 passed (11:37 with a live run alongside)
uv run pytest -m slow -o addopts="" tests/runtime/test_resume.py
30 passed (2:13)
```

Only the experimenter runs the gate; agents run targeted files. Pull requests #58 to #64 were merged on real-jail verification without bot reviews; Codex then reviewed them cold and its four findings were fixed in #65 (seven Codex review rounds, mostly on note rent entering cost measurement without ever counting as a response) and #66 (three rounds, one of which caught population-authored text reaching the system role and was reverted). `tests/cache_contract_check.py::test_marker_partition_includes_transitive_fixtures` fails on main and is never collected by the gate; fix or delete it.

## Secrets and money

`openrouter.key`, `hyperliquid.key` and `reserve.key` sit at the repository root, mode 0600, gitignored; only the CLI loads them. Never print or commit a key. In place: $100 USDC on Hyperliquid perps (classic account mode), about $100 of OpenRouter credit after the audits and rehearsals, a Base reserve of about 4.97 USDC and 4.98 Venice credit. The testnet manifest is the shipped `worlds/testnet.toml`: `PURR/USDC` spot, the real reserve address, a 120 s tick, a 60-event backstop.

## What remains, in order

`docs/launch-decisions.md` explains every remaining decision in plain language with a recommendation, the cost of each tick interval, and the launch steps with their commands. Read it first.

1. Two decisions at the experimenter's keyboard: whether to reseat or provider-pin eval-b (`qwen/qwen3.8-flash`, six OpenRouter errors in the first re-check, clean in the second), and whether to try one Muse Spark evaluator seat now that a call costs a third of a cent. A clean-machine testnet run of an hour would confirm the tick holds at 120 s and give T47's first priced window.
2. Edition 1 re-drafted with the launch roster at the experimenter's keyboard, taking the cadence and those decisions; `worlds/edition1-example.toml` still carries the old 60 s tick.
3. The first-move review: prompts, roster, charter edition, tick, pots.
4. Droplet provisioning from the pinned commit, per `deploy/README.md`; the experimenter places the keys.
5. `worlds/funded.toml`, its hash recorded; mainnet refuses any other name and any manifest without an explicit `[charter]`.
6. Launch. Then nothing changes but the one control: kill.
