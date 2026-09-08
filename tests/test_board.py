"""One screen: what the system says, and what it does not."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from arbcore.decide.board import build_board, summarise_for_day
from arbcore.decide.gate import AccountState
from arbcore.marketdata.candles import Candle
from arbcore.strategy.rules import DonchianBreakout

START = datetime(2024, 1, 1, tzinfo=UTC)


def bar(index: int, price: str) -> Candle:
    p = Decimal(price)
    return Candle(
        at=START + timedelta(days=index),
        open=p,
        high=p,
        low=p * Decimal("0.99"),
        close=p,
        volume=Decimal("1"),
    )


def quiet(count: int = 60) -> list[Candle]:
    return [bar(i, str(100 + (i % 5))) for i in range(count)]


def breaking_out() -> list[Candle]:
    return quiet() + [bar(60, "500"), bar(61, "500")]


def rule() -> DonchianBreakout:
    return DonchianBreakout(entry_length=20, exit_length=10, atr_length=10)


def account(**kwargs: object) -> AccountState:
    defaults = {
        "equity": Decimal("1000"),
        "peak_equity": Decimal("1000"),
        "pnl_today": Decimal("0"),
        "open_positions": 0,
    }
    defaults.update(kwargs)
    return AccountState(**defaults)  # type: ignore[arg-type]


def position(ref: str = "T-1", *, opened_days_ago: int, symbol: str = "BTCUSD"):
    return {
        "ref": ref,
        "symbol": symbol,
        "entry": "100",
        "stop": "95",
        "target": "110",
        "opened_at": (START + timedelta(days=61 - opened_days_ago)).isoformat(),
    }


def board(**kwargs: object):
    defaults = {
        "rule": rule(),
        "markets": {"btc": quiet() + [bar(60, "104"), bar(61, "104")]},
        "account": account(),
        "open_positions": (),
        "starting_capital": Decimal("1000"),
        "max_open_positions": 3,
        "time_stop_bars": 12,
        "now": START + timedelta(days=61),
    }
    defaults.update(kwargs)
    return build_board(**defaults)  # type: ignore[arg-type]


def test_nothing_to_do_is_stated_as_the_normal_case():
    screen = board()
    assert not screen.firing
    rendered = screen.render()
    assert "NICHTS ZU TUN" in rendered
    assert "Normalfall" in rendered


def test_a_firing_market_is_named_and_points_at_the_gate():
    screen = board(markets={"btc": breaking_out()})
    assert [row.market for row in screen.firing] == ["btc"]
    rendered = screen.render()
    assert "SIGNAL" in rendered
    assert "run_gate check" in rendered


def test_a_position_past_its_time_stop_outranks_a_fresh_signal():
    """Closing an overdue position comes before opening anything new."""
    screen = board(
        markets={"btc": breaking_out()},
        open_positions=(position(opened_days_ago=20),),
    )
    assert screen.firing  # the signal is there
    assert screen.overdue  # but so is the deadline
    rendered = screen.render()
    assert "HEUTE SCHLIESSEN" in rendered
    # The buy instruction is not merely lower down the screen, it is absent:
    # one thing to do at a time, and this one is the deadline.
    assert "run_gate check" not in rendered
    assert "SIGNAL" not in rendered.split("-" * 68)[-2]


def test_stale_data_outranks_everything_including_an_overdue_close():
    """A signal computed from old candles is not a signal."""
    old = [bar(i, str(100 + (i % 5))) for i in range(40)]
    screen = build_board(
        rule=rule(),
        markets={"btc": old},
        account=account(),
        open_positions=(position(opened_days_ago=30),),
        starting_capital=Decimal("1000"),
        max_open_positions=3,
        time_stop_bars=12,
        now=START + timedelta(days=61),
    )
    rendered = screen.render()
    assert "DATEN VERALTET" in rendered
    assert "HEUTE SCHLIESSEN" not in rendered.split("DATEN VERALTET")[1][:200]


def test_one_bar_of_lag_is_not_stale():
    """The running candle has not closed yet; that is normal, not an error."""
    screen = board(now=START + timedelta(days=62))
    assert screen.stale_markets == ()


def test_a_full_book_reports_the_signal_but_refuses_to_act():
    screen = board(
        markets={"btc": breaking_out()},
        open_positions=(
            position("A", opened_days_ago=1),
            position("B", opened_days_ago=1),
            position("C", opened_days_ago=1),
        ),
        max_open_positions=3,
    )
    assert screen.room_for_more == 0
    rendered = screen.render()
    assert "KEIN PLATZ" in rendered
    assert "run_gate check" not in rendered


def test_the_countdown_counts_down():
    screen = board(open_positions=(position(opened_days_ago=5),))
    line = screen.open_lines[0]
    assert line.bars_held == 5
    assert line.bars_left == 7
    assert not line.overdue
    assert "Zeit-Stop in 7 Kerzen" in line.render()


def test_an_unreadable_open_date_gives_no_countdown_rather_than_zero():
    """"Held for zero bars" would be a claim, and the convenient one."""
    row = dict(position(opened_days_ago=5))
    row["opened_at"] = "irgendwann"
    screen = board(open_positions=(row,))
    line = screen.open_lines[0]
    assert line.bars_held is None
    assert line.bars_left is None
    assert not line.overdue


def test_without_a_configured_time_stop_no_position_is_ever_overdue():
    screen = board(time_stop_bars=None, open_positions=(position(opened_days_ago=99),))
    assert not screen.overdue
    assert "nicht konfiguriert" in screen.open_lines[0].render()


def test_the_one_line_summary_matches_the_screen():
    today = (START + timedelta(days=61)).date()
    assert "kein Signal" in summarise_for_day(board(), today=today)
    assert "Signal in btc" in summarise_for_day(
        board(markets={"btc": breaking_out()}), today=today
    )
    assert "faellig" in summarise_for_day(
        board(open_positions=(position(opened_days_ago=20),)), today=today
    )
