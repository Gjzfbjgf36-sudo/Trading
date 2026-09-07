# Monitoring and alerting

## Metrics (`monitoring/metrics.py`)

`PerformanceReport` requires every field by construction. **ROI is never
presented alone**: a report that omits rejection counts, failure rates,
slippage and drawdown is not shorter, it is misleading.

Reported: opportunities detected / accepted / rejected, rejection histogram by
reason, trades, wins, losses, gross profit, gross loss, fees, net P/L,
calibration P/L (separately), infrastructure cost, **net after infrastructure**,
average win, average loss, win rate, profit factor, max drawdown, execution
failure rate, partial-fill rate, unknown outcomes, slippage and latency
distributions.

Series report median, p95 and worst alongside the mean, because execution costs
are not symmetric — the mean of a latency distribution hides exactly the tail
that kills opportunities.

Undefined ratios return `None`, never a flattering default: win rate with no
trades is `None`, not 0; profit factor with no losses is `None`, not infinity.

Drawdown tracks the running equity curve, not closed trades, so a sequence of
small losses is visible before it becomes a large one.

## Infrastructure cost

Tracked as a first-class cost, and the reported bottom line is **net profit
after infrastructure**. In the paper runs, trading P/L of +26.15 against an
infrastructure cost of 41.67 produced a net of **−15.52** — the single most
important number either run produced.

## Alerting (`monitoring/alerts.py`)

The scarce resource is operator attention, not messages.

* **Severity is fixed per event kind**, not chosen at the call site, so the same
  condition always alerts the same way.
* **Repeats are suppressed** within a window. An alert that fires two hundred
  times trains the operator to ignore it.
* **CRITICAL is never suppressed.** A repeated CRITICAL is not noise: the
  condition is still live and nobody has dealt with it.

CRITICAL: circuit breaker, SAFE MODE, daily loss limit, wallet mismatch,
reconciliation mismatch, unknown state.
WARN: execution failure, abnormal slippage, inventory imbalance, API/RPC
failure, unusual P/L, strategy degraded.
INFO: high-quality opportunity, trade executed.

## What to watch operationally

| Signal | Why |
|---|---|
| Operator interventions per day | Above a handful, the system is unoperable |
| Unknown-outcome rate | Each one is a manual reconciliation |
| Partial-fill rate | A sizing problem; drives automatic degradation |
| Book resync rate | Feed health; distinct from initial snapshots |
| Rejection histogram | Says *why* the system is not trading |
| Net after infrastructure | The only P/L figure that matters |
