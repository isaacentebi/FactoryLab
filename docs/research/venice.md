# Venice AI as an inference provider for Factory Lab

Research date: 11 September 2026. Claims are labelled **verified** (live unauthenticated probe), **documented** (Venice docs),
or **marketing**. No API key was used; no payment was made.

## 1. Catalogue

`GET https://api.venice.ai/api/v1/models` needs no auth and returns full per-model pricing
in USD per million tokens, context window, and capability flags
(`supportsFunctionCalling`, `supportsReasoning`, `supportsReasoningEffort`,
`supportsResponseSchema`, `supportsWebSearch`, `quantization`, `privacy`).
**Verified by probe, 11 Sep 2026** — a direct substitute for
`OpenRouterProvider.catalogue()`, except prices are per-Mtok floats
(`model_spec.pricing.input.usd`), not per-token strings.

Size: **119 text models, ~21 families** (verified probe). OpenRouter's
`GET https://openrouter.ai/api/v1/models` returned **443** on the same day
(verified probe). Venice covers open weights well beyond Qwen: GLM/Z-ai (14), DeepSeek (8),
Kimi (7), Gemma (5), MiniMax (3), Llama+Hermes (3), Mistral (2), gpt-oss (2), Nemotron (2),
plus proprietary Claude (12), OpenAI GPT (18), Gemini (7), Grok (6). Twelve `e2ee-*` variants run in attested enclaves. **No Tencent/Hunyuan and no Meta model past Llama 3.3.**

Roster mapping (Venice id ← our id):

| worlds/testnet.toml id | Venice id | Same model? |
|---|---|---|
| z-ai/glm-5.3-flash | `z-ai-glm-5-3-flash` | yes |
| deepseek/deepseek-v4.1-flash | `deepseek-v4-1-flash` | yes (fp8) |
| deepseek/deepseek-v4-flash-0731 | `deepseek-v4-flash-0731` | yes |
| qwen/qwen3.8-flash | `qwen-3-8-flash` | yes |
| qwen/qwen3.7-flash | — | **absent**; nearest cheap: `mercury-2-5` or `qwen3-5-9b` |
| tencent/hy3 | — | **absent**; nearest: `zai-org-glm-4.7-flash` |
| openai/gpt-5.6-luna | `openai-gpt-56-luna` | yes |
| meta/muse-spark-1.3 | — | **absent**, no sibling |

Five of eight present. Two of the three gaps are our two cheapest evaluators.

## 2. Pricing

$/Mtok, both columns from live catalogue probes on 11 Sep 2026.

| Model | OR in | OR out | Venice in | Venice out |
|---|---|---|---|---|
| glm-5.3-flash | 0.15 | 0.50 | 0.15 | 0.50 |
| deepseek-v4.1-flash | 0.15 | 0.60 | 0.375 | 1.50 |
| deepseek-v4-flash-0731 | 0.065 | 0.18 | 0.175 | 0.35 |
| qwen3.8-flash | 0.15 | 0.47 | 0.14 | 0.49 |
| gpt-5.6-luna | 0.20 | 1.20 | 0.25 | 1.50 |
| qwen3.7-flash | 0.03 | 0.13 | — | — |
| tencent/hy3 | 0.0825 | 0.33 | — | — |
| meta/muse-spark-1.3 | 1.25 | 4.25 | — | — |

Venice is at parity on two, **1.25x–2.6x dearer on three**. (our toml lists deepseek-v4.1-flash
input at 0.20; OpenRouter's live price today is 0.15.)

Fees: no per-request fee and no stated minimum on inference; web search / scraping / X-search
cost **$10.00 per 1K requests** on top of tokens
(https://docs.venice.ai/overview/pricing, read 11 Sep 2026) — ~$0.01/search vs the $0.007
Exa fast search we budget. Cache reads run 10–25% of input price (catalogue probe).

**Per-request cost is reported.** The chat-completions response carries a top-level
`"cost": {"usd": 0.00042, "diem": 0}` plus the usual `usage` with
`completion_tokens_details.reasoning_tokens`
(https://docs.venice.ai/api-reference/endpoint/chat/completions, read 11 Sep 2026).
Metering can charge the exact amount, but the field is **top-level, not `usage.cost`** —
`OpenRouterProvider.complete` would need a one-line change.

The x402 top-up quote is **flat $5**: I POSTed `{"amount":0.1}`, `{"amount":2}`,
`{"amount":1000000}` and `{}` to `/api/v1/x402/top-up` and every 402 came back with
`amount: "5000000"` (6-decimal USDC). An unauthenticated `/chat/completions` returns a 402
quoting `10000000` (= $10). So the granularity is a fixed prepaid top-up, not true
pay-per-request. **Verified by probe, 11 Sep 2026.** No top-up fee is disclosed anywhere
I read; the $5 arrives as balance, but I could not verify net credited amount without paying.

## 3. Speed and reliability

No published latency or throughput numbers (checked docs and status site).
Rate limits by model size class, standard tier
(https://docs.venice.ai/api-reference/rate-limiting, read 11 Sep 2026):
XS 500 req/min & 5M tok/min, S 150/3M, M 100/2M, L 100/2M; partner tier roughly doubles.
Abuse budget: 50 failed or 200 unsupported-feature requests per rolling 30s → 429.
`x-ratelimit-*` headers are returned; overloaded models return 429 (`MODEL_OVERLOADED`).

Status: https://veniceai-status.com/ (read 11 Sep 2026) shows three components
(App, API, Docs) all operational and **no incidents in the 14-day window it displays** —
it does not publish a longer history or an uptime percentage, so I cannot verify annual uptime.

Hosting: Venice self-hosts open-weight models but **proxies** proprietary ones. Its own
privacy doc distinguishes `privacy: private` (69 of 119 models — not retained after the
request) from `privacy: anonymized` (50 models — Venice strips your identity but "the
underlying provider still sees prompt content")
(https://docs.venice.ai/overview/privacy, read 11 Sep 2026). Venice does **not name** the
third parties. Of our five matches, `qwen-3-8-flash` and `openai-gpt-56-luna` are `anonymized`,
i.e. proxied; the three DeepSeek/GLM ones are `private`.

## 4. API shape

Base URL `https://api.venice.ai/api/v1`, OpenAI-compatible `POST /chat/completions`
(docs api-spec, read 11 Sep 2026). Our payload ports nearly unchanged.

Reasoning, three overlapping controls: top-level `reasoning_effort` taking
`none|minimal|low|medium|high|xhigh|max` (wins over `reasoning.effort`); a `reasoning` object
with `effort`, `enabled`, `summary`; and `venice_parameters.disable_thinking` /
`strip_thinking_response`. That is a **superset** of our `{effort}` / `{enabled:false}` seeds,
but our `reasoning={max_tokens:200}` for qwen3.8-flash has **no equivalent** — and
`qwen-3-8-flash` reports `supportsReasoningEffort: false`, so its thinking is not steerable
at all on Venice. `reasoning_content` and `reasoning_details` come back on the message.
Tools, `tool_choice`, `parallel_tool_calls`, built-in `web_search`/`x_search` tool types,
`response_format: json_schema`, and `stream` with `stream_options.include_usage` are all
supported. Venice-specific: the `venice_parameters` block, and a model-suffix form
(`"model:enable_web_search=auto"`).

x402 flow, verified live on 11 Sep 2026:

1. `POST /api/v1/x402/top-up` with no auth → **HTTP 402**, header `payment-required`
   (base64 of the body) and body
   `{"x402Version":2,"accepts":[{"scheme":"exact","network":"eip155:8453",
   "amount":"5000000","asset":"0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
   "payTo":"0x2670b922ef37c7df47158725c0cc407b5382293f","maxTimeoutSeconds":300,
   "extra":{"name":"USD Coin","version":"2"}}, {…solana…}]}`.
   Canonical USDC on Base; a Solana leg is offered too.
2. Retry with an `X-402-Payment` header built from that quote. Settlement window is implied
   by `maxTimeoutSeconds: 300`; actual settlement time is **not documented**.
3. Balance is bound to the **wallet address**, not a key:
   `GET /api/v1/x402/balance/{walletAddress}` returns `canConsume`, `balanceUsd`,
   `minimumTopUpUsd`, `suggestedTopUpUsd`, `diemBalanceUsd`;
   `GET /api/v1/x402/transactions/{walletAddress}` returns a `TOP_UP`/`CHARGE`/`REFUND`
   ledger. Both take a base64 `X-Sign-In-With-X` SIWE (EIP-4361, chainId 8453) header,
   fresh per request (https://docs.venice.ai/overview/guides/x402-venice-api).
   Paid inference responses return `X-Balance-Remaining`.

Key minting **does require staking**, contradicting the "no staking" framing:
`GET /api/v1/api_keys/generate_web3_key` returns a JWT (I fetched one; `exp - iat` = 900s,
matching the documented 15-minute expiry), you `personal_sign` it (EIP-191) and POST back
`{address, signature, token, apiKeyType:"INFERENCE", consumptionLimit:{usd:N}, limitPeriod}`.
The guide's prerequisites state an EVM wallet on Base, ETH for gas, and **"any non-zero VVV
staked — 1 VVV is enough to mint a key"**, against staking contract
`0x321b7ff75154472B18EDb199033fF4D116F340Ff`
(https://docs.venice.ai/guides/integrations/generating-api-key-agent, read 11 Sep 2026).
Note `limitPeriod` defaults to `EPOCH` (daily reset) — set `LIFETIME` explicitly.
**The x402 path avoids key minting entirely and therefore avoids VVV.**

## 5. Terms

https://venice.ai/legal/tos (read 11 Sep 2026). No KYC beyond an email at registration, and
x402 needs no account at all. §21 requires you warrant you are not in a US-embargoed country;
§20.9 forbids access where illegal. §12(d) prohibits "scripted, looped, or automated requests
through the **web interface**" — API automation is permitted under §7.3 subject to volume
limits. Fees are non-refundable except as required by law; credits "have no monetary value,
cannot be redeemed for cash," some expire, and all rights terminate on suspension. Liability
is capped at the greater of 12-months' fees and US$100, with binding individual arbitration.
Practically: a topped-up balance is **one-way and non-recoverable**, so never hold more there
than we can lose, and the ToS assumes an identified accountholder though x402 does not.

## 6. Would it work for us

Integration steps: (1) get USDC onto Base — Hyperliquid withdraws to Arbitrum, so either
bridge Arbitrum→Base or route via a CEX; this is the piece with real operational risk and it
is outside anything Venice controls. (2) Wallet + SIWE signer, per-request
`X-Sign-In-With-X`. (3) Top-up loop: poll `x402/balance/{addr}`, and when `balanceUsd` falls
below a threshold, run the 402 → `X-402-Payment` retry for a $5 tranche. (4) A
`VeniceProvider` alongside `OpenRouterProvider`: same POST body, read cost from top-level
`cost.usd` instead of `usage.cost`, map `reasoning` → `reasoning_effort`, drop `plugins`
for `venice_parameters.enable_web_search`, and rewrite catalogue parsing for per-Mtok floats.

Risks: three of eight roster models are missing, including the two cheapest evaluators;
three of five matches cost 1.25–2.6x more; thinking budgets in tokens are unsupported;
uptime history is unverifiable past 14 days; Venice proxies two of our five matches to
unnamed third parties; balance is prepaid, non-refundable, wallet-bound, and $5-granular;
and the bridge to Base is a new failure mode we would own.

**Recommendation: run both, with OpenRouter as primary.** Venice is genuinely the only
verified fully-programmatic funding rail here — the 402 quote, the public priced catalogue,
the signed-token key mint and the per-request `cost.usd` are all real and I probed them. But
it is not cheaper, not more complete, and not more observable than what we have. Build the
`VeniceProvider` and a $5-tranche top-up loop as a *funding fallback* the routers may select
when the OpenRouter balance cannot be replenished, keep `provider = "openrouter"` in
`worlds/testnet.toml` for the models that exist on both, and do not migrate the roster.
Marketing to discount: "private and anonymous inference" (the blog, 9 Apr 2026) is not a
technical claim for the 50 `anonymized` models, and "no account required" is true of x402
but not of the API-key path, which needs staked VVV.
