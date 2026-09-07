"""Performance and health metrics.

The reporting rule this module encodes: **never present ROI alone.** A report
that omits rejection counts, failure rates, slippage and drawdown is not a
shorter report, it is a misleading one, so the report object requires all of
them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from statistics import median

from ..domain.types import ZERO, to_decimal

ONE = Decimal(1)


@dataclass(slots=True)
class Counter:
    """Named integer counters with no implicit zero-suppression."""

    values: dict[str, int] = field(default_factory=dict)

    def increment(self, key: str, by: int = 1) -> int:
        self.values[key] = self.values.get(key, 0) + by
        return self.values[key]

    def get(self, key: str) -> int:
        return self.values.get(key, 0)

    def as_dict(self) -> dict[str, int]:
        return dict(sorted(self.values.items()))


@dataclass(slots=True)
class Series:
    """A numeric series with the summary statistics we actually report.

    The median and p95 are kept alongside the mean because execution costs are
    not symmetric: the mean of a latency distribution hides exactly the tail
    that kills opportunities.
    """

    name: str
    values: list[Decimal] = field(default_factory=list)

    def observe(self, value: Decimal | int | str) -> None:
        self.values.append(to_decimal(value))

    @property
    def count(self) -> int:
        return len(self.values)

    @property
    def mean(self) -> Decimal | None:
        if not self.values:
            return None
        return sum(self.values, start=ZERO) / Decimal(len(self.values))

    @property
    def median(self) -> Decimal | None:
        if not self.values:
            return None
        return Decimal(str(median(sorted(self.values))))

    @property
    def p95(self) -> Decimal | None:
        if not self.values:
            return None
        ordered = sorted(self.values)
        index = min(len(ordered) - 1, int(len(ordered) * 95 / 100))
        return ordered[index]

    @property
    def worst(self) -> Decimal | None:
        return max(self.values) if self.values else None

    def summary(self) -> dict[str, str | None]:
        return {
            "count": str(self.count),
            "mean": str(self.mean) if self.mean is not None else None,
            "median": str(self.median) if self.median is not None else None,
            "p95": str(self.p95) if self.p95 is not None else None,
            "worst": str(self.worst) if self.worst is not None else None,
        }


@dataclass(slots=True)
class PnLTracker:
    """Realised P/L with drawdown.

    Drawdown is tracked on the running equity curve, not on closed trades, so a
    sequence of small losses is visible before it becomes a large one.
    """

    realised: Decimal = ZERO
    fees: Decimal = ZERO
    peak: Decimal = ZERO
    max_drawdown: Decimal = ZERO
    wins: int = 0
    losses: int = 0
    gross_profit: Decimal = ZERO
    gross_loss: Decimal = ZERO

    def record(self, pnl: Decimal, fees: Decimal) -> None:
        value = to_decimal(pnl)
        self.realised += value
        self.fees += to_decimal(fees)
        if value > ZERO:
            self.wins += 1
            self.gross_profit += value
        elif value < ZERO:
            self.losses += 1
            self.gross_loss += -value
        self.peak = max(self.peak, self.realised)
        self.max_drawdown = max(self.max_drawdown, self.peak - self.realised)

    @property
    def trades(self) -> int:
        return self.wins + self.losses

    @property
    def win_rate(self) -> Decimal | None:
        """``None`` with no trades — not zero, which would read as "always loses"."""
        if self.trades == 0:
            return None
        return Decimal(self.wins) / Decimal(self.trades)

    @property
    def profit_factor(self) -> Decimal | None:
        """``None`` when there are no losses to divide by; an undefined ratio is
        not an infinitely good one."""
        if self.gross_loss <= ZERO:
            return None
        return self.gross_profit / self.gross_loss

    @property
    def average_win(self) -> Decimal | None:
        return self.gross_profit / Decimal(self.wins) if self.wins else None

    @property
    def average_loss(self) -> Decimal | None:
        return self.gross_loss / Decimal(self.losses) if self.losses else None


@dataclass(slots=True)
class InfrastructureCost:
    """Running cost of operating the system, in the accounting currency.

    Tracked because trading profit that does not cover infrastructure is not
    profit. Values are operator-supplied estimates and are labelled as such.
    """

    per_day: Decimal = ZERO
    days_elapsed: Decimal = ZERO

    @property
    def total(self) -> Decimal:
        return self.per_day * self.days_elapsed


@dataclass(slots=True)
class PerformanceReport:
    """The complete picture. Every field is mandatory by construction."""

    opportunities_detected: int
    opportunities_rejected: int
    opportunities_accepted: int
    rejection_reasons: dict[str, int]
    pnl: PnLTracker
    infrastructure: InfrastructureCost
    execution_failures: int
    partial_fills: int
    unknown_outcomes: int
    slippage: Series
    latency: Series
    calibration_trades: int
    #: P/L from calibration trades, kept apart from strategy performance.
    #: These trades were run to measure execution outcomes, not because their
    #: expected value justified them, so counting them as performance would be
    #: reporting a result the strategy never claimed.
    calibration_pnl: PnLTracker = field(default_factory=PnLTracker)
    notes: tuple[str, ...] = ()

    @property
    def net_after_infrastructure(self) -> Decimal:
        return self.pnl.realised - self.infrastructure.total

    @property
    def execution_failure_rate(self) -> Decimal | None:
        attempts = self.opportunities_accepted
        if attempts == 0:
            return None
        return Decimal(self.execution_failures) / Decimal(attempts)

    @property
    def partial_fill_rate(self) -> Decimal | None:
        attempts = self.opportunities_accepted
        if attempts == 0:
            return None
        return Decimal(self.partial_fills) / Decimal(attempts)

    def as_dict(self) -> dict[str, object]:
        return {
            "opportunities_detected": self.opportunities_detected,
            "opportunities_accepted": self.opportunities_accepted,
            "opportunities_rejected": self.opportunities_rejected,
            "rejection_reasons": self.rejection_reasons,
            "trades": self.pnl.trades,
            "winning_trades": self.pnl.wins,
            "losing_trades": self.pnl.losses,
            "gross_profit": str(self.pnl.gross_profit),
            "gross_loss": str(self.pnl.gross_loss),
            "fees": str(self.pnl.fees),
            "net_pnl_strategy": str(self.pnl.realised),
            "net_pnl_calibration": str(self.calibration_pnl.realised),
            "calibration_trades_pnl_excluded_from_performance": True,
            "infrastructure_cost": str(self.infrastructure.total),
            "net_after_infrastructure": str(self.net_after_infrastructure),
            "average_win": str(self.pnl.average_win) if self.pnl.average_win else None,
            "average_loss": str(self.pnl.average_loss) if self.pnl.average_loss else None,
            "win_rate": str(self.pnl.win_rate) if self.pnl.win_rate is not None else None,
            "profit_factor": (
                str(self.pnl.profit_factor) if self.pnl.profit_factor is not None else None
            ),
            "max_drawdown": str(self.pnl.max_drawdown),
            "execution_failure_rate": (
                str(self.execution_failure_rate)
                if self.execution_failure_rate is not None
                else None
            ),
            "partial_fill_rate": (
                str(self.partial_fill_rate) if self.partial_fill_rate is not None else None
            ),
            "unknown_outcomes": self.unknown_outcomes,
            "calibration_trades": self.calibration_trades,
            "combined_pnl_all_trades": str(self.pnl.realised + self.calibration_pnl.realised),
            "slippage": self.slippage.summary(),
            "latency_ms": self.latency.summary(),
            "notes": list(self.notes),
        }
