from decimal import Decimal

import pytest

from arbcore.domain.types import StrategyKind
from arbcore.risk.budget import Budget, RiskCategory, StrategyBudget
from arbcore.strategy.state import StrategyState, StrategyStatus


def test_unallocated_category_is_not_an_unlimited_one():
    sb = StrategyBudget(StrategyKind.DEX_DEX)
    with pytest.raises(KeyError):
        sb.get(RiskCategory.SMART_CONTRACT)


def test_budget_exhaustion_is_detected():
    b = Budget(RiskCategory.EXECUTION, Decimal("10"))
    b.consume(Decimal("10"))
    assert b.exhausted
    assert b.remaining == Decimal("0")


def test_overshoot_is_recorded_not_clipped():
    """A post-mortem needs to see by how much the allowance was blown."""
    b = Budget(RiskCategory.MARKET, Decimal("10"))
    b.consume(Decimal("25"))
    assert b.remaining == Decimal("-15")


def test_release_never_drives_consumption_negative():
    b = Budget(RiskCategory.MARKET, Decimal("10"))
    b.consume(Decimal("2"))
    b.release(Decimal("5"))
    assert b.consumed == Decimal("0")


def test_negative_consumption_is_refused():
    b = Budget(RiskCategory.MARKET, Decimal("10"))
    with pytest.raises(ValueError):
        b.consume(Decimal("-1"))


def test_strategies_start_paused():
    s = StrategyStatus(StrategyKind.CEX_DEX)
    assert s.state is StrategyState.PAUSED
    assert not s.may_open_trades


def test_degraded_may_still_trade_but_paused_may_not():
    s = StrategyStatus(StrategyKind.CEX_DEX)
    s.set_state(StrategyState.DEGRADED, "slippage error rising", actor="monitor")
    assert s.may_open_trades
    s.set_state(StrategyState.PAUSED, "operator", actor="cedric")
    assert not s.may_open_trades


def test_disabled_strategy_cannot_be_revived_by_a_plain_state_change():
    s = StrategyStatus(StrategyKind.CROSS_CHAIN)
    s.disable("risk budget exhausted")
    with pytest.raises(RuntimeError):
        s.set_state(StrategyState.NORMAL, "looks fine now", actor="script")


def test_manual_enable_lands_in_paused_not_normal():
    s = StrategyStatus(StrategyKind.CROSS_CHAIN)
    s.disable("risk budget exhausted")
    s.enable("cedric", "budget re-approved after review")
    assert s.state is StrategyState.PAUSED


def test_state_changes_require_reason_and_actor():
    s = StrategyStatus(StrategyKind.CEX_CEX)
    with pytest.raises(ValueError):
        s.set_state(StrategyState.NORMAL, "", actor="x")
    with pytest.raises(ValueError):
        s.set_state(StrategyState.NORMAL, "ok", actor=" ")
