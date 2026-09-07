# Paper trading report — findings and fixes

Sessions run against a synthetic market, September 2026.
Reproduce with the commands in each section; every run is seeded and
deterministic.

---

## 0. What these runs can and cannot tell you

**Can:** whether the machinery is correct — integrity handling, the decision
funnel, execution paths, failure recovery, reconciliation, accounting — and
how the system behaves operationally under faults.

**Cannot:** anything about real-market profitability. The price process is a
parameter choice, not a market. Two scenarios are used:

| Scenario | Fees | Dislocations | Purpose |
|---|---|---|---|
| `realistic` | Retail taker (0.60% / 0.26%) | Calm major-pair | The economic question. Believe this one. |
| `mechanics` | Assumed high-volume tier (0.05% / 0.04%) | Wide | Force trades so the execution path gets tested. Its P/L is an artefact. |

No venue adapter exists. No API endpoint, fee schedule or transaction format
was invented; the fee numbers above are labelled `confirmed=False` in code.

---

## 1. The economic result (realistic scenario)

```
python -m arbcore.app.run_paper --ticks 90000 --tick-ms 5000 --scenario realistic
```

~5 simulated days, 55,231 gross-positive spreads detected:

| Metric | Value |
|---|---|
| Opportunities detected (gross spread > 0) | 55,231 |
| Opportunities **accepted** | **0** |
| Rejected on `NEGATIVE_NET_PROFIT` | 55,231 (100%) |
| Median net margin after costs | **−111 bps** |
| Best single opportunity | **−100 bps** |
| Median shortfall vs the safety-adjusted threshold | **173 bps** |
| Smallest shortfall observed | **162 bps** |

**Conclusion: at retail taker fees, pre-funded CEX/CEX arbitrage on a major
pair is not viable, and not marginally so.** Every observed spread was
underwater by about one percent before the safety margin was even applied. The
strategy would need cross-venue dislocations roughly 1.7% wider than anything
that occurred.

The cost stack that produces this:

| Line | Rate |
|---|---|
| Taker fee, buy venue | 0.60% |
| Taker fee, sell venue | 0.26% |
| Leg risk (non-atomic, one side may fill alone) | 0.15% |
| Amortised rebalancing (undoing the inventory shift) | 0.04% |
| Partial-fill allowance | 0.08% |
| **Sub-total** | **1.13%** |
| × `cost_safety_factor` 1.5 | **1.70%** |

Two of those lines are the ones most often left out of an arbitrage
calculation, and both are real: **the trade is not atomic**, and **every trade
shifts inventory that must eventually be moved back**.

This result is a *property of the fee structure*, not of the simulator. It
would not change under a different price process — only under materially lower
fees, which is why the mechanics scenario has to assume them.

---

## 2. The operational result (mechanics scenario)

```
python -m arbcore.app.run_paper --ticks 90000 --tick-ms 5000 --scenario mechanics
```

| Metric | Value |
|---|---|
| Opportunities detected | 33,850 |
| Accepted | 196 (0.58%) |
| Strategy trades (post-calibration) | 132 |
| Win rate | 52.3% |
| Profit factor | 3.16 |
| Average win / average loss | 0.554 / 0.192 |
| Max drawdown | 1.79 |
| Trading P/L | **+26.15** |
| Fees paid | 18.96 |
| Infrastructure cost (5.2 days @ 8/day) | 41.67 |
| **Net after infrastructure** | **−15.52** |
| Partial-fill rate | 62.8% |
| Execution failure rate | 8.2% |
| Unknown outcomes (each halted the system) | 4 |
| Operator interventions required | 4 |
| Reconciliation runs / failures | 449 / **0** |
| Slippage median / p95 | 0.75 bps / 8.94 bps |
| Latency median / p95 | 194 ms / 244 ms |

**Even here the system loses money.** Trading P/L was positive; infrastructure
cost was 1.6× larger. At this capital (~1,800 working inventory) the fixed cost
floor dominates. This is exactly the question §58 exists to force: *positive
trading ROI is not economic viability.*

The 62.8% partial-fill rate is the second finding — the strategy is routinely
asking for more size than the book gives it. That triggered the degradation
mechanism once (automatic size reduction), which is the correct response.

---

## 3. Walk-forward validation

```
python -m arbcore.app.run_walkforward --scenario mechanics --ticks 6000 [--out-of-sample]
```

Disjoint seed ranges: train (4 runs), validation (3), out-of-sample (3).

| Split | Trades | Net P/L | P/L per trade |
|---|---|---|---|
| Train | 187 | 46.27 | 0.2474 |
| Validation | 138 | 39.38 | 0.2854 |
| Out-of-sample | 124 | 31.27 | 0.2522 |

**Degradation factor: 1.02** — the result held up out-of-sample. No parameters
were fitted to any split; the strategy is rule-based, so this measures
consistency across regimes rather than the absence of overfitting.

The out-of-sample budget is enforced in code. A second evaluation of the same
parameter set is refused:

```
OutOfSampleExhausted: parameter set 33c37d06163466a6 has already been evaluated
out-of-sample 1 time(s). Evaluating again and then tuning is how an
out-of-sample dataset silently becomes a training set.
```

---

## 4. Defects found and fixed

Every one of these was found by running the system, not by reading it.

| # | Defect | How it surfaced | Fix |
|---|---|---|---|
| F-1 | Synthetic feed published full depth as *incremental* updates, so stale levels accumulated and the book crossed | 1,674 `CROSSED_BOOK` invalidations in 2,000 ticks | Emit true diffs including zero-size deletions. Invalidations fell to 5. **The order book was right throughout** — it refused to trade on a crossed book |
| F-2 | Market-data breaker compared a *cumulative* invalidation count to a fixed threshold | Tripped permanently; 1,993 of 2,000 ticks halted, 332 operator interventions | Rate over a rolling 200-tick window. Interventions → 0 |
| F-3 | Session funded with 1 BTC against a 5,000 exposure limit | 2,000 ticks rejecting 100% of opportunities | Fail fast at construction with an explicit message |
| F-4 | Three size caps stack (`max_trade_size`, non-atomic ×0.25, calibration ×0.10) and the strategy could see none of them | Every proposal structurally guaranteed to be rejected | Runner derives the largest size satisfying all caps, priced at the worst visible ask |
| F-7 | Quote balance reserved at the **decision** price; fills arrive at a latency-worsened price | `ValueError: settlement spends 49.680 but only 49.678 was reserved` | Reserve at the **limit** price. A real venue reports this as insufficient funds |
| F-8 | Working inventory is marked to market, so its value drifts into the asset-exposure cap without any trading | 95% of all rejections were `ASSET_EXPOSURE_EXCEEDED` from price drift alone | Working inventory capped at 60% of `max_asset_exposure`; the rest is drift headroom |
| F-10 | Calibration-trade P/L was counted as strategy performance | All 32 trades in one run were calibration trades, reported as strategy P/L | Separate `PnLTracker`; the report shows both and never merges them silently |
| F-12 | `trades_today` never reset — a "daily" limit that was really a lifetime limit | 11,414 of 11,596 rejections were a daily limit that could never clear | UTC day rollover resets daily counters |
| F-16 | Runner transitioned `EXECUTING → REJECTED` when a reservation failed | `IllegalTransition` raised by the lifecycle | Reserve *before* declaring the opportunity executing. **The state machine caught this** |
| F-17 | Paper simulator had no adverse selection: 98% win rate, profit factor **672** | A number that says the model is wrong, not that the strategy is good | Competition model: with probability 0.35 a faster participant takes the liquidity, producing a worse price, a smaller fill, or nothing. Win rate → 52%, profit factor → 3.2 |
| F-21 | Runner transitioned `FAILED → SETTLEMENT` | `IllegalTransition` — a path only reachable once F-17 made total failure possible | Failed attempts go straight to `RECONCILIATION`. **The state machine caught this too** |
| F-22 | Partial fills counted as execution failures for the reliability breaker | 62% partial rate tripped the breaker; 81 interventions in 2 simulated days | Reliability and sizing are different problems. Failures trip the breaker (stop); partials drive strategy degradation (smaller size). Interventions → 2 |
| F-24 | A book's first snapshot was counted as a fault *recovery* | 2 "resyncs" in a fault-free run | Distinguish initial sync from recovery, so the recovery rate is honest |
| F-25 | Calibration needs 50 samples; `max_daily_trades` is 50. The strategy could never place a non-calibration trade on day one | Walk-forward showed `trades: 0` in every run | Separate paper-only calibration budget. It cannot weaken real-money controls because calibration is impossible outside paper mode |

Two observations about that table:

* **The safety mechanisms found the bugs.** The opportunity state machine caught
  two illegal control-flow paths (F-16, F-21). The order book refused to price a
  crossed book (F-1). The inventory manager refused to settle more than was
  reserved (F-7). None of these needed a test written in advance.
* **Three of the fixes made the system look worse** (F-17, F-22, F-10) — lower
  win rate, lower reported P/L, honest separation of calibration results. Those
  are the valuable ones.

---

## 5. Risks confirmed by running

| Risk register # | What the runs showed |
|---|---|
| #2 Partial fill leaves one-sided exposure | 123 unmatched legs across 196 attempts. Each was charged at the modelled leg-risk rate rather than carried as an unpriced position |
| #6 MEV / competition | Modelling it at all cut the profit factor from 672 to 3.2. Competition is not a detail |
| #15 Unknown transaction state | 4 occurrences. Each engaged SAFE MODE, was resolved by *querying* the venue, and required a human to clear. Rate: 1 per 49 accepted trades |
| #21/#22 Stale data, clock drift | 139 `QUOTE_EXPIRED` and 139 `DATA_QUALITY_BELOW_THRESHOLD` rejections. Working |
| #28 Inventory imbalance | Reservations blocked 13 times by the minimum-reserve rule. Working |
| #33 Infrastructure cost exceeds trading profit | **Confirmed, and decisive.** +26.15 trading, −41.67 infrastructure |
| #34 Not viable at available capital | **Confirmed.** See §1 and §2 |

New risks discovered, now in the register:

* **R-38 Working-inventory mark drift consumes exposure budget.** Pre-funded
  inventory is valued at market; a few percent of upward drift halts the
  strategy. Mitigated by the 60% headroom rule; residual MEDIUM.
* **R-39 Human-intervention rate.** One unknown-state halt per ~49 accepted
  trades. An unattended system stops roughly that often. Residual MEDIUM — this
  is an *operability* limit, not a safety one, and it is the right trade.
* **R-40 Calibration bootstrap.** Measured probabilities require trades;
  trades require probabilities. Resolved by an explicit, paper-only,
  size-capped calibration mode whose results are excluded from performance.
  Residual LOW, but note it means **live trading can never be reached without a
  paper phase** — which is the intended constraint.

---

## 6. Known limitations of these runs

1. **Synthetic prices.** A mean-reverting offset around a shared random walk.
   Real cross-venue spreads are not this well-behaved.
2. **No real venue behaviour.** No rate limits, no maintenance windows, no
   partial API degradation, no withdrawal queues.
3. **Competition model is a parameter, not a measurement.** 0.35 probability
   and a 2–20 bps adverse move are assumptions (`UNCERTAIN` in
   `docs/assumptions.md`), chosen to be pessimistic rather than to be right.
4. **Single asset, two venues, one strategy.** DEX, CEX→DEX and cross-chain
   strategies have no implementation at all.
5. **Paper fills use the book we saw.** Even with the competition model, real
   queue position and hidden liquidity are not represented.
6. **No paper-vs-reality validation is possible yet** — that needs real market
   data, which needs adapters, which need official documentation (Phase 3/4).

---

## 7. What follows from this

**The honest recommendation: do not pursue CEX/CEX BTC arbitrage at retail fee
tiers.** The gap is ~170 bps, which is not a tuning problem. Options, in order
of how much they actually change the answer:

1. **Change the fee structure** — high-volume tiers or maker rebates. This is
   the only lever that closes a 170 bps gap, and it requires volume the
   strategy cannot generate because it cannot trade.
2. **Change the market** — less liquid pairs or venues have wider spreads and
   correspondingly worse liquidity, higher counterparty risk and thinner exit.
   That is a different strategy with a different risk model, not a parameter
   change.
3. **Change the strategy** — DEX-based approaches have a different cost
   structure (gas and priority fees instead of taker fees) and, where atomic
   execution is possible, no leg risk. That is Phase 4 work and its own
   research question.
4. **Do not trade.** Given the numbers above, this is the default and it needs
   no justification.

Whatever is chosen, the infrastructure result stands independently: at ~1,800
of working capital, an 8/day cost floor cannot be covered by any edge this
strategy produced. **Minimum viable capital is a prerequisite question, not an
afterthought.**
