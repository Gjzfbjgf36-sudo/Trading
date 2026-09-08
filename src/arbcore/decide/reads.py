"""Chart reads, recorded so they can be held against their outcomes.

I have no memory between sessions. Without a record I would give an assessment
today, forget it, and give a differently-worded one tomorrow on the same chart —
and neither of us could tell whether any of them were worth anything.

So every read is written down: what was observed (checkable), what the rule
said (checkable), what I concluded (not checkable), and how strongly. When the
resulting trade closes, the outcome attaches to it. After enough of them the
calibration report answers the only question that matters about my opinions:
are they better than the rule, or are they noise with good grammar?

Reads that were never acted on are counted but never scored. Scoring only the
ones that turned into trades would quietly grade me on a sample I helped select.
"""

from __future__ import annotations

import enum
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from ..domain.types import ZERO, to_decimal

_SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS chart_reads (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ref         TEXT    NOT NULL UNIQUE,
    at          TEXT    NOT NULL,
    symbol      TEXT    NOT NULL,
    timeframe   TEXT    NOT NULL,
    observed    TEXT    NOT NULL,
    rule_says   TEXT    NOT NULL,
    claude_view TEXT    NOT NULL,
    conviction  TEXT    NOT NULL,
    entry       TEXT,
    stop        TEXT,
    target      TEXT,
    acted       INTEGER NOT NULL DEFAULT 0,
    -- An armed read: the condition that must occur before it is actionable.
    -- Written before the fact, so it cannot be rewritten to match what happened.
    trigger_condition TEXT,
    triggered_at      TEXT
);
"""


#: Words that name something on a chart rather than an impression of it. A
#: usable trigger contains a number ("closes above 60000") or one of these
#: ("next bar closes green"). This is a heuristic filter, not a proof of
#: checkability — it catches the common failure, which is a condition phrased
#: so loosely that it can be declared met or unmet after the fact.
_CONCRETE_TERMS: frozenset[str] = frozenset(
    {
        "schliesst", "schließt", "closes", "close", "schluss",
        "kerze", "kerzen", "balken", "bar", "bars", "candle",
        "hoch", "tief", "high", "low",
        "ueber", "über", "unter", "above", "below",
        "gruen", "grün", "rot", "green", "red",
        "bricht", "breaks", "berührt", "beruehrt", "touches",
        "eröffnet", "eroeffnet", "opens",
    }
)


class Conviction(enum.StrEnum):
    """How strongly the read was stated. The axis calibration is measured on."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class RuleVerdict(enum.StrEnum):
    """What the configured rule says, independently of any opinion."""

    FIRES = "FIRES"
    DOES_NOT_FIRE = "DOES_NOT_FIRE"
    #: The screenshot did not contain what the rule needs. Not a soft no.
    NOT_DETERMINABLE = "NOT_DETERMINABLE"


@dataclass(frozen=True, slots=True)
class ChartRead:
    """One assessment of one chart at one time."""

    ref: str
    symbol: str
    timeframe: str
    #: What is factually visible. Checkable against the screenshot.
    observed: str
    #: What the rule says. Checkable.
    rule_says: RuleVerdict
    #: What I concluded. Not checkable — which is why it is scored.
    claude_view: str
    conviction: Conviction
    entry: Decimal | None = None
    stop: Decimal | None = None
    target: Decimal | None = None
    #: An observable event that must occur first, e.g. "next daily bar closes
    #: above 60000". Written before it happens, which is what separates it from
    #: a prediction: the condition either occurs or it does not, and nobody has
    #: to agree about it afterwards. ``None`` means the read applies now.
    trigger_condition: str | None = None

    def __post_init__(self) -> None:
        for name in ("entry", "stop", "target"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, to_decimal(value))
        if len(self.observed.strip()) < 10:
            raise ValueError(
                "record what is actually visible on the chart; without it the "
                "read cannot be checked against the screenshot later"
            )
        if len(self.claude_view.strip()) < 10:
            raise ValueError("record the conclusion in a sentence, or there is nothing to score")
        if self.trigger_condition is not None:
            condition = self.trigger_condition.strip()
            words = {w.strip(".,;:!?()").lower() for w in condition.split()}
            concrete = any(char.isdigit() for char in condition) or bool(
                words & _CONCRETE_TERMS
            )
            if len(condition) < 10 or not concrete:
                raise ValueError(
                    f"the trigger must name something observable, e.g. 'next daily "
                    f"bar closes above 60000' — a price, a level, or a chart event. "
                    f"{condition!r} can be declared met or unmet after the fact, "
                    f"which is exactly what writing it down beforehand is meant to "
                    f"prevent"
                )

    @property
    def is_armed(self) -> bool:
        """True when this waits on a condition rather than applying now."""
        return self.trigger_condition is not None


@dataclass(slots=True)
class ReadLog:
    """Durable record of chart reads."""

    path: str | Path
    _conn: sqlite3.Connection | None = None

    def __post_init__(self) -> None:
        self.path = str(self.path)
        parent = Path(self.path).parent
        if str(parent) not in ("", "."):
            parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        with closing(conn.cursor()) as cur:
            cur.executescript(_SCHEMA)
        conn.commit()
        self._conn = conn

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("read log is closed")
        return self._conn

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()
        self._conn = None

    def record(self, read: ChartRead, *, now: datetime) -> None:
        """Write a read. A ref is used once, so a read cannot be revised later."""
        try:
            self.conn.execute(
                "INSERT INTO chart_reads"
                "(ref, at, symbol, timeframe, observed, rule_says, claude_view,"
                " conviction, entry, stop, target, trigger_condition)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    read.ref,
                    now.isoformat(),
                    read.symbol,
                    read.timeframe,
                    read.observed.strip(),
                    str(read.rule_says),
                    read.claude_view.strip(),
                    str(read.conviction),
                    str(read.entry) if read.entry is not None else None,
                    str(read.stop) if read.stop is not None else None,
                    str(read.target) if read.target is not None else None,
                    read.trigger_condition.strip() if read.trigger_condition else None,
                ),
            )
        except sqlite3.IntegrityError:
            raise ValueError(
                f"a read already exists for {read.ref!r}; reads are not rewritten"
            ) from None
        self.conn.commit()

    def mark_triggered(self, ref: str, *, now: datetime) -> None:
        """Record that the condition actually occurred.

        Separate from acting on it: a condition can trigger and still be
        refused by the gate, and both facts are worth keeping.
        """
        row = self.conn.execute(
            "SELECT trigger_condition, triggered_at FROM chart_reads WHERE ref = ?", (ref,)
        ).fetchone()
        if row is None:
            raise KeyError(f"no read recorded for {ref!r}")
        if row["trigger_condition"] is None:
            raise ValueError(f"{ref!r} has no trigger condition; there is nothing to trigger")
        if row["triggered_at"] is not None:
            raise ValueError(f"{ref!r} already triggered at {row['triggered_at']}")
        self.conn.execute(
            "UPDATE chart_reads SET triggered_at = ? WHERE ref = ?", (now.isoformat(), ref)
        )
        self.conn.commit()

    def armed(self) -> tuple[dict[str, str], ...]:
        """Reads still waiting on their condition."""
        rows = self.conn.execute(
            "SELECT * FROM chart_reads"
            " WHERE trigger_condition IS NOT NULL AND triggered_at IS NULL"
            " ORDER BY at DESC"
        ).fetchall()
        return tuple(
            {k: ("" if r[k] is None else str(r[k])) for k in r.keys()} for r in rows
        )

    def mark_acted(self, ref: str) -> None:
        self.conn.execute("UPDATE chart_reads SET acted = 1 WHERE ref = ?", (ref,))
        self.conn.commit()

    def recent(self, limit: int = 10) -> tuple[dict[str, str], ...]:
        rows = self.conn.execute(
            "SELECT * FROM chart_reads ORDER BY at DESC LIMIT ?", (limit,)
        ).fetchall()
        return tuple(
            {k: ("" if r[k] is None else str(r[k])) for k in r.keys()} for r in rows
        )

    def counts(self) -> tuple[int, int, int, int]:
        """Total reads, acted on, armed with a condition, and triggered.

        The gap between armed and triggered is informative on its own: a read
        whose condition never occurs was not wrong, it simply never applied,
        and counting it as a miss would be as unfair as counting it as a hit.
        """
        row = self.conn.execute(
            "SELECT COUNT(*) AS total,"
            " COALESCE(SUM(acted), 0) AS acted,"
            " COALESCE(SUM(trigger_condition IS NOT NULL), 0) AS armed,"
            " COALESCE(SUM(triggered_at IS NOT NULL), 0) AS triggered"
            " FROM chart_reads"
        ).fetchone()
        return int(row["total"]), int(row["acted"]), int(row["armed"]), int(row["triggered"])


def export_reads(log: ReadLog) -> list[dict[str, object]]:
    rows = log.conn.execute("SELECT * FROM chart_reads ORDER BY id").fetchall()
    return [{k: r[k] for k in r.keys()} for r in rows]


def import_reads(log: ReadLog, rows: list[dict[str, object]]) -> tuple[int, int]:
    """Restore reads. Existing refs are skipped, never overwritten."""
    columns = [
        "ref", "at", "symbol", "timeframe", "observed", "rule_says", "claude_view",
        "conviction", "entry", "stop", "target", "acted", "trigger_condition",
        "triggered_at",
    ]
    imported = skipped = 0
    for row in rows:
        try:
            log.conn.execute(
                f"INSERT INTO chart_reads({', '.join(columns)})"
                f" VALUES ({', '.join('?' * len(columns))})",
                [row.get(name) for name in columns],
            )
            imported += 1
        except sqlite3.IntegrityError:
            skipped += 1
    log.conn.commit()
    return imported, skipped


def calibration(log: ReadLog, journal_path: str | Path) -> str:
    """Were the confident reads better than the hesitant ones?

    Joins reads to the outcomes of the trades they produced. A read that never
    became a trade has no outcome and is reported separately rather than
    quietly dropped — the unacted ones are exactly where a flattering sample
    would come from.
    """
    total, acted, armed, triggered = log.counts()
    if total == 0:
        return "Noch keine Chart-Einschätzungen aufgezeichnet."

    log.conn.execute("ATTACH DATABASE ? AS j", (str(journal_path),))
    try:
        rows = log.conn.execute(
            "SELECT r.conviction AS conviction, c.pnl AS pnl"
            " FROM chart_reads r JOIN j.commitments c ON c.ref = r.ref"
            " WHERE c.closed_at IS NOT NULL"
        ).fetchall()
    finally:
        log.conn.execute("DETACH DATABASE j")

    lines = [
        f"Einschätzungen gesamt   {total}",
        f"davon mit Bedingung     {armed}   (davon eingetreten: {triggered})",
        f"davon gehandelt         {acted}",
        f"davon abgeschlossen     {len(rows)}",
        "",
    ]
    if not rows:
        lines.append(
            "Noch kein abgeschlossener Trade aus einer Einschätzung. Bis dahin "
            "ist über ihre Qualität nichts bekannt."
        )
        return "\n".join(lines)

    grouped: dict[str, list[Decimal]] = {}
    for row in rows:
        grouped.setdefault(row["conviction"], []).append(Decimal(row["pnl"]))

    lines.append("Nach Überzeugung — waren die sicheren Einschätzungen besser?")
    for level in (Conviction.HIGH, Conviction.MEDIUM, Conviction.LOW):
        values = grouped.get(str(level), [])
        if not values:
            lines.append(f"  {level:<8} keine")
            continue
        total_pnl = sum(values, start=ZERO)
        wins = sum(1 for v in values if v > ZERO)
        average = (total_pnl / Decimal(len(values))).quantize(Decimal("0.01"))
        lines.append(
            f"  {level:<8} {len(values)} Trades, {wins} Gewinner, Ø {average} pro Trade"
        )

    if len(rows) < 30:
        lines += [
            "",
            f"{len(rows)} von 30 Trades. Zu wenig für ein Urteil — die Verteilung "
            "über die Stufen ist bei dieser Menge Zufall.",
        ]
    else:
        high = grouped.get(str(Conviction.HIGH), [])
        low = grouped.get(str(Conviction.LOW), [])
        if high and low:
            high_avg = sum(high, start=ZERO) / Decimal(len(high))
            low_avg = sum(low, start=ZERO) / Decimal(len(low))
            verdict = (
                "Hohe Überzeugung schnitt besser ab — die Einschätzungen tragen Information."
                if high_avg > low_avg
                else "Hohe Überzeugung schnitt NICHT besser ab. Dann ist die Überzeugung "
                "kein Signal, sondern nur Tonfall."
            )
            lines += ["", verdict]
    return "\n".join(lines)
