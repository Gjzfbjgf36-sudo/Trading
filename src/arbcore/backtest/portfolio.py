"""Dieselbe Regel über mehrere Märkte.

Die am besten belegte Verbesserung für Trendfolge ist keine Einstellung,
sondern Streuung. Der Grund ist unspektakulär und genau deshalb verlässlich:
die Regel trifft in jedem einzelnen Markt selten, aber die Fehlschläge
verschiedener Märkte fallen nicht auf denselben Tag. Zwanzig Märkte liefern
zwanzigmal so viele Gelegenheiten, ohne dass ein einzelner Verlust zwanzigmal
so weh tut.

Wichtig: Das ist **keine Parameteranpassung**. Es wird nichts an der Regel
verändert und nichts an Daten optimiert — dieselbe Regel läuft nur öfter. Genau
deshalb ist es die einzige Verbesserung in diesem Modul, die keinen
Überanpassungs-Verdacht erzeugt.

Ehrliche Einschränkung: Der Nutzen hängt daran, dass die Märkte sich nicht
gleich bewegen. Krypto-Paare laufen weitgehend im Gleichschritt — fünf
Altcoins sind kaum mehr Streuung als Bitcoin allein. Deshalb wird die
beobachtete Gleichzeitigkeit hier ausgewiesen, statt Streuung zu unterstellen.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from ..domain.types import ZERO
from ..marketdata.candles import Candle
from ..strategy.rules import Rule
from .rule_backtest import BacktestCosts, ClosedTrade, run_backtest

_QUANT = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class MarketResult:
    symbol: str
    trades: int
    net: Decimal
    max_drawdown: Decimal


@dataclass(frozen=True, slots=True)
class PortfolioResult:
    """Das Ergebnis über alle Märkte, plus wie sehr sie sich überlappten."""

    per_market: tuple[MarketResult, ...]
    combined_net: Decimal
    #: Grösster Rückgang der gemeinsamen Kapitalkurve, chronologisch gerechnet.
    combined_drawdown: Decimal
    #: Summe der Einzel-Drawdowns. Der Vergleich zeigt, ob Streuung gewirkt hat.
    summed_drawdowns: Decimal
    total_trades: int
    #: Anteil der Handelstage, an denen mehr als ein Markt gleichzeitig offen war.
    overlap_share: Decimal

    @property
    def diversification_benefit(self) -> Decimal | None:
        """Wie viel kleiner der gemeinsame Drawdown ist als die Summe der einzelnen.

        Nahe 0 heisst: die Märkte verlieren gleichzeitig, die Streuung ist eine
        Illusion. ``None``, wenn es gar keine Verluste gab.
        """
        if self.summed_drawdowns <= ZERO:
            return None
        return (
            Decimal(1) - self.combined_drawdown / self.summed_drawdowns
        ).quantize(Decimal("0.0001"))

    def render(self) -> str:
        lines = [f"{'Markt':<14}{'Trades':>8}{'Netto':>12}{'Drawdown':>12}", "-" * 46]
        for market in self.per_market:
            lines.append(
                f"{market.symbol:<14}{market.trades:>8}"
                f"{market.net.quantize(_QUANT):>12}"
                f"{market.max_drawdown.quantize(_QUANT):>12}"
            )
        lines += [
            "-" * 46,
            f"{'Zusammen':<14}{self.total_trades:>8}"
            f"{self.combined_net.quantize(_QUANT):>12}"
            f"{self.combined_drawdown.quantize(_QUANT):>12}",
            "",
            f"Summe der Einzel-Drawdowns: {self.summed_drawdowns.quantize(_QUANT)}",
        ]
        benefit = self.diversification_benefit
        if benefit is not None:
            pct = (benefit * Decimal(100)).quantize(_QUANT)
            lines.append(f"Streuungsvorteil:           {pct} %")
            if benefit < Decimal("0.15"):
                lines.append(
                    "\nKAUM STREUUNG. Die Märkte verlieren weitgehend gleichzeitig — "
                    "typisch für Krypto-Paare, die fast alle Bitcoin folgen. Mehr "
                    "davon bringt kaum etwas; Streuung müsste über andere "
                    "Anlageklassen laufen."
                )
            else:
                lines.append(
                    "\nDie Märkte verlieren zu unterschiedlichen Zeiten. Das ist der "
                    "Effekt, der Streuung wertvoll macht — und er kostet keine "
                    "Parameteranpassung."
                )
        overlap = (self.overlap_share * Decimal(100)).quantize(_QUANT)
        lines.append(f"Tage mit mehreren offenen Positionen: {overlap} %")
        if self.total_trades < 30:
            lines.append(
                f"\nZU WENIG TRADES ({self.total_trades} von 30) für eine Aussage."
            )
        return "\n".join(lines)


def run_portfolio(
    rule: Rule,
    markets: Mapping[str, Sequence[Candle]],
    *,
    costs: BacktestCosts,
    capital_per_market: Decimal,
    risk_per_trade: Decimal = Decimal("0.01"),
) -> PortfolioResult:
    """Dieselbe Regel auf jedem Markt, dann chronologisch zusammengeführt.

    Jeder Markt bekommt sein eigenes Kapital, damit ein Markt die anderen nicht
    verdrängt. Die gemeinsame Kapitalkurve wird nach Ausstiegsdatum sortiert
    gebildet — nur so ist der gemeinsame Drawdown der, den man tatsächlich
    erlebt hätte, statt der Summe unabhängiger Kurven.
    """
    per_market: list[MarketResult] = []
    all_trades: list[ClosedTrade] = []
    summed = ZERO

    for symbol, candles in sorted(markets.items()):
        result = run_backtest(
            rule,
            candles,
            costs=costs,
            starting_capital=capital_per_market,
            risk_per_trade=risk_per_trade,
        )
        per_market.append(
            MarketResult(
                symbol=symbol,
                trades=len(result.trades),
                net=result.net,
                max_drawdown=result.max_drawdown,
            )
        )
        summed += result.max_drawdown
        all_trades.extend(result.trades)

    all_trades.sort(key=lambda t: t.exited_at)
    peak = running = worst = ZERO
    for trade in all_trades:
        running += trade.pnl
        peak = max(peak, running)
        worst = max(worst, peak - running)

    return PortfolioResult(
        per_market=tuple(per_market),
        combined_net=sum((t.pnl for t in all_trades), start=ZERO),
        combined_drawdown=worst,
        summed_drawdowns=summed,
        total_trades=len(all_trades),
        overlap_share=_overlap_share(all_trades),
    )


def _overlap_share(trades: Sequence[ClosedTrade]) -> Decimal:
    """Anteil der Tage mit Position, an denen mehr als eine offen war.

    Ein hoher Wert heisst: die Märkte sind gleichzeitig investiert, und das
    Klumpenrisiko ist grösser, als die Anzahl der Märkte vermuten lässt.
    """
    if not trades:
        return ZERO
    occupied: dict[date, int] = {}
    for trade in trades:
        day = trade.entered_at.date()
        end = trade.exited_at.date()
        while day <= end:
            occupied[day] = occupied.get(day, 0) + 1
            day = date.fromordinal(day.toordinal() + 1)
    if not occupied:
        return ZERO
    multiple = sum(1 for count in occupied.values() if count > 1)
    return (Decimal(multiple) / Decimal(len(occupied))).quantize(Decimal("0.0001"))
