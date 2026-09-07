# Deployment

Current status: **research and paper only.** There is nothing to deploy to
production, and the live gate is closed.

## Environments

Five, with distinct credentials and distinct limit files:
`development`, `testing`, `paper`, `canary`, `production`. `RuntimeProfile`
refuses combinations that would let a lower environment run a real-money mode.
See [configuration.md](configuration.md).

## Process supervision

The system assumes it can be killed at any instant.

* A process **starts in SAFE MODE**, unconditionally.
* Start-up reconciliation must complete and be cleared by a named operator
  before any new risk is taken.
* The risk engine independently refuses any proposal whose exposure snapshot is
  not marked `reconciled`, so a bug that skipped the sequence still cannot
  produce a trade.
* Restart is therefore always safe — it costs an operator review, by design.

Under a supervisor (systemd, supervisord, a container restart policy), an
automatic restart is acceptable *because* the restart lands in SAFE MODE. What
must never be automatic is clearing it.

## Persistence and backup

SQLite in WAL mode (`persistence/store.py`). Single-operator systems do not
need a database server to misconfigure, and a backup is a file copy.

* Decisions and fills are **append-only**; a correction is a new row.
* `PRAGMA integrity_check` is exposed and must be run on start-up.
* The venue and the chain are the source of truth; the database is a *claim*
  about them. On disagreement, reconcile to the external source.
* Back up before every deployment. Restore is a file copy plus a full
  reconciliation.

## Health checks

| Check | Failing means |
|---|---|
| Database integrity | SAFE MODE, do not start |
| Feed liveness | Stalled feeds invalidate their books |
| Reconciliation clean | SAFE MODE |
| Open breakers | No new trading |
| Open orders at start-up | Must be resolved before clearing SAFE MODE |

## Secrets

Injected by a secret manager where available, never baked into an image, never
logged. Keys are `READ + TRADE` with withdrawal disabled, scoped per environment
and per venue. See [security.md](security.md).

## Progression

```
PAPER → CANARY → VERY SMALL CAPITAL → LIMITED PRODUCTION → MANUAL REVIEW → possible scaling
```

There is no path from paper to full capital. Each stage needs written
acceptance criteria agreed *before* the stage begins, and manual approval to
advance. The live gate's eight readiness criteria are enforced in code and all
default to `False`.
