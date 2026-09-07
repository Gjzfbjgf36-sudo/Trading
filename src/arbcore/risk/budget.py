"""Risk budgets.

Risk is not one number. A strategy can be well inside its market-risk budget
while having burned all of its execution-risk budget on failed transactions.
Budgets are consumed in the accounting currency and are never refilled
automatically — a refill is a governed decision (docs/model-governance.md).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from decimal import Decimal

from ..domain.types import ZERO, StrategyKind, to_decimal


class RiskCategory(enum.StrEnum):
    MARKET = "MARKET"
    EXECUTION = "EXECUTION"
    LIQUIDITY = "LIQUIDITY"
    TECHNOLOGY = "TECHNOLOGY"
    COUNTERPARTY = "COUNTERPARTY"
    SMART_CONTRACT = "SMART_CONTRACT"
    SETTLEMENT = "SETTLEMENT"


@dataclass(slots=True)
class Budget:
    """One category's allowance for one strategy."""

    category: RiskCategory
    allocated: Decimal
    consumed: Decimal = ZERO

    def __post_init__(self) -> None:
        self.allocated = to_decimal(self.allocated)
        self.consumed = to_decimal(self.consumed)
        if self.allocated < ZERO:
            raise ValueError(f"{self.category}: allocated budget must be >= 0")

    @property
    def remaining(self) -> Decimal:
        return self.allocated - self.consumed

    @property
    def exhausted(self) -> bool:
        return self.remaining <= ZERO

    def consume(self, amount: Decimal | int | str) -> Decimal:
        """Record realised consumption. Over-consumption is recorded, not clipped.

        Clipping would hide the fact that a loss exceeded its allowance; the
        overshoot is exactly what a post-mortem needs to see.
        """
        value = to_decimal(amount)
        if value < ZERO:
            raise ValueError("budget consumption must be >= 0; use release() to give back")
        self.consumed += value
        return self.remaining

    def release(self, amount: Decimal | int | str) -> Decimal:
        """Return unused reservation (never below zero consumption)."""
        value = to_decimal(amount)
        if value < ZERO:
            raise ValueError("released amount must be >= 0")
        self.consumed = max(ZERO, self.consumed - value)
        return self.remaining


@dataclass(slots=True)
class StrategyBudget:
    """The full budget set for one strategy module."""

    strategy: StrategyKind
    budgets: dict[RiskCategory, Budget] = field(default_factory=dict)

    def allocate(self, category: RiskCategory, amount: Decimal | int | str) -> Budget:
        if category in self.budgets:
            raise ValueError(f"{self.strategy}: {category} budget already allocated")
        budget = Budget(category=category, allocated=to_decimal(amount))
        self.budgets[category] = budget
        return budget

    def get(self, category: RiskCategory) -> Budget:
        try:
            return self.budgets[category]
        except KeyError:
            # Fail closed: an unallocated category is a zero budget, and a zero
            # budget is exhausted.
            raise KeyError(
                f"{self.strategy}: no {category} budget allocated; strategy may not run"
            ) from None

    @property
    def exhausted_categories(self) -> tuple[RiskCategory, ...]:
        return tuple(c for c, b in self.budgets.items() if b.exhausted)

    @property
    def any_exhausted(self) -> bool:
        return bool(self.exhausted_categories)
