"""Account state derived from the journal.

The risk checks are only as good as the equity, peak and daily-loss figures
they are given. Typing those on every command is an obvious place to get one
wrong — and a wrong equity silently changes the position size, which is the one
number that must never be wrong.

So they are computed from what was actually recorded:

    equity  = starting capital + realised P/L
    peak    = the high-water mark of that curve
    today   = P/L of positions closed today
    open    = commitments with no outcome yet

Nothing here can be overridden from the command line.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from ..domain.types import ZERO
from .gate import AccountState
from .journal import Journal
from .settings import DecideSettings


@dataclass(frozen=True, slots=True)
class EquityPoint:
    at: datetime
    equity: Decimal


class AccountLedger:
    """Reconstructs account state from recorded outcomes."""

    def __init__(self, journal: Journal, settings: DecideSettings) -> None:
        self._journal = journal
        self._settings = settings

    def equity_curve(self) -> tuple[EquityPoint, ...]:
        """Equity after each closed trade, in the order they were closed."""
        rows = self._journal.conn.execute(
            "SELECT closed_at, pnl FROM commitments"
            " WHERE closed_at IS NOT NULL ORDER BY closed_at, id"
        ).fetchall()
        equity = self._settings.starting_capital
        points: list[EquityPoint] = []
        for row in rows:
            equity += Decimal(row["pnl"])
            points.append(
                EquityPoint(at=datetime.fromisoformat(row["closed_at"]), equity=equity)
            )
        return tuple(points)

    def state(self, *, today: date) -> AccountState:
        """Current account state.

        ``peak`` starts at the starting capital, so a losing account is in
        drawdown from day one rather than from its first profit. That is the
        conservative reading and the one that stops a bad start early.
        """
        curve = self.equity_curve()
        equity = curve[-1].equity if curve else self._settings.starting_capital
        peak = max(
            [self._settings.starting_capital] + [point.equity for point in curve]
        )
        pnl_today = sum(
            (
                point.equity - (curve[i - 1].equity if i else self._settings.starting_capital)
                for i, point in enumerate(curve)
                if point.at.date() == today
            ),
            start=ZERO,
        )
        open_count = self._journal.conn.execute(
            "SELECT COUNT(*) AS n FROM commitments WHERE closed_at IS NULL"
        ).fetchone()["n"]

        return AccountState(
            equity=equity,
            peak_equity=peak,
            pnl_today=pnl_today,
            open_positions=open_count,
            paper=not self._settings.real_money,
        )

    def summary(self, *, today: date) -> str:
        state = self.state(today=today)
        drawdown = (state.drawdown * Decimal(100)).quantize(Decimal("0.01"))
        mode = "ECHTGELD" if not state.paper else "Papier"
        return "\n".join(
            [
                f"Modus              {mode}",
                f"Startkapital       {self._settings.starting_capital}",
                f"Aktuelles Kapital  {state.equity}",
                f"Höchststand        {state.peak_equity}",
                f"Drawdown           {drawdown}%",
                f"P/L heute          {state.pnl_today}",
                f"Offene Positionen  {state.open_positions}",
            ]
        )
