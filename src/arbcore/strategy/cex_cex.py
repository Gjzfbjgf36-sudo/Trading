"""CEX → CEX spot arbitrage on pre-funded inventory.

The model: hold base on one venue and quote on another, sell where the bid is
high and buy where the ask is low, **simultaneously and against existing
balances**. No transfer happens on the critical path, because a transfer takes
minutes to hours and the spread does not.

Consequences that shape every number below:

* The strategy is **non-atomic**. Either leg can fail alone, and the leg risk
  is a real modelled cost, not a footnote.
* Every trade shifts inventory one way. The eventual transfer back is a real
  cost and is amortised into each trade, so a strategy that "profits" only by
  ignoring the cost of undoing itself is rejected here rather than discovered
  three months later.
* Prices come from walking the book at *our* size. The top of book is used
  only to detect a candidate; it never prices one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from ..costs.fees import FeeBook, FeeUnknown
from ..domain.types import (
    ZERO,
    AssetId,
    Atomicity,
    Chain,
    Side,
    StrategyKind,
    VenueId,
    to_decimal,
)
from ..inventory.manager import InventoryManager
from ..marketdata.book import OrderBook
from ..pricing.executable import ExecutableQuote, NotExecutable, walk_book
from ..risk.budget import RiskCategory
from ..risk.proposal import CostBreakdown, ExecutionProbabilities, TradeProposal

_QUANT = Decimal("0.00000001")


@dataclass(frozen=True, slots=True)
class CexCexConfig:
    """Strategy parameters. All are configuration, none are literals in logic."""

    base: AssetId
    quote: AssetId
    #: Nominal size per trade, in base units.
    trade_size: Decimal
    #: Size used while calibrating, before probabilities exist.
    calibration_size: Decimal
    #: Fraction of notional assumed lost if one leg fills alone and we must
    #: unwind at an adverse price. UNCERTAIN — see docs/assumptions.md (A14).
    leg_risk_fraction: Decimal = Decimal("0.0015")
    #: Cost of eventually moving inventory back, per unit of base traded,
    #: expressed as a fraction of the traded notional.
    rebalance_cost_fraction: Decimal = Decimal("0.0004")
    #: Additional loss assumed when only part of the size fills.
    partial_fill_fraction: Decimal = Decimal("0.0008")

    def __post_init__(self) -> None:
        if self.trade_size <= ZERO or self.calibration_size <= ZERO:
            raise ValueError("trade sizes must be > 0")
        if self.calibration_size > self.trade_size:
            raise ValueError("calibration size must not exceed the normal trade size")


@dataclass(frozen=True, slots=True)
class CexCexOpportunity:
    """A detected two-leg candidate, priced at our intended size."""

    opportunity_id: str
    buy_venue: VenueId
    sell_venue: VenueId
    size: Decimal
    buy_quote: ExecutableQuote
    sell_quote: ExecutableQuote
    detected_at: datetime

    @property
    def gross_profit(self) -> Decimal:
        """Revenue from selling minus outlay from buying, at executable prices."""
        return (
            self.sell_quote.vwap * self.sell_quote.filled_size
            - self.buy_quote.vwap * self.buy_quote.filled_size
        ).quantize(_QUANT)

    @property
    def notional(self) -> Decimal:
        """Capital committed. Both legs commit capital simultaneously."""
        return (self.buy_quote.vwap * self.buy_quote.filled_size).quantize(_QUANT)

    @property
    def price_impact(self) -> Decimal:
        return max(self.buy_quote.price_impact, self.sell_quote.price_impact)


class CexCexStrategy:
    """Detects candidates and reduces them to risk-engine proposals."""

    kind = StrategyKind.CEX_CEX
    atomicity = Atomicity.NON_ATOMIC

    def __init__(self, config: CexCexConfig, fees: FeeBook) -> None:
        self.config = config
        self.fees = fees

    # ------------------------------------------------------------------
    def detect(
        self,
        books: dict[VenueId, OrderBook],
        inventory: InventoryManager,
        *,
        now: datetime,
        opportunity_id: str,
        size: Decimal | None = None,
    ) -> CexCexOpportunity | None:
        """Find the best executable two-venue spread, or ``None``.

        Returns ``None`` — never a degraded opportunity — when any input is
        unusable. A caller that receives ``None`` has nothing to reason about,
        which is the intended outcome of missing information.
        """
        usable = {v: b for v, b in books.items() if b.usable}
        if len(usable) < 2:
            return None

        intended = to_decimal(size if size is not None else self.config.trade_size)
        best: CexCexOpportunity | None = None

        for buy_venue, buy_book in usable.items():
            for sell_venue, sell_book in usable.items():
                if buy_venue == sell_venue:
                    continue
                # Inventory is the binding constraint: we can only buy where we
                # hold quote, and only sell where we hold base.
                affordable = self._affordable_size(inventory, buy_venue, sell_venue, buy_book)
                tradeable = min(intended, affordable)
                if tradeable <= ZERO:
                    continue
                try:
                    buy_quote = walk_book(buy_book, Side.BUY, tradeable)
                    sell_quote = walk_book(sell_book, Side.SELL, tradeable)
                except NotExecutable:
                    continue
                # A partial walk means the book cannot absorb our size. We do
                # not silently shrink the trade to whatever fits: that is a
                # different opportunity with different economics.
                if not buy_quote.complete or not sell_quote.complete:
                    continue
                candidate = CexCexOpportunity(
                    opportunity_id=opportunity_id,
                    buy_venue=buy_venue,
                    sell_venue=sell_venue,
                    size=tradeable,
                    buy_quote=buy_quote,
                    sell_quote=sell_quote,
                    detected_at=now,
                )
                if candidate.gross_profit <= ZERO:
                    continue
                if best is None or candidate.gross_profit > best.gross_profit:
                    best = candidate
        return best

    def _affordable_size(
        self,
        inventory: InventoryManager,
        buy_venue: VenueId,
        sell_venue: VenueId,
        buy_book: OrderBook,
    ) -> Decimal:
        """Largest size fundable from *spendable* balances on both venues."""
        base_available = inventory.spendable(sell_venue, self.config.base)
        quote_available = inventory.spendable(buy_venue, self.config.quote)
        best_ask = buy_book.best_ask
        if best_ask is None or best_ask.price <= ZERO:
            return ZERO
        # Price the quote-side budget at top of book, then let the real walk
        # decide; using the walk price here would be circular.
        quote_capacity = (quote_available / best_ask.price).quantize(_QUANT)
        return min(base_available, quote_capacity)

    # ------------------------------------------------------------------
    def to_proposal(
        self,
        opportunity: CexCexOpportunity,
        *,
        data_quality: Decimal,
        quote_age_ms: int,
        clock_drift_ms: int,
        expected_latency_ms: int,
        probabilities: ExecutionProbabilities | None,
        calibration: bool,
    ) -> TradeProposal:
        """Reduce an opportunity to the risk engine's vocabulary.

        Raises :class:`FeeUnknown` if either venue has no recorded fee
        schedule; the caller turns that into a `FEE_UNCERTAIN` rejection rather
        than substituting a plausible rate.
        """
        costs = self._cost_breakdown(opportunity)
        notional = opportunity.notional
        return TradeProposal(
            proposal_id=opportunity.opportunity_id,
            strategy=self.kind,
            atomicity=self.atomicity,
            notional=notional,
            assets=(self.config.base,),
            venues=(opportunity.buy_venue, opportunity.sell_venue),
            chains=(),
            gross_expected_profit=opportunity.gross_profit,
            costs=costs,
            expected_slippage=self._expected_slippage(opportunity),
            expected_price_impact=opportunity.price_impact,
            quote_age_ms=quote_age_ms,
            clock_drift_ms=clock_drift_ms,
            expected_execution_latency_ms=expected_latency_ms,
            data_quality_score=data_quality,
            inventory_sufficient=True,
            probabilities=probabilities,
            budget_reservation={
                RiskCategory.EXECUTION: (notional * self.config.leg_risk_fraction).quantize(
                    _QUANT
                ),
                RiskCategory.COUNTERPARTY: (notional / Decimal(100)).quantize(_QUANT),
            },
            calibration=calibration,
            created_at=opportunity.detected_at,
        )

    def _cost_breakdown(self, opportunity: CexCexOpportunity) -> CostBreakdown:
        """The full cost stack.

        Lines that genuinely do not apply to a CEX/CEX spot trade are zero and
        say so; nothing is left ``None`` unless it truly could not be
        estimated, because ``None`` blocks the trade.
        """
        try:
            buy_fees = self.fees.get(opportunity.buy_venue)
            sell_fees = self.fees.get(opportunity.sell_venue)
        except FeeUnknown:
            # Propagate: the caller must reject, not guess a fee.
            raise

        buy_notional = opportunity.buy_quote.vwap * opportunity.buy_quote.filled_size
        sell_notional = opportunity.sell_quote.vwap * opportunity.sell_quote.filled_size
        trading_fees = buy_fees.taker_cost(buy_notional) + sell_fees.taker_cost(sell_notional)
        notional = buy_notional

        # Impact is already inside the walked VWAP, so charging it again as a
        # cost line would double-count. It is reported as zero here and
        # separately limited by max_price_impact.
        return CostBreakdown(
            cex_trading_fees=trading_fees.quantize(_QUANT),
            dex_fees=ZERO,
            gas=ZERO,
            priority_fees=ZERO,
            expected_slippage_cost=(notional * self._expected_slippage(opportunity)).quantize(
                _QUANT
            ),
            price_impact_cost=ZERO,
            withdrawal_fees=ZERO,
            deposit_fees=ZERO,
            bridge_fees=ZERO,
            expected_rebalancing_cost=(
                notional * self.config.rebalance_cost_fraction
            ).quantize(_QUANT),
            expected_execution_loss=(notional * self.config.leg_risk_fraction).quantize(_QUANT),
            partial_fill_cost=(notional * self.config.partial_fill_fraction).quantize(_QUANT),
            other_costs=ZERO,
        )

    def _expected_slippage(self, opportunity: CexCexOpportunity) -> Decimal:
        """Expected adverse move between decision and fill, per leg, summed.

        Uses the observed impact of our own walk as the floor: we already know
        we move the price by at least that much.
        """
        return max(
            opportunity.buy_quote.price_impact, opportunity.sell_quote.price_impact
        )


#: CEX spot trading settles inside the venue, so no chain is touched on the
#: critical path. Recorded explicitly so that the empty tuple in the proposal
#: is a statement rather than an omission.
NO_CHAINS: tuple[Chain, ...] = ()
