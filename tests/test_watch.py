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


# --- scanner over many markets -------------------------------------------


def test_the_scanner_puts_a_firing_market_first():
    """A market that fires outranks every distance, however small."""
    from arbcore.strategy.watch import scan

    breaking_out = sideways() + [bar(60, "500"), bar(61, "500")]
    near_miss = sideways() + [bar(60, "104"), bar(61, "104")]
    rows = scan(
        rule(),
        {"near": near_miss, "firing": breaking_out},
        now=START + timedelta(days=61),
    )
    assert [row.market for row in rows] == ["firing", "near"]
    assert rows[0].status.fires
    assert not rows[1].status.fires


def test_the_scanner_sorts_the_waiting_markets_by_distance():
    from arbcore.strategy.watch import scan

    close = sideways() + [bar(60, "104"), bar(61, "104")]
    far = sideways() + [bar(60, "50"), bar(61, "50")]
    rows = scan(rule(), {"far": far, "close": close}, now=START + timedelta(days=61))
    assert [row.market for row in rows] == ["close", "far"]


def test_a_market_without_a_trigger_level_sorts_last_not_first():
    """Unknown distance is not a small distance."""
    from arbcore.strategy.watch import scan

    rows = scan(
        rule(),
        {"thin": [bar(0, "100")], "known": sideways() + [bar(60, "104")]},
        now=START + timedelta(days=60),
    )
    assert [row.market for row in rows] == ["known", "thin"]


def test_the_scanner_says_plainly_when_nothing_fires():
    from arbcore.strategy.watch import render_scan, scan

    rows = scan(rule(), {"a": sideways()}, now=START + timedelta(days=60))
    rendered = render_scan(rows)
    assert "Kein Markt feuert" in rendered
    assert "KAUFEN" not in rendered


def test_the_scanner_names_the_markets_that_fire():
    from arbcore.strategy.watch import render_scan, scan

    breaking_out = sideways() + [bar(60, "500"), bar(61, "500")]
    rendered = render_scan(
        scan(rule(), {"btc": breaking_out}, now=START + timedelta(days=61))
    )
    assert "btc" in rendered
    assert "KAUFEN" in rendered


def test_an_empty_scan_does_not_crash():
    from arbcore.strategy.watch import render_scan, scan

    assert "Keine Märkte" in render_scan(scan(rule(), {}, now=START))
