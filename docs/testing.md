# Testing strategy

## Running

```bash
make install      # editable install with dev extras
make test         # pytest
make lint         # ruff
make typecheck    # mypy --strict
make check        # all three
```

## Current status

412 tests, all passing. `ruff check` clean. `mypy --strict` clean over
`src/arbcore`.

| Area | File | What it pins down |
|---|---|---|
| Value types | `tests/test_types.py` | floats and bools refused; non-finite refused; cross-asset arithmetic refused; exact decimal arithmetic |
| Lifecycle | `tests/test_state_machine.py` | `RISK_CHECK` cannot be skipped; `DETECTED` cannot jump to `EXECUTING`; `UNKNOWN` resolves only via reconciliation; terminal states are final; every transition needs a reason |
| Decisions | `tests/test_decision.py` | empty check list rejects; all failure reasons retained; malformed check results refused; audit dict is JSON-serialisable |
| Live gate | `tests/test_environment_gate.py` | defaults are safe; live impossible outside production; flag + approval + full checklist all required; one unmet criterion still blocks; ambiguous flag strings read as false |
| Config | `tests/test_limits_and_whitelist.py` | typo'd limit names error rather than silently defaulting; structural consistency; overrides may only tighten (including minima); whitelist lookups raise |
| Safety | `tests/test_safety.py` | process starts in SAFE MODE; breakers latch and do not self-heal; unmonitored conditions raise; operator identity required to clear |
| Budgets/state | `tests/test_budget_and_strategy_state.py` | unallocated ≠ unlimited; overshoot recorded not clipped; strategies start paused; disabled needs explicit re-enable into paused |
| Risk engine | `tests/test_risk_engine.py` | one accept path and ~35 single-fault rejection paths; all failures reported; decisions reproducible; no credentials in records; a huge expected profit cannot buy past a hard limit |
| Market data | `tests/test_marketdata.py` | every integrity failure makes the book unusable; duplicates and out-of-order messages are dropped without invalidating; one zero factor zeroes the quality score |
| Pricing / inventory | `tests/test_pricing_and_inventory.py` | walking the book beats top-of-book; incomplete fills reported; reservations idempotent; settling more than reserved refused |
| Execution | `tests/test_execution.py` | deterministic client ids; a timeout never resends; limit price caps the damage; competition can take the whole opportunity |
| Recovery / persistence | `tests/test_recovery_and_persistence.py` | mismatches engage SAFE MODE; `UNKNOWN` resolves only from venue truth; state survives reopening the database |
| Chaos | `tests/test_chaos_session.py` | overfunded sessions refuse to start; total venue failure leaves no stuck reservations; the simulated operator refuses to work outside paper mode |
| AMM / gas | `tests/test_amm_and_gas.py` | curve impact separated from fee; no size covers a non-positive edge; reverts still cost gas |
| DEX strategy | `tests/test_dex_strategy.py` | L1 gas makes a dislocation untradeable that L2 gas makes tradeable; only atomic strategies may declare a reduced exposure delta |
| DEX session | `tests/test_dex_session.py` | fast competitors remove the strategy entirely; every unknown outcome costs a human intervention |
| Decision system | `tests/test_decide.py` | size follows risk not account size; a vague thesis is refused; plans cannot be rewritten; outcomes written once; no verdict below 30 trades |
| Webhook / account | `tests/test_webhook_and_account.py` | a fee written as a percent is caught; equity derived from the journal closes the gate without anyone typing a number; malformed alerts never reach position sizing; the token never reaches the queue |

Test method: every rejection test starts from a context and proposal that
*should* be accepted, then breaks exactly one thing. A rule that silently stops
firing therefore fails a test rather than passing quietly.

## Planned test programme

### Data tests (Phase 5)
Sequence gap, stale data, corrupted data, duplicate messages, out-of-order
messages, cross-source disagreement, clock drift.

### Execution tests (Phase 10)
Partial fill, rejected order, timeout, duplicate request, delayed confirmation,
failed transaction, idempotency under retry/reconnect/crash.

### Risk tests (Phase 8, extended per phase)
Every hard limit, kill switch, SAFE MODE entry and exit.

### Recovery tests (Phase 14)
Process crash, restart reconciliation, database recovery, API reconnect, RPC
reconnect, order reconciliation.

### Security tests (Phase 15)
Secret leakage, API permission verification, wallet isolation, authentication,
unauthorised action.

### Chaos tests (Phase 12)
Exchange outage, RPC outage, WebSocket disconnect, market-data corruption,
database failure, process crash, slow network, high latency, sudden volatility,
liquidity disappearance, partial execution, unknown transaction state.

**Acceptance rule for every one of the above: the observed behaviour must be
"no new risk". A failure to reject is a test failure.**

## Backtesting rules (Phase 11)

* Strict separation of training, validation and out-of-sample datasets.
* Walk-forward evaluation where appropriate.
* No optimisation against the final evaluation dataset — ever, once.
* Guard against look-ahead bias, data leakage, survivorship bias, unrealistic
  fills and unrealistic liquidity assumptions.
* Fills are modelled against recorded book/pool state at size, not at mid.

## Paper vs reality (Phase 16)

Track and report the *errors*, not just the outcomes: slippage error, latency
error, fill-probability error, fee-estimation error, profit-estimation error.
A model whose errors are large is not made acceptable by a profitable
simulation.
