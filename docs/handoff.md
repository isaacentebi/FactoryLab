# State on main and what remains before launch

## What is merged

The round-two defect pass (B1–B20). The fidelity pass: cadence, the immune
organ, priced penalties, the verdict/payoff split, exposure, judge conflicts,
the novelty reserve, charter disclosure, committee liability, typed windows and
the Venice top-up. From the architecture pass: the deciding agent's propensity,
registrable observations, spot trading, and the wake as an observatory.

Not merged: full composition (registrable `accepts`/`emits`, retirement by vote,
recursive children) is open as PR #44; connectors are specified and not built;
the polish pass has not started. `docs/build-log.md` has the detail.

## The gate

```
uv run pytest
1972 passed in 624.38s (0:10:24)
uv run pytest -m slow -o addopts="" tests/runtime/test_resume.py
30 passed in 146.52s (0:02:26)
```

## Secrets and money

`openrouter.key`, `hyperliquid.key` and `reserve.key` sit at the repository
root, mode 0600, gitignored; only the CLI loads them. Never print or commit a
key. In place: $100 USDC on Hyperliquid perps (classic account mode), about $103
of OpenRouter credit, a Base reserve of about 4.97 USDC and 4.98 Venice credit.

## What remains, in order

1. Testnet watch: a run long enough for a position to close and settle.
2. Round three: a cold audit of this main from three seats.
3. Edition 1 re-drafted with the launch roster.
4. The first-move review: prompts, roster, charter edition, tick, pots.
5. Droplet provisioning from the pinned commit, per `deploy/README.md`.
6. `worlds/funded.toml`, its hash recorded; mainnet refuses any other name.
7. Launch. Then nothing changes.
