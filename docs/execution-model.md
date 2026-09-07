# Execution model

## Executable prices (`pricing/executable.py`)

The best ask is a price for an infinitesimal size, not for ours. `walk_book`
consumes levels until the size is filled and returns the VWAP, the worst price
touched, the levels consumed and — critically — whether the fill was
**complete**.

A partial walk is never returned as if it were a full fill. If the book cannot
absorb the size, the strategy drops the opportunity rather than silently
shrinking the trade: a smaller trade is a different opportunity with different
economics.

Price impact is measured against top-of-book and floored at zero. Slippage is
measured against the *decision* price and also floored at zero — an
unexpectedly good fill is not a risk budget to spend elsewhere.

An unusable book raises `NotExecutable`. A corrupt book has no price, not a bad
one.

## Orders (`execution/order.py`)

State machine: `PENDING → SENT → ACKNOWLEDGED → PARTIALLY_FILLED → FILLED`,
with `REJECTED`, `CANCELLED` and `UNKNOWN` as the other outcomes.

* **ORDER SENT ≠ ORDER FILLED.** State advances only on venue-confirmed
  information.
* `average_price` is `None` when nothing filled — never zero, which would read
  as free.
* Fills that would exceed the order quantity are refused.
* `UNKNOWN` resolves only by querying the venue. **A timeout never generates a
  second order.**

### Idempotency

`client_order_id(opportunity_id, leg, venue)` is a deterministic SHA-256 digest,
not a random UUID. A process that crashes and restarts computes the *same* key
and recognises its own in-flight order instead of placing a second one. The
paper venue refuses duplicate submissions by client id.

## Paper execution (`execution/paper.py`)

Deterministic under a seed, and deliberately pessimistic. Models:

| Effect | Default |
|---|---|
| Round-trip latency | 40–250 ms uniform |
| Adverse drift during latency | 0.4 bps per 100 ms, **always against us** |
| Outright rejection | 1% |
| No confirmation → `UNKNOWN` | 0.5% |
| Partial fill | 8%, filling 25–100% |
| **Competition / adverse selection** | **35%**, costing 2–20 bps and 0–70% of the size |
| Taker fee | 10 bps |

The competition model is the important one. Without it the simulator filled
every order at the book we *saw*, producing a 98% win rate and a profit factor
of 672 — a number that says the model is wrong, not that the strategy is good.
A visible spread is precisely the spread everyone else can also see.

Drift is modelled as one-sided. Symmetric noise would hand back half the cost
as free profit.

`inject_failure("reject" | "timeout" | "partial")` forces a specific pathology
without fighting the generator, which keeps chaos tests deterministic.

## Limit prices

`limit_price_for(decision_price, side, max_slippage)` converts the slippage
budget into a price. Expressing it in price terms means the venue enforces our
slippage limit even if our own post-trade checks are wrong.

Balance is reserved at the **limit** price, not the decision price. Reserving at
the decision price leaves the reservation short by exactly the slippage — which
a real venue reports as insufficient funds (defect F-7).
