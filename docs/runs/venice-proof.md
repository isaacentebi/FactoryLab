# Venice wallet-funded inference: $5 proof

This is the experimenter's live proof procedure. Implementation and HTTP-fake tests
do **not** establish that a real top-up settled or that inference consumed wallet
credits. Run this before a world launches. These commands do not update a running
world's treasury pots or its sealed ledger.

## Commands, in order

Run from the repository root in one shell. Prevent an existing API-key environment
or dotenv entry from diverting the probe to API-key billing, and let the CLI load
the reserve file itself:

```sh
cd /Users/isaacentebi/Desktop/FactoryLab
export VENICE_API_KEY=''
unset RESERVE_PRIVATE_KEY
uv run factorylab reserve init
```

The init command prints **one checksummed `0x...` address and nothing else**. It
creates `reserve.key` with mode `0600`, using `Account.create` and OS randomness.
It refuses to overwrite an existing file or symlink. Never display, copy into the
build log, or commit the private key or a signed authorization header. If this
reserve already exists, use its `reserve status` address instead of creating a new
key. Run every subsequent command from this same repository root.

Fund the printed address with **about 5.50 USDC, net received, on Base**. Use an
exchange withdrawal explicitly selecting **Base**, or bridge to Base from an
existing wallet. The token contract must be
`0x833589fcd6edb6e08f4c7c32d4f71b54bda02913`, chain ID **8453**. Wait for funding to
confirm. Record the funding transaction hash, received amount, and any exchange or
bridge fee separately. Bridging and exchange withdrawal are outside these commands.

The EIP-3009 transfer used here is relayed by the facilitator, which pays settlement
gas. The client signs an authorization; it does not broadcast a native ETH
transaction. Accordingly, affordability means at least 5,000,000 USDC base units
and does not require a positive ETH balance. Venice's general prerequisites mention
native gas funds; record the ETH balance and any live gas-related rejection instead
of interpreting the affordability flag as a promise of settlement.

```sh
uv run factorylab reserve status
```

Expected output is one JSON object containing:

- `address` equal to the init address and `network: "eip155:8453"`.
- `usdc_micro` about `5500000` and `usdc_usd` about `"5.500000"`.
- `eth_wei` and `eth` (integer wei and a decimal ETH string).
- `venice_balance_micro` and `venice_balance_usd`, normally `0` and `"0.000000"`
  for a new reserve.
- `topup_5_affordable: true`. If false, wait for funding or correct its network/token.

Save this entire output as the before state. Missing, malformed or failed balance
responses fail the command; they are never displayed as a zero balance.

```sh
uv run factorylab reserve topup --usd 5
```

The client checks Base USDC, requests a 402 quote, selects `exact` on `eip155:8453`
for **exactly `5000000`** canonical USDC units, signs that quote's recipient, and
submits it once in `X-402-Payment`. Other amounts, networks, assets and transfer
methods fail before signing. This command deliberately accepts only a $5 tranche.

Expected output is two JSON lines. The first contains `address` and `settlement`:
the decoded `PAYMENT-RESPONSE` receipt when supplied, otherwise Venice's JSON body.
Record its transaction hash or settlement/transaction identifier verbatim. The
standard receipt has `success`, `transaction`, `network` and `payer`; Venice may
wrap its own response fields in `data`. The second line is a fresh authenticated
balance read, normally `venice_balance_micro: 5000000` and
`venice_balance_usd: "5.000000"` for a previously empty balance.

Measure the actual credited difference; do not assume $5 net credit or zero fees.
If no transaction/settlement reference is returned, record the response and obtain
the outgoing USDC transaction reference from Base before marking the proof complete.
HTTP success alone is insufficient evidence. The reference line is printed before
the balance read so a failed refresh does not hide it.

**After a timeout or an ambiguous submission, do not rerun topup immediately.**
Check `reserve status` and the wallet's Base transfers first. The command does not
persist or resubmit a pending authorization, and running it again is a new $5
authorization. If the first receipt arrives but the balance is delayed, repeat
status only and record the delay.

```sh
uv run factorylab probe --provider venice --model venice:z-ai-glm-5-3-flash
```

The model above is listed in the repository's 11 September 2026 Venice research.
The probe requests a short `OK` reply, caps output at 32 tokens, and disables
thinking for this proof. It makes one completion request, using fresh SIWE instead
of an API key because `VENICE_API_KEY` was explicitly emptied above.

Expected output is one JSON object with `provider: "venice"`, the served model,
`text`, `input_tokens`, `output_tokens`, integer `cost_micro`, `cost_source`, and
`request_id`. Normally the source is `"reported"`, from top-level `cost.usd`, rounded
up once. `"table"` means the cost field was missing and the current catalogue's
token prices produced an estimate; record that distinction. It does not prove
provider-reported billing. A nonempty reply is expected; record the actual reply,
even if it differs from `OK`.

If the example model has been removed, inspect the public catalogue and substitute
a current, inexpensive text model supporting disabled thinking:

```sh
uv run python -c 'from factorylab.world.venice import VeniceProvider; print("\n".join(e.id for e in VeniceProvider().catalogue()))'
```

Do not remove the `venice:` namespace. It is stripped only on the Venice HTTP wire.

```sh
uv run factorylab reserve status
```

Expected: Base USDC is about `500000` ($0.50), the Venice balance is below the
post-top-up balance by the completion charge, ETH is unchanged by this relayed
payment, and `topup_5_affordable` is false. Compare integer balances. A balance
difference and rounded reported cost can differ by one micro-USD because balance
reads round down and costs round up. Larger discrepancies, a zero-cost call, delayed
updates, DIEM-linked balance, or an estimate need investigation; record the evidence.

## Build-log record

Record UTC timestamps, checkout revision and dirty diff identity; init address;
funding network/token, transaction hash and net receipt; the two status outputs;
the settlement response and transaction/reference; post-top-up balance and credit
delta; probe request ID, served model, text, token usage, cost and its source; and
the final Venice debit. Include observed fees, latency and deviations from the
expectations above. Keep wallet secrets and signed headers out of every log.

Mark the live proof complete only when the Base transfer, Venice credit, successful
wallet-authenticated completion, and Venice balance decrease are all evidenced.
The probe is a standalone provider check; it does not debit a live Factory Lab
kernel wallet. Offline tests separately exercise debit-before-return with the
existing meter and a real sealed test ledger.

## Offline verification and overrides

```sh
uv run ruff check . && uv run pytest
```

The new tests replace urllib HTTP with in-process responses and use synthetic keys
in pytest temporary directories. No real key file, network, or funded wallet is
needed. `eth_account` (and its Keccak dependency used by tests) already comes from
`hyperliquid-python-sdk`; no dependency was added. The EIP-3009 test independently
constructs ABI words and hashes each EIP-712 layer, then compares the result with a
fixed digest and the production encoder's result.

All endpoint overrides include the same request/auth/signature path:

```sh
uv run factorylab reserve status --rpc http://127.0.0.1:8545 --base-url http://127.0.0.1:8080/api/v1
uv run factorylab reserve topup --usd 5 --rpc http://127.0.0.1:8545 --base-url http://127.0.0.1:8080/api/v1
uv run factorylab probe --provider venice --model venice:test-flash --rpc http://127.0.0.1:8545 --base-url http://127.0.0.1:8080/api/v1
```

These example commands require an actual fake server; the test suite supplies its
fakes in-process. Overrides select endpoints, **not a dry-run mode**. Use only a
fresh unfunded synthetic test reserve with fake endpoints. Probe accepts `--rpc`
for interface consistency but needs no RPC request. Native ETH uses JSON-RPC
`eth_getBalance`; ERC-20 USDC uses `eth_call` with `balanceOf(address)`.

## Implementation choices and remaining boundaries

- Both reported and estimated costs populate `ModelResponse.cost_micro`, so the
  existing meter charges the computed amount. `response.raw.cost_source` records
  the distinction. The unchanged generic `MeteredModel.cost_source` labels any
  populated cost as `reported`; use the provider metadata or probe output for this
  distinction. The current assembly does not persist provider raw metadata in its
  runtime return; propagating this into live diary records needs a separate change
  outside this chunk's permitted files.
- Catalogue fallback is explicitly `tokens_only`: it does not claim to reconstruct
  web-search charges or cache discounts. Fractional prices are retained exactly
  through `CatalogueEntry.price()` into `TokenPrice`.
- `complete(req, tools=..., tool_choice=..., parallel_tool_calls=...)` provides
  optional tool inputs while retaining the existing `ModelRequest` protocol.
  Assistant/tool messages pass through, and returned calls are in `response.raw`.
  Wiring native function calls into the runtime's assembly execution is separate.
- Tier token budgets become effort `low`, with the substitution in response raw
  metadata. This cannot enforce a token budget or grant effort control to a model
  that lacks it. Configured web search defaults to `auto`; explicit `on`/`off` are
  supported. OpenRouter-specific web engine/result-count controls are not forwarded.
- `build_provider` supports Venice-only and mixed Venice/OpenRouter manifests by
  namespace. API keys take precedence over SIWE; mixed-provider treasury balance
  aggregation and autonomous top-ups are not connected to the runtime loop.
- A key file must be regular and mode `0600`; existing environment variables win.
  No reserve key was generated for the experimenter during implementation.
- Live net credit, gas requirements in Venice's deployment, receipt shape, chosen
  model support, and settlement latency remain observations for this $5 proof.

The payment-header docstring cites the exact x402 v2 files read:
[PaymentPayload schema](https://github.com/coinbase/x402/blob/main/specs/x402-specification-v2.md),
[EVM exact scheme](https://github.com/coinbase/x402/blob/main/specs/schemes/exact/scheme_exact_evm.md),
and [HTTP transport](https://github.com/coinbase/x402/blob/main/specs/transports-v2/http.md).
The standard uses `PAYMENT-SIGNATURE`; Venice's documented header alias is
`X-402-Payment`. SIWE envelope fields follow the
[Venice guide](https://docs.venice.ai/guides/integrations/x402-venice-api).
The balance `data` wrapper is also present in Venice's
[official client](https://github.com/veniceai/x402-client/blob/main/src/index.ts).
