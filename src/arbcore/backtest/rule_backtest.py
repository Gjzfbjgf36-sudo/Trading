"""Backtest a rule over real candles, with the costs that actually apply.

Charting platforms default to zero commission and zero slippage. That single
default is why so many backtests look excellent and then lose money: the edge
being measured is smaller than the costs being omitted. Here the costs are
required arguments, so a run without them is impossible rather than merely
discouraged.

Three further choices, all made against us on purpose:

* **No look-ahead.** A signal is computed from bars up to and including the
  signal bar, never past it, and the entry is that bar's close.
* **Stop before target within a bar.** When a bar's range covers both, OHLC
  cannot say which came first, so the stop is assumed. Assuming the target
  would inflate every result by exactly the ambiguous cases.
* **Fees on both sides, plus slippage on entry and exit.**
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from ..domain.types import ZERO, to_decimal
from ..marketdata.candles import Candle, validate_series
from ..strategy.rules import Rule

_QUANT = Decimal("0.00000001")


class BacktestError(ValueError):
    """Raised when a backtest cannot be run honestly."""


@dataclass(frozen=True, slots=True)
class BacktestCosts:
    """What a round trip costs. Required, never defaulted to zero."""

    fee_rate: Decimal
    #: Fraction of price lost to slippage on each side.
    slippage: Decimal = Decimal("0.0005")

    def __post_init__(self) -> None:
        for name in ("fee_rate", "slippage"):
            value = to_decimal(getattr(self, name))
            object.__setattr__(self, name, value)
            if value < ZERO or value > Decimal("0.1"):
                raise BacktestError(f"{name}={value} is outside a plausible range")


@dataclass(frozen=True, slots=True)
class ClosedTrade:
    entered_at: datetime
    exited_at: datetime
    entry: Decimal
    exit: Decimal
    quantity: Decimal
    pnl: Decimal
    fees: Decimal
    reason: str
    bars_held: int


@dataclass(slots=True)
class BacktestResult:
    """What the rule did, reported so it cannot flatter itself."""

    rule: str
    candles: int
    signals: int
    trades: list[ClosedTrade] = field(default_factory=list)
    #: Signals that never became trades because a position was already open.
    skipped_while_in_position: int = 0

    @property
    def wins(self) -> int:
        return sum(1 for t in self.trades if t.pnl > ZERO)

    @property
    def losses(self) -> int:
        return sum(1 for t in self.trades if t.pnl < ZERO)

    @property
    def net(self) -> Decimal:
        return sum((t.pnl for t in self.trades), start=ZERO)

    @property
    def fees_paid(self) -> Decimal:
        return sum((t.fees for t in self.trades), start=ZERO)

    @property
    def win_rate(self) -> Decimal | None:
        if not self.trades:
            return None
        return (Decimal(self.wins) / Decimal(len(self.trades))).quantize(Decimal("0.01"))

    @property
    def expectancy(self) -> Decimal | None:
        if not self.trades:
            return None
        return (self.net / Decimal(len(self.trades))).quantize(Decimal("0.01"))

    @property
    def max_drawdown(self) -> Decimal:
        peak = running = ZERO
        worst = ZERO
        for trade in self.trades:
            running += trade.pnl
            peak = max(peak, running)
            worst = max(worst, peak - running)
        return worst

    def render(self, starting_capital: Decimal) -> str:
        if not self.trades:
            return (
                f"Regel {self.rule}: {self.signals} Signale, aber kein "
                f"abgeschlossener Trade über {self.candles} Kerzen."
            )
        gross_wins = sum((t.pnl for t in self.trades if t.pnl > ZERO), start=ZERO)
        gross_losses = sum((-t.pnl for t in self.trades if t.pnl < ZERO), start=ZERO)
        factor = (
            (gross_wins / gross_losses).quantize(Decimal("0.01"))
            if gross_losses > ZERO
            else None
        )
        ret = (self.net / starting_capital * Decimal(100)).quantize(Decimal("0.01"))
        lines = [
            f"Regel                {self.rule}",
            f"Kerzen               {self.candles}",
            f"Signale              {self.signals}"
            + (
                f"  ({self.skipped_while_in_position} übersprungen, Position offen)"
                if self.skipped_while_in_position
                else ""
            ),
            f"Trades               {len(self.trades)}",
            f"Gewinner / Verlierer {self.wins} / {self.losses}",
            f"Trefferquote         {self.win_rate}",
            f"Gebühren gezahlt     {self.fees_paid.quantize(Decimal('0.01'))}",
            f"Netto                {self.net.quantize(Decimal('0.01'))}",
            f"Rendite auf Kapital  {ret} %",
            f"Erwartungswert       {self.expectancy}",
            f"Profit-Faktor        {factor}",
            f"Max. Drawdown        {self.max_drawdown.quantize(Decimal('0.01'))}",
        ]
        if len(self.trades) < 30:
            lines += [
                "",
                f"ZU WENIG TRADES ({len(self.trades)} von 30). Diese Zahlen liegen "
                "im Bereich des Zufalls. Ein guter Wert hier ist kein Ergebnis.",
            ]
        return "\n".join(lines)


def run_backtest(
    rule: Rule,
    candles: Sequence[Candle],
    *,
    costs: BacktestCosts,
    starting_capital: Decimal,
    risk_per_trade: Decimal = Decimal("0.01"),
    time_stop_bars: int | None = None,
) -> BacktestResult:
    """Walk the series once, forward, taking every signal the rule gives.

    Position size follows the same rule as live: risk a fixed fraction of
    capital against the stop distance. Sizing off a fixed notional instead
    would measure a strategy nobody would actually trade.
    """
    validate_series(candles)
    result = BacktestResult(rule=rule.name, candles=len(candles), signals=0)

    equity = to_decimal(starting_capital)
    open_entry: Decimal | None = None
    open_stop = open_target = ZERO
    open_quantity = ZERO
    open_index = 0
    entry_fees = ZERO

    for index in range(1, len(candles) + 1):
        history = candles[:index]
        bar = history[-1]

        if open_entry is not None:
            exit_price: Decimal | None = None
            reason = ""
            # Stop first: within one bar OHLC cannot order the two, and
            # assuming the target would inflate exactly the ambiguous cases.
            if bar.low <= open_stop:
                exit_price, reason = open_stop, "STOP"
            elif open_target > ZERO and bar.high >= open_target:
                exit_price, reason = open_target, "TARGET"
            elif rule.exits(history):
                exit_price, reason = bar.close, "SIGNAL"
            elif time_stop_bars is not None and index - open_index >= time_stop_bars:
                exit_price, reason = bar.close, "ZEIT"

            if exit_price is not None:
                filled = exit_price * (Decimal(1) - costs.slippage)
                exit_fee = filled * open_quantity * costs.fee_rate
                pnl = (
                    (filled - open_entry) * open_quantity - entry_fees - exit_fee
                ).quantize(_QUANT)
                equity += pnl
                result.trades.append(
                    ClosedTrade(
                        entered_at=candles[open_index - 1].at,
                        exited_at=bar.at,
                        entry=open_entry,
                        exit=filled.quantize(_QUANT),
                        quantity=open_quantity,
                        pnl=pnl,
                        fees=(entry_fees + exit_fee).quantize(_QUANT),
                        reason=reason,
                        bars_held=index - open_index,
                    )
                )
                open_entry = None

        signal = rule.evaluate(history)
        if signal is None or not signal.fires:
            continue
        result.signals += 1
        if open_entry is not None:
            result.skipped_while_in_position += 1
            continue

        fill = signal.entry * (Decimal(1) + costs.slippage)
        risk_distance = fill - signal.stop
        if risk_distance <= ZERO or equity <= ZERO:
            continue
        quantity = ((equity * to_decimal(risk_per_trade)) / risk_distance).quantize(_QUANT)
        if quantity <= ZERO:
            continue

        open_entry = fill.quantize(_QUANT)
        open_stop = signal.stop
        open_target = signal.target or ZERO
        open_quantity = quantity
        open_index = index
        entry_fees = (fill * quantity * costs.fee_rate).quantize(_QUANT)

    return result
