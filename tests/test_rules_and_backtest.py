"""Rules over real candles, and a backtest that cannot flatter itself."""

import os
from pathlib import Path

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from arbcore.backtest.rule_backtest import BacktestCosts, BacktestError, run_backtest
from arbcore.marketdata.candles import (
    BadCandleData,
    Candle,
    load_csv,
    validate_series,
    write_csv,
)
from arbcore.strategy.rules import (
    AVAILABLE,
    DonchianBreakout,
    EngulfingInTrend,
    average_true_range,
    exponential_moving_average,
)

START = datetime(2024, 1, 1, tzinfo=UTC)


def candle(index: int, close: str, *, open_=None, high=None, low=None) -> Candle:
    c = Decimal(close)
    o = Decimal(open_) if open_ is not None else c
    return Candle(
        at=START + timedelta(days=index),
        open=o,
        high=Decimal(high) if high is not None else max(o, c),
        low=Decimal(low) if low is not None else min(o, c),
        close=c,
        volume=Decimal("1"),
    )


def rising(count: int, start: int = 100, step: int = 1) -> list[Candle]:
    return [candle(i, str(start + i * step)) for i in range(count)]


# --- candle validation ---------------------------------------------------


def test_a_high_below_the_low_is_refused():
    with pytest.raises(BadCandleData, match="below low"):
        Candle(
            at=START,
            open=Decimal("100"),
            high=Decimal("90"),
            low=Decimal("110"),
            close=Decimal("100"),
            volume=Decimal("1"),
        )


def test_a_close_outside_the_range_is_refused():
    with pytest.raises(BadCandleData, match="close"):
        Candle(
            at=START,
            open=Decimal("100"),
            high=Decimal("105"),
            low=Decimal("95"),
            close=Decimal("120"),
            volume=Decimal("1"),
        )


def test_out_of_order_candles_are_refused():
    """A rolling window over shuffled bars is wrong in a way nothing shows."""
    with pytest.raises(BadCandleData, match="increasing in time"):
        validate_series([candle(5, "100"), candle(1, "101")])


def test_duplicate_timestamps_are_refused():
    with pytest.raises(BadCandleData, match="increasing in time"):
        validate_series([candle(1, "100"), candle(1, "101")])


def test_csv_round_trip(tmp_path):
    path = tmp_path / "c.csv"
    original = rising(10)
    assert write_csv(path, original) == 10
    loaded = load_csv(path)
    assert len(loaded) == 10
    assert loaded[0].close == original[0].close


def test_a_missing_column_is_named(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("time,open,high,close\n1,1,1,1\n", encoding="utf-8")
    with pytest.raises(BadCandleData, match="low"):
        load_csv(path)


def test_a_missing_file_is_reported(tmp_path):
    with pytest.raises(BadCandleData, match="does not exist"):
        load_csv(tmp_path / "nope.csv")


# --- indicators ----------------------------------------------------------


def test_ema_is_seeded_with_a_simple_average():
    """Seeding from a single close leaves a bias that outlives a small dataset."""
    flat = [candle(i, "100") for i in range(50)]
    assert exponential_moving_average(flat, 20) == Decimal("100")


def test_ema_needs_enough_history():
    assert exponential_moving_average(rising(5), 20) == Decimal(0)


def test_atr_is_positive_on_a_moving_series():
    assert average_true_range(rising(30), 14) > Decimal(0)


# --- rules ---------------------------------------------------------------


def test_donchian_does_not_fire_without_enough_history():
    assert DonchianBreakout().evaluate(rising(10)) is None


def test_donchian_fires_on_a_new_high():
    rule = DonchianBreakout(entry_length=10, exit_length=5, atr_length=5)
    signal = rule.evaluate(rising(40))
    assert signal is not None and signal.fires
    assert signal.stop < signal.entry
    assert "Hoch" in signal.observed


def test_donchian_does_not_fire_inside_the_range():
    rule = DonchianBreakout(entry_length=10, exit_length=5, atr_length=5)
    series = rising(40) + [candle(40, "100")]  # drops back inside
    signal = rule.evaluate(series)
    assert signal is not None and not signal.fires


def test_the_breakout_level_excludes_the_current_bar():
    """Otherwise the rule compares a bar against itself and always fires."""
    rule = DonchianBreakout(entry_length=10, exit_length=5, atr_length=5)
    flat = [candle(i, "100") for i in range(40)]
    signal = rule.evaluate(flat)
    assert signal is None or not signal.fires


def test_donchian_exit_triggers_below_the_recent_low():
    rule = DonchianBreakout(entry_length=10, exit_length=5, atr_length=5)
    series = rising(40) + [candle(40, "50")]
    assert rule.exits(series)
    assert not rule.exits(rising(40))


def test_engulfing_needs_the_trend_filter():
    """A pattern without context is the weakest form of a rule."""
    rule = EngulfingInTrend(trend_length=10)
    # Falling series: pattern may be present, trend filter must veto it.
    falling = [candle(i, str(200 - i * 2)) for i in range(20)]
    falling += [
        candle(20, "150", open_="160", high="161", low="149"),
        candle(21, "162", open_="148", high="163", low="147"),
    ]
    signal = rule.evaluate(falling)
    assert signal is not None and not signal.fires


def test_engulfing_stop_comes_from_the_pattern():
    rule = EngulfingInTrend(trend_length=10)
    series = rising(20, start=100, step=3)
    series += [
        candle(20, "155", open_="162", high="163", low="154"),
        candle(21, "168", open_="153", high="169", low="152"),
    ]
    signal = rule.evaluate(series)
    assert signal is not None
    assert signal.stop == Decimal("152")  # the lower of the two pattern lows
    assert signal.target is not None and signal.target > signal.entry


def test_every_registered_rule_has_a_parameterised_name():
    for key, rule in AVAILABLE.items():
        assert key in rule.name


# --- backtest ------------------------------------------------------------


def costs(fee: str = "0.0026", slip: str = "0.0005") -> BacktestCosts:
    return BacktestCosts(fee_rate=Decimal(fee), slippage=Decimal(slip))


def test_implausible_costs_are_refused():
    with pytest.raises(BacktestError):
        BacktestCosts(fee_rate=Decimal("0.5"))


def test_costs_reduce_the_result():
    """The default that makes charting-platform backtests lie."""
    rule = DonchianBreakout(entry_length=10, exit_length=5, atr_length=5)
    series = rising(60, step=2) + [candle(60 + i, str(180 - i * 4)) for i in range(20)]
    free = run_backtest(
        rule, series, costs=costs("0", "0"), starting_capital=Decimal("1000")
    )
    real = run_backtest(rule, series, costs=costs(), starting_capital=Decimal("1000"))
    assert real.net < free.net
    assert real.fees_paid > Decimal(0)


def test_the_stop_is_assumed_before_the_target_within_one_bar():
    """OHLC cannot order them; assuming the target inflates every ambiguous case."""
    rule = EngulfingInTrend(trend_length=5, target_r=Decimal("2.0"))
    series = rising(10, start=100, step=3)
    series += [
        candle(10, "118", open_="124", high="125", low="117"),
        candle(11, "130", open_="116", high="131", low="115"),
        # A bar spanning both the stop (115) and the target.
        candle(12, "128", open_="129", high="200", low="100"),
    ]
    result = run_backtest(rule, series, costs=costs("0", "0"), starting_capital=Decimal("1000"))
    assert result.trades
    assert result.trades[0].reason == "STOP"


def test_a_second_signal_while_in_position_is_counted_not_taken():
    rule = DonchianBreakout(entry_length=5, exit_length=50, atr_length=5)
    series = rising(60, step=2)
    result = run_backtest(rule, series, costs=costs(), starting_capital=Decimal("1000"))
    assert result.signals > len(result.trades)
    assert result.skipped_while_in_position > 0


def test_position_size_follows_risk_not_capital():
    rule = DonchianBreakout(entry_length=10, exit_length=5, atr_length=5)
    series = rising(60, step=2) + [candle(60 + i, str(180 - i * 4)) for i in range(20)]
    small = run_backtest(
        rule, series, costs=costs(), starting_capital=Decimal("1000"),
        risk_per_trade=Decimal("0.005"),
    )
    large = run_backtest(
        rule, series, costs=costs(), starting_capital=Decimal("1000"),
        risk_per_trade=Decimal("0.02"),
    )
    if small.trades and large.trades:
        assert large.trades[0].quantity > small.trades[0].quantity


def test_a_small_sample_is_labelled_rather_than_reported_as_a_result():
    rule = DonchianBreakout(entry_length=10, exit_length=5, atr_length=5)
    series = rising(60, step=2) + [candle(60 + i, str(180 - i * 4)) for i in range(20)]
    result = run_backtest(rule, series, costs=costs(), starting_capital=Decimal("1000"))
    assert "ZU WENIG TRADES" in result.render(Decimal("1000"))


def test_a_backtest_over_shuffled_candles_is_refused():
    rule = DonchianBreakout()
    with pytest.raises(BadCandleData):
        run_backtest(
            rule,
            [candle(5, "100"), candle(1, "101")],
            costs=costs(),
            starting_capital=Decimal("1000"),
        )


def test_undefined_statistics_are_none_not_zero():
    rule = DonchianBreakout()
    result = run_backtest(
        rule, rising(80), costs=costs(), starting_capital=Decimal("1000")
    )
    if not result.trades:
        assert result.win_rate is None
        assert result.expectancy is None


# --- standalone script ---------------------------------------------------


def test_the_standalone_script_runs_without_the_package(tmp_path):
    """It exists for a machine that cannot install anything; it must not import us."""
    import json
    import subprocess
    import sys
    from datetime import UTC, datetime

    rows = []
    stamp = int(datetime(2023, 1, 1, tzinfo=UTC).timestamp())
    price = 100.0
    for i in range(300):
        price += 1 if (i // 40) % 3 != 2 else -1
        rows.append(
            [stamp + i * 86400, f"{price:.1f}", f"{price + 1:.1f}",
             f"{price - 1:.1f}", f"{price:.1f}", f"{price:.1f}", "1", 1]
        )
    payload = tmp_path / "ohlc.json"
    payload.write_text(json.dumps({"error": [], "result": {"XXBTZUSD": rows}}))

    script = Path(__file__).resolve().parents[1] / "standalone" / "backtest.py"
    proc = subprocess.run(
        [sys.executable, str(script), str(payload)],
        capture_output=True,
        text=True,
        cwd=tmp_path,  # nowhere near the package
        env={"PATH": os.environ.get("PATH", "")},  # no PYTHONPATH
    )
    assert proc.returncode == 0, proc.stderr
    assert "Donchian" in proc.stdout
    assert "Kosten" in proc.stdout


def test_the_standalone_script_reports_a_kraken_error(tmp_path):
    import json
    import subprocess
    import sys

    payload = tmp_path / "bad.json"
    payload.write_text(json.dumps({"error": ["EGeneral:Invalid arguments"], "result": {}}))
    script = Path(__file__).resolve().parents[1] / "standalone" / "backtest.py"
    proc = subprocess.run(
        [sys.executable, str(script), str(payload)], capture_output=True, text=True
    )
    assert proc.returncode != 0
    assert "Kraken meldet einen Fehler" in (proc.stdout + proc.stderr)
