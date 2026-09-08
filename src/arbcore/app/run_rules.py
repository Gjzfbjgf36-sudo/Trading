"""CLI: eine Regel gegen echte Kerzen prüfen — ohne Charting-Dienst.

    # Kurse einmal herunterladen (braucht ccxt) und als CSV sichern
    python -m arbcore.app.run_rules fetch --exchange kraken --symbol BTC/USD --out data/btc.csv

    # Backtest mit deinen echten Kosten
    python -m arbcore.app.run_rules backtest --csv data/btc.csv --rule donchian

    # Feuert die Regel auf der letzten Kerze?
    python -m arbcore.app.run_rules signal --csv data/btc.csv --rule donchian
"""

from __future__ import annotations

import argparse
from decimal import Decimal

from ..backtest.rule_backtest import BacktestCosts, run_backtest
from ..decide.settings import DEFAULT_PATH, SettingsError, load_settings
from ..marketdata.candles import BadCandleData, fetch_ohlcv, load_csv, write_csv
from ..strategy.rules import AVAILABLE


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Regeln gegen echte Kerzen — kein Charting-Dienst nötig"
    )
    parser.add_argument("--config", default=DEFAULT_PATH)
    sub = parser.add_subparsers(dest="command", required=True)

    fetch = sub.add_parser("fetch", help="Kurse herunterladen und als CSV sichern")
    fetch.add_argument("--exchange", default="kraken")
    fetch.add_argument("--symbol", default="BTC/USD")
    fetch.add_argument("--timeframe", default="1d")
    fetch.add_argument("--limit", type=int, default=720)
    fetch.add_argument("--out", default="data/candles.csv")

    back = sub.add_parser("backtest", help="Regel über die Kerzen laufen lassen")
    back.add_argument("--csv", required=True)
    back.add_argument("--rule", choices=sorted(AVAILABLE), default="donchian")
    back.add_argument("--slippage", default="0.0005")
    back.add_argument("--time-stop", dest="time_stop", type=int, default=None)

    signal = sub.add_parser("signal", help="feuert die Regel auf der letzten Kerze?")
    signal.add_argument("--csv", required=True)
    signal.add_argument("--rule", choices=sorted(AVAILABLE), default="donchian")

    args = parser.parse_args()

    try:
        settings = load_settings(args.config)
    except SettingsError as exc:
        print(f"Konfiguration: {exc}")
        return 2

    if args.command == "fetch":
        try:
            candles = fetch_ohlcv(args.exchange, args.symbol, args.timeframe, args.limit)
        except BadCandleData as exc:
            print(str(exc))
            return 1
        written = write_csv(args.out, candles)
        print(f"{written} Kerzen von {args.exchange} nach {args.out} gesichert.")
        print(f"Zeitraum: {candles[0].at.date()} bis {candles[-1].at.date()}")
        print("Ab jetzt offline nutzbar — der Download muss nicht wiederholt werden.")
        return 0

    try:
        candles = load_csv(args.csv)
    except BadCandleData as exc:
        print(str(exc))
        return 1
    rule = AVAILABLE[args.rule]

    if args.command == "signal":
        result = rule.evaluate(candles)
        if result is None:
            print(f"Zu wenig Historie für {rule.name} ({len(candles)} Kerzen).")
            return 1
        print(f"Regel      {result.source}")
        print(f"Gesehen    {result.observed}")
        print(f"Feuert     {'JA' if result.fires else 'nein'}")
        if result.fires:
            print(f"  Einstieg {result.entry}")
            print(f"  Stop     {result.stop}")
            print(f"  Ziel     {result.target or 'keins (Ausstieg per Regel)'}")
            print("\nJetzt `run_gate check` — das Gate entscheidet über die Größe.")
        return 0 if result.fires else 1

    costs = BacktestCosts(fee_rate=settings.fee_rate, slippage=Decimal(args.slippage))
    outcome = run_backtest(
        rule,
        candles,
        costs=costs,
        starting_capital=settings.starting_capital,
        risk_per_trade=settings.risk.risk_per_trade,
        time_stop_bars=args.time_stop,
    )
    fee_pct = (costs.fee_rate * Decimal(100)).quantize(Decimal("0.001"))
    slip_pct = (costs.slippage * Decimal(100)).quantize(Decimal("0.001"))
    print(f"Kosten: {fee_pct} % Gebühr, {slip_pct} % Slippage pro Seite\n")
    print(outcome.render(settings.starting_capital))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
