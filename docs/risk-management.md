# Risk management

## Hierarchy of objectives

1. Capital preservation
2. Security
3. Data integrity
4. Execution correctness
5. Risk control
6. Profitability — last, and only after the five above hold

When these conflict, the lower number wins. Concretely: a change that increases
expected profit while weakening a data-integrity check is rejected.

## Hard limits

Defined in `src/arbcore/config/limits.py`, configured in
`config/risk_limits.paper.yaml`. All are validated at load time; an inconsistent
configuration fails at start-up rather than at trade time.

| Limit | Meaning |
|---|---|
| `max_trade_size` | Largest notional per opportunity (× 0.25 for non-atomic strategies) |
| `max_total_exposure` | Portfolio-wide ceiling |
| `max_asset_exposure` / `max_exchange_exposure` / `max_chain_exposure` | Concentration ceilings |
| `max_daily_loss` | New trades stop for the day |
| `max_daily_trades` / `max_concurrent_trades` | Activity ceilings |
| `max_inventory_imbalance` / `min_reserve_balance` / `max_rebalance_cost` | Inventory safety |
| `max_slippage` / `max_price_impact` | Microstructure ceilings |
| `max_quote_age_ms` / `max_execution_latency_ms` / `max_clock_drift_ms` | Freshness ceilings |
| `min_net_profit_margin` / `cost_safety_factor` | Economic floor |
| `min_data_quality_score` | Data floor |
| `max_stablecoin_depeg` | Peg tolerance |

Two properties are enforced in code, not by convention:

* **A limit is a wall.** No score, ranking, expected value or operator
  enthusiasm crosses one. The risk engine does not receive a score at all.
* **Overrides only tighten.** `StrategyLimits.apply` refuses any per-strategy
  value that loosens the global limit — including *lowering* a minimum such as
  `min_data_quality_score`, which is loosening even though the number falls.

### The per-venue counterparty cap

Each whitelisted venue carries `max_balance` independent of the risk limits.
The engine applies the tighter of the two. An exchange is a counterparty, and a
counterparty cap is not a risk parameter to be tuned for yield.

## Risk budgets

Risk is not one number (`risk/budget.py`). Each strategy holds separate budgets
for `MARKET`, `EXECUTION`, `LIQUIDITY`, `TECHNOLOGY`, `COUNTERPARTY`,
`SMART_CONTRACT` and `SETTLEMENT`.

* A category with no allocation is **not** unlimited — `StrategyBudget.get`
  raises, and the engine rejects.
* A proposal that reserves no budget at all is rejected: every trade consumes
  something.
* Over-consumption is **recorded, not clipped**. A post-mortem needs to see by
  how much an allowance was blown.
* Exhaustion disables the strategy. Refilling is a governance action
  (`docs/model-governance.md`), never automatic.

## Prohibited behaviours

These are design constraints, not preferences:

* **No martingale, no revenge trading.** Position size is never a function of
  recent losses.
* **No risk increase to recover a loss.** A losing strategy gets smaller or
  stops; it never gets bigger.
* **No automatic capital scaling after profit.** Scaling requires a minimum
  observation period, stable execution, acceptable drawdown / failure rate /
  slippage, successful reconciliation and manual approval.
* **No blind retries.** After a failure the system identifies the transaction
  or order, queries its actual status, queries balances, determines which legs
  executed, computes exposure and reconciles — and only then decides. An
  unresolvable state is `UNKNOWN`, which means SAFE MODE.
* **No backtest-driven parameter fitting.** Parameters are not optimised
  against the evaluation dataset; see `docs/testing.md`.

## Strategy degradation

`StrategyStatus` moves a strategy through `NORMAL → DEGRADED → PAUSED →
DISABLED`. Degradation is automatic; **diagnosis is not**. When a strategy
deteriorates, the first action is to determine the cause — market regime, fees,
competition, latency, liquidity, data quality, execution quality, inventory, or
a software bug — before any parameter is touched. Optimising a degraded
strategy before diagnosing it is how a software bug becomes a permanently
mis-tuned system.

## Inventory (design, Phase 9)

The CEX/CEX strategy assumes **pre-funded inventory**. `BUY → TRANSFER → SELL`
is not a real-time execution model: withdrawal, chain settlement and deposit
crediting are minutes to hours, over which the spread that justified the trade
does not survive.

The intended model is: hold BTC on one venue and USDC on another, trade both
sides simultaneously against existing balances, and rebalance later as a
**separate operation with its own risk model, its own cost budget
(`max_rebalance_cost`) and its own approval**. Transfers never happen as an
implicit side effect of a trade.

Tracked per venue and asset: free, reserved, available, asset exposure, venue
exposure, imbalance versus target, and rebalance requirement. If inventory
becomes unsafe, new trades stop; existing commitments are seen through.

## Circuit breakers and SAFE MODE

See `docs/architecture.md` §5. The operating rule: **entering is automatic,
leaving is manual**. There is no timed, heuristic or metric-recovery exit from
either. After a breaker trips, a human decides whether the cause is understood.
