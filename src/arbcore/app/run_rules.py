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
from pathlib import Path

from ..backtest.portfolio import run_portfolio
from ..backtest.robustness import sweep
from ..backtest.rule_backtest import BacktestCosts, run_backtest
from ..decide.settings import DEFAULT_PATH, DecideSettings, SettingsError, load_settings
from ..marketdata.candles import (
    BadCandleData,
    fetch_ohlcv,
    list_symbols,
    load_csv,
    load_kraken_json,
    symbol_to_filename,
    write_csv,
)
from ..strategy.rules import AVAILABLE, DonchianBreakout
from ..strategy.watch import render_scan, scan, status


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

    many = sub.add_parser(
        "fetch-many",
        help="alle Paare einer Börse herunterladen (braucht ccxt und Netz)",
    )
    many.add_argument("--exchange", default="kraken")
    many.add_argument("--quote", default="USD", help="nur Paare gegen diese Währung")
    many.add_argument("--timeframe", default="1d")
    many.add_argument("--limit", type=int, default=720)
    many.add_argument("--out-dir", default="records/markets")
    many.add_argument(
        "--max", type=int, default=None, help="höchstens so viele Paare laden"
    )

    kraken = sub.add_parser(
        "import-kraken",
        help="im Browser gespeicherte Kraken-Antwort in eine CSV umwandeln",
    )
    kraken.add_argument("--json", required=True, dest="json_path")
    kraken.add_argument("--out", required=True)

    back = sub.add_parser("backtest", help="Regel über die Kerzen laufen lassen")
    back.add_argument("--csv", required=True)
    back.add_argument("--rule", choices=sorted(AVAILABLE), default="donchian")
    back.add_argument("--slippage", default="0.0005")
    back.add_argument(
        "--time-stop",
        dest="time_stop",
        type=int,
        default=None,
        help="überschreibt time_stop_bars aus der Konfiguration",
    )

    signal = sub.add_parser("signal", help="feuert die Regel auf der letzten Kerze?")
    signal.add_argument("--csv", required=True)
    signal.add_argument("--rule", choices=sorted(AVAILABLE), default="donchian")

    robust = sub.add_parser(
        "robustness",
        help="Plateau oder Zufallsspitze? Prüft, ob dem Ergebnis zu glauben ist",
    )
    robust.add_argument("--csv", required=True)
    robust.add_argument("--slippage", default="0.0005")

    portfolio = sub.add_parser(
        "portfolio", help="dieselbe Regel über mehrere Märkte (Streuung)"
    )
    portfolio.add_argument(
        "--csv", required=True, nargs="+", help="mehrere CSV-Dateien, eine je Markt"
    )
    portfolio.add_argument("--rule", choices=sorted(AVAILABLE), default="donchian")
    portfolio.add_argument("--slippage", default="0.0005")

    scan_cmd = sub.add_parser(
        "scan", help="dieselbe Regel über viele Märkte: welcher feuert gerade?"
    )
    scan_cmd.add_argument(
        "--csv", required=True, nargs="+", help="mehrere CSV-Dateien, eine je Markt"
    )
    scan_cmd.add_argument("--rule", choices=sorted(AVAILABLE), default="donchian")

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

    if args.command == "robustness":
        try:
            candles = load_csv(args.csv)
        except BadCandleData as exc:
            print(str(exc))
            return 1
        grid = [
            (entry, exit_)
            for entry in (30, 40, 55, 70, 90)
            for exit_ in (10, 15, 20, 25, 30)
        ]
        report = sweep(
            lambda e, x: DonchianBreakout(entry_length=e, exit_length=x),
            grid,
            candles,
            costs=BacktestCosts(
                fee_rate=settings.fee_rate, slippage=Decimal(args.slippage)
            ),
            starting_capital=settings.starting_capital,
            risk_per_trade=settings.risk.risk_per_trade,
        )
        print(report.render())
        return 0

    if args.command == "portfolio":
        markets = {}
        for path in args.csv:
            try:
                markets[Path(path).stem] = load_csv(path)
            except BadCandleData as exc:
                print(str(exc))
                return 1
        portfolio_result = run_portfolio(
            AVAILABLE[args.rule],
            markets,
            costs=BacktestCosts(
                fee_rate=settings.fee_rate, slippage=Decimal(args.slippage)
            ),
            capital_per_market=settings.starting_capital / Decimal(len(markets)),
            risk_per_trade=settings.risk.risk_per_trade,
            time_stop_bars=settings.time_stop_bars,
        )
        print(portfolio_result.render())
        return 0

    if args.command == "scan":
        scanned = {}
        for path in args.csv:
            try:
                scanned[Path(path).stem] = load_csv(path)
            except BadCandleData as exc:
                print(str(exc))
                return 1
        rows = scan(AVAILABLE[args.rule], scanned, now=datetime.now(tz=UTC))
        print(f"Regel: {AVAILABLE[args.rule].name}")
        print(render_scan(rows))
        return 0

    if args.command == "watch":
        return _watch(args, settings)

    if args.command == "fetch-many":
        try:
            symbols = list_symbols(args.exchange, args.quote)
        except BadCandleData as exc:
            print(str(exc))
            return 1
        if args.max is not None:
            symbols = symbols[: args.max]
        if not symbols:
            print(f"Keine aktiven {args.quote}-Paare auf {args.exchange} gefunden.")
            return 1
        folder = Path(args.out_dir)
        folder.mkdir(parents=True, exist_ok=True)
        print(f"{len(symbols)} Paare auf {args.exchange} gegen {args.quote}.")
        print("Das dauert: ccxt hält das Ratenlimit der Börse ein.\n")
        written = 0
        failed: list[str] = []
        for number, symbol in enumerate(symbols, start=1):
            name = symbol_to_filename(symbol, args.timeframe)
            try:
                candles = fetch_ohlcv(
                    args.exchange, symbol, args.timeframe, args.limit
                )
            except Exception as exc:  # noqa: BLE001
                # Ein einzelnes totes Paar darf einen Lauf über hunderte
                # Märkte nicht abbrechen — es wird genannt und übersprungen.
                failed.append(f"{symbol}: {type(exc).__name__}: {exc}")
                continue
            write_csv(folder / name, candles)
            written += 1
            print(f"  [{number}/{len(symbols)}] {symbol}: {len(candles)} Kerzen")
        print(f"\n{written} Märkte in {folder} geschrieben.")
        if failed:
            print(f"{len(failed)} nicht geladen:")
            for problem in failed[:20]:
                print(f"  {problem}")
            if len(failed) > 20:
                print(f"  ... und {len(failed) - 20} weitere")
        print(
            "\nWICHTIG: Viele Märkte gleichzeitig zu scannen ist NICHT dasselbe "
            "wie\nmehr Beobachtungen derselben Messung. Siehe "
            "docs/VIELE_MAERKTE.md."
        )
        return 0

    if args.command == "import-kraken":
        try:
            candles = load_kraken_json(args.json_path)
        except BadCandleData as exc:
            print(str(exc))
            return 1
        written = write_csv(args.out, candles)
        print(f"{written} abgeschlossene Kerzen nach {args.out} geschrieben.")
        print(f"Zeitraum: {candles[0].at.date()} bis {candles[-1].at.date()}")
        print("Eine noch laufende Kerze wurde verworfen, falls Kraken eine mitgeliefert hat.")
        return 0

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
        current = rule.evaluate(candles)
        if current is None:
            print(f"Zu wenig Historie für {rule.name} ({len(candles)} Kerzen).")
            return 1
        print(f"Regel      {current.source}")
        print(f"Gesehen    {current.observed}")
        print(f"Feuert     {'JA' if current.fires else 'nein'}")
        if current.fires:
            print(f"  Einstieg {current.entry}")
            print(f"  Stop     {current.stop}")
            print(f"  Ziel     {current.target or 'keins (Ausstieg per Regel)'}")
            if settings.time_stop_bars is not None:
                # Der Zeit-Stop ist keine Empfehlung, sondern Teil der Regel, die
                # gemessen wurde. Wer ihn im Eifer weglässt, handelt die Variante,
                # die in beiden Zeithälften verloren hat.
                latest = candles[-1].at + _bar_duration(
                    "1d"
                ) * settings.time_stop_bars
                print(
                    f"  Zeit-Stop nach {settings.time_stop_bars} Kerzen: "
                    f"spätestens {latest.date()} schliessen, egal wo der Kurs steht"
                )
            print("\nJetzt `run_gate check` — das Gate entscheidet über die Größe.")
        return 0 if current.fires else 1

    costs = BacktestCosts(fee_rate=settings.fee_rate, slippage=Decimal(args.slippage))
    backtest_result = run_backtest(
        rule,
        candles,
        costs=costs,
        starting_capital=settings.starting_capital,
        risk_per_trade=settings.risk.risk_per_trade,
        time_stop_bars=(
            args.time_stop if args.time_stop is not None else settings.time_stop_bars
        ),
    )
    fee_pct = (costs.fee_rate * Decimal(100)).quantize(Decimal("0.001"))
    slip_pct = (costs.slippage * Decimal(100)).quantize(Decimal("0.001"))
    print(f"Kosten: {fee_pct} % Gebühr, {slip_pct} % Slippage pro Seite\n")
    print(backtest_result.render(settings.starting_capital))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
