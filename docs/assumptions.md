# Assumptions register

Every assumption the system makes is listed here with an epistemic status.

| Status | Meaning |
|---|---|
| `CONFIRMED` | Verified against a primary source (official docs, on-chain state, our own measurement) with the source recorded |
| `LIKELY` | Strong indirect evidence, no primary verification yet |
| `UNCERTAIN` | Plausible, materially unverified |
| `UNKNOWN` | Not established at all |

**`UNKNOWN` never silently becomes `CONFIRMED`.** Promotion is an explicit,
reviewed change with the source cited. The `Confidence` enum in
`config/whitelist.py` carries this status in code, and the Phase 1 stablecoin
entries are deliberately `UNKNOWN`.

## Design assumptions (Phase 1)

| # | Assumption | Status | Basis / how it gets promoted |
|---|---|---|---|
| A1 | Decimal arithmetic is required; float is unsafe for money | CONFIRMED | Well-established; enforced in `domain/types.py` |
| A2 | A displayed price is not an executable price | CONFIRMED | Universal market-microstructure fact; drives the Phase 6 executable-price engine |
| A3 | Two independent transactions on two venues are not atomic | CONFIRMED | No shared commit protocol exists between a CEX and a chain |
| A4 | A CEX/CEX spread cannot be captured by buy→transfer→sell in real time | CONFIRMED | Withdrawal + settlement + deposit crediting far exceed spread lifetime |
| A5 | Profitable DEX opportunities attract competition | LIKELY | Widely documented MEV behaviour; to be measured in Phase 4/6 |
| A6 | Stablecoins can deviate from 1.00 materially | CONFIRMED | Multiple historical depeg episodes |
| A7 | `USDC` on Ethereum ≠ `USDC` on Solana for inventory purposes | CONFIRMED | Distinct issuances/representations; modelled in `AssetId` |
| A8 | Exchanges, RPCs, DEXs and bridges have outages | CONFIRMED | Operational history of every such system |
| A9 | The process can crash at any instant | CONFIRMED | Assumed unconditionally; drives start-in-SAFE-MODE |
| A10 | Our cost models understate real costs | LIKELY | Standard experience; expressed as `cost_safety_factor ≥ 1` |
| A11 | The non-atomic size factor of 0.25 is prudent | UNCERTAIN | A judgement, not a measurement. Revisit with Phase 10 leg-risk data |
| A12 | Paper-trading fills approximate real fills | UNKNOWN | Only Phase 16 paper-vs-reality measurement can promote this |
| A13 | Execution probabilities can be estimated from our own data | LIKELY | Demonstrated in paper: 196 measured attempts produced stable estimates. Promotion to CONFIRMED needs real fills |
| A14 | Leg-risk fraction of 0.15% for an unmatched CEX/CEX leg | UNCERTAIN | A judgement. Paper runs produced 123 unmatched legs in 196 attempts, so the line is material and worth measuring properly |
| A15 | Competition takes ~35% of visible opportunities at a 2–20 bps cost | UNCERTAIN | Chosen to be pessimistic, not to be right. It is the single most influential parameter in the paper results (profit factor 672 → 3.2) and must be replaced by measurement |
| A16 | Retail taker fees exceed available CEX/CEX BTC spreads by ~170 bps | CONFIRMED (arithmetic) | Follows from the published fee tiers and the cost stack; independent of the price process. See PAPER_RUN_REPORT.md §1 |
| A17 | Paper fills approximate real fills | UNKNOWN | Unchanged. Only Phase 16 paper-vs-reality measurement can promote this |

## Venue and integration assumptions

All are `UNKNOWN` in Phase 1 because no integration exists. Phase 3/4 records
each from **current official documentation**, with API version, authentication
model, rate limits, WebSocket semantics, order semantics, fee schedule and
withdrawal constraints.

| # | Assumption | Status |
|---|---|---|
| B1 | Coinbase API version, semantics, fees, limits | UNKNOWN |
| B2 | Kraken API version, semantics, fees, limits | UNKNOWN |
| B3 | Binance availability to this operator and its API semantics | UNKNOWN — also a legal question, see `docs/regulatory.md` |
| B4 | Which DEXs offer executable (not indicative) quotes | UNKNOWN |
| B5 | Contract addresses for every protocol we would touch | UNKNOWN — no protocol is whitelisted |
| B6 | Stablecoin mint / freeze / transfer-restriction findings per chain | UNKNOWN — recorded as such in the whitelist |
| B7 | Bridge latency, fee and failure characteristics | UNKNOWN |

**No endpoint, SDK method, fee number or transaction format is invented.**
Where documentation is unclear, the integration stops and the ambiguity is
flagged rather than guessed.

## Assumptions the system explicitly refuses to make

* That an order sent is an order filled.
* That an order will fill completely.
* That a retry is safe when the prior state is unknown.
* That high daily volume implies executable depth at our size.
* That a positive theoretical ROI implies economic viability at our capital.
* That an unknown fee is a zero fee.
* That a missing probability is a favourable probability.
