# State on main and what remains before launch

## What is merged

Everything from three audit rounds. Round three (`docs/audits/v3/`) closed 49 of its 52 triage rows across seventeen pull requests (#47 to #63) and built the six decisions in `docs/audits/v3/fix-plan.md`: a kill command; a two-minute tick with a 60-event consequence horizon; judges answer for their verdicts against the charter's own blame; every proposal names the card it promises to improve and its voters are liable for it; the population may register new kinds of work, its own forecast predicates and one of four reward shapes; the population may read the venue's public data for any listed coin, register markets, pay for data through x402, and keep a metered public notebook. Two rows are known by decision (testnet rehearses on the seed charter; the crash world's overshoot is its demonstration) and one is open: T47, the launch roster's well-formed rate, which needs a live measurement and the decision whether to reseat evaluator C (`tencent/hy3`). `docs/audits/v3/closure.md` is the cold closure review; `docs/manifest.md` describes the code as it is.

## The gate

```
uv run pytest
2504 passed (10:51 with the machine idle)
uv run pytest -m slow -o addopts="" tests/runtime/test_resume.py
30 passed (2:13)
```

Only the experimenter runs the gate; agents run targeted files. Codex is out of usage until 19 September; the review bots did not run on the last six pull requests, which were merged on real-jail verification and covered by the closure review.

## Secrets and money

`openrouter.key`, `hyperliquid.key` and `reserve.key` sit at the repository root, mode 0600, gitignored; only the CLI loads them. Never print or commit a key. In place: $100 USDC on Hyperliquid perps (classic account mode), about $100 of OpenRouter credit after the audits and rehearsals, a Base reserve of about 4.97 USDC and 4.98 Venice credit. The testnet manifest is the shipped `worlds/testnet.toml`: `PURR/USDC` spot, the real reserve address, a 120 s tick, a 60-event backstop.

## What remains, in order

1. The live rehearsal (`docs/audits/v3/rehearsal.md`): ten lines made true on testnet, including the well-formed rate for T47.
2. Edition 1 re-drafted with the launch roster at the experimenter's keyboard, taking the cadence and the evaluator C decision; `worlds/edition1-example.toml` still carries the old 60 s tick.
3. The first-move review: prompts, roster, charter edition, tick, pots.
4. Droplet provisioning from the pinned commit, per `deploy/README.md`; the experimenter places the keys.
5. `worlds/funded.toml`, its hash recorded; mainnet refuses any other name and any manifest without an explicit `[charter]`.
6. Launch. Then nothing changes but the one control: kill.
