# Model and parameter governance

Any change to fees, thresholds, risk parameters, execution logic or strategy
parameters is a versioned change. Nothing is adjusted silently.

## Change log format

| Date | Parameter | Old | New | Reason | Performance before | Performance after | Approved by |
|---|---|---|---|---|---|---|---|
| 2026-09-07 | (initial values) | — | see `config/risk_limits.paper.yaml` | Phase 1 baseline; conservative starting points, not recommendations | n/a | n/a | pending operator review |

## Rules

1. **Reason first.** A change entry without a causal explanation is not a
   reason ("improves backtest" is a result, not a cause).
2. **Never optimise against the evaluation dataset.** Parameters fitted to the
   final out-of-sample set have destroyed that set's value permanently.
3. **Never loosen a limit because the strategy is unprofitable.** If a strategy
   only works with wider slippage tolerance, the strategy does not work.
4. **Tightening is cheap, loosening is expensive.** Tightening may proceed on
   operator judgement; loosening requires evidence that the old value was
   wrong, and the evidence goes in the log.
5. **Structural changes carry a version bump.** Changing what a parameter
   *means* (not its value) invalidates prior performance comparisons; say so
   explicitly in the entry.
6. **Per-strategy overrides may only tighten** — enforced in
   `StrategyLimits.apply`, not left to review.

## What must be recorded per change

`old value`, `new value`, `reason`, `date`, `performance before`,
`performance after`, and who approved it. The "performance after" cell is
filled in at the next review, not at change time; an empty cell is a reminder
that the change has not yet been evaluated.
