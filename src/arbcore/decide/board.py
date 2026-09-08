"""Ein Bildschirm: was das System gerade sagt, und was nicht.

Der Betreiber hat bisher fünf Befehle gebraucht, um zu wissen, wo er steht —
Kontostand, offene Positionen, Scanner, Zeit-Stop, Grenzen. Fünf Befehle heißen
in der Praxis: einer wird vergessen, meistens derselbe.

Was dieses Modul bewusst *nicht* tut: raten. Jede Zeile hier kommt aus dem
Journal oder aus abgeschlossenen Kerzen. Wo eine Information fehlt, steht das
da — nicht ein Näherungswert, der aussieht wie eine Messung.

Die wichtigste Zeile ist der Zeit-Stop-Countdown. Der teuerste Fehler dieser
Strategie ist nicht ein falscher Einstieg, sondern eine Position, die über ihren
Ausstieg hinaus offen bleibt, weil sie gerade gut aussieht.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from ..marketdata.candles import Candle
from ..strategy.rules import Rule
from ..strategy.watch import ScanRow, scan
from .gate import AccountState

_PCT = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class OpenLine:
    """Eine offene Position mit dem, was über ihren Ausstieg entscheidet."""

    ref: str
    symbol: str
    entry: str
    stop: str
    target: str
    #: Kerzen seit Einstieg. ``None``, wenn das Eröffnungsdatum fehlt.
    bars_held: int | None
    #: Kerzen bis zum Zeit-Stop. Negativ heißt: überfällig.
    bars_left: int | None

    @property
    def overdue(self) -> bool:
        return self.bars_left is not None and self.bars_left <= 0

    def render(self) -> str:
        head = f"  {self.ref:<10} {self.symbol:<10} Einstieg {self.entry}"
        lines = [head, f"    Stop {self.stop}   Ziel {self.target}"]
        if self.bars_left is None:
            lines.append("    Zeit-Stop: nicht konfiguriert")
        elif self.overdue:
            lines.append(
                f"    ### ZEIT-STOP ERREICHT ({self.bars_held} Kerzen) — "
                "HEUTE SCHLIESSEN, egal wie der Kurs steht ###"
            )
        else:
            lines.append(
                f"    Zeit-Stop in {self.bars_left} Kerzen "
                f"({self.bars_held} von {(self.bars_held or 0) + self.bars_left} gehalten)"
            )
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class Board:
    """Der ganze Stand auf einmal."""

    now: datetime
    account: AccountState
    starting_capital: Decimal
    max_open_positions: int
    rule_name: str
    time_stop_bars: int | None
    open_lines: tuple[OpenLine, ...]
    scan_rows: tuple[ScanRow, ...]
    #: Märkte, deren CSV älter ist als eine Kerze — die Anzeige wäre veraltet.
    stale_markets: tuple[tuple[str, int], ...] = ()

    @property
    def firing(self) -> tuple[ScanRow, ...]:
        return tuple(row for row in self.scan_rows if row.status.fires)

    @property
    def overdue(self) -> tuple[OpenLine, ...]:
        return tuple(line for line in self.open_lines if line.overdue)

    @property
    def room_for_more(self) -> int:
        return max(0, self.max_open_positions - len(self.open_lines))

    def _verdict(self) -> list[str]:
        """Was jetzt zu tun ist. Höchste Dringlichkeit zuerst."""
        if self.stale_markets:
            names = ", ".join(f"{n} ({d} Tage alt)" for n, d in self.stale_markets)
            return [
                "   DATEN VERALTET",
                "",
                f"   {names}",
                "   Erst neu laden. Ein Signal aus alten Kerzen ist kein Signal.",
            ]
        if self.overdue:
            refs = ", ".join(line.ref for line in self.overdue)
            return [
                "   HEUTE SCHLIESSEN",
                "",
                f"   {refs} — der Zeit-Stop ist erreicht.",
                "   Auch im Plus. Auch wenn es gerade läuft.",
                "   run_gate close --ref <ref> --exit-price <kurs> --reason time_stop",
            ]
        if self.firing:
            names = ", ".join(row.market for row in self.firing)
            if self.room_for_more == 0:
                return [
                    "   SIGNAL, ABER KEIN PLATZ",
                    "",
                    f"   {names} feuert, aber {self.max_open_positions} Positionen "
                    "sind das Limit.",
                    "   Nichts tun. Das Limit ist der Grund, warum es dich noch gibt.",
                ]
            return [
                "   SIGNAL",
                "",
                f"   {names}",
                "   run_gate check — das Gate entscheidet über die Größe.",
                "   Danach run_gate commit, BEVOR du kaufst.",
            ]
        return [
            "   NICHTS ZU TUN",
            "",
            "   Kein Markt feuert. Das ist der Normalfall.",
            "   Der nächstbeste Markt ist kein Signal.",
        ]

    def render(self) -> str:
        state = self.account
        drawdown = (state.drawdown * Decimal(100)).quantize(_PCT)
        mode = "ECHTGELD" if not state.paper else "PAPIER"
        change = state.equity - self.starting_capital
        lines = [
            "=" * 68,
            f"  {self.now:%Y-%m-%d %H:%M}   Modus {mode}   Regel {self.rule_name}",
            "=" * 68,
            "",
            f"  Kapital     {state.equity}   ({change:+}) seit Start",
            f"  Drawdown    {drawdown} % vom Höchststand",
            f"  Heute       {state.pnl_today:+}",
            f"  Positionen  {len(self.open_lines)} von {self.max_open_positions}",
            "",
        ]

        lines.append("  OFFEN")
        if not self.open_lines:
            lines.append("    keine")
        else:
            lines.extend(line.render() for line in self.open_lines)
        lines.append("")

        lines.append("  MÄRKTE")
        if not self.scan_rows:
            lines.append("    keine übergeben")
        else:
            lines.append(
                f"    {'Markt':<20}{'Kurs':>12}{'Auslöser':>12}{'Abstand':>10}  Stand"
            )
            for row in self.scan_rows:
                s = row.status
                if s.warmup_missing > 0:
                    lines.append(f"    {row.market:<20}{'zu wenig Historie':>46}")
                    continue
                level = str(s.trigger_level) if s.trigger_level is not None else "-"
                gap = "-"
                if s.distance is not None:
                    gap = f"{(s.distance * Decimal(100)).quantize(_PCT)} %"
                mark = "KAUFEN" if s.fires else "wartet"
                lines.append(
                    f"    {row.market:<20}{s.price!s:>12}{level:>12}{gap:>10}  {mark}"
                )
        lines.extend(["", "-" * 68, ""])
        lines.extend(self._verdict())
        lines.extend(["", "-" * 68])
        return "\n".join(lines)


def build_board(
    *,
    rule: Rule,
    markets: Mapping[str, Sequence[Candle]],
    account: AccountState,
    open_positions: Sequence[Mapping[str, str]],
    starting_capital: Decimal,
    max_open_positions: int,
    time_stop_bars: int | None,
    now: datetime,
    bar_duration: timedelta = timedelta(days=1),
) -> Board:
    """Alles einsammeln, nichts berechnen, was nicht belegt ist."""
    rows = scan(rule, markets, now=now, bar_duration=bar_duration)

    stale: list[tuple[str, int]] = []
    for name, candles in sorted(markets.items()):
        if not candles:
            continue
        age = now - candles[-1].at
        # Eine Kerze Verzug ist normal: die laufende ist noch nicht geschlossen.
        # Zwei sind es nicht mehr — dann fehlt ein Tag.
        if age > bar_duration * 2:
            stale.append((name, age.days))

    lines: list[OpenLine] = []
    for row in open_positions:
        held = _bars_since(row.get("opened_at", ""), now=now, bar_duration=bar_duration)
        left = None if (held is None or time_stop_bars is None) else time_stop_bars - held
        lines.append(
            OpenLine(
                ref=row.get("ref", "?"),
                symbol=row.get("symbol", "?"),
                entry=row.get("entry", "?"),
                stop=row.get("stop", "?"),
                target=row.get("target") or "keins (Ausstieg per Regel)",
                bars_held=held,
                bars_left=left,
            )
        )

    return Board(
        now=now,
        account=account,
        starting_capital=starting_capital,
        max_open_positions=max_open_positions,
        rule_name=rule.name,
        time_stop_bars=time_stop_bars,
        open_lines=tuple(lines),
        scan_rows=rows,
        stale_markets=tuple(stale),
    )


def _bars_since(
    raw: str, *, now: datetime, bar_duration: timedelta
) -> int | None:
    """Wie viele Kerzen seit dem Einstieg vergangen sind.

    Ein unlesbares Datum ergibt ``None``, nicht null: "seit null Kerzen offen"
    wäre eine Behauptung, und zwar die bequeme.
    """
    text = raw.strip()
    if not text:
        return None
    try:
        opened = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if opened.tzinfo is None:
        opened = opened.replace(tzinfo=now.tzinfo)
    seconds = bar_duration.total_seconds()
    if seconds <= 0:
        return None
    elapsed = (now - opened).total_seconds()
    return max(0, int(elapsed // seconds))


def summarise_for_day(board: Board, *, today: date) -> str:
    """Eine Zeile für Protokolle und Benachrichtigungen."""
    if board.overdue:
        return f"{today}: {len(board.overdue)} Position(en) zum Schliessen faellig"
    if board.firing:
        return f"{today}: Signal in {', '.join(r.market for r in board.firing)}"
    return f"{today}: kein Signal"
