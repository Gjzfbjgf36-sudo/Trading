# arbcore — multi-market crypto arbitrage research platform

**Status: Phase 1 (architecture and risk model). Research only.**
No market-data feeds, no exchange adapters, no order placement. Nothing in this
repository can move money, and live trading is disabled by default behind a
gate that requires explicit human approval.

This is a research and risk-control platform. It is not a promise of profit,
and arbitrage is not risk-free.

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

## Quick start

```bash
make install
make check      # ruff + mypy --strict + pytest
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

## Non-negotiables

* No live trading without explicit manual approval and a fully satisfied
  readiness checklist.
* No secrets in source, logs, reports or version control.
* No blind retries on unknown state — unknown state means SAFE MODE.
* No martingale, no revenge trading, no automatic capital scaling.
* No parameter optimisation against the final evaluation dataset.
* No LLM anywhere on the trade-decision path; decisions are deterministic and
  reproducible.
