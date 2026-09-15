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
`b184b1d8dc55daf56978ea51181be0d06e59493bef2727d97b2f67717efdbf8b`, pinned in
`tests/runtime/test_manifests.py`): the compute-continuity roster with the observer moved to
`deepseek/deepseek-v4.1-flash`, Hyperliquid testnet with BTC and ETH perps and the `PURR/USDC` and
`HYPE/USDC` spot pairs, the ten-minute tick, one-hour windows, and the edition 2 physics
(`docs/plans/edition2.md`). Decisions taken after the first rehearsals
(`docs/audits/v5/rehearsal.md`):

- Endowment: 90 USD in the wallet, 40 USD unlocked at genesis, 50 USD released as 10 USD on days
  7, 14, 21, 28 and 35. At the clean rehearsal's burn of about 5.6 USD a day genesis lasts a week
  and each tranche about two days; added credit scales the schedule the same way.
- Charter: five norms (the reviewer's four plus fidelity: a measurement stands for a value, and
  satisfying it without serving the value is failure a judge must say so about) and three cards
  (consequence paid off, forecast skill, censorship bound). Every frugality card was removed:
  under per-seat entitlements the wallet already prices cost, tool calls, malformed answers and
  fees, and the half-cent cost cap had made looking at the market a violation (fourteen wakes,
  fourteen holds). The concentration card was removed because a card prices a bet after it is
  on; ruin is bounded by the kernel's leverage wall, a hard cast. Ratified by the seeded committee
  of this roster, all three cards carried, `docs/charter/edition2-ratification.json`; the earlier
  ballots (eight cards; three cards on a roster hash polluted by an empty new field) are kept
  under `docs/charter/history/`.
- Observer: GLM 5.3 flash via OpenRouter wrapped one reply in five under the edition 2 prompt with
  and without the host pin; DeepSeek 4.1 flash scored 100% on every calibration column.
- The wake publishes every agent's answer live and unredacted (`returns`). Sealing the diary was
  secrecy, not darkness; the experimenter's only lever is kill and he does not want protecting.
- Uncertain provider bills are settled from the provider's balance, not charged at the ceiling.

The funded manifest derives from this file.
