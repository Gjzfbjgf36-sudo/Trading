from decimal import Decimal

import pytest

from arbcore.safety.circuit_breaker import (
    BreakerCondition,
    BreakerPanel,
    BreakerState,
    CircuitBreaker,
)
from arbcore.safety.safe_mode import SafeMode, SafeModeError, SafeModeTrigger


def test_process_starts_in_safe_mode():
    """A fresh process has not reconciled anything yet."""
    sm = SafeMode()
    assert sm.active
    with pytest.raises(SafeModeError):
        sm.assert_may_take_new_risk()


def test_safe_mode_clears_only_with_a_named_operator():
    sm = SafeMode()
    with pytest.raises(ValueError):
        sm.clear("")
    sm.clear("cedric")
    sm.assert_may_take_new_risk()
    assert sm.cleared_by == "cedric"


def test_engaging_records_the_trigger_and_relatches():
    sm = SafeMode()
    sm.clear("op")
    sm.engage(SafeModeTrigger.WALLET_MISMATCH, "hot wallet balance differs from ledger")
    assert sm.active
    assert sm.cleared_by is None
    assert sm.last_event.trigger is SafeModeTrigger.WALLET_MISMATCH
    with pytest.raises(SafeModeError, match="WALLET_MISMATCH"):
        sm.assert_may_take_new_risk()


def test_breaker_trips_on_breach_and_does_not_self_heal():
    cb = CircuitBreaker(BreakerCondition.EXECUTION_FAILURE_RATE, Decimal("0.10"))
    assert not cb.observe(Decimal("0.05"))
    assert cb.observe(Decimal("0.30"))
    # Metric recovers; the breaker stays open until a human resets it.
    assert cb.observe(Decimal("0.00"))
    assert cb.state is BreakerState.OPEN
    cb.reset("cedric")
    assert cb.state is BreakerState.CLOSED


def test_breaker_reset_requires_an_operator():
    cb = CircuitBreaker(BreakerCondition.DRAWDOWN, Decimal("100"))
    cb.trip("manual")
    with pytest.raises(ValueError):
        cb.reset("  ")


def test_lower_is_worse_direction():
    cb = CircuitBreaker(
        BreakerCondition.MARKET_DATA_INCONSISTENT, Decimal("0.9"), higher_is_worse=False
    )
    assert not cb.observe(Decimal("0.95"))
    assert cb.observe(Decimal("0.50"))


def test_first_trip_is_recorded_once_with_the_breaching_value():
    cb = CircuitBreaker(BreakerCondition.DRAWDOWN, Decimal("100"))
    cb.observe(Decimal("150"), detail="drawdown")
    cb.observe(Decimal("200"), detail="worse")
    assert len(cb.trips) == 1
    assert cb.trips[0].observed == Decimal("150")


def test_unmonitored_condition_is_an_error_not_a_pass():
    panel = BreakerPanel()
    with pytest.raises(KeyError):
        panel.observe(BreakerCondition.RPC_FAILURE, Decimal("1"))


def test_panel_reports_open_breakers():
    panel = BreakerPanel()
    panel.add(CircuitBreaker(BreakerCondition.API_FAILURE, Decimal("0.1")))
    assert not panel.any_open
    panel.observe(BreakerCondition.API_FAILURE, Decimal("0.9"))
    assert panel.any_open
    assert panel.open_breakers[0].condition is BreakerCondition.API_FAILURE
