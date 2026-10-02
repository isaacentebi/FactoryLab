"""Tool admission facts are public without exposing any caller's local state."""

from __future__ import annotations

from typing import Any


def tool_admission_schematics(manifest: Any) -> dict[str, Any]:
    """Return detached admission limits and predicates, never advice or local state.

    Chapter II §I.b requires public informational schematics; §II.b leaves the
    enforcement in the existing runtime and adapters. Imports stay local because
    the runtime also retrieves these facts through the cortex.
    """
    from factorylab.cortex.tools import _TYPES, DEFAULT_MAX_OUTPUT_BYTES, TOOL_CPU_S
    from factorylab.runtime.compute import ComputeMixin
    from factorylab.runtime.websearch import (
        DEFAULT_RESULTS,
        MAX_QUERY_CHARS,
        MAX_RESULT_BYTES,
        MAX_RESULTS,
        MAX_SNIPPET_CHARS,
        MIN_QUERY_CHARS,
    )
    from factorylab.world.connector import (
        HEADER_ALLOWANCE_BYTES,
        MAX_HOST_LABEL_CHARS,
        MAX_ORIGIN_CHARS,
        MAX_PATH_CHARS,
        ORIGIN_HOST_PATTERN,
    )
    from factorylab.world.exchange import FakeExchange
    from factorylab.world.treasury import FEE_FREE
    from factorylab.world.universe import named_dexes
    from factorylab.world.vaults import (
        CREATE_FEE_USD,
        DESCRIPTION_LENGTH,
        LEADER_MIN_FRACTION,
        MIN_CREATE_USD,
        NAME_LENGTH,
    )
    from factorylab.world.venue_tools import (
        _BASE_WEIGHT,
        _ITEMS_PER_WEIGHT,
        PER_DEX_WEIGHT,
        READ_WINDOW_NS,
    )
    from factorylab.world.x402 import TOP_UP_MICRO

    return {
        "dispatch": {
            "max_tool_calls": manifest.tools.max_tool_calls,
            "max_tool_rounds": ComputeMixin.MAX_TOOL_ROUNDS,
            "outside_text_tools": sorted(ComputeMixin.OUTSIDE_TEXT_TOOLS),
            "outside_text_continuation_kinds": sorted(ComputeMixin.PARSE_KINDS),
            "read_only_kinds": sorted(ComputeMixin.READ_ONLY_KINDS),
            "consequence_writes": sorted(ComputeMixin.CONSEQUENCE_WRITES),
            "writing_channels": sorted(ComputeMixin.WRITING_CHANNELS),
            "rules": {
                "registered": "A call requires a registered tool held by its caller.",
                "budget": "The tool's price bound fits the request's remaining ceiling after "
                "earlier compute, tools and the final-answer reserve; a connector's "
                "bound includes its registered seller cap and web.search uses its "
                "manifest call cap. Meter reservations also require spending authority.",
                "write_authority": "Each decision in the entire parent chain has a producing "
                "writing channel and an open consequence account, and none has return "
                "kind Verdict, MetaVerdict or CounterVerdict. A missing ancestor refuses "
                "consequence writes.",
                "continuation": "Another tool round requires a successful previously unanswered "
                "read, no dispatched non-read or child, a live wallet, and room below "
                "max_tool_rounds. Remaining money covers two priced continuation calls. "
                "Unpriced calls and withheld read bodies grant no further read round. "
                "Continuation children are refused.",
                "outside_text": "Successful outside-text reads and catalogue.search results "
                "containing models restrict subsequent rounds to "
                "outside_text_continuation_kinds. Any other kind ends the round grant.",
                "invalid_batch": "A batch containing a write is dropped as a whole when a call "
                "was already refused in validation; a read-only batch can lose individual "
                "invalid or over-limit calls. Refused calls do not count toward the live-call cap.",
                "batch": "Multiple venue and Polymarket writes are preflighted together; a "
                "preflight refusal prevents their submission while reads still run. "
                "This is admission, not transactional rollback of venue execution.",
            },
        },
        "population": {
            "cpu_seconds": TOOL_CPU_S,
            "default_max_output_bytes": DEFAULT_MAX_OUTPUT_BYTES,
            "property_types": sorted(_TYPES),
            "rules": {
                "arguments": "The schema has type object and properties with supported top-level "
                "types, string required names and boolean additionalProperties. Arguments "
                "are objects with string keys, all required names and exact declared "
                "Python JSON types; booleans are not integers or numbers. Extra arguments "
                "are refused unless additionalProperties is true. Nested constraints "
                "are not checked by this subset. Arguments serialize as finite JSON.",
                "execution": "No available jail means no execution. Wall time is the registered "
                "timeout; CPU time is min(timeout_s, cpu_seconds). Timeout and nonzero "
                "exit precede result validation. Stdout fits the runner's UTF-8 byte cap "
                "and parses as a JSON object without non-JSON numeric constants.",
                "result": "An optional returns_schema checks the same top-level property types "
                "and required names; additional result properties default to allowed. "
                "A broken result contract returns an error, not the object.",
            },
        },
        "connector": {
            "max_origin_chars": MAX_ORIGIN_CHARS,
            "max_host_label_chars": MAX_HOST_LABEL_CHARS,
            "max_path_chars": MAX_PATH_CHARS,
            "origin_host_pattern": ORIGIN_HOST_PATTERN,
            "header_allowance_bytes": HEADER_ALLOWANCE_BYTES,
            "max_bytes": manifest.connectors.max_bytes,
            "timeout_s": manifest.connectors.timeout_s,
            "max_calls_per_window": manifest.connectors.max_calls_per_window,
            "rules": {
                "origin": "The canonical origin is exactly https:// plus its lowercase hostname, "
                "with no port, credentials, path, query or fragment. Host labels are "
                "nonempty and do not start or end with hyphens; host characters are "
                "lowercase ASCII letters, digits, dots and hyphens.",
                "host": "Literal IP origins, localhost and .localhost/.local/.internal names, "
                "denylisted hosts and their subdomains are refused.",
                "addresses": "Every DNS address is globally routable unicast, not loopback, "
                "private, link-local, multicast or IPv4-mapped, and outside denylisted "
                "networks. An empty DNS answer is refused.",
                "path": "The path begins with one slash, contains no backslash or fragment, "
                "and contains only ASCII characters strictly between space and DEL.",
                "state": "Recorded-market worlds have no connector reads. The id names a "
                "registered connector. Attempts fit the caller's per-window cap; a "
                "paid source's whole cap fits available spending authority before fetch. "
                "Attempts count before transport, including failed transport.",
                "transport": "Redirects are refused. Body bytes fit max_bytes; the raw response "
                "including headers fits max_bytes plus header_allowance_bytes. The "
                "whole fetch fits timeout_s. Paid data is returned only after metering.",
            },
        },
        "web_search": {
            "query_chars": [MIN_QUERY_CHARS, MAX_QUERY_CHARS],
            "max_results": MAX_RESULTS,
            "default_results": DEFAULT_RESULTS,
            "max_snippet_chars": MAX_SNIPPET_CHARS,
            "max_result_bytes": MAX_RESULT_BYTES,
            "max_call_micro": manifest.web.max_call_micro,
            "rules": {
                "arguments": "The stripped query fits query_chars and max_results is an integer "
                "from one through max_results. Unknown arguments are refused.",
                "price": "The priced search completion ceiling fits both max_call_micro and "
                "the caller's available spending authority before a provider call.",
            },
        },
        "venue_reads": {
            "window_ns": READ_WINDOW_NS,
            "base_weights": dict(_BASE_WEIGHT),
            # A read the live adapter sends once per perp dex weighs this much more for
            # each HIP-3 dex the world names (``named_hip3_dexes``).
            "per_dex_weights": dict(PER_DEX_WEIGHT),
            "named_hip3_dexes": len(named_dexes(manifest.exchange.coins)),
            "item_weights": {
                tool: {"argument": key, "items_per_weight": per, "fallback_items": most}
                for tool, (key, per, most) in _ITEMS_PER_WEIGHT.items()
            },
            "rules": {
                "slot": "Venue and Polymarket public reads require a venue reader slot.",
                "weight": "Schema-invalid reads are refused before admission or charge. A read's "
                "base weight plus per_dex_weights times named_hip3_dexes plus "
                "ceiling(items/items_per_weight) fits its seat's "
                "remaining sliding-window share. A zero-weight read uses no share. "
                "Cached tick answers still consume the same slot admission charge.",
                "market": "Public reads use listed instruments; funding_history refuses spot "
                "pairs. A stale account snapshot does not answer venue.positions.",
            },
        },
        "orders": {
            "collateral_max_age_ns": manifest.tick_interval_ns,
            "rules": {
                "anchor": "Non-cancellation order writes require the measured fill stream's "
                "account baseline and no submitted spot/perps class transfer awaiting "
                "receipt. A recorded market that ended refuses writes.",
                "identity": "An existing client id can only name the identical operation and "
                "arguments; retrying reconciles or returns the old result, never resubmits.",
                "inventory": "A spot sell or close is positive and no larger than both accounted "
                "spot inventory and the sum of accounted spot lots.",
                "freshness": "A non-reduce-only order requires an available collateral view and "
                "mid, no stale marker, and an observation timestamp. A nondeterministic "
                "venue's observation is no older than collateral_max_age_ns. Explicit "
                "reduce_only bypasses this runtime collateral check.",
                "perps": "Mark is max(mid, limit price when supplied). Incremental margin is "
                "max(0, abs(position + signed size) - abs(position)) * mark / venue "
                "leverage. It plus open-order holds not already in margin used and "
                "earlier batch commitments fits eligible equity minus margin used. "
                "Zero exposure increase needs no margin. Unknown leverage or holds "
                "defers this comparison to venue acceptance, not an assumed leverage.",
                "spot": "A buy's size * mark plus earlier batch commitments fits available "
                "spot USDC; a sell's size fits available base balance.",
                "batch": "Identical placements within one batch are refused. Earlier placements "
                "and vault writes consume their respective perp or spot collateral "
                "pools for subsequent admission; an existing intent is a retry, not "
                "a new commitment.",
                "answer": "Only an ok, nondeclining answer of an answer-order kind places an "
                "action=order with explicit coin, buy/sell side and readable size. A "
                "dead wallet, prior venue tool write, refused venue write or whole "
                "dropped tool batch prevents an answer from placing a replacement order.",
            },
        },
        "venue_adapters": {
            "default_fake_max_leverage": str(
                FakeExchange.__dataclass_fields__["max_leverage"].default
            ),
            "rules": {
                "order": "An order names perp or spot, a finite positive size and, for a limit, "
                "a finite positive price. Trading permission and market class match "
                "the instrument; the venue's listed lot, price precision and minimum "
                "order value constrain acceptance. A live wire value is positive, "
                "finite and representable after venue precision rounding.",
                "reduce_only": "A perp reduction requires an opposite-side open position and "
                "is capped at its size; a spot buy cannot reduce. A close needs a "
                "position or spot balance, a positive size when supplied, and a size "
                "that survives venue precision rounding.",
                "cancel": "A fake cancellation names a known order on the supplied coin. A "
                "live cancellation names a nonzero decimal integer venue identity; one that "
                "names no coin is sent on the order's own market as the venue's open orders "
                "state it, never on every market, and is not sent while the order is not "
                "among them; already-terminal orders and a lookup bound to another id are "
                "refused.",
                "leverage": "Spot has no leverage. Perp leverage is a positive integer. The "
                "default random-walk fake also caps it at default_fake_max_leverage; "
                "a supplied fake adapter uses its own max_leverage. Live leverage above a "
                "market's listed max_leverage is refused before signing, and a market "
                "whose listed margin is isolated takes an isolated setting. "
                "Recorded-market leverage and other recorded-market refusals are "
                "published in venue.instruments.",
                "fake_margin": "At an exposure-increasing simulated fill, total per-coin "
                "absolute notional divided by that coin's leverage does not exceed "
                "perp equity. Spot buys reserve cost plus fees from available USDC "
                "and sells require available base, with resting-order holds deducted.",
                "signing": "Live writes without a signing adapter are refused. Repeated client "
                "identities reconcile rather than submitting another order.",
            },
        },
        "vaults": {
            "minimum_create_usd": str(MIN_CREATE_USD),
            "create_fee_usd": str(CREATE_FEE_USD),
            "name_chars": list(NAME_LENGTH),
            "description_chars": list(DESCRIPTION_LENGTH),
            "leader_min_fraction": str(LEADER_MIN_FRACTION),
            "rules": {
                "amount": "Vault writes use finite positive amounts exactly representable in "
                "integer micro-USD. Creation meets the named length and deposit limits.",
                "state": "A submitted class transfer blocks vault writes. An existing client "
                "identity only reconciles the identical operation and arguments.",
                "vault": "A transfer requires a readable open vault. A deposit requires "
                "deposits enabled unless the account leads it; the simulated venue "
                "also refuses a nonleader deposit diluting the leader below "
                "leader_min_fraction.",
                "withdraw": "Withdrawal fits owned equity and venue max_withdrawable_usd; "
                "the vault observation is at or after the lockup end. A leader's "
                "remaining share cannot fall below leader_min_fraction.",
                "collateral": "Creation needs deposit plus create_fee_usd; a deposit needs "
                "its amount. These plus earlier batch commitments fit fresh perps "
                "eligible equity minus used margin and unreflected resting-order "
                "holds. Unknown holds or unreadable collateral refuse the write.",
            },
        },
        "composition": {
            "max_depth": manifest.tools.max_depth,
            "rules": {
                "depth": "The root has depth zero; a request at max_depth cannot open a child.",
                "executor": "A kind request draws a live offered contract other than the "
                "requester and NOOP. No eligible contract refuses the request. "
                "Commissioned judging and exposure-shaped adversarial contracts "
                "cannot be requested, including through self.",
                "budget": "The child ceiling is no larger than its parent's remaining "
                "request allowance or currently available compute authority.",
            },
        },
        "treasury": {
            "top_up_micro": TOP_UP_MICRO,
            "fee_free_directions": sorted(FEE_FREE),
            "max_transfer_fee_micro": manifest.treasury.max_transfer_fee_micro,
            "withdrawal_fee_micro": manifest.treasury.withdrawal_fee_micro,
            "cctp_max_fee_micro": manifest.treasury.cctp_max_fee_micro,
            "fake_fee_micro": manifest.treasury.fake_fee_micro,
            "max_venice_per_window": manifest.treasury.max_venice_per_window,
            "max_venice_total_micro": manifest.treasury.max_venice_total_micro,
            "venice_reserve_floor_micro": manifest.treasury.venice_reserve_floor_micro,
            "max_forward_fee_micro": manifest.treasury.max_forward_fee_micro,
            "max_forward_fees_per_window": manifest.treasury.max_forward_fees_per_window,
            "rules": {
                "amount": "USD is a string or integer, not float or boolean; it parses as a "
                "finite positive amount exactly representable as integer micro-USD.",
                "pending": "A current submitted transfer or a nonrecoverable stranded transfer "
                "blocks a new transfer. Parked recoverable strands do not generally block; "
                "a shadow-paid hybrid strand still blocks a new Venice top-up. A withdrawal "
                "or shadow send past the venue's nonce window with no ledger evidence is "
                "parked with its principal and fee held; it blocks no transfer, except that "
                "a parked shadow send blocks a new Venice top-up.",
                "venice": "A Venice top-up equals top_up_micro and fits the remaining window "
                "allowance. Where a lifetime cap exists, prior authorized micro-USD plus "
                "top_up_micro for each stranded or parked Venice transfer and any "
                "submitted current Venice transfer plus the new amount fits "
                "max_venice_total_micro. Every "
                "authorization, reauthorization included, counts toward lifetime use.",
                "fees": "Principal plus the whole transfer fee ceiling fits available wallet "
                "authority; fee_free_directions reserve no fee budget. Prepared fee ceiling "
                "plus fees already incurred fits max_transfer_fee_micro; Venice pays no "
                "separate fee. Forwarded routes fit both the per-route fee cap and remaining "
                "window forwarding fee cap.",
                "scripted": "Nonclass scripted transfers require at least the scripted rail's "
                "five-dollar minimum, fit the source pot, and exceed the fixed fee (zero for "
                "Venice). Class transfers have no such minimum; they fit available class cash.",
                "class": "Live class transfers require a main-account signer without a vault "
                "and sufficient source perps cash or available spot USDC.",
                "cctp": "A live deposit exceeds cctp_max_fee_micro. An exit exceeds both "
                "withdrawal_fee_micro + cctp_max_fee_micro and withdrawal_fee_micro plus the "
                "current route's CCTP fee quote. Each fits the source reserve USDC or venue "
                "perps withdrawable pot. The prepared native gas ceiling fits the remaining "
                "network gas budget and native balance; pots.gas publishes current blockers "
                "and required amounts. A self mint needs Base gas; forwarding may replace it.",
                "hybrid": "Hybrid Venice conversion requires its configured shadow sink, "
                "venue main wallet, sufficient venue perps withdrawable balance and real "
                "mainnet reserve USDC; the reserve after the top-up is at least "
                "venice_reserve_floor_micro. Ordinary Venice conversion is mainnet-only "
                "and credits the wallet account, not the API-key account.",
            },
        },
        "polymarket_orders": {
            "max_order_micro": manifest.polymarket.max_order_micro,
            "max_open_micro": manifest.polymarket.max_open_micro,
            "max_orders_per_window": manifest.polymarket.max_orders_per_window,
            "principal_micro": manifest.polymarket.principal_micro,
            "live_orders": manifest.polymarket.venue == "live" and manifest.polymarket.orders,
            "order_requests_per_10s": manifest.polymarket.order_requests_per_10s,
            "rules": {
                "principal": "When principal_micro is set, a buy is refused when the world's "
                "lifetime signed commitment plus the buy would exceed principal_micro. The "
                "commitment is size times limit price of every placement the world ever "
                "signed, forever: no cancel, read-back, matched size, failed leg, "
                "quarantine, resolution, payout or redemption gives room back, so once the "
                "cap is used, buying stops for the world's life. Only an order that never "
                "existed does not count: one the venue refused with a documented 4xx "
                "refusal of its submission, whose body is exactly one documented error "
                "naming no other order and no duplicate, or the simulated venue's "
                "rejection, and one refused locally before it was signed; a timeout, a 5xx "
                "or any other answer counts in full. No wallet balance or listing enters "
                "it. Cancellations are not refused by it.",
                "maker": "Every order, on every venue kind, is a GTC post-only limit order: "
                "it rests as a maker, and the venue rejects one that would cross before it "
                "executes. A maker is charged no fee, so no fee is booked; a trade that "
                "reports this world's order as a taker, or a fee on it, at any settlement "
                "status and on any sighting, contradicts that, read from the raw trade rows before "
                "any is parsed: it is recorded as drift, and buying stops for the world's "
                "life. A leg of this world's order is that signed order but for its size: its "
                "token, a BUY at exactly its limit; what is booked of an order never passes "
                "its signed size. A leg that is not is malformed: the read stalls, "
                "ledgered, and nothing of it is booked. At a resolution a decision owns only "
                "what its own lots realised; the rest stays in the pot, owned by no "
                "decision.",
                "live": "With live_orders, an order is signed by the pot's wallet and sent to "
                "Polymarket's CLOB; the pot's collateral is pUSD, Polymarket's USDC-backed "
                "token. Its identity is its EIP-712 order hash, recorded with the intent "
                "before it is sent, and a lost answer is looked up by that hash and never "
                "sent again. A fill is booked once, when Polymarket reports its trade "
                "CONFIRMED. No order is taken before the pot's opening (its reconciliation "
                "baseline) is read, and no fill is booked before it. A cancelled or otherwise"
                " terminal order holds only what it matched and is not yet booked. A buy's "
                "exposure and collateral also count, from the world's own records, every buy "
                "not yet booked from a confirmed trade and its booked inventory at cost; the "
                "positions listing is read to its end or the pot is unavailable; no buy is "
                "taken while the pot's last reconciliation found money, gone or arrived, that "
                "its books do not explain. The pot's own requests (orders, cancels, lookups, "
                "fills, account, marks and a write's market read) are at most "
                "order_requests_per_10s"
                " in any sliding 10 s of wall time; one past it is not sent and reads as "
                "unavailable. A placement's submission slot is taken before its intent and"
                " signature: a placement the budget cannot send is refused there, signs "
                "nothing and commits nothing.",
                "arguments": "Schema, string length and token pattern checks precede dispatch. "
                "Size and price are finite decimals, not booleans; size is positive "
                "and price is strictly between zero and one.",
                "cancel": "Cancellation names an order id placed by this world.",
                "market": "New orders fit the window count, name a listed token whose market "
                "is readable and accepting orders, use the market's price tick, and "
                "meet its minimum token size. The custody pot is readable.",
                "notional": "Ceiling-rounded micro-USD notional fits max_order_micro. A buy's "
                "held tokens at cost plus resting buys plus batch commitments and new "
                "notional fits max_open_micro; new notional plus batch commitments "
                "fits this pot's available USDC. Orders are BUY "
                "orders only; a sell is refused before any intent, and a position is held "
                "until its market resolves.",
                "batch": "Duplicate placements are refused. Earlier buys reserve notional, "
                "and earlier orders count toward the window "
                "cap. Existing intents are retries; client identities cannot change "
                "operation or arguments.",
            },
        },
    }
