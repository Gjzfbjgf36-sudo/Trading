# Recovery and reconciliation

Design for Phases 14 and 24-related behaviour. Phase 1 implements the entry
conditions (start in SAFE MODE, `UNKNOWN` lifecycle state, reconciled-exposure
gate); the mechanics land with persistence and adapters.

## Start-up sequence

A process **starts in SAFE MODE** (`SafeMode(active=True)`), unconditionally.

1. Load persisted state.
2. Query every venue: balances, open orders, recent fills.
3. Query relevant chain state: transactions, receipts, token balances.
4. Reconcile balances against internal records.
5. Reconcile order states against internal records.
6. Compute current exposure.
7. Detect incomplete strategies — anything in an `AT_RISK` lifecycle state.
8. Only if all of the above agree, an operator may clear SAFE MODE.

The risk engine independently refuses any proposal whose `ExposureSnapshot` is
not marked `reconciled`, so a bug that skipped the sequence still cannot
produce a trade.

## Failure handling

Never retry blindly. After any failure:

1. Identify the transaction or order by its idempotency key.
2. Query its actual status from the authoritative source.
3. Query wallet / account state.
4. Query token balances.
5. Determine whether any leg executed.
6. Compute current exposure.
7. Reconcile internal state.
8. **Only then** decide what is permitted.

If the true state cannot be established, the opportunity moves to `UNKNOWN`,
which reaches `CLOSED` only through `RECONCILIATION`. Unknown state means SAFE
MODE.

## Idempotency

Every order and execution attempt carries a unique client-side identifier. The
system must be safe against retries, reconnects, process crashes and duplicate
API calls. **A timeout never generates a second order** — it triggers a state
query.

## Ongoing reconciliation

An independent process periodically compares internal state against actual
venue balances, actual chain balances, actual order states and actual
transaction states. Any mismatch engages SAFE MODE
(`RECONCILIATION_MISMATCH`). The venue and the chain are the source of truth;
our database is a claim about them.

## Kill switch

A single operator action that stops all new commitments immediately. It must be
tested — it is an item on the live-readiness checklist — and testing it is part
of routine operation, not a one-off.
