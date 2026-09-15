# Current launch decisions

This is preparation for the first move, not authorization to start mainnet trading.
The earlier planning text is preserved in
[the historical record](history/launch-decisions-2026-09-13.md). Its old balances,
model prices, tick recommendations and allocation limits are not current requirements.

## Resources and allocation

The experimenter's decision is the entire Hyperliquid account, the remaining
OpenRouter account credit, and Venice for additional compute. No new daily budget,
order-size limit, gross-exposure limit or architect-selected slippage limit is part
of this launch. The economic limits added during the September 13 repair session
were removed, rather than made optional. Existing kernel accounting and the
population's ratified charter remain part of the experiment.

The initial kernel wallet must match the fresh, reconciled starting resources in
integer micro-USD. It is not just the OpenRouter pot, and the $90 testnet roster is
not the funded starting balance. See the dated, read-only observations in
[the deployment record](audits/v3/digitalocean-deployment.md); refresh them immediately
before preparing the final manifest. The operator accepts the current OpenRouter key allowance as the finite seed
endowment. Do not remove it or buy additional OpenRouter credit. Additional inference
uses Venice; the mixed roster and exhaustion proofs are recorded in
[audit](audits/v3/compute-continuity.md).

## Charter and time

The existing 13-card charter was adopted unchanged by the mixed OpenRouter/Venice
roster in a 5–0 seed-committee vote. Its approval and ballots are in
`docs/charter/edition1-compute-continuity-*`; the matching prepared testnet manifest is
`worlds/compute-continuity-testnet.toml`. Verify the same roster and charter hashes
when preparing the funded manifest. See [adoption evidence](audits/v3/charter-adoption.md).

A short rehearsal targets ten minutes of wall-clock runtime. A declared tick is a
scheduling request: serial model calls may take longer. Report achieved tick gaps,
model-call duration and actual elapsed time. A shorter tick is not proof of
high-frequency execution or a profitable strategy. Never mistake an acknowledged
order for a confirmed fill.

## Compute replenishment

Venice credit already deposited to the reserve wallet can be spent. The population
also has an implemented Venice top-up operation; the old statement that it can
never top itself up was wrong. The vendor currently requires a $5 additional top-up.
The last observed Base USDC balance was $4.966, while existing Venice credit was
$4.976619 and was reported usable by Venice's authenticated endpoint.

The existing exchange-to-Base transfer route requires native gas. The last read-only
checks found no Base ETH or HyperEVM HYPE in the reserve. Native-gas acquisition is
not currently an implemented treasury operation. Do not claim an autonomous,
receipt-verified gas bootstrap merely because another vendor advertises one.

## Deployment and first move

The existing DigitalOcean droplet is `superdarkfactory`, ID `599960972`, IP
`152.42.221.133`. Its source and service definitions are installed. The funded
service is stopped; no funded manifest or wallet credentials have been installed
there. The exact release hashes and test history are in the deployment record.

Finish the engineering checks, actual funding-path preparation, source verification,
and operating configuration described in `deploy/README.md`. The operator has no
existing off-host backup bucket and prefers a website for operational status rather
than email notifications. The wake is the existing status surface. A page on the
same droplet cannot independently announce a complete droplet outage. Off-host
backup remains evidence-preservation work; it does not constrain trading.

Only after the launch prerequisites are genuinely met should `worlds/funded.toml`
be created with the ratified charter and fresh starting accounting. Validate its
hash, verify the installed artifact and credentials, and leave the final funded
start for the experimenter's first move. Before that action, no mainnet orders,
fund transfers, or funded event loop are part of verification.

After launch, the operating covenant remains: no new manifest, no manual refill,
no population steering, and no live private-diary inspection. The wake publishes
its sealed views; the kill action ends the world. Automatic process recovery must
resume the same identity and ledger, rather than resetting the experiment.

## Self-serve gas (decided 14 September)

The population must be able to pay its own exit fees without an architect seed. The
design is in `docs/audits/v4/gas-design.md`. Two facts reframed it: the Hyperliquid-side fee
is paid from HYPE held in the venue spot account (about a third of a cent per exit, and
`HYPE/USDC` is a listed spot pair the population can buy with its trading money), and the
Base-side mint can be done by Circle's forwarding service for a flat on-chain-quoted fee of
$0.20 with zero ETH at the reserve, a route our code had deliberately switched off.

Built as "forward-on-empty": at each exit the world reads its own Base ETH balance and
either self-mints for about a cent or lets Circle mint for the quoted fee; the branch and
the quote are ledgered (`treasury.gas_route`) and shown to the population in the treasury
view. Decisions taken: `HYPE/USDC` is seeded as a spot market in the funded manifest as the
physics of the exit, not a trading instruction; the reverse direction (reserve to venue)
may refuse with a public reason rather than require gas at the reserve; no API key and no
architect gas seed are part of the launch.

## Edition 2 (15 September)

The edition 2 testnet manifest is `worlds/edition2-testnet.toml` (hash
`9eb460e4b3d4c974feefbc7cd6a5e182defda231f4f33a5d3cb4ac060fa9d224`, pinned in
`tests/runtime/test_manifests.py`). It keeps the mixed OpenRouter/Venice roster of
`worlds/compute-continuity-testnet.toml` (the same nine seats and ten models), the Hyperliquid
testnet with BTC and ETH perps and the `PURR/USDC` and `HYPE/USDC` spot pairs, the reserve
address, and the ten-minute tick, and sets the edition 2 physics
(`docs/plans/edition2.md`):

- Endowment (C1): 90 USD in the wallet, 30 USD unlocked at genesis, 60 USD locked and released
  as 10 USD on days 7, 14, 21, 28, 35 and 42 after the ledgered Launch. `base_share` 0.8
  (C10): genesis gives each of the nine seats 2,666,666 micro-USD and leaves 6,000,006
  unallocated; each tranche adds 888,888 a seat while nine seats live. At the rehearsal burn
  of $3.47 a day the genesis money lasts about 8.6 days and a tranche about 2.9 days, so a
  population that neither gets cheaper nor earns is dormant part of every week rather than
  dead.
- Novelty window one hour, never the compute-continuity two-minute window: at one micro-USD
  per byte-window that window drained $47 a day at 64 KiB of notes
  (`docs/audits/v4/gpt6-triage.md`). Storage rent `notes.micro_per_byte_day` at the loader
  default `"0.04"`: the whole 256 KiB cap costs about a cent a day.
- `trial_amount_usd` 0.05, sized as a child's whole endowment (C10) rather than a fee: three
  calls of the cheapest seat (eval-b, `qwen/qwen3.7-flash` at 3000 tokens) at the meter's
  reservation ceiling for the largest measured request (52,832 input tokens) cost 3 x 5,811 =
  17,433 micro-USD; the arithmetic is in the manifest. `prices.min_blame_share` 0.1 (C6) and
  `prices.program_micro_per_call` 50 (C8), both the loader defaults, stated.
- The charter is the architect's draft `docs/charter/edition2-draft.toml` verbatim: the four
  reviewer norms and eight cards, the five quota cards gone. It is not ratified; the manifest
  carries no `ratified_sha256` or `roster_sha256`.

Ratification is a separate step: a testnet committee vote through `scripts/ratify_charter.py`
stamps the hashes. The funded manifest is derived from this file after that vote, with fresh
reconciled starting accounting, and is not named `funded` before the launch gates.
