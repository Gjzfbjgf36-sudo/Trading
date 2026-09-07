# Security

## Secrets

* **Never** in source, logs, prompts, reports, error messages, audit records,
  commit history or issue text.
* `.env` and every `.env.*` except `.env.example` are git-ignored, along with
  `*.pem`, `*.key`, `secrets/` and `credentials/`.
* `.env.example` contains placeholders only and holds no credentials at all —
  Phase 1 has no adapters, so the system currently needs none.
* Audit records are constructed from typed, enumerated fields
  (`Decision.as_dict`); there is no free-form capture of request payloads. A
  test asserts that a serialised decision contains no credential-shaped keys.
* Where available, prefer a secret manager over a file. Rotate on any
  suspicion, and treat "was it exposed?" as "yes" until proven otherwise.

## API key policy

* Minimum permissions: **READ + TRADE**.
* **Withdrawal permission disabled.** The system never needs it: inventory
  rebalancing is a separate, deliberately manual-approval operation.
* IP-allowlist keys where the venue supports it.
* One key per venue per environment. A paper-environment key must never work
  against a production account, and vice versa.

## Wallet architecture

```
COLD / RESERVE  (majority of capital, never touched by the platform)
      │  manual, deliberate transfers only
      ▼
LIMITED HOT TRADING WALLET  (only what a strategy needs to operate)
```

* Never a personal primary wallet.
* Separate keys per chain and per environment.
* The hot wallet's balance is itself a risk limit: only capital the operator
  can afford to lose entirely enters the trading environment.
* A wallet-balance mismatch against internal state is a SAFE MODE trigger and a
  circuit-breaker condition, not a warning to be logged.

## Environment separation

`Environment` and `TradingMode` are independent and validated together
(`config/environment.py`). A production process cannot be started with
development credentials by accident, because credentials are environment-scoped
and the profile is resolved and frozen at start-up.

## Fail-closed principles in code

| Situation | Behaviour |
|---|---|
| Asset/venue/protocol not whitelisted | `WhitelistError` (raises; never returns `None`) |
| Unmonitored breaker condition | `KeyError` (never reads as healthy) |
| Unallocated risk-budget category | `KeyError` (never reads as unlimited) |
| Unknown cost line | Reject (`FEE_UNCERTAIN`) |
| No measured probabilities | Reject (research-only) |
| No checks evaluated | Reject (`CHECK_UNEVALUABLE`) |
| Unreconciled exposure | Reject (`UNKNOWN_STATE`) |
| Fresh process | SAFE MODE |

## Planned security testing (Phase 15)

Secret-leakage scanning of logs and audit records; API-permission verification
(assert withdrawal is disabled before enabling any venue); wallet isolation
tests; authentication failure handling; unauthorised-action attempts. Each must
end in **no new risk**.
