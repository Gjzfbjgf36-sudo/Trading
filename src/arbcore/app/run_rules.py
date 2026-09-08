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
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from ..backtest.rule_backtest import BacktestCosts, run_backtest
from ..decide.settings import DEFAULT_PATH, DecideSettings, SettingsError, load_settings
from ..marketdata.candles import BadCandleData, fetch_ohlcv, load_csv, write_csv
from ..strategy.rules import AVAILABLE
from ..strategy.watch import status


def _watch(args: argparse.Namespace, settings: DecideSettings) -> int:
    """Poll und zeige den Abstand zum Auslöser.

    Das Signal kommt immer aus abgeschlossenen Kerzen. Die laufende Kerze wird
    nur angezeigt — wer auf ihr handelt, handelt eine andere Regel als die
    getestete, und zwar eine, die häufiger und schlechter feuert.
    """
    rule = AVAILABLE[args.rule]
    duration = _bar_duration(args.timeframe)

    if args.csv:
        # Offline-Durchlauf: dieselbe Anzeige, ohne Netz. Gut zum Anschauen,
        # bevor man sie stundenlang laufen lässt.
        try:
            candles = load_csv(args.csv)
        except BadCandleData as exc:
            print(str(exc))
            return 1
        for end in range(max(61, len(candles) - 10), len(candles) + 1):
            state = status(
                rule, candles[:end], now=candles[end - 1].at, bar_duration=duration
            )
            print(state.render())
        return 0

    print(f"Beobachte {args.symbol} auf {args.exchange} mit {rule.name}.")
    print(f"Abfrage alle {args.interval}s. Beenden mit Strg+C.")
    print("Das Signal kommt aus geschlossenen Kerzen — die laufende wird nur angezeigt.\n")
    last_line = ""
    try:
        while True:
            try:
                candles = fetch_ohlcv(args.exchange, args.symbol, args.timeframe, 400)
            except BadCandleData as exc:
                print(f"Datenabruf fehlgeschlagen: {exc}")
                return 1
            state = status(
                rule, candles, now=datetime.now(UTC), bar_duration=duration
            )
            line = state.render()
            if state.fires:
                print(line)
                _alert_sound()
                return 0
            # Eine Zeile, die sich aktualisiert, statt endloser Ausgabe.
            if line != last_line:
                print(f"\r{line:<100}", end="", flush=True)
                last_line = line
            time.sleep(max(5, args.interval))
    except KeyboardInterrupt:
        print("\nBeendet.")
        return 0


def _bar_duration(timeframe: str) -> timedelta:
    units = {"m": "minutes", "h": "hours", "d": "days", "w": "weeks"}
    suffix = timeframe[-1:].lower()
    if suffix not in units or not timeframe[:-1].isdigit():
        return timedelta(days=1)
    return timedelta(**{units[suffix]: int(timeframe[:-1])})


def _alert_sound() -> None:
    """Terminal-Piepser. Damit man es hört, wenn man nicht hinschaut."""
    print("\a", end="", flush=True)


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

    watch = sub.add_parser(
        "watch", help="live mitschauen: wie weit ist die Regel vom Auslösen entfernt?"
    )
    watch.add_argument("--rule", choices=sorted(AVAILABLE), default="donchian")
    watch.add_argument("--exchange", default="kraken")
    watch.add_argument("--symbol", default="BTC/USD")
    watch.add_argument("--timeframe", default="1d")
    watch.add_argument(
        "--interval", type=int, default=60, help="Sekunden zwischen zwei Abfragen"
    )
    watch.add_argument("--csv", default=None, help="statt live: eine CSV durchspielen")

    args = parser.parse_args()

    try:
        settings = load_settings(args.config)
    except SettingsError as exc:
        print(f"Konfiguration: {exc}")
        return 2

    if args.command == "watch":
        return _watch(args, settings)

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
