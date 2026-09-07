"""Circuit breakers.

A breaker watches one measurable condition and trips when it degrades. Tripped
breakers do not reset themselves, do not reset on a timer and do not reset
because the metric recovered: the operator decides whether the underlying
cause is understood.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from ..domain.types import ZERO, to_decimal


class BreakerCondition(enum.StrEnum):
    EXECUTION_FAILURE_RATE = "EXECUTION_FAILURE_RATE"
    ABNORMAL_SLIPPAGE = "ABNORMAL_SLIPPAGE"
    RPC_FAILURE = "RPC_FAILURE"
    API_FAILURE = "API_FAILURE"
    MARKET_DATA_INCONSISTENT = "MARKET_DATA_INCONSISTENT"
    ABNORMAL_LATENCY = "ABNORMAL_LATENCY"
    DRAWDOWN = "DRAWDOWN"
    INVENTORY_UNSAFE = "INVENTORY_UNSAFE"
    WALLET_MISMATCH = "WALLET_MISMATCH"
    UNEXPECTED_PNL = "UNEXPECTED_PNL"
    DAILY_LOSS = "DAILY_LOSS"


class BreakerState(enum.StrEnum):
    CLOSED = "CLOSED"  # normal operation
    OPEN = "OPEN"  # tripped; blocks new trading until manually reset


@dataclass(frozen=True, slots=True)
class TripRecord:
    condition: BreakerCondition
    observed: Decimal
    threshold: Decimal
    detail: str
    at: datetime


@dataclass(slots=True)
class CircuitBreaker:
    """One breaker over one condition.

    ``threshold`` is the value at which the condition is considered abnormal.
    ``higher_is_worse`` covers metrics such as failure rate; set it False for
    metrics where a falling value is the problem.
    """

    condition: BreakerCondition
    threshold: Decimal
    higher_is_worse: bool = True
    state: BreakerState = BreakerState.CLOSED
    trips: list[TripRecord] = field(default_factory=list)
    reset_by: str | None = None

    def __post_init__(self) -> None:
        self.threshold = to_decimal(self.threshold)

    def observe(
        self,
        value: Decimal | int | str,
        *,
        detail: str = "",
        now: datetime | None = None,
    ) -> bool:
        """Feed an observation. Returns True if the breaker is (now) open."""
        observed = to_decimal(value)
        breached = observed > self.threshold if self.higher_is_worse else observed < self.threshold
        if breached and self.state is BreakerState.CLOSED:
            self.trips.append(
                TripRecord(
                    condition=self.condition,
                    observed=observed,
                    threshold=self.threshold,
                    detail=detail,
                    at=now or datetime.now(UTC),
                )
            )
            self.state = BreakerState.OPEN
            self.reset_by = None
        return self.state is BreakerState.OPEN

    def trip(self, detail: str, *, now: datetime | None = None) -> None:
        """Trip explicitly, for conditions that are observed as events."""
        self.trips.append(
            TripRecord(
                condition=self.condition,
                observed=ZERO,
                threshold=self.threshold,
                detail=detail,
                at=now or datetime.now(UTC),
            )
        )
        self.state = BreakerState.OPEN
        self.reset_by = None

    def reset(self, operator: str) -> None:
        """Manual reset only. Requires a named operator; no automatic restart."""
        if not operator.strip():
            raise ValueError("resetting a circuit breaker requires an operator identity")
        self.state = BreakerState.CLOSED
        self.reset_by = operator

    @property
    def is_open(self) -> bool:
        return self.state is BreakerState.OPEN


@dataclass(slots=True)
class BreakerPanel:
    """All breakers. Trading is permitted only while every breaker is closed."""

    breakers: dict[BreakerCondition, CircuitBreaker] = field(default_factory=dict)

    def add(self, breaker: CircuitBreaker) -> None:
        if breaker.condition in self.breakers:
            raise ValueError(f"duplicate breaker for {breaker.condition}")
        self.breakers[breaker.condition] = breaker

    def observe(
        self,
        condition: BreakerCondition,
        value: Decimal | int | str,
        *,
        detail: str = "",
    ) -> bool:
        try:
            breaker = self.breakers[condition]
        except KeyError:
            # Fail closed: an unmonitored condition must not read as healthy.
            raise KeyError(f"no breaker configured for {condition}") from None
        return breaker.observe(value, detail=detail)

    @property
    def open_breakers(self) -> tuple[CircuitBreaker, ...]:
        return tuple(b for b in self.breakers.values() if b.is_open)

    @property
    def any_open(self) -> bool:
        return bool(self.open_breakers)
