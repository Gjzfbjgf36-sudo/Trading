# Economic viability

A strategy with a positive theoretical ROI can still be a losing business. The
platform must answer, per strategy, **"is this viable at the capital actually
available?"** before any capital is committed. Phase 11 produces the numbers;
this document fixes the method.

## The question

```
Net trading profit
  − infrastructure cost (RPC, market-data, APIs, servers, database, monitoring, network)
  = net profit after infrastructure cost           ← the only figure that matters
```

A strategy that is trading-profitable and infrastructure-negative is not
profitable. It is reported as what it is.

## Minimum viable capital

For each strategy, estimate:

* **Inventory requirement.** Pre-funded CEX/CEX arbitrage needs both sides
  stocked on both venues; the capital is committed whether or not an
  opportunity appears.
* **Opportunity frequency × average captured edge.** Both measured, not
  assumed. Edge is net of the full cost stack, not gross spread.
* **Capital efficiency.** How often committed inventory is actually used.
* **Fixed cost floor.** Infrastructure runs whether or not we trade.

Minimum viable capital is the level at which expected net profit exceeds the
fixed cost floor by a margin that survives the measured estimation error.

## Structural observations to test, not assume

These are hypotheses for Phase 11, listed so they are not smuggled in as facts:

* Small capital may be structurally disadvantaged: fixed per-trade costs (gas,
  priority fees, withdrawal fees) do not scale down, so small notional sizes
  are disproportionately eaten by them.
* Deep, mature markets (BTC, ETH, SOL on major venues) are the most competed;
  the edge that survives may be too small to clear our own cost floor.
* Inventory-based CEX/CEX arbitrage converts an execution problem into a
  capital-allocation problem: the relevant return is on *all* committed
  inventory, not on the traded notional.

## Honest reporting rule

**Never present ROI alone.** Every performance report carries opportunities
detected / rejected / accepted, trade counts, win rate, gross and net P/L,
fees, average profit and loss, profit factor, maximum drawdown, risk-adjusted
return, execution failure rate, partial-fill rate, slippage and latency —
together with the paper-vs-reality error terms.
