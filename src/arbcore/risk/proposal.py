"""The risk-relevant summary of a candidate trade.

Each strategy module builds its own opportunity in its own terms, then reduces
it to a :class:`TradeProposal` — the only thing the risk engine sees. That
keeps the engine strategy-agnostic while leaving each strategy free to model
its own economics.

Optional fields use ``None`` to mean *not known*. ``None`` is never treated as
zero, never treated as favourable, and always rejects: an unknown fee is not a
zero fee.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import datetime
from decimal import Decimal

from ..domain.types import ZERO, AssetId, Atomicity, Chain, StrategyKind, VenueId, to_decimal
from .budget import RiskCategory


@dataclass(frozen=True, slots=True)
class CostBreakdown:
    """Every cost line that stands between gross spread and net profit.

    A line that does not apply to a strategy is ``Decimal(0)`` and says so
    deliberately; a line that applies but could not be estimated is ``None``
    and blocks the trade.
    """

    cex_trading_fees: Decimal | None = None
    dex_fees: Decimal | None = None
    gas: Decimal | None = None
    priority_fees: Decimal | None = None
    expected_slippage_cost: Decimal | None = None
    price_impact_cost: Decimal | None = None
    withdrawal_fees: Decimal | None = None
    deposit_fees: Decimal | None = None
    bridge_fees: Decimal | None = None
    expected_rebalancing_cost: Decimal | None = None
    expected_execution_loss: Decimal | None = None
    partial_fill_cost: Decimal | None = None
    other_costs: Decimal | None = None

    def unknown_lines(self) -> tuple[str, ...]:
        return tuple(f.name for f in fields(self) if getattr(self, f.name) is None)

    @property
    def complete(self) -> bool:
        return not self.unknown_lines()

    def total(self) -> Decimal:
        """Sum of all lines. Raises if any line is unknown — never guesses."""
        unknown = self.unknown_lines()
        if unknown:
            raise ValueError(f"cannot total an incomplete cost breakdown; unknown: {unknown}")
        return sum(
            (to_decimal(getattr(self, f.name)) for f in fields(self)),
            start=ZERO,
        )


@dataclass(frozen=True, slots=True)
class ExecutionProbabilities:
    """Estimated execution outcomes.

    These are only ever populated from measured data. If a strategy has no
    measurement basis yet, it leaves this ``None`` on the proposal and the
    opportunity is classified research-only rather than assigned invented
    numbers.
    """

    full_execution: Decimal
    partial_execution: Decimal
    failure: Decimal
    opportunity_decay: Decimal
    adverse_price_move: Decimal
    #: How the estimates were produced (sample size, window, method).
    basis: str

    def __post_init__(self) -> None:
        for f in fields(self):
            if f.name == "basis":
                continue
            value = to_decimal(getattr(self, f.name))
            object.__setattr__(self, f.name, value)
            if not (ZERO <= value <= Decimal(1)):
                raise ValueError(f"{f.name} must be a probability in [0, 1] (got {value})")
        if not self.basis.strip():
            raise ValueError("execution probabilities must record their empirical basis")


@dataclass(frozen=True, slots=True)
class TradeProposal:
    """What the risk engine evaluates.

    All monetary values are in the accounting currency; all fractions are
    decimals in [0, 1].
    """

    proposal_id: str
    strategy: StrategyKind
    atomicity: Atomicity
    #: Total capital committed across all legs.
    notional: Decimal
    assets: tuple[AssetId, ...]
    venues: tuple[VenueId, ...]
    chains: tuple[Chain, ...]

    gross_expected_profit: Decimal
    costs: CostBreakdown

    expected_slippage: Decimal
    expected_price_impact: Decimal
    quote_age_ms: int
    expected_execution_latency_ms: int
    clock_drift_ms: int
    data_quality_score: Decimal

    #: True only when the required free balances were verified against a
    #: reconciled inventory snapshot. ``None`` means unverified — a rejection.
    inventory_sufficient: bool | None = None
    #: ``None`` when there is no measured basis (research-only classification).
    probabilities: ExecutionProbabilities | None = None
    #: Budget the strategy intends to reserve, per risk category.
    budget_reservation: dict[RiskCategory, Decimal] = field(default_factory=dict)
    #: Data-gathering trade run to *measure* execution probabilities that do
    #: not exist yet. Permitted only in paper mode, only below a hard size cap,
    #: and excluded from performance reporting. See docs/paper-trading.md.
    calibration: bool = False
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in ("notional", "gross_expected_profit"):
            object.__setattr__(self, name, to_decimal(getattr(self, name)))
        for name in ("expected_slippage", "expected_price_impact", "data_quality_score"):
            value = to_decimal(getattr(self, name))
            object.__setattr__(self, name, value)
            if not (ZERO <= value <= Decimal(1)):
                raise ValueError(f"{name} must be within [0, 1] (got {value})")
        if self.notional <= ZERO:
            raise ValueError("notional must be > 0")
        if not self.assets or not self.venues:
            raise ValueError("a proposal must reference at least one asset and one venue")
        for name in ("quote_age_ms", "expected_execution_latency_ms"):
            value_int = getattr(self, name)
            if not isinstance(value_int, int) or value_int < 0:
                raise ValueError(f"{name} must be a non-negative integer (got {value_int!r})")
        # Clock drift is signed: our clock may be ahead of or behind the venue's,
        # and both directions invalidate a time-sensitive opportunity. The
        # engine compares the magnitude.
        if not isinstance(self.clock_drift_ms, int):
            raise ValueError(f"clock_drift_ms must be an integer (got {self.clock_drift_ms!r})")

    def net_expected_profit(self) -> Decimal:
        """Gross profit minus every modelled cost. Raises if costs are incomplete."""
        return self.gross_expected_profit - self.costs.total()

    def safety_adjusted_profit(self, cost_safety_factor: Decimal) -> Decimal:
        """Net profit with modelled costs inflated by the safety factor.

        Costs are the quantity we most reliably underestimate, so the margin is
        taken on costs rather than discounting the revenue.
        """
        factor = to_decimal(cost_safety_factor)
        return self.gross_expected_profit - (self.costs.total() * factor)

    def risk_adjusted_ev(self, cost_safety_factor: Decimal) -> Decimal | None:
        """Expected value weighted by measured execution outcomes.

        Returns ``None`` when no measured probabilities exist. Callers must
        treat ``None`` as "not evaluable", never as zero.
        """
        if self.probabilities is None:
            return None
        p = self.probabilities
        upside = self.safety_adjusted_profit(cost_safety_factor)
        # Failure and decay forfeit the upside and still pay the modelled
        # execution loss; partial fills are conservatively modelled as earning
        # nothing while paying the full partial-fill cost.
        failure_cost = to_decimal(self.costs.expected_execution_loss or ZERO)
        partial_cost = to_decimal(self.costs.partial_fill_cost or ZERO)
        return (
            p.full_execution * upside
            - p.partial_execution * partial_cost
            - (p.failure + p.opportunity_decay + p.adverse_price_move) * failure_cost
        )
