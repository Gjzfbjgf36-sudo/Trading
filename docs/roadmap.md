# Development roadmap

Each phase ends with a report (what was built, files created/changed,
architecture changes, tests run and results, risks discovered/mitigated/
remaining, known limitations) and **stops for approval**. No phase is skipped.

| Phase | Content | Status |
|---|---|---|
| 1 | Architecture and risk model | **COMPLETE — awaiting approval** |
| 2 | Market-data infrastructure (WebSocket, snapshots, heartbeats, sequencing, gap/dup/order detection) | Not started |
| 3 | CEX adapters (from current official docs; version, auth, rate limits, order semantics, fees, withdrawal constraints recorded) | Not started |
| 4 | DEX adapters (executable quotes, pools, simulation, protocol whitelist with verified addresses) | Not started |
| 5 | Data validation and data-quality scoring | Not started |
| 6 | Opportunity engine (executable-price/VWAP-at-size, per strategy) | Not started |
| 7 | Profit engine (full cost stack, safety margin, risk-adjusted EV) | Not started |
| 8 | Risk engine extension (wired to live limits, budgets, breakers) | Partly delivered in Phase 1 |
| 9 | Inventory engine (per-venue balances, imbalance, rebalancing as a separate operation) | Not started |
| 10 | Paper execution (same decision architecture as live; simulated slippage, impact, fees, latency, partial fills, failures, decay) | Not started |
| 11 | Backtesting (train/validation/out-of-sample, walk-forward, bias controls) + economic viability | Not started |
| 12 | Failure and chaos testing | Not started |
| 13 | Monitoring, alerting, infrastructure-cost tracking | Not started |
| 14 | Reconciliation and crash recovery | Not started |
| 15 | Security testing | Not started |
| 16 | Long-duration paper trading + paper-vs-reality validation | Not started |
| 17 | Canary architecture | Not started |
| 18 | Optional live execution — **operator decision, never automatic** | Gated |

## Deployment progression

```
PAPER → CANARY → VERY SMALL CAPITAL → LIMITED PRODUCTION → MANUAL REVIEW → possible scaling
```

There is no path from paper to full capital. Each stage needs explicit,
written acceptance criteria agreed before the stage begins, and manual approval
to advance.

## Live-trading gate

Enforced in code by `RuntimeProfile`; every item must hold:

- [ ] All critical tests passing
- [ ] Security review complete
- [ ] Risk limits configured and reviewed
- [ ] Monitoring operational
- [ ] Reconciliation operational
- [ ] Kill switch tested
- [ ] Paper trading completed
- [ ] Out-of-sample validation completed
- [ ] `live_trading_enabled` explicitly set
- [ ] Manual approval reference recorded
- [ ] Production environment

## Strategy-specific gating

| Strategy | Earliest permitted mode |
|---|---|
| CEX→CEX (pre-funded inventory) | Paper now; canary only after Phases 10–17 |
| DEX→DEX | Paper only until atomicity and simulation are proven |
| CEX→DEX | Paper only; treated as high-risk, non-atomic |
| Cross-chain | **Paper only, indefinitely.** Bridge latency, liquidity, failure and exploit risk are not currently controllable to a standard that would justify capital |
