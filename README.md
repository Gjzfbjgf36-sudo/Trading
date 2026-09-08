# arbcore — multi-market crypto arbitrage research platform

**Status: research and paper trading. No real money, no exchange adapters.**
The complete pipeline runs end to end against a synthetic market. Nothing in
this repository can move money, and live trading is disabled by default behind
a gate that requires explicit human approval.

This is a research and risk-control platform. It is not a promise of profit,
and arbitrage is not risk-free.

## The result so far

Over ~5 simulated days at retail taker fees, the system detected **55,231**
gross-positive cross-venue spreads and accepted **zero** of them. Median net
margin after the full cost stack: **−111 bps**. Median shortfall against the
safety-adjusted threshold: **173 bps**.

In a deliberately favourable scenario (high-volume fee tier, wide dislocations)
it traded 132 times for **+26.15** — against an infrastructure cost of
**41.67**, for a net of **−15.52**.

**Read [PAPER_RUN_REPORT.md](docs/PAPER_RUN_REPORT.md) before anything else.**
It contains the numbers, the 14 defects the runs exposed, and the honest
recommendation (which is: do not pursue this strategy at retail fee tiers).

### DEX/DEX: a different cost structure, and a cliff

Following that recommendation, a second strategy was built with the opposite
cost shape — gas is **fixed per attempt**, so there is a *minimum* viable size,
while pool impact imposes a *maximum*.

At L1 gas (30/attempt) the strategy found **one** opportunity in 20,000 ticks.
At L2 gas it traded 95 times for **+75.50 after infrastructure** — the first
net-positive configuration in the project.

But the whole result hinges on one unmeasured parameter. At an assumed
competitor-arbitrage rate of 0.35 it earns +48; at **0.50 it places zero
trades**. Nobody has measured where the real value lies.

**[DEX_RUN_REPORT.md](docs/DEX_RUN_REPORT.md) §5** explains why that makes the
positive number a statement about an assumption rather than about the strategy,
and what read-only measurement would settle it.

## Entscheidungssystem (Einstieg)

Neben der Forschungsplattform gibt es ein **Entscheidungssystem ohne Orderpfad**:
Es prüft ein Signal aus einer Regel, die du selbst geschrieben und backgetestet
hast, sagt dir ob und wie groß — und hält fest, was du vorher behauptet hast.

```bash
./setup-mac.sh                                     # einmalig (macOS/Linux)
source .venv/bin/activate                          # in jedem neuen Terminal
python -m arbcore.app.run_gate setup               # was fehlt, und was der nächste Schritt ist
python -m arbcore.app.run_gate wizard              # geführt: prüfen und Plan festhalten
python -m arbcore.app.run_gate serve --token ...   # optional: TradingView-Webhook
```

Anleitung ohne Vorkenntnisse: **[docs/ANLEITUNG.md](docs/ANLEITUNG.md)**.
Zwei Startregeln für TradingView — **eine** auswählen, nicht beide:
`strategies/donchian_trend.pine` (Ausbruch, 2 Parameter) oder
`strategies/engulfing_trend.pine` (Kerzenmuster im Trend, 3 Parameter).

Es erzeugt keinen Edge. Es verhindert die Fehler, die kleine Konten zerlegen,
und misst über 30+ Trades, ob deine Regel überhaupt eine ist.

## Operating principle

> **When in doubt → do not trade.**

Missing, stale, contradictory or unverified information produces a rejection,
never a guess. Unknown costs are not zero costs; unknown probabilities are not
favourable probabilities; an unmonitored condition is not a healthy one.

## What exists today

| Component | File | Purpose |
|---|---|---|
| Value types | `src/arbcore/domain/types.py` | Decimal-only money; floats refused; chain-scoped asset identity |
| Lifecycle | `src/arbcore/domain/state.py` | Enforced opportunity state machine; `RISK_CHECK` cannot be skipped |
| Decisions | `src/arbcore/domain/decision.py` | Auditable accept/reject records with a closed reason vocabulary |
| Live gate | `src/arbcore/config/environment.py` | Environment × trading mode × readiness checklist |
| Risk limits | `src/arbcore/config/limits.py` | Validated limits; per-strategy overrides may only tighten |
| Whitelists | `src/arbcore/config/whitelist.py`, `universe.py` | Fail-closed asset/venue/protocol admission |
| Risk engine | `src/arbcore/risk/engine.py` | The single, pure, deterministic gate |
| Proposals | `src/arbcore/risk/proposal.py` | Cost stack, execution probabilities, EV |
| Budgets | `src/arbcore/risk/budget.py` | Per-category, per-strategy risk budgets |
| Safety | `src/arbcore/safety/` | Latching SAFE MODE and circuit breakers; manual exit only |
| Strategy state | `src/arbcore/strategy/state.py` | `NORMAL / DEGRADED / PAUSED / DISABLED` |
| Order book | `src/arbcore/marketdata/book.py` | Sequence/gap/duplicate/crossed detection; invalid books are unusable |
| Feeds & clocks | `src/arbcore/marketdata/{feed,clock}.py` | Heartbeats, saturating backoff, signed median drift |
| Data quality | `src/arbcore/marketdata/quality.py` | Product of six factors — one zero disqualifies |
| Executable price | `src/arbcore/pricing/executable.py` | VWAP at *our* size; incomplete fills never hidden |
| CEX/CEX strategy | `src/arbcore/strategy/cex_cex.py` | Pre-funded inventory, non-atomic, full cost stack |
| Measured statistics | `src/arbcore/strategy/statistics.py` | The only source of execution probabilities |
| Inventory | `src/arbcore/inventory/manager.py` | Idempotent reservations, minimum reserve, imbalance |
| Orders | `src/arbcore/execution/order.py` | Deterministic client ids; a timeout never resends |
| Paper venue | `src/arbcore/execution/paper.py` | Latency, partials, rejections, **adverse selection** |
| Persistence | `src/arbcore/persistence/store.py` | SQLite WAL; append-only decisions and fills |
| Reconciliation | `src/arbcore/recovery/reconciliation.py` | Detects and halts; never auto-repairs |
| Metrics & alerts | `src/arbcore/monitoring/` | Never ROI alone; CRITICAL alerts never suppressed |
| AMM pricing | `src/arbcore/pricing/amm.py` | Exact `x*y=k`; curve impact separated from pool fee |
| Gas economics | `src/arbcore/costs/gas.py` | Fixed cost per attempt; minimum viable notional |
| DEX/DEX strategy | `src/arbcore/strategy/dex_dex.py` | Size window, profit-maximising sizing, atomic semantics |
| Chain execution | `src/arbcore/execution/chain.py` | Simulate-then-send, reverts, dropped txs, idempotency |
| Walk-forward | `src/arbcore/backtest/walkforward.py` | Enforced, persisted out-of-sample budget |
| Paper session | `src/arbcore/app/paper_session.py` | Wires all of the above |
| Position sizing | `src/arbcore/decide/sizing.py` | Size from risk, never from account size; fees are part of the loss |
| Pre-commitment journal | `src/arbcore/decide/journal.py` | Thesis and invalidation before entry; plans cannot be rewritten |
| Signal gate | `src/arbcore/decide/gate.py` | Green or no, with the specific check that blocked it. No order path |
| Decision review | `src/arbcore/review/performance.py` | No verdict below 30 trades; measures what deviating costs you |
| Account ledger | `src/arbcore/decide/account.py` | Equity, peak and daily loss derived from the journal, never typed |
| Webhook receiver | `src/arbcore/decide/webhook.py` | TradingView alerts; queues verdicts, never files a plan for you |
| Affordability | `src/arbcore/decide/costcheck.py` | Which strategy classes your fee rate can support at all |
| Chart reads | `src/arbcore/decide/reads.py` | Assessments and armed conditions, recorded before the fact and scored after |
| Candles | `src/arbcore/marketdata/candles.py` | Validated OHLC from CSV or a public endpoint via ccxt |
| Rules in Python | `src/arbcore/strategy/rules.py` | The same logic as the Pine scripts, without a charting service |
| Honest backtest | `src/arbcore/backtest/rule_backtest.py` | Costs are required; stop assumed before target within a bar |
| Live watch | `src/arbcore/strategy/watch.py` | Distance to the trigger; signal only from closed bars |
| Robustness | `src/arbcore/backtest/robustness.py` | Plateau or spike — whether the result may be believed |
| Diversification | `src/arbcore/backtest/portfolio.py` | Same rule, many markets; reports whether the spread was real |

## Quick start

```bash
make install
make check      # ruff + mypy --strict + pytest (482 tests)

# The honest scenario: retail fees, calm spreads
python -m arbcore.app.run_paper --ticks 90000 --tick-ms 5000 --scenario realistic

# Exercises the execution path (its P/L is an artefact of chosen parameters)
python -m arbcore.app.run_paper --ticks 90000 --tick-ms 5000 --scenario mechanics

# Walk-forward; --out-of-sample spends this parameter set's budget, once
python -m arbcore.app.run_walkforward --scenario mechanics

# DEX/DEX: compare the fixed-cost economics across chains
python -m arbcore.app.run_paper_dex --chain ethereum --ticks 20000
python -m arbcore.app.run_paper_dex --chain base --ticks 20000

# Which assumption is the result standing on?
python -m arbcore.app.run_sensitivity --ticks 12000
```

Configuration lives in `config/risk_limits.paper.yaml`; copy `.env.example` to
`.env` (git-ignored) for runtime posture. The defaults are the safe ones:
development environment, research mode, live trading off.

## Documentation

| Document | Contents |
|---|---|
| [architecture.md](docs/architecture.md) | Layering, the risk-engine contract, lifecycle, safety subsystem |
| [risk-management.md](docs/risk-management.md) | Limits, budgets, prohibited behaviours, inventory model |
| [risk-register.md](docs/risk-register.md) | 37 tracked risks with detection, mitigation and residual rating |
| [assumptions.md](docs/assumptions.md) | Every assumption with `CONFIRMED / LIKELY / UNCERTAIN / UNKNOWN` status |
| [security.md](docs/security.md) | Secrets, API-key policy, wallet architecture, fail-closed table |
| [testing.md](docs/testing.md) | Current test coverage and the planned test programme |
| [recovery.md](docs/recovery.md) | Start-up reconciliation, failure handling, idempotency, kill switch |
| [model-governance.md](docs/model-governance.md) | Versioned parameter changes |
| [economic-viability.md](docs/economic-viability.md) | Minimum viable capital, net profit after infrastructure cost |
| [regulatory.md](docs/regulatory.md) | DE/EU areas flagged for professional review (not advice) |
| [roadmap.md](docs/roadmap.md) | 18 phases, deployment progression, live gate |
| [PAPER_RUN_REPORT.md](docs/PAPER_RUN_REPORT.md) | **CEX/CEX results, defects found, and what follows** |
| [DEX_RUN_REPORT.md](docs/DEX_RUN_REPORT.md) | **DEX/DEX results, and the parameter the answer hangs on** |
| [paper-trading.md](docs/paper-trading.md) | Calibration mode, the simulated operator, scenarios |
| [market-data-model.md](docs/market-data-model.md) | Book integrity rules, feeds, clocks, quality scoring |
| [execution-model.md](docs/execution-model.md) | Executable prices, order state, idempotency, paper fills |
| [backtesting.md](docs/backtesting.md) | Walk-forward and the out-of-sample ledger |
| [monitoring.md](docs/monitoring.md) | Metrics, alerting, infrastructure cost |
| [deployment.md](docs/deployment.md) | Environments, supervision, backup, health checks |
| [incident-response.md](docs/incident-response.md) | Severities and the standing rules |

## Non-negotiables

* No live trading without explicit manual approval and a fully satisfied
  readiness checklist.
* No secrets in source, logs, reports or version control.
* No blind retries on unknown state — unknown state means SAFE MODE.
* No martingale, no revenge trading, no automatic capital scaling.
* No parameter optimisation against the final evaluation dataset.
* No LLM anywhere on the trade-decision path; decisions are deterministic and
  reproducible.
* No invented API endpoints, SDK methods or fee schedules. Venue facts are
  recorded as `UNKNOWN` until read from official documentation.
