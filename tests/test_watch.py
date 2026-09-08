"""Live watch: signal from closed bars, the running bar only displayed."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from arbcore.marketdata.candles import Candle
from arbcore.strategy.rules import DonchianBreakout, EngulfingInTrend
from arbcore.strategy.watch import status, trigger_level

START = datetime(2024, 1, 1, tzinfo=UTC)


def bar(index: int, price: str) -> Candle:
    p = Decimal(price)
    return Candle(
        at=START + timedelta(days=index),
        open=p,
        high=p * Decimal("1.01"),
        low=p * Decimal("0.99"),
        close=p,
        volume=Decimal("1"),
    )


def sideways(count: int = 60) -> list[Candle]:
    return [bar(i, str(100 + (i % 5))) for i in range(count)]


def rule() -> DonchianBreakout:
    return DonchianBreakout(entry_length=20, exit_length=10, atr_length=10)


def test_the_running_bar_is_shown_but_not_traded():
    """Acting on an unclosed bar is a different rule than the one tested."""
    series = sideways() + [bar(60, "500")]  # huge running bar
    state = status(rule(), series, now=START + timedelta(days=60))
    assert state.price == Decimal("500")  # displayed
    assert not state.fires  # but not acted on


def test_a_closed_breakout_fires():
    series = sideways() + [bar(60, "130"), bar(61, "135")]
    state = status(rule(), series, now=START + timedelta(days=61))
    assert state.fires
    assert state.signal is not None
    assert state.signal.stop < state.signal.entry
    assert "KAUFEN" in state.render()


def test_the_distance_to_the_trigger_is_reported():
    state = status(rule(), sideways(), now=START + timedelta(days=59))
    assert state.trigger_level is not None
    assert state.distance is not None
    assert "Auslöser" in state.render()


def test_being_above_the_trigger_is_reported_as_such():
    series = sideways() + [bar(60, "200")]
    state = status(rule(), series, now=START + timedelta(days=60))
    assert state.distance is not None and state.distance < Decimal(0)
    assert "darüber" in state.render()


def test_a_pattern_rule_has_no_single_trigger_price():
    """Inventing a level for a pattern would be inventing a number."""
    assert trigger_level(EngulfingInTrend(), sideways()) is None


def test_time_to_bar_close_is_shown_and_never_negative():
    series = sideways()
    late = START + timedelta(days=200)
    state = status(rule(), series, now=late)
    assert state.time_to_close == timedelta(0)


def test_too_little_history_says_how_much_is_missing():
    state = status(rule(), [bar(0, "100"), bar(1, "101")], now=START)
    assert state.warmup_missing > 0
    assert "Historie nötig" in state.render()


def test_an_empty_series_does_not_crash():
    state = status(rule(), [], now=START)
    assert not state.fires
    assert state.warmup_missing == 1
