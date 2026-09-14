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
