# State on main and what remains before launch

## What is merged

The round-two defect pass (B1–B20). The fidelity pass: cadence, the immune
organ, priced penalties, the verdict/payoff split, exposure, judge conflicts,
the novelty reserve, charter disclosure, committee liability, typed windows and
the Venice top-up. From the architecture pass: the deciding agent's propensity,
registrable observations, spot trading, and the wake as an observatory.

Everything from both audit rounds is merged, including full composition (PR #44), registrable connectors (PR #45) and the polish pass (PR #46). `docs/build-log.md` has the detail.

## The gate

```
uv run pytest
2114 passed (about ten minutes with the machine idle; twenty under load)
uv run pytest -m slow -o addopts="" tests/runtime/test_resume.py
30 passed (about two and a half minutes)
```

## Secrets and money

`openrouter.key`, `hyperliquid.key` and `reserve.key` sit at the repository
root, mode 0600, gitignored; only the CLI loads them. Never print or commit a
key. In place: $100 USDC on Hyperliquid perps (classic account mode), about $103
of OpenRouter credit, a Base reserve of about 4.97 USDC and 4.98 Venice credit.

## What remains, in order

1. Testnet watch: a run long enough for a position to close and settle.
2. Round three: a cold audit of this main from six seats (`docs/audit-brief-v3.md`), launched with the experimenter.
3. Edition 1 re-drafted with the launch roster.
4. The first-move review: prompts, roster, charter edition, tick, pots.
5. Droplet provisioning from the pinned commit, per `deploy/README.md`.
6. `worlds/funded.toml`, its hash recorded; mainnet refuses any other name.
7. Launch. Then nothing changes.
