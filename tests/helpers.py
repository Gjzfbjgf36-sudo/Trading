"""Test data builders shared by fixtures and individual tests."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from arbcore.risk.proposal import CostBreakdown, ExecutionProbabilities

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def complete_costs(**overrides: Decimal) -> CostBreakdown:
    """A fully estimated, cheap cost breakdown; override single lines per test."""
    base: dict[str, Decimal] = {f: Decimal("0") for f in CostBreakdown().unknown_lines()}
    base["cex_trading_fees"] = Decimal("0.40")
    base["expected_slippage_cost"] = Decimal("0.10")
    base["expected_execution_loss"] = Decimal("0.50")
    base["partial_fill_cost"] = Decimal("0.30")
    base.update(overrides)
    return CostBreakdown(**base)


def probabilities(**overrides: object) -> ExecutionProbabilities:
    base: dict[str, object] = dict(
        full_execution=Decimal("0.90"),
        partial_execution=Decimal("0.05"),
        failure=Decimal("0.03"),
        opportunity_decay=Decimal("0.01"),
        adverse_price_move=Decimal("0.01"),
        basis="unit-test fixture: synthetic values, not a measurement",
    )
    base.update(overrides)
    return ExecutionProbabilities(**base)  # type: ignore[arg-type]
