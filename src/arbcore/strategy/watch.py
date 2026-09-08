"""Live-Beobachtung: wie weit ist die Regel vom Auslösen entfernt?

Was hier bewusst *nicht* passiert: den Chart abfotografieren und Pixel deuten.
Ein Chart ist ein Bild der Daten, und die Daten gibt es direkt — exakt statt
geraten, und ohne einen fremden Bildschirm zu belauschen.

Das Kernproblem einer Live-Anzeige für eine Tagesregel: Die Regel gilt für
*geschlossene* Kerzen. Wer die laufende Kerze mitrechnet, handelt eine andere
Regel als die, die er getestet hat — und zwar eine, die häufiger und schlechter
feuert. Deshalb wird hier streng getrennt: das Signal kommt aus abgeschlossenen
Kerzen, die laufende Kerze wird nur *angezeigt*, damit man sieht, wie nah es
ist.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from ..domain.types import ZERO
from ..marketdata.candles import Candle
from .rules import Rule, RuleSignal

_QUANT = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class WatchStatus:
    """Wie der Markt gerade zur Regel steht."""

    price: Decimal
    #: Kurs, den es zum Auslösen braucht. ``None``, wenn die Regel keinen
    #: einzelnen Schwellenwert hat oder die Historie nicht reicht.
    trigger_level: Decimal | None
    #: Signal aus den ABGESCHLOSSENEN Kerzen. Das ist das, was zählt.
    signal: RuleSignal | None
    bar_closes_at: datetime | None
    now: datetime
    #: Kerzen, die noch fehlen, bis die Regel überhaupt rechnen kann.
    warmup_missing: int = 0

    @property
    def fires(self) -> bool:
        return self.signal is not None and self.signal.fires

    @property
    def distance(self) -> Decimal | None:
        """Abstand zum Auslöser als Bruchteil. Negativ heisst: schon darüber."""
        if self.trigger_level is None or self.price <= ZERO:
            return None
        return ((self.trigger_level - self.price) / self.price).quantize(
            Decimal("0.000001")
        )

    @property
    def time_to_close(self) -> timedelta | None:
        if self.bar_closes_at is None:
            return None
        return max(timedelta(0), self.bar_closes_at - self.now)

    def render(self) -> str:
        if self.warmup_missing > 0:
            return (
                f"Noch {self.warmup_missing} Kerzen Historie nötig, bevor die Regel "
                f"überhaupt rechnen kann."
            )
        if self.fires and self.signal is not None:
            return "\n".join(
                [
                    "",
                    "   ####  KAUFEN  ####",
                    "",
                    f"   Einstieg   {self.signal.entry}",
                    f"   Stop       {self.signal.stop}   <- sofort mitsetzen",
                    f"   Ziel       {self.signal.target or 'keins (Ausstieg per Regel)'}",
                    f"   Regel      {self.signal.source}",
                    f"   Gesehen    {self.signal.observed}",
                    "",
                    "   Groesse holen:  run_gate check --entry ... --stop ...",
                    "",
                ]
            )

        parts = [f"Kurs {self.price}"]
        if self.trigger_level is not None:
            distance = self.distance
            parts.append(f"Auslöser {self.trigger_level}")
            if distance is not None:
                pct = (distance * Decimal(100)).quantize(_QUANT)
                parts.append(
                    f"noch {pct} %" if distance > ZERO else f"darüber ({pct} %)"
                )
        remaining = self.time_to_close
        if remaining is not None:
            hours, seconds = divmod(int(remaining.total_seconds()), 3600)
            parts.append(f"Kerzenschluss in {hours}h {seconds // 60}m")
        parts.append("kein Signal")
        return "  |  ".join(parts)


def trigger_level(rule: Rule, closed: Sequence[Candle]) -> Decimal | None:
    """Der Kurs, den die nächste Kerze zum Auslösen braucht.

    Nur für Regeln, die einen einzelnen Schwellenwert haben. Für ein Muster wie
    Engulfing gibt es keinen einzelnen Kurs, ab dem es feuert — dann ``None``,
    statt eine Zahl zu erfinden, die keine ist.
    """
    from .rules import DonchianBreakout

    if isinstance(rule, DonchianBreakout):
        if len(closed) < rule.entry_length:
            return None
        return max(c.high for c in closed[-rule.entry_length :])
    return None


def status(
    rule: Rule,
    candles: Sequence[Candle],
    *,
    now: datetime,
    bar_duration: timedelta = timedelta(days=1),
    include_last_as_open: bool = True,
) -> WatchStatus:
    """Aktueller Stand.

    ``include_last_as_open`` behandelt die letzte Kerze als noch laufend: sie
    liefert den Preis für die Anzeige, wird aber aus der Signalberechnung
    herausgehalten. Das ist der ganze Punkt — sonst feuert die Anzeige auf einer
    Kerze, die sich noch ändern kann.
    """
    if not candles:
        return WatchStatus(
            price=ZERO,
            trigger_level=None,
            signal=None,
            bar_closes_at=None,
            now=now,
            warmup_missing=1,
        )

    current = candles[-1]
    closed = candles[:-1] if include_last_as_open else candles
    signal = rule.evaluate(closed) if closed else None
    level = trigger_level(rule, closed)
    missing = 0
    if signal is None and level is None:
        # Kein Signal und kein Schwellenwert: entweder zu wenig Historie oder
        # eine Regel ohne einzelnen Auslöser. Ersteres ist behebbar und wird
        # beziffert.
        missing = max(0, 60 - len(closed))

    return WatchStatus(
        price=current.close,
        trigger_level=level,
        signal=signal,
        bar_closes_at=current.at + bar_duration if include_last_as_open else None,
        now=now,
        warmup_missing=missing,
    )


@dataclass(frozen=True, slots=True)
class ScanRow:
    """Eine Zeile im Scanner: ein Markt, sein Stand zur Regel."""

    market: str
    status: WatchStatus

    @property
    def sort_key(self) -> tuple[int, Decimal]:
        """Feuernde zuerst, dann nach Abstand zum Auslöser.

        Märkte ohne Schwellenwert landen hinten statt vorne — unbekannter
        Abstand ist kein kleiner Abstand.
        """
        if self.status.fires:
            return (0, ZERO)
        distance = self.status.distance
        if distance is None:
            return (2, ZERO)
        return (1, distance)


def scan(
    rule: Rule,
    markets: Mapping[str, Sequence[Candle]],
    *,
    now: datetime,
    bar_duration: timedelta = timedelta(days=1),
) -> tuple[ScanRow, ...]:
    """Dieselbe Regel über viele Märkte, sortiert nach Nähe zum Auslöser.

    Der Scanner sucht *nicht* den stärksten Aufwärtstrend heraus. Er wendet
    genau die Regel an, die gemessen wurde, auf jeden Markt einzeln. Das ist
    der Unterschied zwischen "mehr Beobachtungen derselben Sache" und "sich aus
    vielen Märkten das schönste Bild aussuchen" — Letzteres fügt eine Auswahl
    hinzu, die nie getestet wurde, und ist der übliche Weg, einen gemessenen
    Vorteil wieder zu verlieren.
    """
    rows = [
        ScanRow(market=name, status=status(rule, candles, now=now, bar_duration=bar_duration))
        for name, candles in markets.items()
    ]
    return tuple(sorted(rows, key=lambda row: (row.sort_key, row.market)))


def render_scan(rows: Sequence[ScanRow]) -> str:
    """Tabelle für das Terminal."""
    if not rows:
        return "Keine Märkte übergeben."
    lines = [f"{'Markt':<12} {'Kurs':>12} {'Auslöser':>12} {'Abstand':>10}  Stand"]
    lines.append("-" * 72)
    for row in rows:
        s = row.status
        if s.warmup_missing > 0:
            lines.append(f"{row.market:<12} {'':>12} {'':>12} {'':>10}  zu wenig Historie")
            continue
        level = str(s.trigger_level) if s.trigger_level is not None else "-"
        distance = s.distance
        gap = "-" if distance is None else f"{distance * Decimal(100):.2f} %"
        state = "KAUFEN" if s.fires else "wartet"
        lines.append(f"{row.market:<12} {s.price:>12} {level:>12} {gap:>10}  {state}")
    firing = [row for row in rows if row.status.fires]
    lines.append("")
    if firing:
        lines.append(f"{len(firing)} Markt/Märkte feuern: " + ", ".join(r.market for r in firing))
        lines.append("Jetzt `run_gate check` — das Gate entscheidet über die Größe.")
    else:
        lines.append("Kein Markt feuert. Das ist der Normalfall und keine Aufforderung,")
        lines.append("den nächstbesten zu nehmen.")
    return "\n".join(lines)
