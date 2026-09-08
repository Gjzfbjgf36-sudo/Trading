"""Robustness and diversification: the two improvements that are not fitting."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from arbcore.backtest.portfolio import run_portfolio
from arbcore.backtest.robustness import sweep
from arbcore.backtest.rule_backtest import BacktestCosts
from arbcore.marketdata.candles import Candle
from arbcore.strategy.rules import DonchianBreakout

START = datetime(2024, 1, 1, tzinfo=UTC)
COSTS = BacktestCosts(fee_rate=Decimal("0.0026"), slippage=Decimal("0.0005"))
GRID = [(e, x) for e in (10, 15, 20) for x in (5, 8, 12)]


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


def trending(count: int = 200, step: int = 2, start: int = 100) -> list[Candle]:
    """A series where a trend rule should do something, so a sweep has data."""
    out = []
    price = start
    for i in range(count):
        price += step if (i // 30) % 3 != 2 else -step  # trends with pullbacks
        out.append(bar(i, str(max(10, price))))
    return out


def build(entry: int, exit_: int) -> DonchianBreakout:
    return DonchianBreakout(entry_length=entry, exit_length=exit_, atr_length=10)


def a_sweep(candles=None):
    return sweep(
        build,
        GRID,
        candles or trending(),
        costs=COSTS,
        starting_capital=Decimal("1000"),
    )


# --- robustness ----------------------------------------------------------


def test_the_sweep_covers_the_whole_grid_without_searching():
    """Searching is what creates the bias this module exists to expose."""
    report = a_sweep()
    assert len(report.points) == len(GRID)


def test_neighbours_are_found_by_grid_rank_not_numeric_distance():
    report = a_sweep()
    assert report.best is not None
    # A point in the interior of a 3x3 grid has 8 neighbours; a corner has 3.
    assert 3 <= report.neighbours_total <= 8


def test_a_plateau_needs_both_a_broad_majority_and_stable_neighbours():
    report = a_sweep()
    if report.looks_like_a_plateau:
        assert report.positive_share >= Decimal("0.6")
        assert report.neighbours_positive * 10 >= report.neighbours_total * 6


def test_the_report_warns_about_small_samples():
    """Below 30 trades the ranking is noise, whatever the ordering shows."""
    report = a_sweep()
    if any(p.trades < 30 for p in report.points):
        text = report.render()
        assert "unter 30 Trades" in text
        assert "Zufall" in text


def test_the_report_never_simply_recommends_the_best_value():
    report = a_sweep()
    text = report.render()
    assert "PLATEAU" in text or "KEIN PLATEAU" in text
    if "KEIN PLATEAU" in text:
        assert "nicht glauben" in text


def test_an_empty_grid_produces_no_verdict():
    report = sweep(build, [], trending(), costs=COSTS, starting_capital=Decimal("1000"))
    assert report.best is None
    assert not report.looks_like_a_plateau
    assert "Keine auswertbaren" in report.render()


# --- portfolio -----------------------------------------------------------


def test_markets_are_run_independently_and_summed():
    markets = {"A": trending(), "B": trending(step=3)}
    result = run_portfolio(
        DonchianBreakout(entry_length=15, exit_length=8, atr_length=10),
        markets,
        costs=COSTS,
        capital_per_market=Decimal("500"),
    )
    assert {m.symbol for m in result.per_market} == {"A", "B"}
    assert result.total_trades == sum(m.trades for m in result.per_market)


def test_identical_markets_show_no_diversification_benefit():
    """Two copies of the same series lose on exactly the same days."""
    series = trending()
    result = run_portfolio(
        DonchianBreakout(entry_length=15, exit_length=8, atr_length=10),
        {"A": series, "B": list(series)},
        costs=COSTS,
        capital_per_market=Decimal("500"),
    )
    benefit = result.diversification_benefit
    if benefit is not None:
        assert benefit < Decimal("0.05")
        assert "KAUM STREUUNG" in result.render()


def test_the_combined_drawdown_is_chronological_not_a_sum():
    """Summing independent curves would report a drawdown nobody experienced."""
    result = run_portfolio(
        DonchianBreakout(entry_length=15, exit_length=8, atr_length=10),
        {"A": trending(), "B": trending(step=3, start=200)},
        costs=COSTS,
        capital_per_market=Decimal("500"),
    )
    assert result.combined_drawdown <= result.summed_drawdowns


def test_overlap_is_reported_so_concentration_is_visible():
    result = run_portfolio(
        DonchianBreakout(entry_length=15, exit_length=8, atr_length=10),
        {"A": trending(), "B": trending(step=3)},
        costs=COSTS,
        capital_per_market=Decimal("500"),
    )
    assert Decimal(0) <= result.overlap_share <= Decimal(1)
    assert "offenen Positionen" in result.render()


def test_a_market_with_no_trades_is_still_listed():
    flat = [bar(i, "100") for i in range(120)]
    result = run_portfolio(
        DonchianBreakout(entry_length=15, exit_length=8, atr_length=10),
        {"flat": flat, "trend": trending()},
        costs=COSTS,
        capital_per_market=Decimal("500"),
    )
    assert any(m.symbol == "flat" for m in result.per_market)
