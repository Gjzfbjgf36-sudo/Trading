# Market-data model

## Principle

The local book is the component most likely to be quietly wrong, and a quietly
wrong book prices trades that then lose money. Every known failure mode
therefore **invalidates** the book rather than being repaired in place.

## Integrity rules (`marketdata/book.py`)

| Condition | Response |
|---|---|
| Sequence gap | `INVALID` — the missing delta cannot be guessed |
| Duplicate message (`seq == current`) | Dropped; state already incorporated |
| Out-of-order (`seq < current`) | Dropped; older than what we hold |
| Venue timestamp regression | `INVALID` |
| Crossed book (best bid ≥ best ask) | `INVALID` |
| One side empty | `INVALID` |
| Update against uninitialised/invalid book | Refused |
| Message for the wrong venue/asset | Raises — a routing bug, not a data problem |

**The only exit from `INVALID` is an authoritative snapshot.** Counters
(`gaps_detected`, `resyncs`, …) are kept because the *rate* of invalidation is a
circuit-breaker input: a book that resyncs constantly is not healthy even if
each snapshot is individually fine.

An uninitialised book reports an age of `2^31 − 1` ms, so every staleness check
rejects it without special-casing.

## Feeds (`marketdata/feed.py`)

Heartbeat monitoring with a `DISCONNECTED → CONNECTING → LIVE → STALLED` state
machine. Reconnect backoff is exponential and saturating (1s → 30s): reconnect
storms turn a transient outage into a rate-limited or banned account. An
unregistered feed raises rather than reading as healthy.

## Clocks (`marketdata/clock.py`)

Drift is `local − venue`, **signed**: running ahead of a venue and behind it are
different failures, and the risk engine compares the magnitude. The estimate is
the **median** of a rolling window, so one delayed message does not look like a
clock problem. `has_samples` distinguishes "no drift" from "never measured" —
an unmeasured clock is an unknown clock.

## Data quality (`marketdata/quality.py`)

Six factors in [0, 1]: book integrity, quote freshness, feed health, clock
confidence, cross-source agreement, liquidity confidence.

The score is their **product, not their average**. A single disqualifying input
must drive the score to zero rather than being outvoted by five healthy ones —
averaging is how a corrupt feed gets traded on. Freshness and clock confidence
decay linearly to zero at their configured budgets.

A pair of venues scores the **worse** of the two: a pair is only as good as its
weaker leg.
