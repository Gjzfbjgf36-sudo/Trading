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
            f"No closed trades yet ({r.open_count} open).\n"
            "Come back after 30. Before then there is nothing to conclude."
        )

    lines = [
        f"Closed trades      {r.closed}   (open: {r.open_count})",
        f"Win rate           {r.win_rate}",
        f"Average win        {r.average_win}",
        f"Average loss       {r.average_loss}",
        f"Payoff ratio       {r.payoff_ratio}",
        f"Largest loss       {r.largest_loss}",
        f"Net                {r.net}",
        f"Expectancy/trade   {r.expectancy}",
        f"Plan deviations    {r.deviations}",
        "",
    ]

    if not r.sufficient:
        lines.append(
            f"NOT ENOUGH DATA. {r.closed} of {MIN_TRADES_FOR_A_VERDICT} trades. "
            "Whatever these numbers show is within the range of chance. Do not "
            "change the rule on the strength of them — that is how a rule becomes "
            "a random walk."
        )
        return "\n".join(lines)

    expectancy = r.expectancy or ZERO
    if expectancy > ZERO:
        lines.append(
            "Expectancy is positive over a usable sample. That is evidence, not "
            "proof: keep the rule unchanged and keep recording. The next thirty "
            "trades are the actual test."
        )
    else:
        lines.append(
            "Expectancy is negative. On this evidence the rule costs money. The "
            "honest options are to stop, or to go back to research with a "
            "different idea — not to adjust the parameters until the past looks "
            "better."
        )

    if r.deviations:
        share = (Decimal(r.deviations) / Decimal(r.closed)).quantize(Decimal("0.01"))
        lines.append(
            f"\n{r.deviations} of {r.closed} exits ({share}) departed from the plan. "
            "Compare those against the ones that followed it: if the deviations are "
            "worse, the discipline is the edge. If they are better, the rule is wrong "
            "and should be rewritten deliberately rather than overridden in the moment."
        )
    return "\n".join(lines)


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
