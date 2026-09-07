# DEX/DEX paper report — the fixed-cost economy

Companion to [PAPER_RUN_REPORT.md](PAPER_RUN_REPORT.md), which concluded that
CEX/CEX arbitrage at retail fees is not viable and pointed at DEX strategies as
the next question because their cost structure is different. This is that
question, answered.

**Read §5 before quoting any number from §3.** The headline result is
positive, and it rests almost entirely on one parameter nobody has measured.

---

## 1. Why this is a separate strategy, not a variant

| | CEX/CEX | DEX/DEX |
|---|---|---|
| Atomicity | Non-atomic; a leg can fill alone | **Atomic** when both swaps share one transaction |
| Leg risk | Real, priced at 15 bps | **None** — a failed round trip reverts as a whole |
| Dominant cost | Taker fees, **proportional** to size | Gas, **fixed** per attempt |
| Failure mode | One-sided exposure | Reverted transaction; gas spent, position unchanged |
| Competition | Someone fills the order first | Someone's transaction is ordered ahead of ours |
| Capital | Pre-funded on both venues | One wallet, one asset |

The consequence is a mirror image. In CEX/CEX, cost scaled with size and **no**
size was profitable. Here the dominant cost is fixed, so there is a **minimum**
viable size — while price impact on the pool curve imposes a **maximum**. The
strategy exists only where the two overlap, and `size_window` computes that up
front instead of discovering it after the fact.

---

## 2. The structural constraint

Gas is charged per attempt, successful or not. That produces a floor:

```
minimum viable notional = gas per attempt / edge fraction
```

and the constant-product curve produces a ceiling: past a point, our own price
impact eats the edge faster than size adds to it.

With ~1,000 ETH of pool depth and a 0.3% pool fee on each side:

| Chain | Gas/attempt (assumed) | Minimum pool divergence that works |
|---|---|---|
| Ethereum L1 | 30.00 | **≥ 2.0%** |
| Arbitrum | 0.30 | **≥ 0.8%** |
| Base | 0.15 | **≥ 0.8%** |

The synthetic pool market produced a divergence distribution of median 0.31%,
p90 0.77%, max 1.63%. Against that distribution:

* **L1 qualifies on 0% of ticks.** In a 20,000-tick run it found *one*
  opportunity and rejected it on cost.
* **L2 qualifies on roughly 9% of ticks.**

The only thing that changed between those two rows is a fixed cost. That is the
entire finding of this section.

---

## 3. Session results

```
python -m arbcore.app.run_paper_dex --chain ethereum --ticks 20000
python -m arbcore.app.run_paper_dex --chain base --ticks 20000
```

~2.8 simulated days, wallet 1,800, competitor arbitrage rate 0.35:

| | Ethereum | Arbitrum | Base |
|---|---|---|---|
| Opportunities detected | 1 | 2,941 | 3,295 |
| Accepted | 0 | 200 | 200 |
| Transactions attempted | 0 | 200 | 200 |
| **Confirmed** | 0 | 87 (43.5%) | 91 (45.5%) |
| Reverted | 0 | 36 | 33 |
| Dropped (never included) | 0 | 72 | 73 |
| Unknown (each halted the system) | 0 | 5 | 3 |
| Gas spent | 0 | 39.42 | 18.56 |
| Strategy P/L | 0 | +82.39 | +97.73 |
| Infrastructure cost | 22.22 | 22.22 | 22.22 |
| **Net after infrastructure** | **−22.22** | **+60.17** | **+75.50** |

This is the **first configuration in the whole project that is net positive
after infrastructure costs.** It is also the one that most needs §5.

Two details that matter more than the P/L:

* **Fewer than half of transactions confirm.** 43–46%. Losing the race is
  normal on chain, not a malfunction — which is why the failure-rate breaker
  here is set at 0.75 rather than the 0.35 the CEX session uses.
* **The reported win rate (70–74%) flatters.** It counts only attempts with a
  non-zero P/L, and a dropped transaction costs nothing. Read
  `confirmed / attempts` instead.

---

## 4. Sensitivity

```
python -m arbcore.app.run_sensitivity --ticks 12000
```

**Seed** (Base, 12,000 ticks) — stable, so this is not one lucky path:

| Seed | Trades | Net | After infrastructure |
|---|---|---|---|
| 101 | 63 | 61.76 | +48.42 |
| 202 | 54 | 58.61 | +45.28 |
| 303 | 57 | 34.78 | +21.44 |
| 404 | 63 | 52.13 | +38.80 |
| 505 | 62 | 56.11 | +42.78 |

**Inclusion probability** — degrades gracefully, stays positive throughout:

| Inclusion | Trades | After infrastructure |
|---|---|---|
| 0.20 | 26 | +7.19 |
| 0.60 | 63 | +48.42 |
| 1.00 | 99 | +74.92 |

**Revert probability** — likewise graceful, positive even at 75% reverts:

| Revert | After infrastructure |
|---|---|
| 0.10 | +60.20 |
| 0.50 | +29.95 |
| 0.75 | +1.47 |

**Competitor arbitrage rate — a cliff, not a slope:**

| Rate | Trades | After infrastructure |
|---|---|---|
| 0.10 | 64 | **+103.86** |
| 0.25 | 64 | +74.57 |
| 0.35 | 63 | +48.42 |
| **0.50** | **0** | **−13.33** |
| 0.70 | 0 | −13.33 |
| 0.90 | 0 | −13.33 |

At 0.50 the collapse is sharper than "zero trades" suggests. The run made 38
attempts and confirmed 11 — but the calibration threshold is 50 samples, so it
**never gathered enough data to leave calibration mode at all**. At 0.70 and
above there were no attempts whatsoever. The strategy does not degrade toward
unprofitability; it stops finding anything to measure.

---

## 5. What the cliff means

The competitor arbitrage rate is *how much of any pool divergence other people
close before we get there*. At 0.35 the strategy earns ~+48. At 0.50 it places
**zero trades** and loses the infrastructure cost.

**Nobody has measured this parameter.** It is marked `UNCERTAIN` in
`docs/assumptions.md`, and the honest range of belief for competitive pools on
a public chain comfortably spans 0.35 to 0.50 and beyond. The strategy is
profitable on one side of a line whose position is unknown.

So the correct reading of §3 is **not** "DEX arbitrage on L2 makes money". It is:

> Under an assumed competition rate, an assumed inclusion probability, an
> assumed gas cost and a synthetic divergence process, the machinery produces a
> positive number. Change one unmeasured assumption by an amount smaller than
> the uncertainty in it, and the number becomes zero trades.

That is a statement about the assumption, not about the strategy. Everything
else in the sweep is a slope; only this one is a cliff, and it is the one we
know least about.

What would make the result meaningful, in order:

1. **Measure the competition rate** on real pools: observe divergence, observe
   how fast it closes, and how much of that closing we could realistically
   capture. This requires only read access and no capital.
2. **Measure real gas** and inclusion rates for a two-swap transaction on the
   target chain, again read-only.
3. **Measure real pool divergence** rather than generating it. The synthetic
   distribution was chosen, not observed, and it sets the entire opportunity
   frequency.

All three are read-only research tasks. None of them requires committing money,
and none of them can be skipped by running the simulator for longer.

---

## 6. Defects found in this phase

| # | Defect | How it surfaced | Fix |
|---|---|---|---|
| F-29 | Price impact conflated the pool fee with curve impact, so any impact limit below the 0.3% pool fee was unsatisfiable at every size | Every opportunity blocked; the strategy looked impossible | Separate `curve_impact` (bounded by the risk limit) from `fee_fraction` (a known cost already inside the quote) |
| F-30 | Sizing always chose the impact **ceiling** — the most expensive permitted trade | Profitable dislocations priced as losses | Ternary search for the profit-maximising size within the window |
| F-31 | One global `max_execution_latency_ms` cannot serve both a CEX round-trip and on-chain inclusion | `LATENCY_EXCEEDED` on every DEX opportunity | Global limit set to the loosest legitimate value; CEX/CEX tightens it to 2,000 ms via a `StrategyLimits` override, which can only ever tighten |
| F-32 | Session reported the whole wallet as exposure against **every** pool venue | `EXCHANGE_EXPOSURE_EXCEEDED` twice per opportunity | A DEX protocol never custodies our balance; venue exposure at rest is zero and the engine adds the notional at risk during the swap |
| F-33 | The engine added the full notional to every exposure dimension, double-counting capital already held for a same-asset round trip | Correctly-sized trades blocked in both strategies | `TradeProposal.exposure_delta`, honoured **only** for atomic strategies — a non-atomic failure is a one-sided position worth the full notional, whatever it declares |
| F-34 | Sizing diagnostics used a hardcoded edge, so they always blamed the impact cap | Misleading `sizing_blocks` histogram | Use the real probe edge; distinguish "no positive edge after pool fees" from "fixed cost exceeds impact cap" |
| F-35 | An absurdly large swap input raised a raw `decimal.InvalidOperation` | Found by a test asking for 10³⁰ | Surfaces as `PoolUnusable` — a size the pool cannot serve, not an arithmetic accident |

F-33 is the one worth dwelling on. The original blanket rule was chosen as
"conservative", and for a while it was. Once two different strategies both hit
it on legitimately-sized trades, "conservative" was no longer the right word for
it: it was **wrong**, and being wrong in the safe direction still blocks correct
work. The fix keeps the conservative default and lets only atomic execution —
where a failure genuinely leaves the position untouched — declare otherwise.

---

## 7. Risks confirmed and added

| Risk | What the runs showed |
|---|---|
| #6 MEV / competition | Decisive here in a way it was not for CEX. It is both the cost of losing races (54% of attempts) and the reason opportunities exist at all |
| #7 Gas / priority-fee spike | Injected congestion multiplies the cost of an attempt fourfold. At L1 gas the strategy is already unviable; a spike removes it at L2 too |
| #14 Failed on-chain transaction | 33–36 reverts per 200 attempts, each paying 90% of gas. Priced, not ignored |
| #15 Unknown transaction state | Every unresolved transaction halts the system and needs a human. Forcing it to 100% produced 49 halts in 4,000 ticks — the operability limit, working as designed |
| #33 Infrastructure cost | The only strategy configuration so far that clears it |

New:

* **R-44 Fixed-cost floor vs impact ceiling.** A strategy can be impossible at
  *every* size, not merely unprofitable at the size tried. Now computed before
  trading rather than discovered after. Residual LOW.
* **R-45 Result depends on an unmeasured competition rate.** Between 0.35 and
  0.50 lies the difference between a working strategy and none. Residual
  **HIGH** until measured — and it is the reason this report recommends
  measurement rather than capital.

---

## 8. Recommendation

**Do not commit capital on the strength of §3.** The positive result is real
inside its assumptions and worthless outside them, and the decisive assumption
is the one with no measurement behind it.

Do this instead, in order:

1. **Read-only measurement** of pool divergence, competition rate, real gas and
   inclusion rates on one target L2. No capital, no keys, no execution.
2. Re-run this sensitivity sweep with measured values in place of the assumed
   ones. If the measured competition rate lands above ~0.45, the strategy is
   over and no further work is warranted.
3. Only if it survives that: venue adapters (Phase 4), which need verified
   contract addresses and current official documentation — neither of which may
   be guessed.

L1 needs no further investigation at this pool depth: 30 per attempt against a
divergence distribution that peaks at 1.6% is not a close call.
