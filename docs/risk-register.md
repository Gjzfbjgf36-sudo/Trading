# Risk register

Living document. Review cadence per row; every review updates `Status` and
`Residual`. Probability/Impact are qualitative (L/M/H) — they are judgements,
not measurements, and are labelled as such.

Owner is `operator` throughout: this is currently a single-operator system.
Rows whose mitigation is not yet built are marked **OPEN** and name the phase
that closes them.

| # | Risk | Category | Prob | Impact | Detection | Mitigation | Residual | Status | Review |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Adverse price move during execution | Market | H | M | Latency + decay metrics | Risk-adjusted EV, latency ceiling, size limits | M | OPEN (P6–7) | Weekly |
| 2 | Partial fill leaves one-sided exposure | Execution | H | H | Order state polling, reconciliation | Non-atomic size factor 0.25, partial-fill cost line, PARTIAL state | M | OPEN (P10) | Weekly |
| 3 | Slippage beyond model | Execution | H | M | Paper-vs-reality slippage error | `max_slippage`, per-venue limits, ABNORMAL_SLIPPAGE breaker | M | Partly mitigated (limits exist) | Weekly |
| 4 | Insufficient executable liquidity at size | Liquidity | H | M | Depth/pool checks at intended size | Executable-price engine (VWAP at size), `max_price_impact` | M | OPEN (P6) | Weekly |
| 5 | Latency exceeds opportunity lifetime | Execution | H | M | Per-venue latency histograms | `max_execution_latency_ms`, decay model | M | Partly mitigated | Weekly |
| 6 | MEV / competition takes the opportunity | Execution | H | M | Inclusion + revert rates | Treat observed spread as not ours; priority-fee and inclusion modelling | H | OPEN (P4, P6) | Weekly |
| 7 | Gas / priority-fee spike | Market | M | M | Fee oracles, pre-trade estimate | Fee lines mandatory in cost model; reject when unknown | M | Partly mitigated | Weekly |
| 8 | Exchange API failure | Technology | M | M | Health checks, error rates | API_FAILURE breaker, SAFE MODE | L | OPEN (P3, P13) | Weekly |
| 9 | RPC failure or disagreement | Technology | M | H | Multi-RPC comparison | RPC_DISAGREEMENT → SAFE MODE | M | OPEN (P4) | Weekly |
| 10 | Exchange outage mid-trade | Counterparty | M | H | Heartbeats, order-state polling | UNKNOWN state → SAFE MODE, no blind retry | M | OPEN (P10, P14) | Weekly |
| 11 | Withdrawal freeze / account restriction | Counterparty | L | H | Withdrawal-status polling | Per-venue `max_balance`, capital spread across venues | M | Partly mitigated | Monthly |
| 12 | Exchange insolvency | Counterparty | L | H | External monitoring (manual) | Per-venue cap, minimum idle balances | H | Partly mitigated | Monthly |
| 13 | Duplicate order from retry/reconnect | Execution | M | H | Client order-ID uniqueness checks | Idempotency keys; a timeout never generates a second order | L | OPEN (P10) | Weekly |
| 14 | Failed on-chain transaction | Execution | H | M | Receipt polling | Simulate before send; reject on simulation failure | M | OPEN (P4, P10) | Weekly |
| 15 | Unknown transaction state | Settlement | M | H | Receipt + balance divergence | UNKNOWN → SAFE MODE, human reconciliation only | M | Partly mitigated (state machine) | Weekly |
| 16 | Smart-contract exploit | Smart contract | L | H | Protocol monitoring (manual) | Protocol whitelist with per-protocol `max_exposure` | H | OPEN (P4) | Monthly |
| 17 | Token scam / malicious asset | Smart contract | L | H | Admission review | Asset whitelist; mint/freeze/transfer findings required | L | Partly mitigated (schema exists, findings UNKNOWN) | Monthly |
| 18 | Stablecoin depeg | Market | M | H | Peg monitoring across venues | `max_stablecoin_depeg`; disable dependent strategies | M | OPEN (P5) | Weekly |
| 19 | Bridge failure or exploit | Settlement | M | H | Bridge status + balance checks | Cross-chain stays PAPER ONLY | M | Mitigated by scope | Monthly |
| 20 | Market-data corruption | Data | M | H | Sequence/gap/duplicate/order checks | Drop the local book, resync from snapshot, do not trade | L | OPEN (P2, P5) | Weekly |
| 21 | Stale data used as live | Data | M | H | Timestamp + age checks | `max_quote_age_ms`, exposure snapshot age check | L | Mitigated in engine | Weekly |
| 22 | Clock drift | Data | M | M | NTP + venue timestamp comparison | `max_clock_drift_ms` (signed, magnitude compared) | L | Mitigated in engine | Weekly |
| 23 | Software bug in decision path | Technology | M | H | Tests, replayable decisions | Pure engine, exhaustive checks, 100+ unit tests | M | Partly mitigated | Per change |
| 24 | Process crash mid-trade | Technology | M | H | Restart detection | Start in SAFE MODE; reconcile before resuming | M | OPEN (P14) | Weekly |
| 25 | Database corruption / loss | Technology | L | H | Integrity checks, backups | Durable storage, reconcile against venues as truth | M | OPEN (P14) | Monthly |
| 26 | Credential compromise | Security | L | H | Access logs, key rotation | Least-privilege keys (READ+TRADE, withdrawals off), secrets never in code/logs/git | M | Partly mitigated (policy) | Monthly |
| 27 | Private-key compromise | Security | L | H | Balance monitoring | Cold/reserve vs limited hot wallet split; only limited capital in the trading environment | M | OPEN (P4) | Monthly |
| 28 | Inventory imbalance blocks trading | Liquidity | H | L | Imbalance metric | `max_inventory_imbalance` stops new trades | L | Mitigated in engine | Weekly |
| 29 | Capital concentration on one venue | Counterparty | M | M | Per-venue exposure | `max_exchange_exposure` + whitelist `max_balance` | L | Mitigated in engine | Weekly |
| 30 | Backtest bias (look-ahead, survivorship) | Model | M | H | Methodology review | Strict train/validation/out-of-sample split, walk-forward | M | OPEN (P11) | Per change |
| 31 | Overfitting to history | Model | H | H | Out-of-sample degradation | No optimisation against the evaluation set | M | OPEN (P11) | Per change |
| 32 | Model error (fees, probabilities) | Model | M | H | Paper-vs-reality error tracking | `cost_safety_factor`; unknown ⇒ reject | M | Partly mitigated | Weekly |
| 33 | Infrastructure cost exceeds trading profit | Economic | H | M | Cost tracking | Net profit after infrastructure cost is the reported figure | M | OPEN (P13) | Monthly |
| 34 | Strategy not viable at available capital | Economic | H | M | Minimum-viable-capital analysis | Explicit viability assessment before any capital commitment | M | OPEN (P11) | Monthly |
| 35 | Regulatory / licensing exposure (DE/EU) | Regulatory | M | H | Professional review | See `docs/regulatory.md`; flagged, not self-assessed | H | OPEN | Quarterly |
| 36 | Tax treatment and record keeping | Regulatory | H | M | Professional review | Full immutable trade records; professional advice required | M | OPEN | Quarterly |
| 37 | Market-abuse considerations | Regulatory | L | H | Professional review | Flagged for review before any commercial use | H | OPEN | Quarterly |

## How to use this register

* A row marked **OPEN** is not a to-do that can be waived. The phase named in
  its status must close it before the capability it guards is enabled.
* Residual **H** on any row is grounds to keep the affected strategy in paper
  mode regardless of its measured performance.
