# Development roadmap

Each phase ends with a report (what was built, files created/changed,
architecture changes, tests run and results, risks discovered/mitigated/
remaining, known limitations) and **stops for approval**. No phase is skipped.

| Phase | Content | Status |
|---|---|---|
| 1 | Architecture and risk model | **Complete** |
| 2 | Market-data infrastructure: book integrity, sequencing, gap/duplicate/out-of-order detection, heartbeats, reconnect backoff, clock tracking | **Complete** (transport layer pending adapters) |
| 3 | CEX adapters | **Blocked** — needs current official documentation; interfaces defined, nothing invented |
| 4 | DEX adapters | **Blocked** — needs verified contract addresses and official docs |
| 5 | Data validation and data-quality scoring | **Complete** |
| 6 | Opportunity engine (VWAP-at-size, CEX/CEX) | **Complete** for CEX/CEX |
| 7 | Profit engine (full cost stack, safety margin, risk-adjusted EV) | **Complete** |
| 8 | Risk engine | **Complete** |
| 9 | Inventory engine (reservations, idempotency, minimum reserve, imbalance) | **Complete**; automated rebalancing deliberately not built |
| 10 | Paper execution incl. adverse selection | **Complete** |
| 11 | Walk-forward with an enforced out-of-sample budget + economic viability | **Complete** (synthetic data) |
| 12 | Failure and chaos testing | **Complete** — 253 tests |
| 13 | Monitoring, alerting, infrastructure-cost tracking | **Complete** |
| 14 | Reconciliation and crash recovery | **Complete** |
| 15 | Security testing | Partial — secret-leakage and isolation tests exist; API-permission tests need adapters |
| 16 | Long-duration paper trading | **Done on synthetic data**; paper-vs-reality blocked on adapters |
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
