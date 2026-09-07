"""DEX → DEX arbitrage on one chain.

A genuinely different strategy from CEX/CEX, not a reskin — which is why it is
a separate module with its own opportunity, cost, execution and settlement
models:

| | CEX/CEX | DEX/DEX |
|---|---|---|
| Atomicity | Non-atomic; a leg can fill alone | **Atomic** when both swaps share one tx |
| Leg risk | Real and priced | **None** when atomic — the transaction reverts as a whole |
| Dominant cost | Taker fees, proportional to size | **Gas, fixed per attempt** |
| Failure mode | One-sided exposure | Reverted transaction; gas spent, position unchanged |
| Competition | Someone else fills the order | Someone else's transaction is ordered before ours |
| Inventory | Must be pre-funded on both venues | Single wallet, one asset |

The consequence is a mirror image of the CEX/CEX finding. There, cost scaled
with size and no size was profitable. Here, the dominant cost is *fixed*, so
there is a **minimum** viable size — and price impact on the pool curve imposes
a **maximum**. If the minimum exceeds the maximum, the strategy is impossible
at any size, and that is computed up front rather than discovered later.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from ..costs.gas import GasBook, GasModel, InclusionModel
from ..domain.types import (
    ZERO,
    AssetId,
    Atomicity,
    Chain,
    StrategyKind,
    VenueId,
    VenueKind,
)
from ..pricing.amm import (
    Pool,
    PoolUnusable,
    SwapQuote,
    max_input_for_impact,
    quote_swap,
)
from ..risk.budget import RiskCategory
from ..risk.proposal import CostBreakdown, ExecutionProbabilities, TradeProposal

_QUANT = Decimal("0.00000001")


class NotViableAtAnySize(RuntimeError):
    """Raised when the fixed-cost minimum exceeds the impact-constrained maximum."""


@dataclass(frozen=True, slots=True)
class DexDexConfig:
    """Strategy parameters."""

    chain: Chain
    base: AssetId
    quote: AssetId
    #: Maximum notional per attempt, in quote units.
    max_notional: Decimal
    #: Price impact ceiling used when sizing against the pool curve.
    max_pool_impact: Decimal = Decimal("0.0030")
    #: Multiple of the break-even size required before a trade is worth making.
    #: 1.0 would mean trading at exactly break-even, which is not a trade.
    minimum_size_margin: Decimal = Decimal("2.0")
    #: Time from pricing to inclusion. A per-chain fact — roughly a block time
    #: on an L1, far less on a rollup — and the quantity the latency limit is
    #: actually about.
    expected_inclusion_ms: int = 12_000

    def __post_init__(self) -> None:
        if self.max_notional <= ZERO:
            raise ValueError("max_notional must be > 0")
        if self.minimum_size_margin < Decimal(1):
            raise ValueError("minimum_size_margin below 1 trades below break-even")


@dataclass(frozen=True, slots=True)
class DexDexOpportunity:
    """A two-swap round trip priced against actual reserves.

    The round trip starts and ends in ``quote``: spend quote on the cheap pool,
    sell the base on the dear pool, and compare what came back to what went out.
    Framing it as a round trip rather than two independent prices is what makes
    the profit unambiguous — there is no marking convention to argue about.
    """

    opportunity_id: str
    buy_pool: Pool
    sell_pool: Pool
    input_quote: Decimal
    first_leg: SwapQuote
    second_leg: SwapQuote
    detected_at: datetime
    atomic: bool

    @property
    def output_quote(self) -> Decimal:
        return self.second_leg.output_amount

    @property
    def gross_profit(self) -> Decimal:
        """Quote returned minus quote spent. Pool fees are already inside both legs."""
        return (self.output_quote - self.input_quote).quantize(_QUANT)

    @property
    def notional(self) -> Decimal:
        return self.input_quote

    @property
    def curve_impact(self) -> Decimal:
        """Worst curve impact across the two legs — the liquidity constraint."""
        return max(self.first_leg.curve_impact, self.second_leg.curve_impact)

    @property
    def total_execution_cost(self) -> Decimal:
        """Curve impact plus both pool fees. Already inside `gross_profit`."""
        return max(self.first_leg.price_impact, self.second_leg.price_impact)

    @property
    def edge_fraction(self) -> Decimal:
        if self.input_quote <= ZERO:
            return ZERO
        return (self.gross_profit / self.input_quote).quantize(_QUANT)


@dataclass(frozen=True, slots=True)
class SizingWindow:
    """The band of sizes that is simultaneously large enough and small enough."""

    minimum: Decimal
    maximum: Decimal
    chosen: Decimal
    reason: str

    @property
    def viable(self) -> bool:
        return self.chosen > ZERO


class DexDexStrategy:
    """Detects and prices single-chain, two-pool round trips."""

    kind = StrategyKind.DEX_DEX

    def __init__(
        self,
        config: DexDexConfig,
        gas: GasBook,
        inclusion: InclusionModel,
        *,
        atomic: bool = True,
    ) -> None:
        self.config = config
        self.gas = gas
        self.inclusion = inclusion
        #: Whether both swaps execute in one transaction. A caller that cannot
        #: guarantee this must say so: the risk difference is the whole point.
        self.atomic = atomic

    @property
    def atomicity(self) -> Atomicity:
        return Atomicity.ATOMIC if self.atomic else Atomicity.NON_ATOMIC

    # ------------------------------------------------------------------
    def size_window(self, buy_pool: Pool, sell_pool: Pool, edge_hint: Decimal) -> SizingWindow:
        """Reconcile the fixed-cost floor with the price-impact ceiling.

        Returns a zero size rather than a "best effort" one when the two do not
        overlap. There is no partially viable trade here: below the floor it
        loses money on gas, above the ceiling it loses money on impact.
        """
        gas = self.gas.get(self.config.chain)
        ceiling = min(
            self.config.max_notional,
            max_input_for_impact(buy_pool, self.config.quote, self.config.max_pool_impact),
        )
        if edge_hint <= ZERO:
            return SizingWindow(ZERO, ceiling, ZERO, "no positive edge to cover fixed costs")
        break_even = gas.minimum_viable_notional(edge_hint)
        floor = (break_even * self.config.minimum_size_margin).quantize(_QUANT)
        if floor > ceiling:
            return SizingWindow(
                floor,
                ceiling,
                ZERO,
                (
                    f"fixed costs need at least {floor} but pool impact caps the size at "
                    f"{ceiling}; no size satisfies both"
                ),
            )
        best = self._optimal_size(buy_pool, sell_pool, floor, ceiling, gas)
        if best <= ZERO:
            return SizingWindow(
                floor, ceiling, ZERO, "no size in the window covers the fixed cost"
            )
        return SizingWindow(floor, ceiling, best, "sized at the profit-maximising point")

    def _optimal_size(
        self,
        buy_pool: Pool,
        sell_pool: Pool,
        floor: Decimal,
        ceiling: Decimal,
        gas: GasModel,
        *,
        iterations: int = 60,
    ) -> Decimal:
        """Size that maximises net profit, not the largest size permitted.

        Revenue grows linearly with size while curve impact grows faster, so
        net profit is concave and has an interior maximum. Sizing at the impact
        ceiling — the obvious thing, and what this did first — deliberately
        picks the most expensive permitted trade, and turned profitable
        dislocations into losses.

        Ternary search over the concave region, then a final check that the
        winner actually clears the fixed cost.
        """

        def net(size: Decimal) -> Decimal:
            priced = self._price(buy_pool, sell_pool, size, "sizing", None)
            if priced is None:
                return Decimal("-1e30")
            return priced.gross_profit - gas.total_cost

        low, high = floor, ceiling
        for _ in range(iterations):
            span = high - low
            if span <= _QUANT:
                break
            first = low + span / Decimal(3)
            second = high - span / Decimal(3)
            if net(first) < net(second):
                low = first
            else:
                high = second
        candidate = ((low + high) / Decimal(2)).quantize(_QUANT)
        return candidate if net(candidate) > ZERO else ZERO

    def detect(
        self,
        pools: dict[str, Pool],
        *,
        now: datetime,
        opportunity_id: str,
        available_quote: Decimal,
    ) -> DexDexOpportunity | None:
        """Find the best round trip, or ``None``.

        A first pass at a probe size establishes the direction and a rough edge;
        the real size is then chosen from that edge and re-priced exactly. Using
        the probe's economics as the answer would systematically overstate the
        edge, because impact grows with the size we actually intend to trade.
        """
        if len(pools) < 2 or available_quote <= ZERO:
            return None

        best: DexDexOpportunity | None = None
        for buy_name, buy_pool in pools.items():
            for sell_name, sell_pool in pools.items():
                if buy_name == sell_name:
                    continue
                if buy_pool.spot_price >= sell_pool.spot_price:
                    continue  # buy where base is cheap, sell where it is dear
                probe = min(available_quote, self.config.max_notional) / Decimal(100)
                hint = self._round_trip_edge(buy_pool, sell_pool, probe)
                if hint is None or hint <= ZERO:
                    continue
                window = self.size_window(buy_pool, sell_pool, hint)
                size = min(window.chosen, available_quote)
                if size <= ZERO:
                    continue
                priced = self._price(buy_pool, sell_pool, size, opportunity_id, now)
                if priced is None or priced.gross_profit <= ZERO:
                    continue
                if best is None or priced.gross_profit > best.gross_profit:
                    best = priced
        return best

    def round_trip_edge(
        self, buy_pool: Pool, sell_pool: Pool, size: Decimal
    ) -> Decimal | None:
        """Edge as a fraction of notional at a probe size. Diagnostics only."""
        return self._round_trip_edge(buy_pool, sell_pool, size)

    def _round_trip_edge(
        self, buy_pool: Pool, sell_pool: Pool, size: Decimal
    ) -> Decimal | None:
        priced = self._price(buy_pool, sell_pool, size, "probe", None)
        return priced.edge_fraction if priced else None

    def _price(
        self,
        buy_pool: Pool,
        sell_pool: Pool,
        input_quote: Decimal,
        opportunity_id: str,
        now: datetime | None,
    ) -> DexDexOpportunity | None:
        try:
            first = quote_swap(buy_pool, self.config.quote, input_quote)
            # The second leg is priced against the pool as it stands. Both pools
            # are distinct, so the first swap does not move the second — but the
            # helper is used where they could be, to keep that explicit.
            second = quote_swap(sell_pool, self.config.base, first.output_amount)
        except PoolUnusable:
            return None
        return DexDexOpportunity(
            opportunity_id=opportunity_id,
            buy_pool=buy_pool,
            sell_pool=sell_pool,
            input_quote=input_quote,
            first_leg=first,
            second_leg=second,
            detected_at=now or datetime.min,
            atomic=self.atomic,
        )

    # ------------------------------------------------------------------
    def to_proposal(
        self,
        opportunity: DexDexOpportunity,
        *,
        venues: tuple[VenueId, ...],
        data_quality: Decimal,
        quote_age_ms: int,
        clock_drift_ms: int,
        expected_latency_ms: int,
        probabilities: ExecutionProbabilities | None,
        calibration: bool,
        congestion: Decimal = Decimal(1),
    ) -> TradeProposal:
        """Reduce to the risk engine's vocabulary.

        Raises :class:`GasUnknown` if the chain has no gas model; the caller
        turns that into a `FEE_UNCERTAIN` rejection rather than guessing.
        """
        gas = self.gas.get(self.config.chain).with_congestion(congestion)
        costs = self._cost_breakdown(opportunity, gas)
        return TradeProposal(
            proposal_id=opportunity.opportunity_id,
            strategy=self.kind,
            atomicity=self.atomicity,
            notional=opportunity.notional,
            assets=(self.config.base, self.config.quote),
            venues=venues,
            chains=(self.config.chain,),
            gross_expected_profit=opportunity.gross_profit,
            costs=costs,
            # Slippage here is the move between pricing and inclusion; the
            # curve impact is what our own size costs. Both are bounded, and
            # the pool fee is deliberately in neither: it is a known cost that
            # is already inside gross_profit.
            expected_slippage=opportunity.curve_impact,
            expected_price_impact=opportunity.curve_impact,
            quote_age_ms=quote_age_ms,
            clock_drift_ms=clock_drift_ms,
            expected_execution_latency_ms=expected_latency_ms,
            data_quality_score=data_quality,
            inventory_sufficient=True,
            probabilities=probabilities,
            budget_reservation={
                RiskCategory.SMART_CONTRACT: (opportunity.notional / Decimal(50)).quantize(
                    _QUANT
                ),
                RiskCategory.EXECUTION: gas.total_cost,
            },
            calibration=calibration,
            # An atomic round trip that fails reverts: we are left holding the
            # same quote balance we started with, minus gas. The gas is the
            # exposure the attempt actually adds.
            exposure_delta=gas.total_cost if self.atomic else None,
            created_at=opportunity.detected_at,
        )

    def _cost_breakdown(
        self, opportunity: DexDexOpportunity, gas: GasModel
    ) -> CostBreakdown:
        """The DEX cost stack.

        Note what is **zero and why**: no CEX fees (no exchange involved), no
        withdrawal, deposit or bridge fees (one chain, one wallet), and — when
        atomic — no execution loss, because a failed round trip reverts as a
        whole and leaves the position untouched. Those zeros are claims about
        the execution model, not omissions.
        """
        expected_gas = self.inclusion.expected_gas_cost(gas)
        # Pool fees and curve impact are already inside the leg quotes and thus
        # inside gross_profit. Charging them again here would double-count.
        leg_risk = (
            ZERO
            if self.atomic
            else (opportunity.notional * Decimal("0.0030")).quantize(_QUANT)
        )
        return CostBreakdown(
            cex_trading_fees=ZERO,
            dex_fees=ZERO,
            gas=gas.base_cost,
            # The expected cost of attempts that never land or that revert,
            # over and above the base cost of the one we hope succeeds.
            priority_fees=max(ZERO, expected_gas - gas.base_cost).quantize(_QUANT),
            expected_slippage_cost=ZERO,
            price_impact_cost=ZERO,
            withdrawal_fees=ZERO,
            deposit_fees=ZERO,
            bridge_fees=ZERO,
            expected_rebalancing_cost=ZERO,
            expected_execution_loss=leg_risk,
            partial_fill_cost=ZERO if self.atomic else (leg_risk / Decimal(2)),
            other_costs=ZERO,
        )


#: DEX venues are protocol deployments, not exchanges holding our balance.
def dex_venue(protocol: str) -> VenueId:
    return VenueId(protocol.lower(), VenueKind.DEX)
