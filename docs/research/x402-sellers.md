# x402 inference sellers (read 11 September 2026)

Second research pass, after `venice.md`. What a program can pay for LLM inference with USDC and no human.

## Discovery index

- The documented endpoint is `GET https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources` (no key, paginated with `limit`/`offset`), per https://docs.cdp.coinbase.com/x402/bazaar. Live total on 11 September: 14,349 resources. The semantic `/discovery/search` returned zero hits for every inference term tried; `urlSubstring` filtering works.
- Coinbase's Agentic.Market is a curated front end over the same index; its inference category names OpenAI and Venice (https://www.coinbase.com/developer-platform/discover/launches/agentic-market). Launch marketing, not independently audited.

## Sellers found live in the index (OpenAI-compatible `/v1/chat/completions`)

| Seller | Endpoint | Network | Notes |
|---|---|---|---|
| FarOuter | `https://farouter.tech/v1/chat/completions` (per-model paths too) | not stated in listing | Live `GET /v1/models` with per-token USD prices; carries GLM 5.3 Flash (0.0108/0.036 $/Mtok), DeepSeek V4 Flash (0.0158/0.0475), GPT-5.6 Luna (0.0304/0.1824). 5–10x below OpenRouter; reliability unverified; treat as a loss leader until proven. |
| NetIntel | `https://netintel.dev/v1/chat/completions` | Base and Solana | Live `GET /v1/models`; OpenAI and Claude Sonnet catalogue. |
| aispace.bot | `https://x402.aispace.bot/api/v1/chat/completions` | Base | Resells "100+ Venice models"; third-party wrapper. |
| agent402.tools | `https://agent402.tools/v1/chat/completions` | not stated | GPT-4o-mini proxy. |
| animica.dev | `https://animica.dev/x402/v1/chat/completions` | not stated | Tiered pricing. |
| glianalabs.com | `https://glianalabs.com/x402/claude-opus-5` | not stated | Claims 90+ models. |

None carry Qwen 3.7/3.8 Flash, Tencent HY3 or Muse Spark.

## Named candidates

- Venice: verified, see `venice.md`.
- Hyperbolic: `github.com/HyperbolicLabs/hyperbolic-x402`, archived June 2026; chain not stated; no roster models.
- Nous Portal: `inference-api.nousresearch.com/v1`, x402 on Solana USDC (beta), reported by third parties only.
- Chutes: USDC claims are secondary sources; docs describe API-key billing.
- OpenRouter: crypto API 410 (https://openrouter.ai/docs/cookbook/administration/crypto-api); web checkout only; no x402 documented as of this date.
- Vercel and Cloudflare gateways, Requesty, Kilo, Together, Fireworks, DeepInfra, Novita, SiliconFlow, Alibaba: no human-free USDC path found.

## Ranking for the compute-rail proof

1. Venice (credible, verified flow).
2. FarOuter and NetIntel: real endpoints with roster overlap; buy one request each for cents before trusting either.
3. Everything else: not until a concrete USDC-in, completion-out endpoint is verified.
