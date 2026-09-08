"""Robustheit statt Bestwert.

Die naheliegende Frage an einen Parameter lautet „welcher Wert ist am besten?".
Sie ist die falsche. Wer zwanzig Varianten testet und die beste nimmt, hat aus
zwanzig Ziehungen das Maximum gewählt — dessen Ergebnis ist systematisch zu
gut, ganz ohne Absicht.

Die richtige Frage ist: **ist der gute Bereich ein Plateau oder eine Spitze?**

* Ein **Plateau** — 40/15, 55/20 und 70/25 funktionieren alle ähnlich — deutet
  darauf hin, dass etwas Reales gemessen wurde. Der genaue Wert ist dann egal,
  und das ist ein gutes Zeichen.
* Eine **Spitze** — nur 55/20 funktioniert, die Nachbarn verlieren — ist fast
  immer angepasstes Rauschen. Ein Markt, der zwischen 54 und 55 Tagen
  umschaltet, existiert nicht.

Dieses Modul beantwortet also nicht „welcher Parameter", sondern „darf ich
diesem Ergebnis überhaupt glauben".
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal

from ..domain.types import ZERO
from ..marketdata.candles import Candle
from ..strategy.rules import Rule
from .rule_backtest import BacktestCosts, run_backtest

_QUANT = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class SweepPoint:
    """Ein Parametersatz und was er über die Historie geliefert hat."""

    label: str
    parameters: tuple[int, ...]
    trades: int
    net: Decimal
    expectancy: Decimal | None
    win_rate: Decimal | None
    max_drawdown: Decimal

    @property
    def positive(self) -> bool:
        return self.net > ZERO


@dataclass(frozen=True, slots=True)
class RobustnessReport:
    """Was der Sweep über die Verlässlichkeit aussagt."""

    points: tuple[SweepPoint, ...]
    #: Anteil der geprüften Parametersätze, die profitabel waren.
    positive_share: Decimal
    best: SweepPoint | None
    #: Nachbarn des besten Punktes, die ebenfalls profitabel sind.
    neighbours_positive: int
    neighbours_total: int

    @property
    def looks_like_a_plateau(self) -> bool:
        """Plateau, wenn die Mehrheit trägt UND die Nachbarn mitgehen.

        Beide Bedingungen sind nötig: eine breite Mehrheit ohne stabile
        Nachbarschaft kann ein Zufallsmuster sein, und gute Nachbarn in einem
        sonst verlustreichen Feld sind eine Insel.
        """
        if self.best is None or self.neighbours_total == 0:
            return False
        neighbour_share = Decimal(self.neighbours_positive) / Decimal(self.neighbours_total)
        return self.positive_share >= Decimal("0.6") and neighbour_share >= Decimal("0.6")

    def render(self) -> str:
        if not self.points:
            return "Keine auswertbaren Parametersätze."
        lines = [
            f"{'Parameter':<16}{'Trades':>8}{'Netto':>12}{'Erwartung':>12}{'Treffer':>10}",
            "-" * 58,
        ]
        for point in self.points:
            lines.append(
                f"{point.label:<16}{point.trades:>8}"
                f"{point.net.quantize(_QUANT):>12}"
                f"{str(point.expectancy or '-'):>12}"
                f"{str(point.win_rate or '-'):>10}"
            )
        share = (self.positive_share * Decimal(100)).quantize(_QUANT)
        lines += [
            "",
            f"Profitabel: {share} % der geprüften Parametersätze",
        ]
        if self.best is not None:
            lines.append(
                f"Bester: {self.best.label} — davon {self.neighbours_positive} von "
                f"{self.neighbours_total} direkten Nachbarn ebenfalls profitabel"
            )
        lines.append("")
        if self.looks_like_a_plateau:
            lines.append(
                "PLATEAU. Der gute Bereich ist breit, nicht ein einzelner Wert. Das "
                "spricht dafür, dass etwas Reales gemessen wurde. Nimm einen Wert "
                "aus der Mitte des Plateaus — nicht den besten, denn der ist das "
                "Maximum einer Zufallsziehung."
            )
        else:
            lines.append(
                "KEIN PLATEAU. Der gute Bereich ist schmal oder die Nachbarn "
                "verlieren. Das ist fast immer angepasstes Rauschen: einen Markt, "
                "der zwischen benachbarten Parametern umschaltet, gibt es nicht. "
                "Diesem Ergebnis nicht glauben."
            )
        if any(p.trades < 30 for p in self.points):
            lines.append(
                "\nWARNUNG: Mindestens ein Parametersatz hat unter 30 Trades. Bei "
                "so wenigen ist jede Reihenfolge im Ranking Zufall."
            )
        return "\n".join(lines)


def sweep(
    build: Callable[[int, int], Rule],
    grid: Sequence[tuple[int, int]],
    candles: Sequence[Candle],
    *,
    costs: BacktestCosts,
    starting_capital: Decimal,
    risk_per_trade: Decimal = Decimal("0.01"),
) -> RobustnessReport:
    """Dieselbe Regel über ein Parametergitter, ohne einen Sieger zu küren.

    ``build`` erzeugt die Regel aus zwei ganzzahligen Parametern. Das Gitter
    wird vollständig gerechnet — es gibt keine Suche und keinen Abbruch, weil
    genau das Suchen den Bias erzeugt, den dieses Modul sichtbar machen soll.
    """
    points: list[SweepPoint] = []
    for first, second in grid:
        rule = build(first, second)
        result = run_backtest(
            rule,
            candles,
            costs=costs,
            starting_capital=starting_capital,
            risk_per_trade=risk_per_trade,
        )
        points.append(
            SweepPoint(
                label=f"{first}/{second}",
                parameters=(first, second),
                trades=len(result.trades),
                net=result.net,
                expectancy=result.expectancy,
                win_rate=result.win_rate,
                max_drawdown=result.max_drawdown,
            )
        )

    if not points:
        return RobustnessReport((), ZERO, None, 0, 0)

    positive = sum(1 for p in points if p.positive)
    share = (Decimal(positive) / Decimal(len(points))).quantize(Decimal("0.0001"))
    best = max(points, key=lambda p: p.net)
    neighbours = _neighbours(best, points)
    return RobustnessReport(
        points=tuple(points),
        positive_share=share,
        best=best,
        neighbours_positive=sum(1 for n in neighbours if n.positive),
        neighbours_total=len(neighbours),
    )


def _neighbours(point: SweepPoint, points: Sequence[SweepPoint]) -> list[SweepPoint]:
    """Parametersätze, die im Gitter direkt an ``point`` grenzen.

    Nachbarschaft wird über den Rang je Achse bestimmt, nicht über den
    Zahlenabstand: ein Gitter mit ungleichmässigen Schritten hätte sonst
    willkürlich viele oder gar keine Nachbarn.
    """
    first_values = sorted({p.parameters[0] for p in points})
    second_values = sorted({p.parameters[1] for p in points})
    first_index = first_values.index(point.parameters[0])
    second_index = second_values.index(point.parameters[1])

    found: list[SweepPoint] = []
    for candidate in points:
        if candidate.parameters == point.parameters:
            continue
        try:
            i = first_values.index(candidate.parameters[0])
            j = second_values.index(candidate.parameters[1])
        except ValueError:  # pragma: no cover - grid is built from these values
            continue
        if abs(i - first_index) <= 1 and abs(j - second_index) <= 1:
            found.append(candidate)
    return found
