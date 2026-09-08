"""Candles, and where they come from.

The decision system needs three things: prices, a rule over them, and a
notification. None of those require a third-party charting service, a plan, or
a subscription that expires. This module supplies the first one.

Data arrives either from a CSV file (works offline, forever, and is what the
tests use) or from an exchange's public market-data endpoint. The second path
goes through `ccxt` rather than hand-written URLs: exchange endpoints are
integration facts that must not be guessed, and a maintained library keeps them
current in a way a hard-coded string cannot.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from ..domain.types import ZERO, to_decimal


class BadCandleData(ValueError):
    """Raised when candle data is missing, malformed or internally impossible."""


@dataclass(frozen=True, slots=True)
class Candle:
    """One bar. Validated, because a wrong high silently corrupts every rule."""

    at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def __post_init__(self) -> None:
        for name in ("open", "high", "low", "close", "volume"):
            value = to_decimal(getattr(self, name))
            object.__setattr__(self, name, value)
            if value < ZERO:
                raise BadCandleData(f"{name} must be >= 0 (got {value})")
        for name in ("open", "high", "low", "close"):
            if getattr(self, name) <= ZERO:
                raise BadCandleData(f"{name} must be > 0")
        if self.high < self.low:
            raise BadCandleData(f"high {self.high} is below low {self.low}")
        if not (self.low <= self.open <= self.high):
            raise BadCandleData(f"open {self.open} is outside the {self.low}-{self.high} range")
        if not (self.low <= self.close <= self.high):
            raise BadCandleData(f"close {self.close} is outside the {self.low}-{self.high} range")

    @property
    def is_green(self) -> bool:
        return self.close > self.open

    @property
    def body(self) -> Decimal:
        return abs(self.close - self.open)

    @property
    def range(self) -> Decimal:
        return self.high - self.low


def validate_series(candles: Sequence[Candle]) -> None:
    """Reject a series that cannot be a real price history.

    Out-of-order or duplicated timestamps make every rolling window wrong in a
    way that is invisible in the output — the backtest still produces a number,
    it is just the wrong one.
    """
    if not candles:
        raise BadCandleData("no candles")
    for earlier, later in zip(candles, candles[1:], strict=False):
        if later.at <= earlier.at:
            raise BadCandleData(
                f"candles are not strictly increasing in time: "
                f"{earlier.at.isoformat()} then {later.at.isoformat()}"
            )


def load_csv(path: str | Path) -> tuple[Candle, ...]:
    """Read candles from CSV.

    Expects a header with ``time,open,high,low,close,volume``. ``time`` may be a
    unix timestamp (seconds or milliseconds) or an ISO date. Extra columns are
    ignored; a missing required column is an error rather than a default.
    """
    file = Path(path)
    if not file.exists():
        raise BadCandleData(f"{path} does not exist")
    required = ("time", "open", "high", "low", "close")
    candles: list[Candle] = []
    with file.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise BadCandleData(f"{path} has no header row")
        missing = [c for c in required if c not in reader.fieldnames]
        if missing:
            raise BadCandleData(f"{path} is missing columns: {', '.join(missing)}")
        for number, row in enumerate(reader, start=2):
            try:
                candles.append(
                    Candle(
                        at=_parse_time(row["time"]),
                        open=to_decimal(row["open"]),
                        high=to_decimal(row["high"]),
                        low=to_decimal(row["low"]),
                        close=to_decimal(row["close"]),
                        volume=to_decimal(row.get("volume") or "0"),
                    )
                )
            except (BadCandleData, InvalidOperation, ValueError, TypeError) as exc:
                raise BadCandleData(f"{path} line {number}: {exc}") from None
    validate_series(candles)
    return tuple(candles)


def _parse_time(raw: str) -> datetime:
    text = raw.strip()
    if not text:
        raise BadCandleData("empty timestamp")
    if text.isdigit():
        value = int(text)
        # Milliseconds if the number is far too large to be seconds.
        seconds = value / 1000 if value > 10**11 else value
        return datetime.fromtimestamp(seconds, tz=UTC)
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def write_csv(path: str | Path, candles: Iterable[Candle]) -> int:
    """Save candles, so a download is done once and reused offline."""
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    rows = 0
    with file.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["time", "open", "high", "low", "close", "volume"])
        for candle in candles:
            writer.writerow(
                [
                    candle.at.isoformat(),
                    candle.open,
                    candle.high,
                    candle.low,
                    candle.close,
                    candle.volume,
                ]
            )
            rows += 1
    return rows


def fetch_ohlcv(
    exchange_id: str, symbol: str, timeframe: str = "1d", limit: int = 500
) -> tuple[Candle, ...]:
    """Public market data via ``ccxt``. No API key, no account, read-only.

    ``ccxt`` is used deliberately instead of hand-written request URLs: an
    exchange endpoint is an integration fact, and this project does not guess
    those. A maintained library also survives the endpoint changes that a
    hard-coded string would not.

    Install with ``pip install ccxt``. Raises a clear error if it is absent
    rather than failing somewhere deeper.
    """
    try:
        import ccxt
    except ImportError:
        raise BadCandleData(
            "ccxt ist nicht installiert. `pip install ccxt`, oder Kerzen als CSV "
            "laden — das Programm braucht keine Internetverbindung, um eine "
            "Regel auszuwerten."
        ) from None

    try:
        exchange_class = getattr(ccxt, exchange_id)
    except AttributeError:
        raise BadCandleData(f"ccxt kennt keine Börse namens {exchange_id!r}") from None

    exchange = exchange_class({"enableRateLimit": True})
    raw = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    candles = tuple(
        Candle(
            at=datetime.fromtimestamp(row[0] / 1000, tz=UTC),
            open=to_decimal(str(row[1])),
            high=to_decimal(str(row[2])),
            low=to_decimal(str(row[3])),
            close=to_decimal(str(row[4])),
            volume=to_decimal(str(row[5] or 0)),
        )
        for row in raw
    )
    validate_series(candles)
    return candles
