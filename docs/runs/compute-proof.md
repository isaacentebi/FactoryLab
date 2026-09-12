# Compute rail proof, 12 September 2026 (real money, Base mainnet)

Reserve address `0x1228e5620944a79D268Afc7522E00891526EdEBb`, funded by the experimenter with 10 USDC on Base (inbound `0xae130f2c921df0a4dea062bf79f66f0130b3543a84c397c0a38a00560f227251`). Every payment below was signed by the process from `reserve.key`; no key value was ever printed.

| Step | How run | Receipt | Cost | Result |
|---|---|---|---|---|
| status | `scripts/compute_proof.py` | — | — | 10.000000 USDC, Venice credit 0 |
| Venice top-up, exactly $5 | script | tx `0x83e0be2c0067376c437284f5a64449ea8719e0f21454614cc339ca9d183923c9` | $5.000000 | Venice balance 0 → 5.000000 |
| Venice `z-ai-glm-5-3-flash`, thinking off | script | request `chatcmpl-258b68bd76bdee055cf55d6538ff5785` | $0.000232 (reported) | "OK", finish stop, 1499 in / 14 out; balance 4.999768 |
| FarOuter `glm-5.3-flash` (x402 per request) | provider called directly (see note) | tx `0xdc1a4dc9dce1c569cd60701527bfdffa7eaeecab9fd1353277187200fdba345e` | $0.001000 (quote; true-up billing) | "OK", finish stop |
| Venice reseller (aispace) `z-ai-glm-5-3-flash`, thinking off | provider called directly (see note) | tx `0x15c6d196bf79f0f764fa4cc0a69af9effba7156903fb8b4159be59668eb99e8f` | $0.010000 | "OK" preceded by one leaked line of the model's reasoning |

Balances after: reserve 4.989000 USDC, Venice credit 4.999768. Total spent from the reserve: $5.011; total consumed for inference: $0.011232.

**Verdict.** The factory can buy its own thinking with its own money on three independent rails: a wallet-bound prepaid seller (Venice), and two per-request x402 sellers (FarOuter at a tenth of a cent per call with true-up billing, the Venice reseller at a cent). Signing, quoting, ceilings and settlement references all behaved as designed. The reseller's thinking switch did not fully suppress reasoning text in the answer; a judge would mark that return down, which is the mechanism working.

**Note on the two direct steps.** `scripts/compute_proof.py` proved the first three steps but failed the seller steps inside its own transport wrapper (`X402Error` before any payment; the provider path succeeds on the identical request). The seller purchases were therefore run once each through `factorylab.world.market.X402Provider` directly, under the experimenter's authorisation for this proof, with the raw settlement headers captured. The wrapper defect is filed for the defects workstream; the guards against a second payment were not needed and were not bypassed.
