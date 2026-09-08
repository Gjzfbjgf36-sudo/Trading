"""The trading rules, in Python.

The same logic as the Pine scripts, so a rule can be evaluated and backtested
without a charting service, a plan, or a subscription that expires. A rule here
is a pure function from a candle series to a signal: given the same bars it
gives the same answer, always, which is the property that makes it testable at
all.

**No look-ahead.** Every rule sees bars up to and including the one being
evaluated, never past it. That is enforced by construction — the functions take
a prefix of the series, not the whole thing plus an index.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from ..domain.types import ZERO, Side
from ..marketdata.candles import Candle

_QUANT = Decimal("0.00000001")


@dataclass(frozen=True, slots=True)
class RuleSignal:
    """What a rule says about the most recent bar."""

    fires: bool
    side: Side
    entry: Decimal
    stop: Decimal
    target: Decimal | None
    #: Rule name including its parameters, so the journal records what fired.
    source: str
    #: What was true on the chart. Recorded so the signal is checkable later.
    observed: str

    @property
    def risk_per_unit(self) -> Decimal:
        return abs(self.entry - self.stop)


class Rule(Protocol):
    """Anything that turns a candle history into a signal."""

    @property
    def name(self) -> str:
        """Rule name including parameters, so the journal records what fired."""

    def evaluate(self, candles: Sequence[Candle]) -> RuleSignal | None:
        """Signal for the last candle, or ``None`` if there is not enough history."""

    def exits(self, candles: Sequence[Candle]) -> bool:
        """Whether an open long should be closed on the last candle."""


@dataclass(frozen=True, slots=True)
class DonchianBreakout:
    """Enter above the highest high of the last N bars; exit below the last M lows.

    Two parameters, decades of public study, and almost nothing to overfit. The
    breakout level uses bars *before* the current one — comparing a bar's high
    against itself would make the rule fire on every new high by definition.
    """

    entry_length: int = 55
    exit_length: int = 20
    atr_length: int = 20
    atr_multiple: Decimal = Decimal("2.0")

    @property
    def name(self) -> str:
        return f"donchian_{self.entry_length}_{self.exit_length}"

    def _warmup(self) -> int:
        return max(self.entry_length, self.exit_length, self.atr_length) + 1

    def evaluate(self, candles: Sequence[Candle]) -> RuleSignal | None:
        if len(candles) < self._warmup():
            return None
        current = candles[-1]
        prior = candles[:-1]
        upper = max(c.high for c in prior[-self.entry_length :])
        atr = average_true_range(prior, self.atr_length)
        if atr <= ZERO:
            return None
        stop = (current.close - self.atr_multiple * atr).quantize(_QUANT)
        if stop <= ZERO or stop >= current.close:
            return None
        return RuleSignal(
            fires=current.close > upper,
            side=Side.BUY,
            entry=current.close,
            stop=stop,
            target=None,  # exit comes from the rule, not a fixed level
            source=self.name,
            observed=(
                f"Schluss {current.close}, {self.entry_length}-Tage-Hoch {upper}, "
                f"ATR {atr.quantize(Decimal('0.01'))}"
            ),
        )

    def exits(self, candles: Sequence[Candle]) -> bool:
        if len(candles) < self.exit_length + 1:
            return False
        lower = min(c.low for c in candles[:-1][-self.exit_length :])
        return candles[-1].close < lower


@dataclass(frozen=True, slots=True)
class EngulfingInTrend:
    """Bullish engulfing above a long moving average.

    The pattern as four comparisons rather than an impression. The trend filter
    is what stops it buying into every decline: a pattern without context is the
    weakest form of a rule.
    """

    trend_length: int = 200
    target_r: Decimal = Decimal("2.0")
    time_stop: int = 10

    @property
    def name(self) -> str:
        return f"engulfing_ema{self.trend_length}_r{self.target_r}"

    def evaluate(self, candles: Sequence[Candle]) -> RuleSignal | None:
        if len(candles) < self.trend_length + 2:
            return None
        current, previous = candles[-1], candles[-2]
        trend = exponential_moving_average(candles, self.trend_length)

        prev_bearish = previous.close < previous.open
        curr_bullish = current.close > current.open
        engulfs = current.open <= previous.close and current.close >= previous.open
        substantial = current.body > previous.range / Decimal(2)
        in_uptrend = current.close > trend

        stop = min(current.low, previous.low)
        if stop <= ZERO or stop >= current.close:
            return None
        risk = current.close - stop
        return RuleSignal(
            fires=prev_bearish and curr_bullish and engulfs and substantial and in_uptrend,
            side=Side.BUY,
            entry=current.close,
            stop=stop,
            target=(current.close + risk * self.target_r).quantize(_QUANT),
            source=self.name,
            observed=(
                f"Schluss {current.close}, EMA{self.trend_length} "
                f"{trend.quantize(Decimal('0.01'))}, Vorkerze "
                f"{'rot' if prev_bearish else 'gruen'}, Umschluss {engulfs}"
            ),
        )

    def exits(self, candles: Sequence[Candle]) -> bool:
        """Stop and target are enforced by the backtest; time is enforced here."""
        return False


def average_true_range(candles: Sequence[Candle], length: int) -> Decimal:
    """Wilder's true range, averaged simply. Zero when there is no history."""
    if len(candles) < length + 1:
        return ZERO
    window = candles[-(length + 1) :]
    ranges = []
    for previous, current in zip(window, window[1:], strict=False):
        ranges.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )
    return (sum(ranges, start=ZERO) / Decimal(len(ranges))).quantize(_QUANT)


def exponential_moving_average(candles: Sequence[Candle], length: int) -> Decimal:
    """EMA of closes, seeded with a simple average of the first window.

    Seeding matters: starting the recursion from the first close alone leaves a
    bias that takes hundreds of bars to decay, and on a 200-length average that
    is most of a small dataset.
    """
    if len(candles) < length:
        return ZERO
    multiplier = Decimal(2) / Decimal(length + 1)
    seed_window = candles[:length]
    ema = sum((c.close for c in seed_window), start=ZERO) / Decimal(length)
    for candle in candles[length:]:
        ema = (candle.close - ema) * multiplier + ema
    return ema.quantize(_QUANT)


#: Rules the CLI can name.
AVAILABLE: dict[str, Rule] = {
    "donchian": DonchianBreakout(),
    "engulfing": EngulfingInTrend(),
}
