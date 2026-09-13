# State on main and what remains before launch

## What is merged

Everything from three audit rounds. Round three (`docs/audits/v3/`) closed 57 of its 60 triage rows across nineteen pull requests (#47 to #65), four found by the live rehearsal (`docs/audits/v3/rehearsal.md`) and four by the cold review of the unreviewed pull requests (`docs/audits/v3/review-58-64.md`) and built the six decisions in `docs/audits/v3/fix-plan.md`: a kill command; a two-minute tick with a 60-event consequence horizon; judges answer for their verdicts against the charter's own blame; every proposal names the card it promises to improve and its voters are liable for it; the population may register new kinds of work, its own forecast predicates and one of four reward shapes; the population may read the venue's public data for any listed coin, register markets, pay for data through x402, and keep a metered public notebook. Two rows are known by decision (testnet rehearses on the seed charter; the crash world's overshoot is its demonstration) and one is open: T47, the launch roster's well-formed rate. The live re-check (`docs/audits/v3/recheck.md`) measured it at 0.87 against the charter's 0.9 floor over one closed window: 19 malformed and 7 failed returns in 202, only four of the malformed on the token limit, the rest complete JSON that failed the schema; evaluator B (six provider errors) is the worst seat, not evaluator C. The same run showed six world events per tick, a delivered tick of 179 s against the declared 120 s because every invocation carries the whole world block (160k input tokens on average), and OpenRouter spend of $5.08 in 70 minutes, about $102 a day at the delivered pace against the plan's $12 to $15; every other rail moved $0. The well-formed and cost cards use a 100-returns-per-role window, so they price roughly three hours behind the observation. `docs/audits/v3/closure.md` is the cold closure review; `docs/manifest.md` describes the code as it is.

## The gate

```
uv run pytest
2520 passed (10:55 with the machine idle)
uv run pytest -m slow -o addopts="" tests/runtime/test_resume.py
30 passed (2:13)
```

Only the experimenter runs the gate; agents run targeted files. Pull requests #58 to #64 were merged on real-jail verification without bot reviews; Codex then reviewed them cold and its four findings were fixed in #65, which the Codex bot reviewed.

## Secrets and money

`openrouter.key`, `hyperliquid.key` and `reserve.key` sit at the repository root, mode 0600, gitignored; only the CLI loads them. Never print or commit a key. In place: $100 USDC on Hyperliquid perps (classic account mode), about $100 of OpenRouter credit after the audits and rehearsals, a Base reserve of about 4.97 USDC and 4.98 Venice credit. The testnet manifest is the shipped `worlds/testnet.toml`: `PURR/USDC` spot, the real reserve address, a 120 s tick, a 60-event backstop.

## What remains, in order

1. Three decisions at the experimenter's keyboard from `docs/audits/v3/recheck.md`: which seat to reseat for T47 (recommendation: evaluator B, keep evaluator C); whether to shrink the world block per invocation before launch, which is also what brings the tick back to 120 s; and whether to accept about $100 a day for edition 1 or cut the roster's call rate.
2. Edition 1 re-drafted with the launch roster at the experimenter's keyboard, taking the cadence and those decisions; `worlds/edition1-example.toml` still carries the old 60 s tick.
3. The first-move review: prompts, roster, charter edition, tick, pots.
4. Droplet provisioning from the pinned commit, per `deploy/README.md`; the experimenter places the keys.
5. `worlds/funded.toml`, its hash recorded; mainnet refuses any other name and any manifest without an explicit `[charter]`.
6. Launch. Then nothing changes but the one control: kill.
