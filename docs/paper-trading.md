# Paper trading

## Running

```bash
python -m arbcore.app.run_paper --ticks 90000 --tick-ms 5000 --scenario realistic
python -m arbcore.app.run_paper --ticks 90000 --tick-ms 5000 --scenario mechanics --db artifacts/run.sqlite
```

Every run is seeded and reproducible. Results and analysis:
[PAPER_RUN_REPORT.md](PAPER_RUN_REPORT.md).

## Same architecture as live

Paper mode runs the **entire** decision path: market-data integrity, quality
scoring, executable pricing, the full cost stack, the risk engine, inventory
reservation, order state, settlement, reconciliation, persistence and metrics.
The only substitution is the venue itself. There is no simplified paper branch,
because a simplified simulator validates a system that will not be the one
running.

## The calibration problem, and how it is resolved

Execution probabilities must be **measured** (§14: never invent probabilities).
That creates a bootstrap: no data → no EV → no trade → no data.

Resolved by an explicit calibration mode, not by inventing a prior:

* Flagged per proposal (`TradeProposal.calibration`), never implicit.
* **Paper mode only** — enforced in the risk engine. Real capital is never
  committed to gather statistics.
* Capped at 10% of `max_trade_size`, on top of every other limit.
* Has its own daily budget (`max_daily_calibration_trades`), which cannot
  weaken real-money controls because calibration cannot happen outside paper.
* Subject to **every** other hard limit unchanged.
* Its P/L is tracked separately and **excluded from strategy performance**.

Once `ExecutionStatistics` has 50 observations it produces measured
probabilities, calibration stops, and the normal EV path takes over.
`UNKNOWN` outcomes are counted as failures — an unresolved outcome cost us a
reconciliation and possibly money; treating it as "didn't happen" would flatter
every estimate.

## The simulated operator

SAFE MODE and circuit breakers require a human to clear. Correct, but it means
an unattended session stops permanently at the first fault.
`SimulatedOperator` stands in for that human, and is **deliberately visible**:
every intervention is counted and reported.

A session needing 81 interventions in two simulated days has not demonstrated
that the system works — it has demonstrated that the system is unoperable. That
number was a headline finding (F-22) and drove a real design change. The
operator refuses to run outside paper mode: a simulated human must never clear
a halt that guards real capital.

## Scenarios

`realistic` is the configuration whose economic conclusion should be believed.
`mechanics` exists to make trades happen so the execution and failure paths get
exercised; its P/L is an artefact of chosen parameters and every report says so.

## What paper trading here does *not* establish

* Real profitability — the price process is a choice, not a market.
* Real venue behaviour — no rate limits, outages, or withdrawal queues.
* Paper-vs-reality error — that needs real market data (Phase 3/4/16).
