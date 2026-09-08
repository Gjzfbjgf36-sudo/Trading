#!/usr/bin/env python3
"""Donchian-Backtest ohne Installation. Nur Python, keine Pakete.

Für den Fall, dass das Terminal nicht ins Netz kommt: Kursdaten im Browser
holen, hier hineinreichen, fertig. Kein git, kein pip, kein venv.

    python3 backtest.py ohlc.json
    python3 backtest.py kurse.csv --fee 0.0026

Die Rechnung ist dieselbe wie im grossen Programm, mit denselben drei
Entscheidungen gegen uns:

  * Kein Look-ahead — ein Signal sieht nur Kerzen bis einschliesslich sich
    selbst.
  * Stop vor Ziel innerhalb einer Kerze — deckt eine Kerze beides ab, kann man
    aus Hoch/Tief nicht sagen, was zuerst kam. Das Ziel anzunehmen würde genau
    die mehrdeutigen Fälle schönrechnen.
  * Gebühren auf beiden Seiten, plus Slippage.

Unter 30 Trades wird kein Urteil ausgegeben, sondern gesagt, dass die Zahlen
im Bereich des Zufalls liegen.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

ZERO = Decimal(0)
QUANT = Decimal("0.01")


@dataclass(frozen=True)
class Candle:
    at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal


def load(path: Path) -> list[Candle]:
    """Kraken-JSON oder CSV. Das Format wird am Inhalt erkannt, nicht am Namen."""
    text = path.read_text(encoding="utf-8").strip()
    if text.startswith("{"):
        return _load_kraken(text)
    return _load_csv(path)


def _load_kraken(text: str) -> list[Candle]:
    payload = json.loads(text)
    if payload.get("error"):
        raise SystemExit(f"Kraken meldet einen Fehler: {payload['error']}")
    result = payload.get("result") or {}
    series = [v for k, v in result.items() if k != "last" and isinstance(v, list)]
    if not series:
        raise SystemExit("Keine Kerzen im JSON gefunden.")
    # Kraken: [zeit, open, high, low, close, vwap, volumen, anzahl]
    return [
        Candle(
            at=datetime.fromtimestamp(int(row[0]), tz=UTC),
            open=Decimal(str(row[1])),
            high=Decimal(str(row[2])),
            low=Decimal(str(row[3])),
            close=Decimal(str(row[4])),
        )
        for row in series[0]
    ]


def _load_csv(path: Path) -> list[Candle]:
    out: list[Candle] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            raw = row["time"].strip()
            when = (
                datetime.fromtimestamp(
                    int(raw) / (1000 if len(raw) > 11 else 1), tz=UTC
                )
                if raw.isdigit()
                else datetime.fromisoformat(raw.replace("Z", "+00:00"))
            )
            out.append(
                Candle(
                    at=when,
                    open=Decimal(row["open"]),
                    high=Decimal(row["high"]),
                    low=Decimal(row["low"]),
                    close=Decimal(row["close"]),
                )
            )
    return out


def atr(candles: list[Candle], length: int) -> Decimal:
    if len(candles) < length + 1:
        return ZERO
    window = candles[-(length + 1) :]
    spans = [
        max(
            b.high - b.low,
            abs(b.high - a.close),
            abs(b.low - a.close),
        )
        for a, b in zip(window, window[1:], strict=False)
    ]
    return sum(spans, ZERO) / Decimal(len(spans))


def backtest(
    candles: list[Candle],
    *,
    entry_len: int,
    exit_len: int,
    fee: Decimal,
    slippage: Decimal,
    capital: Decimal,
    risk: Decimal,
) -> dict[str, object]:
    trades: list[Decimal] = []
    fees_paid = ZERO
    signals = 0
    equity = capital
    position: tuple[Decimal, Decimal, Decimal, Decimal] | None = None  # entry, stop, qty, fee

    warmup = max(entry_len, exit_len, 20) + 1
    for i in range(warmup, len(candles) + 1):
        history = candles[:i]
        bar = history[-1]
        prior = history[:-1]

        if position is not None:
            entry, stop, qty, paid = position
            lower = min(c.low for c in prior[-exit_len:])
            exit_price = None
            # Stop zuerst: innerhalb einer Kerze ist die Reihenfolge unbekannt.
            if bar.low <= stop:
                exit_price = stop
            elif bar.close < lower:
                exit_price = bar.close
            if exit_price is not None:
                filled = exit_price * (Decimal(1) - slippage)
                exit_fee = filled * qty * fee
                pnl = (filled - entry) * qty - paid - exit_fee
                trades.append(pnl)
                fees_paid += paid + exit_fee
                equity += pnl
                position = None

        upper = max(c.high for c in prior[-entry_len:])
        band = atr(prior, 20)
        if band <= ZERO or bar.close <= upper or position is not None:
            continue
        signals += 1
        fill = bar.close * (Decimal(1) + slippage)
        stop = bar.close - Decimal(2) * band
        if stop <= ZERO or stop >= fill:
            continue
        qty = (equity * risk) / (fill - stop)
        if qty <= ZERO:
            continue
        position = (fill, stop, qty, fill * qty * fee)

    wins = sum(1 for p in trades if p > ZERO)
    net = sum(trades, ZERO)
    peak = run = worst = ZERO
    for p in trades:
        run += p
        peak = max(peak, run)
        worst = max(worst, peak - run)
    return {
        "trades": len(trades),
        "signals": signals,
        "wins": wins,
        "losses": len(trades) - wins,
        "net": net,
        "fees": fees_paid,
        "drawdown": worst,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Donchian-Backtest, ohne Installation")
    parser.add_argument("datei", help="Kraken-JSON oder CSV mit Kerzen")
    parser.add_argument("--fee", default="0.0026", help="Gebühr pro Seite als Bruchteil")
    parser.add_argument("--slippage", default="0.0005")
    parser.add_argument("--capital", default="1000")
    parser.add_argument("--risk", default="0.01", help="Risiko je Trade als Bruchteil")
    parser.add_argument("--entry", type=int, default=55)
    parser.add_argument("--exit", dest="exit_len", type=int, default=20)
    args = parser.parse_args()

    path = Path(args.datei)
    if not path.exists():
        print(f"{path} gibt es nicht. Liegt die Datei wirklich hier?")
        return 1

    candles = load(path)
    candles.sort(key=lambda c: c.at)
    if len(candles) < 100:
        print(f"Nur {len(candles)} Kerzen — zu wenig für eine Aussage.")
        return 1

    capital = Decimal(args.capital)
    result = backtest(
        candles,
        entry_len=args.entry,
        exit_len=args.exit_len,
        fee=Decimal(args.fee),
        slippage=Decimal(args.slippage),
        capital=capital,
        risk=Decimal(args.risk),
    )

    fee_pct = (Decimal(args.fee) * 100).quantize(Decimal("0.001"))
    print(f"Kerzen         {len(candles)}  ({candles[0].at.date()} bis {candles[-1].at.date()})")
    print(f"Regel          Donchian {args.entry}/{args.exit_len}")
    print(f"Kosten         {fee_pct} % Gebühr, {Decimal(args.slippage) * 100} % Slippage")
    print()
    print(f"Signale        {result['signals']}")
    print(f"Trades         {result['trades']}")
    print(f"Gewinner       {result['wins']} / Verlierer {result['losses']}")
    print(f"Gebühren       {Decimal(str(result['fees'])).quantize(QUANT)}")
    print(f"Netto          {Decimal(str(result['net'])).quantize(QUANT)}")
    print(f"Max. Drawdown  {Decimal(str(result['drawdown'])).quantize(QUANT)}")
    trades = int(str(result["trades"]))
    if trades:
        rate = (Decimal(str(result["wins"])) / Decimal(trades) * 100).quantize(QUANT)
        avg = (Decimal(str(result["net"])) / Decimal(trades)).quantize(QUANT)
        ret = (Decimal(str(result["net"])) / capital * 100).quantize(QUANT)
        print(f"Trefferquote   {rate} %")
        print(f"Ø pro Trade    {avg}")
        print(f"Rendite        {ret} % auf {capital}")
    print()
    if trades < 30:
        print(
            f"ZU WENIG TRADES ({trades} von 30). Diese Zahlen liegen im Bereich des\n"
            "Zufalls. Ein guter Wert hier ist kein Ergebnis, und ein schlechter\n"
            "kein Beweis. Mehr Historie oder mehr Märkte nötig."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
