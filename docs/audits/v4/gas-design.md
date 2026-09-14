# Self-serve gas for the compute-refill route

Read-only design pass on `codex/production-readiness`. Nothing was edited; no key was read; the
only network calls made were public read-only `eth_call`/`info` reads (no transaction, no
signature). Live reads are dated 2026-09-14 and marked **[measured]**.

---

## 1. Summary

The headline finding is that **the Base-ETH requirement is self-inflicted and removable**, and the
**HYPE requirement is already self-servable with code that exists**.

* The refill route needs Base ETH at exactly one place: the reserve's own `receiveMessage` mint on
  Base (`mint_base`). Hyperliquid's `sendToEvmWithData` action has a `data` field whose *empty*
  value (`"0x"`) tells Circle's Crosschain Forwarding Service to perform that mint for you and
  deduct a **fixed $0.20** from the minted USDC. `factorylab/world/treasury_rails.py:262` sets
  `data: "0x00"` specifically to *disable* this ("Nonempty inert metadata disables Circle's
  automatic forwarding fee"), and `docs/build-log.md:300` records the decision: "self-mint without
  paid automatic forwarding". Flipping that byte removes the Base-ETH dependency entirely.
* The HYPE requirement is *not* HyperEVM native HYPE at the reserve. For the refill direction it is
  **spot HYPE in the venue account** (`_core_gas_bound`, `treasury_rails.py:240-249`), and
  `HYPE/USDC` is a listed spot pair on **both** networks **[measured]**. The population can already
  buy it with `venue.place_market(coin="HYPE/USDC", market="spot", …)`. No treasury operation, no
  spot→HyperEVM send, no new code — only a market registration (or a launch seed).

So the minimal self-serve gas capability is: **(a) list `HYPE/USDC` so the population can buy its
own Core gas, and (b) make the Base leg gasless by choosing the forwarded mint when the reserve has
no ETH.** Both are bounded by the manifest, ledgered by machinery that already exists, replayed by
resume, and need no new credential and no architect action after launch.

A USDC→ETH swap operation (CoW, 0x, paymaster) is designed below as **Track C**, but it is not
needed for compute refill, it cannot be tested on Base Sepolia, and it is 3× the engineering of the
recommended path. It is only required if reserve→venue transfers must keep working post-launch.

---

## 2. The route as it is

### 2.1 Operations that exist

`Treasury.transfer(direction, usd, handle, now_ns)` (`factorylab/world/treasury.py:135`) is the only
money-moving operation. Five directions, all reachable by the population through the
`treasury.transfer` tool (`factorylab/runtime/bootstrap.py:373`, dispatched at
`factorylab/runtime/compute.py:555-570`):

| direction | `LiveRail.plan` steps | needs Core HYPE? | needs Base ETH? | needs HyperEVM HYPE? |
|---|---|---|---|---|
| `perps_to_spot` / `spot_to_perps` | `(direction,)` — signed `usdClassTransfer` | no | no | no |
| `to_reserve` | `withdraw_burn`, `mint_base` | **yes** (spot, venue acct) | **yes** (reserve, `mint_base`) | budget only |
| `to_venice` | `venice_top_up` — x402 EIP-3009 authorization | no | **no** | no |
| `to_venue` | `approve_base`, `burn_base`, `mint_hyper`, `approve_core`, `deposit_core` | no | **yes** | **yes** |

The compute-refill route the experimenter described is `to_reserve` then `to_venice`. The Venice leg
is gasless by construction: `X402Client`/`venice.top_up` sign an EIP-3009
`TransferWithAuthorization` and Venice's facilitator submits it (`x402.py:306-331`,
`venice.py:33-84`); `LiveRail.preflight` for `to_venice` performs no gas check at all
(`treasury_rails.py:146-158`). That is consistent with the observed mainnet state: $4.97 USDC and
**0 wei ETH** at the reserve, yet existing Venice credit that paid for a real completion
(`docs/audits/v3/digitalocean-deployment.md:105-118`).

### 2.2 Exactly which step fails today

**Without spot HYPE** — `to_reserve` is refused *before any signature*, in preflight, by
`_core_gas_bound`:

```python
gas = 200_000 * int(self.hyper.call("eth_gasPrice", []), 16) * 2
hype = sum(Decimal(r["total"]) for r in spot["balances"] if r["coin"] == "HYPE")
if gas <= 0 or gas > self.remaining("hyper", spent):
    raise RailError("HyperCore transfer gas budget is exhausted")
if Decimal(gas) > hype * 10**18:
    raise RailError("venue requires spot HYPE for the Core-to-EVM gas charge")
```

It is called from `preflight` (`:186`) and again from `prepare("withdraw_burn")` (`:285`). The
ceiling is `200_000 × gasPrice × 2`. HyperEVM gas price is **0.1 gwei on both mainnet and testnet**
**[measured]** → the ceiling is `4 × 10^13` wei = **0.00004 HYPE ≈ $0.0033** at the measured mainnet
mid of $81.91. The *actual* charge observed on the live testnet round trip was **0.00002 HYPE**
(`docs/build-log.md:283-286`). This is charged by HyperCore as `nativeTokenFee` on the withdrawal
row and is verified against the reserved ceiling in `_withdrawal` (`:485`). The ceiling is also
debited from `treasury.hyperevm_gas_budget_wei`, so that budget must be non-zero even though no
HyperEVM transaction is sent.

**Without Base ETH** — `to_reserve` is refused in preflight by the executing-chain loop
(`treasury_rails.py:215-220`), `executing = ("base",)`:

```python
if self.remaining(key, gas_spent) <= 0: raise RailError("native gas budget is not configured or is exhausted")
if chain.balance() == 0:                raise RailError(f"reserve requires native {chain.chain.gas_symbol} on {key}")
```

The ETH is consumed by one transaction: `mint_base`, i.e. `receiveMessage(message, attestation)` on
the Base MessageTransmitter, prepared by `CCTP.mint` and broadcast by the reserve
(`cctp.py:234-238`). Measured on the recorded testnet run: 1,056,152,931,299 wei
(≈ 2,631 micro-USD) for that mint (`docs/build-log.md:277`).

Nothing else in the refill route touches Base gas. `to_venue` additionally needs Base ETH for
`approve_base`+`burn_base` and HyperEVM HYPE for `mint_hyper` — that is the only place where native
HYPE at the *reserve* address matters.

### 2.3 How each operation is proposed, bounded, ledgered and resumed

* **Proposed.** `treasury.transfer` is a zero-priced tool with `kind: "treasury"`. Only a producing
  return with an open decision slot may call it (`compute.py:461`, `WRITE_REFUSAL`). The call writes
  `treasury.intent` and emits `TRANSFER_INTENT` before the treasury sees it.
* **Bounded.** `TreasurySpec` (`factorylab/runtime/worlds.py:127-141`) carries
  `max_transfer_fee_micro` ($2 total economic fee ceiling), `withdrawal_fee_micro` ($1),
  `cctp_max_fee_micro` ($0.10), `hyperevm_gas_budget_wei`, `base_gas_budget_wei`,
  `max_venice_per_window` ($10/window). `Treasury.transfer` reserves principal **plus** the fee
  ceiling from the kernel wallet before submitting, refuses a second transfer while one is pending
  or stranded, and `_check_fee` refuses any step whose quoted ceiling would exceed the remaining fee
  budget.
* **Ledgered.** `treasury.intent`, `.refused`, `.submitted`, `.broadcast`, `.acknowledged`,
  `.pending`, `.step_confirmed`/`.step_failed`, `.advance`, `.step_submitted`, `.retry`, `.gas`,
  `.fee_unfunded`, `.confirmed`, `.failed`, `.pots`, `.venice_window`. Every rail call also goes
  through `JournalProxy(..., "treasury.rail")`.
* **Resumed.** `snapshot()`/`restore()` persist the in-flight state, nonce, gas spent, pots, hold
  ids and the window budget; replay re-executes journaled rail calls and never signs a replacement
  nonce (`treasury.py:428-466`, and the acceptance session's replay loop in
  `runtime/treasury_cli.py:75-95`).

### 2.4 What the testnet acceptance CLI already proves, and how long it takes

`factorylab treasury {status,transfer,advance}` (`runtime/cli.py:715-733`,
`runtime/treasury_cli.py`) pins HyperEVM testnet **998** and Base Sepolia **84532** by assertion,
uses the production `Treasury` + `LiveRail` with an encrypted, lock-protected, replayable journal,
and refuses to run if a world journal exists. It proved both directions with real testnet funds:

* `to_reserve` 3 USDC submitted 07:18:40 UTC, confirmed 07:38:30 — **~20 min**.
* `to_venue` 3 USDC submitted 07:38:47, confirmed 07:56:23 — **~18 min**.
* Seven step receipts, canonical HyperCore system-call evidence, Circle attestation, finalized
  destination mint, HyperCore perps credit (`digitalocean-deployment.md:527-551`).

The latency is dominated by **finalized** inclusion (`EVM.proof(..., finalized=True)` waits for the
`finalized` block tag). That is a property of the chains, not of this code, and it bounds what
"testable in minutes" can mean end-to-end: a *submission* is seconds, a *confirmation* is ~15-25
minutes on Base Sepolia. Preflight, quotes and fee reads are seconds.

---

## 3. (a) The HYPE side — already solved, one manifest line

**Question: is `HYPE/USDC` a listed spot pair?** Yes, on both networks. Read live from
`info/spotMeta` **[measured]**:

| network | pair name | wire name | szDecimals | mid |
|---|---|---|---|---|
| mainnet | `HYPE/USDC` | `@107` | 2 | $81.91 |
| testnet | `HYPE/USDC` | `@1035` | 2 | $35.00 |

`LiveRail`'s `_configure_spot` builds exactly this `BASE/QUOTE` name from `spotMeta`, so
`"HYPE/USDC"` satisfies the manifest's "must exist verbatim in SDK spot metadata" rule on both
networks. (`docs/audits/v3/rehearsal.md:419` already shows a seeded `HYPE/USDC` spot lot on the
testnet account, confirming the pair resolves through this adapter in practice.)

**What moves HYPE to where the exit route pays gas?** Nothing. The gas is charged on HyperCore, from
the venue account's **spot** balance, and a spot buy lands there directly. `_core_gas_bound` reads
`spot_user_state(venue_address)` and sums the `HYPE` row's `total`. A spot purchase is the whole
mechanism.

**Is a treasury operation needed?** No. The complete population-side procedure uses only existing
tools:

1. `treasury.transfer(direction="perps_to_spot", usd="11")` — existing direction, signed
   `usdClassTransfer`, receipt-confirmed against a unique `accountClassTransfer` row.
2. `venue.place_market(coin="HYPE/USDC", market="spot", side="buy", size="0.13")` — existing tool.
   Hyperliquid's `min_order_value_usd` is `"10"`, and szDecimals 2 → lot 0.01 HYPE ≈ $0.82, so
   0.13 HYPE ≈ $10.65 is the smallest legal buy on mainnet.
3. Done. 0.13 HYPE covers ≈ 3,200 withdrawal *ceilings* and ≈ 6,500 *actual* charges at the observed
   0.00002 HYPE per withdrawal. One buy, once, is effectively the world's lifetime Core gas.

**The only requirement is that `HYPE/USDC` be registered for trading.** Two ways, both existing:

* Manifest launch seed: `venue.spot_pairs = [..., "HYPE/USDC"]` (`docs/manifest.md:583-585`). Zero
  code, available from tick one.
* Population governance: the `{"kind": "market", "pair": "HYPE/USDC"}` proposal already implemented
  (`docs/manifest.md:775-786`) — one novelty trial, `market.registered` ledger item, `REGISTERED`
  payload, survives resume.

**Recommendation: seed it.** A population that cannot refill compute because it has not yet won a
governance vote to list its own gas token is a bootstrap trap, and the seed is the launch decision
the experimenter is entitled to make. Leave the registration path available as the general
mechanism.

Two consequences to accept, both small:

* The world now holds a volatile non-USDC asset. `LiveRail.balances()` computes `venue` from
  `exchange.account().equity_usd` when spot pairs are configured, so HYPE is counted at mark and
  there is no phantom loss; the world simply carries ~$11 of HYPE price exposure.
* HYPE consumed as `nativeTokenFee` is not a fill, so runtime spot inventory will slowly exceed the
  real venue balance. The consequence is bounded and benign: an oversized HYPE *sell* would be
  refused by the venue and ledgered as `order.refused`. Worth one sentence in the manifest so the
  population is not surprised.

**What is *not* solved by this:** `to_venue` needs native HYPE at the **reserve** address on
HyperEVM, which a spot buy does not provide. Getting it there would need the venue account to
`spotSend` HYPE to the reserve on Core and the reserve to `spotSend` it to the HYPE system address
`0x2222…2222` to credit HyperEVM — two new Core actions, both signable with keys already held, but
new code. Only required if reserve→venue must work post-launch (see §8).

---

## 4. (b) The ETH side — do not buy ETH; stop needing it

### 4.1 The mechanism: Circle's Crosschain Forwarding Service, selected by one byte

`CoreDepositWallet` on HyperEVM decides forwarding from the user's `data` field. From Circle's
contract (`circlefin/hyperevm-circle-contracts`, `src/messages/CrossChainWithdrawalHookData.sol`),
`_shouldForward(data)` returns **true** for empty data or data beginning with the forwarding magic,
and **false** otherwise; the hook is then

```
abi.encodePacked(magic, HOOK_VERSION, uint32(data.length + 20 + 8), from, nonce, data)
```

| offset | len | field | forwarding | current code (`data = 0x00`) |
|---|---|---|---|---|
| 0 | 24 | magic | `bytes24("cctp-forward")` | 24 zero bytes |
| 24 | 4 | version | 0 | 0 |
| 28 | 4 | `uint32` length | **28** (0 + 20 + 8) | 29 (1 + 20 + 8) |
| 32 | 20 | `from` (venue address) | same | same |
| 52 | 8 | HyperCore nonce | same | same |
| 60 | n | user data | *empty* | `0x00` |

`treasury_rails.py:497-498` reconstructs exactly the non-forwarding layout
(`bytes(28) + (29).to_bytes(4) + from + nonce + b"\x00"`), which confirms the reading.

Fee, read live from the deployed contracts **[measured]** (`eth_call`, no transaction):

| network | `CoreDepositWallet` | `fee(false, 6)` | `fee(true, 6)` | `cctpDefaultForwardFee` | `cctpMaxFee` |
|---|---|---|---|---|---|
| HyperEVM testnet 998 | `0x0B80659a…5C206` | 0 | **200000** | 200000 | 0 |
| HyperEVM mainnet 999 | `0x6B9E7731…390A24` | 0 | **200000** | 200000 | 0 |

`calculateCrossChainWithdrawalFee(bool shouldForward, uint32 destinationChainId)` adds the forward
fee to `cctpMaxFee` when `shouldForward` is true. The fee is a **fixed $0.20 to Base**, set on-chain,
identical on testnet and mainnet, and — crucially — **already configured on testnet**, so the
forwarded route is exercisable on Base Sepolia with the existing acceptance CLI.

Against the required properties:

| property | forwarded mint |
|---|---|
| works from a plain EOA with **zero ETH** | yes — the reserve submits **no Base transaction at all** |
| chains | HyperEVM 999 → Base 8453; HyperEVM 998 → Base Sepolia 84532, both fee-configured **[measured]** |
| API key | **none** (an on-chain read plus the existing keyless Iris attestation endpoint) |
| how the fee is charged | fixed 200,000 micro-USDC deducted from the minted USDC by Circle's fee recipient; quoted *before* burning by the contract read |
| on-chain verification | `MessageReceived(caller, sourceDomain, nonce indexed, …)` on the Base MessageTransmitter for **our** CCTP nonce, plus `Transfer(0x0 → reserve, amount − fee)` on canonical Base USDC — i.e. exactly the evidence `CCTP.minted` already checks |
| offline test | recorded RPC/HTTP fixtures through the existing `Transport` seam |
| live in minutes | the fee read and preflight are seconds; the end-to-end confirmation is finality-bound (~20 min), same as today |

### 4.2 Alternatives, and why each loses

**0x Gasless API (permit2 / EIP-2612).** Rejected on two independent grounds. Every 0x API call
requires an `0x-api-key` header — that is a new credential, which the brief forbids. And the permit2
path needs an initial *gasful* `approve()` to the permit2 contract before any gasless swap, which is
precisely the transaction a zero-ETH EOA cannot make. Base Sepolia is not a supported gasless chain.

**CoW Protocol on Base.** The strongest swap candidate: no API key (orders are authenticated by an
EIP-712 signature), Base is a production network (`https://api.cow.fi/base/api/v1/`), buying native
ETH is supported, and an EIP-2612 `permit` **pre-hook** in `appData` removes the need for a gasful
approval — so it genuinely works from a zero-ETH EOA, with the solver paying gas and the fee taken
from the sold USDC. It loses on three points. (1) **No Base Sepolia.** CoW's only testnet is
Ethereum Sepolia; the "live in minutes on 84532" requirement cannot be met, and a mainnet-only first
execution violates the project's own discipline of proving rails on testnet first. (2) It introduces
a whole off-chain order lifecycle — quote, appData hashing, EIP-712 `GPv2Order` signing, order UID,
solver-dependent fill latency (minutes to never), partial/expired orders, slippage — into a treasury
that currently only ever waits on a receipt it can name in advance. (3) It buys an asset the
accounting model has no pot for: ETH is tracked as a *wei gas budget*, not as wallet money, so an
ETH purchase must debit USDC principal and credit a non-wallet inventory, a genuinely new accounting
shape. Kept as Track C for the `to_venue` case only.

**CCTP v2 fast transfer / hooks / relayers that deliver gas.** Fast transfer changes finality and
fee, not who submits the mint. The relaying capability that matters is precisely the Forwarding
Service in §4.1 — so this option *is* the recommendation, reached from the other direction. Circle's
newer prepaid-fee Quote API would let fees be paid on the source chain, but it is an HTTP API that
is not needed here: the fixed fee is readable on-chain and deducted from the mint.

**ERC-4337 paymaster (e.g. Circle Paymaster on Base).** Out of reach for a plain EOA. ERC-4337
UserOperations come from smart accounts; an EOA can only participate via an EIP-7702 delegation, and
either way the operation must reach a **bundler**, which in practice means an API key
(Pimlico/Alchemy/Biconomy) plus a paymaster integration and a second signing scheme. Two new
credentials and a new account model to avoid one $0.01 transaction.

**x402-style seller of ETH.** None exists. x402 is a payment scheme for HTTP resources; its sellers
deliver data and inference, not native ETH, and the world's own x402 client
(`x402.py:183-212`) is hard-bound to `scheme: exact`, `eip155:8453`, canonical Base USDC and
EIP-3009. An "ETH seller" would be an unverifiable counterparty holding a signed USDC authorization
with no on-chain obligation to send anything back. Rejected.

### 4.3 The chosen design: forward-on-empty

Do not choose statically. Choose **per transfer, from the world's own observed balance**, at
`prepare("withdraw_burn")`:

```
if base_eth_balance >= estimated_mint_cost and base gas budget remains:
        data = "0x00"   # self-mint: fee = gas only (~$0.01), plan = (withdraw_burn, mint_base)
else:
        data = "0x"     # forwarded: fee = $0.20 USDC,     plan = (withdraw_burn, await_forward_mint)
```

This is what makes the capability genuinely self-serve: the world reads its own gas position,
records the branch it took and why, and refills compute either way. It never needs the architect,
never strands on an empty ETH balance, and automatically reverts to the cheaper route if ETH ever
appears. The branch, the on-chain fee quote and the resulting fee ceiling are journaled in the
`withdraw_burn` reference before anything is signed.

Cost: $0.20 per refill instead of ~$0.01. At a $5 Venice tranche that is 4% overhead on the
transfer, ~$20 over a hundred refills. That buys the removal of an entire unfundable prerequisite.

---

## 5. Concrete changes

### 5.1 `factorylab/world/treasury_rails.py`

1. `_withdraw_action(amount, nonce, forward)` — `"data": "0x" if forward else "0x00"`. The action
   dict is reconstructed and compared byte-for-byte in `send` (`:371-373`), so `forward` must be
   carried on the reference; add `"forward": bool` to the `withdraw_burn` reference and pass it
   through.
2. `_core_fee(forward)` — `calculateCrossChainWithdrawalFee(bool,uint32)` with `[forward, domain]`
   instead of the hard-coded `False`.
3. `plan("to_reserve")` becomes state-dependent: `("withdraw_burn", "mint_base")` or
   `("withdraw_burn", "await_forward_mint")`. `Treasury` treats steps as opaque, so this is legal;
   decide in `preflight`/`prepare` and persist the decision in `state["route_data"]["forward"]`
   before `plan` is consulted, or (simpler) always plan two steps and let step 2 dispatch on the
   recorded flag.
4. `preflight` — when forwarding, drop `executing = ("base",)`; keep `_core_gas_bound`. Add:
   `amount > withdrawal_fee_micro + fee(true, domain)` and refuse if the on-chain forward fee is 0
   or exceeds `spec.cctp_max_fee_micro` (a 0 means forwarding is not configured for that domain —
   fail closed to self-mint, or refuse).
5. `_withdrawal` — the expected hook becomes

   ```python
   magic = b"cctp-forward".ljust(24, b"\0") if forward else bytes(24)
   body  = b"" if forward else b"\x00"
   hook  = magic + bytes(4) + (len(body) + 28).to_bytes(4) + bytes.fromhex(venue[2:]) \
           + nonce.to_bytes(8) + body
   ```

   and `max_fee_micro=ref["cctp_max_fee_micro"]` already carries the $0.20 because it came from
   `_core_fee(True)`. `min_finality` stays 2000.
6. New `poll("await_forward_mint")`:

   ```python
   message, _proof, fee = self.cctp.attestation(self.hyper, self.base, state["route_data"]["burn"])
   nonce  = "0x" + message[12:44].hex()
   topics = [event_topic("MessageReceived(address,uint32,bytes32,bytes32,uint32,bytes)"),
             None, nonce]                      # nonce is an indexed topic
   logs   = self.base.logs(self.base.chain.transmitter, topics, ref["start_block"])
   # exactly one log, else pending/ambiguous
   receipt = self.base.proof(logs[0]["transactionHash"])          # finalized + canonical
   if not self.cctp.minted(receipt, self.base, amount - fee): raise RailError(...)
   return {"confirmed": True, "received_micro": amount - fee,
           "fee_micro": fee, "wallet_fee_micro": fee,             # real USDC, no native gas
           "principal_moved": True, "chain_key": "base",
           "evidence": {"network": f"eip155:{self.base.chain.id}",
                        "tx_hash": logs[0]["transactionHash"], "block_hash": receipt["blockHash"],
                        "cctp_nonce": nonce, "cctp_fee_micro": fee, "forwarded": True,
                        "forwarder": receipt["from"]}}
   ```

   `EVM.logs` already accepts `None` topic placeholders and already requires finalized, canonical
   inclusion; `CCTP.attestation` already refuses an executed fee above the burn's `maxFee` and
   already handles attestation expiry via `/v2/reattest`. No new trust is introduced: the mint is
   verified against **our** CCTP nonce and a canonical `Transfer(0x0 → reserve, amount − fee)`.
7. `prepare("await_forward_mint")` returns a non-transaction reference
   `{"network": f"eip155:{base.chain.id}", "start_block": …, "fee_ceiling_micro": forward_fee_micro,
   "chain_key": "base"}`; `send` for that step is a no-op. `_check_fee` then charges the $0.20
   against the $2 transfer fee ceiling, which is the correct economics.
8. Nothing in `treasury.py`, `wallet`, resume or the population-facing tool schema changes. The
   direction is still `to_reserve`; the fee is still bounded; the principal is still held until the
   final step confirms.

### 5.2 Manifest fields

| key | type | proposed value | cast |
|---|---|---|---|
| `venue.spot_pairs` | list of `BASE/USDC` | add `"HYPE/USDC"` on mainnet and testnet seeds | launch seed, fixed at launch (existing key) |
| `treasury.cctp_forwarding` | `"never" \| "on_empty_gas" \| "always"` | `"on_empty_gas"` | configured resource bound, fixed for a run |
| `treasury.cctp_max_fee_micro` | int micro-USD | **300000** (was 100000): $0.20 forward + headroom | existing key, raised |
| `treasury.max_forward_fee_micro` | int micro-USD | `200000` — refuse a burn whose on-chain quote exceeds it | new hard bound |
| `treasury.base_gas_budget_wei` | int wei | may now be `0` on a forwarding world; `to_venue` then refuses explicitly | existing key |
| `treasury.hyperevm_gas_budget_wei` | int wei | must stay `> 0` (it bounds the Core-to-EVM charge) — `5 × 10^16` is fine | existing key |

Manifest prose to add, in the Venice/treasury section: that the refill route's Core gas is spot HYPE
bought by the population on `HYPE/USDC`; that HYPE spent as `nativeTokenFee` is not a fill and so
runtime spot inventory may exceed the venue balance; that `to_reserve` forwards its destination mint
when the reserve holds no Base ETH, at a fixed on-chain-quoted fee bounded by
`treasury.max_forward_fee_micro`; and that `to_venue` requires reserve ETH and HyperEVM HYPE and is
refused without them.

### 5.3 Ledger items

Existing items carry almost everything (the reference is journaled at `treasury.submitted` /
`treasury.step_submitted`, and the receipt at `treasury.step_confirmed` / `treasury.confirmed`).
Add two, so the *choice* is public and not merely inferable:

* `treasury.gas_route` — `{transfer_id, direction, forward: bool, reason: "no_base_eth" |
  "base_eth_available" | "manifest", base_eth_wei, base_gas_remaining_wei, core_hype_wei,
  core_hype_required_wei, forward_fee_micro, quote_source:
  "CoreDepositWallet.calculateCrossChainWithdrawalFee"}` — written at prepare, before signing.
* `treasury.forward_unavailable` — `{domain, quoted_micro}` when the on-chain quote is 0 or above
  the cap, i.e. the route the world expected is not offered.

`treasury.pots` gains a `gas` block (see §5.4) so every refresh records the gas position.

### 5.4 What the population sees

The population must be able to answer "can I refill compute right now, and what will it cost?"
*before* spending a $5 tranche. Extend the pots view (`Treasury.refresh_pots`, already published
into every request as `pots_view` via `loop.py:265,337`) with:

```json
"gas": {"core_hype": "0.13", "core_hype_required": "0.00004",
        "base_eth_wei": 0, "base_gas_remaining_wei": 0,
        "route": "forwarded", "forward_fee_micro": 200000,
        "refill_ready": true, "blocked_by": null}
```

`blocked_by` is the *exact* preflight reason string when `refill_ready` is false — "venue requires
spot HYPE for the Core-to-EVM gas charge", "amount is below the fee-covered venue withdrawal
minimum", "CoreDepositWallet cannot currently forward" — so the population can act on it (buy HYPE,
raise the amount) rather than guessing. Add the same three facts to the `treasury` block of
`world.mechanics` (`cortex/schematics.py:475`): that Core gas is spot HYPE it buys itself, that the
destination mint is forwarded for a fixed fee when the reserve has no ETH, and what the per-window
Venice budget is. A refused transfer already returns its reason to the caller and is ledgered;
`fill.counted` already publishes the HYPE purchase.

---

## 6. (c) Test plan

**Tier 1 — pure functions with recorded fixtures (seconds, `fast` marker).** The existing
`tests/world/test_treasury_rails.py` already fakes the exchange `Info` and both `Chain`s in-process,
and `tests/world/test_evm.py`/`test_cctp.py` fake the `Transport` with canned JSON-RPC and Iris
bodies. Everything below fits that shape:

1. `_withdraw_action` emits `data: "0x"` under forwarding and `"0x00"` otherwise; `send` refuses a
   reference whose `forward` flag disagrees with its action.
2. The expected hook bytes equal `bytes24("cctp-forward") + 0x00000000 + uint32(28) + from + nonce`
   — asserted against the literal 60-byte string, so a future layout change fails loudly.
3. `_core_fee` calls `calculateCrossChainWithdrawalFee(true, 6)`; a quote above
   `max_forward_fee_micro` refuses; a quote of 0 refuses (or falls back) rather than silently
   burning with a zero fee.
4. Preflight with `base.balance() == 0` **passes** under `on_empty_gas` and **fails** under `never`;
   preflight with zero spot HYPE fails in both.
5. `await_forward_mint` confirms only when there is exactly one `MessageReceived` log with our
   nonce, a finalized canonical receipt, and a `Transfer(0x0 → reserve, amount − fee)`; two logs →
   pending; wrong amount → `RailError`; missing attestation → `Pending` with the principal held.
6. Fee accounting: the $0.20 is `fee_micro == wallet_fee_micro` (real USDC) with no `gas_fee_wei`,
   so `treasury.gas` is not written and the Base gas budget is untouched — the mirror image of the
   defect corrected at `docs/build-log.md:287-293`.
7. Resume: cut the process between `withdraw_burn` confirmation and the forwarded mint; the replayed
   session must re-derive the same nonce and the same attestation lookup and must not re-sign a
   withdrawal (reuse the wedge style of `tests/audit/test_audit_resume_wedges.py`).
8. Audit-level: a scripted `to_reserve` → `to_venice` chain with the window budget, in the shape of
   `tests/audit/test_a12_venice_treasury.py`; plus one case asserting `refill_ready` / `blocked_by`
   reach the population's pots view.
9. A launch-time check that `"HYPE/USDC"` resolves in live spot metadata (the existing
   `spot pairs unavailable in venue metadata` failure already covers this once seeded).

**Tier 2 — live testnet acceptance, extending `factorylab treasury`.**

* New `factorylab treasury probe` — **seconds, zero transactions**: prints `usdcRouting`, the
  CoreDepositWallet `token()`/linkage checks, `calculateCrossChainWithdrawalFee(false|true, 6)`,
  venue spot HYPE and the required ceiling, reserve Base ETH and HyperEVM HYPE, the route that
  `on_empty_gas` would select, and a `refill_ready` verdict. This is the "minutes, not rehearsals"
  loop the experimenter asked for: it is the same preflight the world runs, exercised without a
  world.
* `factorylab treasury transfer --direction to_reserve --usd 3 --forward` — the real thing on
  HyperCore testnet → Base Sepolia, **with the reserve's Base ETH left at zero**, which is the whole
  claim. Submission is seconds; confirmation is finality-bound (~15-25 min, matching the recorded
  07:18→07:38 round trip). Evidence to retain: the HyperCore ledger row and `nativeTokenFee`, the
  system-call/archive hash, the Circle attestation, the forwarder's Base Sepolia tx hash, the CCTP
  nonce, and the reserve's USDC delta = amount − 200,000.
* A testnet `HYPE/USDC` (`@1035`) spot buy through `venue.place_market`, then the same withdrawal —
  proving the population-side gas acquisition end to end. Minutes.
* `--direction to_venue` remains the ETH/HYPE-funded path and stays covered by the existing fixture.

**Tier 3 — mainnet dry run, no funds moved.** `prepare` deliberately broadcasts nothing
(`treasury_rails.py:266-267`), so a `--dry-run` that runs `preflight` + `prepare("withdraw_burn")`
and prints the reference, the quoted fees and the fee ceiling — then discards — is free and safe on
mainnet. It proves: routing is still `cctp`, the CoreDepositWallet USDC linkage is unchanged, the
forward fee is $0.20 **[already measured]**, spot HYPE covers the Core ceiling, and the amount clears
every minimum. It signs nothing and posts nothing.

**What cannot be proven without mainnet money.** That Circle's *mainnet* forwarder actually mints for
a HyperCore mainnet withdrawal (testnet exercises the sandbox forwarder and Iris sandbox); the real
mainnet `nativeTokenFee` and HyperCore withdrawal fee (the adapter's $1 `withdrawal_fee_micro`
ceiling is conservative against a documented $0.20); that Venice's mainnet `/x402/top-up` settles a
$5 tranche from this reserve; the true fill price and slippage of a $10.65 mainnet HYPE buy; and
end-to-end wall-clock on Base mainnet finality. Every one of those is a first-mainnet-transfer fact,
not a design fact.

---

## 7. (d) Blast radius and casts

**Bounds that already hold, unchanged.** One transfer at a time. Principal *and* the fee ceiling are
reserved from the kernel wallet before submission and released only on a confirmed receipt or a
non-stranded failure. `max_transfer_fee_micro` ($2) caps the total economic fee across all steps and
`_check_fee` refuses any step that would exceed the remainder. `withdrawal_fee_micro` caps the
HyperCore fee and `_withdrawal` refuses a receipt outside the supported schedule.
`max_venice_per_window` caps the outflow into Venice per reserve window, counting uncertain and
later-failed submissions. Retries re-broadcast the identical nonce and transaction, never a
replacement.

**New bounds.** `max_forward_fee_micro` (200,000) refuses a burn whose on-chain quote exceeds it —
and because that quote is read *before* signing and the same value is written into the burn
message's `maxFee`, the quote and the receipt are the same number by construction.
`CCTP.attestation` already refuses an attestation whose executed fee exceeds the burn's `maxFee`
(`cctp.py:220-222`), and `CCTP.minted` requires the exact `amount − fee` credited to the reserve —
so quote-vs-receipt verification is total: an overcharging forwarder cannot confirm the step.
`cctp_max_fee_micro = 300000` raises the minimum transfer to >$1.20 (and >$1.30 for `to_reserve`),
which is below the ~$5.40 a Venice-funding transfer needs anyway.

**Max per window.** There is no per-window cap on `to_reserve` today, only the fee caps and the
source pot. If the experimenter wants one, `treasury.max_to_reserve_per_window` mirrors
`max_venice_per_window` exactly (same `open_window` counter, same "submitted counts even if it
fails" rule) and is ~20 lines. Recommended: the reserve is the compute pot, and an uncapped drain
from the venue into it is a real way for a confused population to stop trading.

**HYPE inventory.** A spot buy is an ordinary order, so the only bound today is available spot USDC —
a population could in principle put the whole account into HYPE. That is also true of any other
listed pair, so it is not new; but gas is the one purchase the world is being *encouraged* to make,
and a cap is cheap. Option: refuse a `HYPE/USDC` buy above `venue.max_gas_inventory_usd` (say $25).
This is an experimenter call, not a technical one — it trades a Class 3 principle against a
bootstrap risk.

**Partial failure — signed but not executed.** Three cases, all already shaped by the existing
state machine:

* *Withdrawal submitted, outcome unknown.* `send` raises `Pending`; the principal stays held; `tick`
  re-broadcasts the same nonce after 60 s; `_withdrawal` confirms only on a unique matching
  HyperCore row. Unchanged by this design.
* *Burn confirmed, forwarder never mints.* `principal_moved` is already true, so the transfer
  **strands** rather than failing — the correct outcome, and it is visible in `treasury.failed` with
  `stranded_micro` and in the pots view. Recovery is real and permissionless: `destinationCaller` is
  the zero address on this route, so **anyone** can submit `receiveMessage` for that message, and
  the attestation can be re-requested via Iris `/v2/reattest` (already implemented) if it expires.
  Keep `mint_base` as an available recovery step: a stranded forwarded transfer whose reserve later
  holds ETH can be completed by self-mint with no new code path, and the `advance` subcommand is
  exactly the operator gesture for it.
* *Forwarding quoted but not configured.* The quote read is 0 → `treasury.forward_unavailable` and a
  refusal before signing. Nothing moves.

**Reporting.** Every attempt and outcome is already public: `treasury.intent` + `TRANSFER_INTENT` on
the way in, the refusal reason returned to the calling assembly and ledgered, the step receipts and
the final `confirmed`/`failed`/stranded item, the pots refresh, and — new — `treasury.gas_route`
naming which route was chosen and why. The population reads the gas position and `refill_ready` in
every request's pots view.

---

## 8. Effort estimate

Hours are model-working-hours: **Opus** for spec, review and the byte-level/evidence questions;
**Codex** for implementation and fixtures.

| track | work | Opus | Codex |
|---|---|---|---|
| **A. HYPE** | seed `HYPE/USDC` in the mainnet/testnet manifests, raise `hyperevm_gas_budget_wei`, manifest prose, launch-metadata test, one testnet buy | 1 | 1–2 |
| **B. Forwarding** | the `treasury_rails.py` diff in §5.1, `TreasurySpec` keys, `gas_route`/`forward_unavailable` items, pots `gas` block + schematics text, ~12 fixture tests, `treasury probe` and `--dry-run` CLI, docs/manifest updates | 2–3 | 6–8 |
| **B-live** | testnet forwarded `to_reserve` with zero reserve ETH, evidence capture, audit note | 1 | 1 (+~30 min wall clock) |
| **Subtotal (recommended path)** | | **4–5** | **8–11** |
| **C. `acquire_gas` via CoW** *(only if `to_venue` must work post-launch)* | new `world/cow.py` (quote, appData, EIP-712 `GPv2Order`, permit pre-hook, order UID, trade→tx verification), a sixth treasury direction with its own plan/preflight/prepare/send/poll, ETH-inventory accounting, window/slippage caps, ~20 fixture tests; **no Base Sepolia proof possible** | 3–4 | 16–24 |
| **C-alt. HyperEVM HYPE for `to_venue`** | two HyperCore `spotSend` actions (venue → reserve, reserve → `0x2222…2222`) as a bounded treasury step | 2 | 6–8 |

The recommended path is roughly **one Opus day of supervision and one to one-and-a-half Codex days**,
plus an hour of testnet wall clock. Track C triples it and cannot be proven on the target testnet.

---

## 9. Open questions only the experimenter can answer

1. **Is `to_venue` required after launch?** If reserve→venue must work (returning unspent compute
   money to trading), the world needs reserve Base ETH *and* HyperEVM HYPE, and that is Track C or
   C-alt. If it is acceptable for `to_venue` to refuse cleanly with a ledgered reason, set
   `base_gas_budget_wei = 0` and the recommended path is complete. **This is the single decision
   that sets the size of the work.**
2. **Seed `HYPE/USDC`, or make the population register it?** Seeding removes a bootstrap trap;
   registering is more Class 3. Recommendation: seed, and leave registration available.
3. **Cap the gas inventory?** Should a `HYPE/USDC` buy above ~$25 be refused, or is the population
   free to hold what it likes?
4. **Add `treasury.max_to_reserve_per_window`?** Recommended; needs a number.
5. **Accept $0.20 per refill?** Forwarding costs ~$0.19 more per transfer than self-minting. Over a
   hundred refills that is ~$20 to remove an unfundable prerequisite. Confirm.
6. **Testnet funds to place:** HyperCore testnet USDC in the venue account (≥ $15) and a small
   testnet `HYPE/USDC` buy (testnet mid $35, min order $10 → 0.29 HYPE). Deliberately **do not**
   fund Base Sepolia ETH for the forwarded run — a zero balance is the evidence. Keep ~0.001 ETH
   available separately if the self-mint fallback should also be re-proven.
7. **Mainnet first move:** who runs the mainnet `--dry-run`, and does the first real mainnet
   `to_reserve` happen before launch (architect-run, proving the rail) or after (population-run,
   proving self-service)? The design supports either; only the second is a Class 3 claim.
8. **API keys to create: none.** Confirmed for the recommended path — the forwarding fee is an
   `eth_call`, Iris attestation is keyless, and no swap provider is involved. Track C (CoW) is also
   keyless; only 0x would require a new credential, which is why it was rejected.

---

## Sources

Live reads (2026-09-14, read-only): Hyperliquid `info/spotMeta` and `info/allMids` on mainnet and
testnet; `eth_call` of `calculateCrossChainWithdrawalFee(bool,uint32)`, `cctpDefaultForwardFee()`,
`cctpForwardFees(uint32)`, `isCctpForwardFeeSet(uint32)` and `cctpMaxFee()` on
`CoreDepositWallet` at `0x0B80659a4076E9E93C7DbE0f10675A16a3e5C206` (998) and
`0x6B9E773128f453f5c2C60935Ee2DE2CBc5390A24` (999); `eth_gasPrice` on both HyperEVM RPCs.

- [Circle — CCTP on HyperCore](https://developers.circle.com/cctp/concepts/cctp-on-hypercore)
- [Circle — Withdraw USDC from HyperCore to EVM](https://developers.circle.com/cctp/howtos/withdraw-usdc-from-hypercore-to-evm)
- [Circle — Crosschain Forwarding Service](https://www.circle.com/blog/introducing-our-new-crosschain-forwarding-service-now-integrated-into-cctp)
- [circlefin/hyperevm-circle-contracts — CrossChainWithdrawalHookData / CoreDepositWallet](https://github.com/circlefin/hyperevm-circle-contracts)
- [Hyperliquid — exchange endpoint (`sendToEvmWithData`)](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/exchange-endpoint)
- [CoW Protocol — API integration](https://docs.cow.fi/cow-protocol/integrate/api), [native tokens](https://docs.cow.fi/cow-protocol/tutorials/cow-swap/native), [hooks](https://docs.cow.fi/cow-protocol/reference/core/intents/hooks)
- [0x — Gasless API introduction](https://0x.org/docs/gasless-api/introduction) and [FAQ](https://0x.org/docs/gasless-api/gasless-faq)
