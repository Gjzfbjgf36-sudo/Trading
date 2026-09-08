"""What your own decisions say.

The point of the journal is this report. After enough closed trades it answers
the only question that matters about a rule you are running:

    is this better than nothing, or am I paying to find out?

It is deliberately unflattering. Small samples get told they are small samples
rather than given a verdict, because the most expensive mistake here is
concluding you have an edge from twelve trades.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..decide.journal import ExitReason, Journal
from ..domain.types import ZERO

#: Below this many closed trades, no performance statement is meaningful. It is
#: not a rule of thumb dressed up as maths — it is the point below which the
#: numbers are noise and reporting them invites a false conclusion.
MIN_TRADES_FOR_A_VERDICT = 30


@dataclass(frozen=True, slots=True)
class JournalReview:
    closed: int
    wins: int
    losses: int
    gross_win: Decimal
    gross_loss: Decimal
    net: Decimal
    deviations: int
    largest_loss: Decimal
    open_count: int

    @property
    def win_rate(self) -> Decimal | None:
        if self.closed == 0:
            return None
        return (Decimal(self.wins) / Decimal(self.closed)).quantize(Decimal("0.01"))

    @property
    def average_win(self) -> Decimal | None:
        if not self.wins:
            return None
        return (self.gross_win / Decimal(self.wins)).quantize(Decimal("0.01"))

    @property
    def average_loss(self) -> Decimal | None:
        if not self.losses:
            return None
        return (self.gross_loss / Decimal(self.losses)).quantize(Decimal("0.01"))

    @property
    def expectancy(self) -> Decimal | None:
        """Average result per trade. The number that decides everything."""
        if self.closed == 0:
            return None
        return (self.net / Decimal(self.closed)).quantize(Decimal("0.01"))

    @property
    def payoff_ratio(self) -> Decimal | None:
        """Average win divided by average loss."""
        win, loss = self.average_win, self.average_loss
        if win is None or loss is None or loss <= ZERO:
            return None
        return (win / loss).quantize(Decimal("0.01"))

    @property
    def sufficient(self) -> bool:
        return self.closed >= MIN_TRADES_FOR_A_VERDICT


def collect(journal: Journal) -> JournalReview:
    rows = journal.conn.execute(
        "SELECT pnl, followed_plan FROM commitments WHERE closed_at IS NOT NULL"
    ).fetchall()
    open_count = journal.conn.execute(
        "SELECT COUNT(*) AS n FROM commitments WHERE closed_at IS NULL"
    ).fetchone()["n"]

    wins = losses = deviations = 0
    gross_win = gross_loss = net = ZERO
    largest_loss = ZERO
    for row in rows:
        pnl = Decimal(row["pnl"])
        net += pnl
        if pnl > ZERO:
            wins += 1
            gross_win += pnl
        elif pnl < ZERO:
            losses += 1
            gross_loss += -pnl
            largest_loss = max(largest_loss, -pnl)
        if not row["followed_plan"]:
            deviations += 1

    return JournalReview(
        closed=len(rows),
        wins=wins,
        losses=losses,
        gross_win=gross_win,
        gross_loss=gross_loss,
        net=net,
        deviations=deviations,
        largest_loss=largest_loss,
        open_count=open_count,
    )


def review_journal(journal: Journal) -> str:
    """Plain-text review, written to be read rather than admired."""
    r = collect(journal)
    if r.closed == 0:
        return (
            f"Noch keine abgeschlossenen Trades ({r.open_count} offen).\n"
            "Komm nach 30 wieder. Vorher gibt es nichts zu schliessen."
        )

    lines = [
        f"Abgeschlossen       {r.closed}   (offen: {r.open_count})",
        f"Trefferquote        {r.win_rate}",
        f"Ø Gewinn            {r.average_win}",
        f"Ø Verlust           {r.average_loss}",
        f"Payoff-Verhältnis   {r.payoff_ratio}",
        f"Grösster Verlust    {r.largest_loss}",
        f"Netto               {r.net}",
        f"Erwartungswert      {r.expectancy}",
        f"Planabweichungen    {r.deviations}",
        "",
    ]

    if not r.sufficient:
        lines.append(
            f"ZU WENIG DATEN. {r.closed} von {MIN_TRADES_FOR_A_VERDICT} Trades. "
            "Was diese Zahlen zeigen, liegt im Bereich des Zufalls. Ändere die "
            "Regel nicht deswegen — genau so wird aus einer Regel ein Zufallslauf."
        )
        return "\n".join(lines)

    expectancy = r.expectancy or ZERO
    if expectancy > ZERO:
        lines.append(
            "Erwartungswert positiv über eine brauchbare Stichprobe. Das ist ein "
            "Hinweis, kein Beweis: Regel unverändert lassen und weiter aufzeichnen. "
            "Die nächsten dreissig Trades sind der eigentliche Test."
        )
    else:
        lines.append(
            "Erwartungswert negativ. Nach dieser Datenlage kostet die Regel Geld. "
            "Die ehrlichen Optionen sind aufhören oder mit einer anderen Idee "
            "zurück in die Forschung — nicht die Parameter drehen, bis die "
            "Vergangenheit besser aussieht."
        )

    if r.deviations:
        share = (Decimal(r.deviations) / Decimal(r.closed)).quantize(Decimal("0.01"))
        lines.append(
            f"\n{r.deviations} von {r.closed} Ausstiegen ({share}) wichen vom Plan ab. "
            "Vergleiche sie mit denen, die ihm folgten: Sind die Abweichungen "
            "schlechter, ist die Disziplin dein Edge. Sind sie besser, ist die Regel "
            "falsch und gehört bewusst neu geschrieben — nicht im Moment übergangen."
        )
    return "\n".join(lines)


def source_comparison(journal: Journal) -> dict[str, str]:
    """Results grouped by which source produced the signal.

    The reason this exists: a rule and an opinion can sit side by side in the
    same journal, sized by the same limits and closed under the same
    discipline. After enough trades the comparison answers a question that no
    amount of arguing can — did the opinion add anything, or did it cost?

    An opinion that beats the rule over a usable sample is evidence worth
    having. One that loses to it is the cheapest lesson available.
    """
    rows = journal.conn.execute(
        "SELECT signal_source, pnl FROM commitments WHERE closed_at IS NOT NULL"
    ).fetchall()
    grouped: dict[str, list[Decimal]] = {}
    for row in rows:
        grouped.setdefault(row["signal_source"], []).append(Decimal(row["pnl"]))

    out: dict[str, str] = {}
    for source, values in sorted(grouped.items()):
        total = sum(values, start=ZERO)
        wins = sum(1 for v in values if v > ZERO)
        average = (total / Decimal(len(values))).quantize(Decimal("0.01"))
        note = "" if len(values) >= MIN_TRADES_FOR_A_VERDICT else "  (zu wenig Daten)"
        out[source] = (
            f"{len(values)} Trades, {wins} Gewinner, netto {total}, "
            f"Ø {average} pro Trade{note}"
        )
    return out


def deviation_comparison(journal: Journal) -> dict[str, str]:
    """Results of planned exits versus discretionary ones.

    Usually the most useful table in the whole system: it measures the cost of
    overriding your own rule, in your own money.
    """
    rows = journal.conn.execute(
        "SELECT pnl, exit_reason FROM commitments WHERE closed_at IS NOT NULL"
    ).fetchall()
    planned = [Decimal(r["pnl"]) for r in rows if r["exit_reason"] != str(ExitReason.DISCRETIONARY)]
    ad_hoc = [Decimal(r["pnl"]) for r in rows if r["exit_reason"] == str(ExitReason.DISCRETIONARY)]

    def summarise(values: list[Decimal]) -> str:
        if not values:
            return "none"
        total = sum(values, start=ZERO)
        average = (total / Decimal(len(values))).quantize(Decimal("0.01"))
        return f"{len(values)} trades, net {total}, avg {average}"

    return {"followed_plan": summarise(planned), "discretionary": summarise(ad_hoc)}
