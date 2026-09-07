# Backtesting and walk-forward evaluation

## The problem being solved

Out-of-sample data is a **consumable resource**. Each time a parameter set is
evaluated against it and then changed in response, the dataset stops being
out-of-sample — the information leaks into the parameters via the researcher.
The usual defence is a rule in a document that nobody enforces.

## The enforcement (`backtest/walkforward.py`)

* `DataSplit` refuses overlapping or empty splits. Overlapping splits are not a
  split.
* Out-of-sample evaluation is **opt-in**. `walk_forward` runs train and
  validation by default and does not touch the out-of-sample set unless
  explicitly asked. It should take a deliberate act, not a default argument.
* `OutOfSampleLedger` records every evaluation against a hash of the exact
  parameters, **persisted to disk** so the budget survives a restart — an
  in-memory budget is reset by whoever least wants to be limited by it.
* A second evaluation of the same parameter set raises `OutOfSampleExhausted`.

The ledger does not prevent the obvious loophole (change one parameter, get a
fresh budget). It makes it **visible and recorded**, which is what a governance
control can honestly achieve.

## Running

```bash
python -m arbcore.app.run_walkforward --scenario mechanics --ticks 6000
python -m arbcore.app.run_walkforward --scenario mechanics --ticks 6000 --out-of-sample
```

Results: [PAPER_RUN_REPORT.md §3](PAPER_RUN_REPORT.md).

## Bias controls

| Bias | Control |
|---|---|
| Look-ahead | Decisions consume only the book state as of the decision tick; fills are computed afterwards |
| Data leakage | Disjoint splits, enforced at construction |
| Overfitting | No parameter is fitted to any split; the strategy is rule-based. The ledger enforces the out-of-sample budget |
| Unrealistic fills | Fills walk the book at size, with latency drift, partial fills, rejections and competition |
| Unrealistic liquidity | Sizes that the book cannot absorb are dropped, not silently shrunk |
| Survivorship | Not yet applicable — a fixed whitelist of two venues and one asset |

## Honest limitation

With synthetic data, walk-forward measures **consistency across regimes**, not
the absence of overfitting to real markets. A degradation factor near 1.0 (the
observed result was 1.02) means the rules behave the same on unseen seeds. It
does not mean they would behave the same on unseen *markets*. Only recorded real
market data can answer that, and that requires venue adapters.
