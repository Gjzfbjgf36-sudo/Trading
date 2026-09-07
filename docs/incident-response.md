# Incident response

## Severity

| Level | Examples | Immediate action |
|---|---|---|
| **SEV-1** | Wallet/balance mismatch, unknown transaction or order state, suspected credential compromise, reconciliation failure | SAFE MODE, stop all new commitments, operator investigates before anything else |
| **SEV-2** | Circuit breaker tripped, abnormal slippage or latency, exchange or RPC outage, market-data corruption | Affected strategy paused; breaker stays open until the cause is understood |
| **SEV-3** | Elevated rejection rate, degraded data quality, single API error spike | Monitor; consider `DEGRADED` state |

## Standing rules

1. **Stop first, diagnose second.** Engaging SAFE MODE costs an opportunity.
   Not engaging it can cost the capital.
2. **Do not unwind under uncertainty.** SAFE MODE blocks *new* risk. Closing an
   existing position whose true state is unknown can create the very exposure
   you are trying to remove.
3. **The venue and the chain are the truth.** Our database is a claim about
   them. On disagreement, reconcile to the external source.
4. **No blind retry.** Follow the sequence in `docs/recovery.md`.
5. **Nothing auto-restarts.** A tripped breaker and an engaged SAFE MODE both
   require a named operator to clear.

## Procedure

1. Engage SAFE MODE (automatic for the listed triggers; manual otherwise).
2. Record the trigger, timestamp and observed state. Do not paste credentials
   into the record.
3. Establish ground truth: venue balances, open orders, recent fills, chain
   receipts, token balances.
4. Determine actual exposure and which legs executed.
5. Reconcile internal state to ground truth; move affected opportunities
   through `RECONCILIATION` to `CLOSED`.
6. Write up cause, impact, and what would have detected it earlier.
7. If a control was missing, add it — and add the test — before clearing.
8. Clear SAFE MODE and reset breakers, each with a named operator.

## Credential compromise

Assume exposure is real until proven otherwise. Revoke and rotate the key
immediately, audit account activity, verify that withdrawal permission was
disabled (it must have been), and move hot-wallet balances if a private key is
implicated. Then treat it as a SEV-1 with a full write-up.
