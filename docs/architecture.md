# Architecture

Status: **Phase 1 (architecture and risk model).** No venue adapters, no market
data, no execution. Nothing in this repository can place an order.

## 1. Operating posture

The platform has one default answer: **do not trade**. Every component is built
so that absence of information produces a rejection rather than a guess.

Three independent switches must all be in the permissive position before any
real capital can move:

| Switch | Default | Where |
|---|---|---|
| `Environment` | `development` | `ARBCORE_ENV` |
| `TradingMode` | `research` | `ARBCORE_TRADING_MODE` |
| `live_trading_enabled` + approval ref + full readiness checklist | off | `ARBCORE_LIVE_TRADING_*` |

`RuntimeProfile` (`src/arbcore/config/environment.py`) resolves these once at
start-up and raises `LiveGateError` if a real-money mode is requested without
all of them. Downstream code can only observe the profile, never widen it.

## 2. Layering

```
                 ┌──────────────────────────────────────────┐
                 │  Strategy modules (independent)          │
                 │  CEX_CEX │ DEX_DEX │ CEX_DEX │ CROSS_CHAIN│
                 │  own: opportunity / execution / cost /    │
                 │       settlement / inventory / failure    │
                 └───────────────┬──────────────────────────┘
                                 │ TradeProposal  (risk-relevant summary)
                                 ▼
   ┌───────────────────────────────────────────────────────────────┐
   │  RiskEngine — the single gate. Pure, deterministic, replayable │
   └───────────────┬───────────────────────────────────────────────┘
                   │ Decision (ACCEPT / REJECT + every check + reason)
                   ▼
   ┌───────────────────────────────────────────────────────────────┐
   │  Execution (paper first) → Settlement → Reconciliation → Audit │
   └───────────────────────────────────────────────────────────────┘

  Cross-cutting: SafeMode · CircuitBreakers · Whitelists · RiskBudgets ·
                 StrategyStatus · Inventory · Observability
```

Deliberately **not** built: a generic arbitrage algorithm. `StrategyKind`
enumerates four kinds because their economics differ in kind, not degree — a
CEX/CEX inventory trade and a cross-chain bridge trade share almost nothing
beyond the word "spread".

## 3. The risk engine contract

`RiskEngine.evaluate(proposal, ctx) -> Decision`.

* **Pure.** It reads nothing it was not handed. A `RiskContext` carries the
  profile, limits, whitelist, exposure snapshot, safe-mode state, breakers,
  strategy status, budget and the evaluation timestamp. This is what makes a
  decision reproducible from its audit record and testable in its failure
  paths.
* **Exhaustive.** All checks run even after one fails, so the record shows
  everything that was wrong.
* **Fail-closed.** A check that cannot be evaluated returns
  `CHECK_UNEVALUABLE`, which rejects. An empty check list rejects. An unknown
  cost line rejects. Missing execution probabilities reject.
* **Limit-supreme.** The engine has no concept of a score. A high-quality
  opportunity and a marginal one are subject to identical hard limits.

### Checks implemented in Phase 1

| Group | Checks |
|---|---|
| Posture | trading mode, safe mode, circuit breakers, strategy state, exposure reconciled |
| Whitelist | assets admitted, venues admitted |
| Data integrity | quote age, clock drift, expected latency, data-quality score, exposure snapshot age |
| Economics | cost model complete, net expected profit, safety-margin-adjusted profit, risk-adjusted EV |
| Microstructure | expected slippage, expected price impact |
| Exposure | trade size (tightened for non-atomic), total / per-asset / per-venue / per-chain exposure, daily loss, daily trade count, concurrent trades |
| Inventory | free-balance sufficiency verified, inventory imbalance |
| Budget | per-category risk budget remaining |

### Profit, and what it is not

`gross_expected_profit − Σ(costs)` is the *net* number. Every cost line in
`CostBreakdown` is `Decimal | None`; `None` means *not estimated* and rejects
the trade. A line that genuinely does not apply is `Decimal(0)`, recorded
deliberately.

Two further numbers gate a trade:

* **Safety-margin-adjusted profit** — costs multiplied by
  `cost_safety_factor` (≥ 1). The margin is taken on costs, because costs are
  what we systematically underestimate.
* **Risk-adjusted EV** — the adjusted profit weighted by *measured* execution
  probabilities. With no measurement basis the value is `None`, the opportunity
  is research-only, and it is rejected. Probabilities are never invented.

## 4. Opportunity lifecycle

`OpportunityLifecycle` (`src/arbcore/domain/state.py`) enforces:

```
DETECTED → VALIDATING → VALIDATED → RISK_CHECK → EXECUTION_READY → EXECUTING
        → PARTIAL/FILLED/FAILED → SETTLEMENT → RECONCILIATION → CLOSED
```

* Skipping a state raises `IllegalTransition` — notably, `VALIDATED` cannot
  reach `EXECUTION_READY` without passing `RISK_CHECK`.
* `REJECTED` is reachable from every pre-execution state, including
  `EXECUTION_READY` (a cleared opportunity whose quote decayed must not become
  an order).
* `UNKNOWN` is reachable from every state where an external system may have
  acted without us learning the outcome, and can *only* be left via
  `RECONCILIATION`. There is no path from `UNKNOWN` to `CLOSED`.
* Every transition requires a recorded reason.

## 5. Safety subsystem

**SAFE MODE** (`safety/safe_mode.py`) blocks *new* risk. It does not unwind
positions: blind unwinding under uncertain state converts a bookkeeping problem
into a realised loss. A process **starts in SAFE MODE** and leaves it only
after successful start-up reconciliation, cleared by a named operator.

**Circuit breakers** (`safety/circuit_breaker.py`) watch one condition each.
They latch open, do not reset on a timer, and do not reset because the metric
recovered. `BreakerPanel.observe` on an unconfigured condition raises — an
unmonitored condition must never read as healthy.

**Strategy states** (`strategy/state.py`): `NORMAL → DEGRADED → PAUSED →
DISABLED`. Strategies start `PAUSED`. A `DISABLED` strategy cannot be revived
by an ordinary state change; `enable()` requires an operator and lands in
`PAUSED`, never straight back into trading.

## 6. Determinism and the role of AI

Trading decisions are deterministic functions of recorded inputs. No model
inference sits anywhere on the accept path. AI assistance is confined to code
generation, review, documentation, research, anomaly investigation and
performance analysis — all of which produce artefacts a human reviews, never
an order.

## 7. What Phase 1 does not do

Market data, adapters, quotes, order placement, inventory rebalancing,
persistence, monitoring and backtesting are all later phases. The interfaces
above were designed so those phases plug in without loosening any gate; see
`docs/roadmap.md`.
