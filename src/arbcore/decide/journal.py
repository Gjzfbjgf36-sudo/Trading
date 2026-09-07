"""Pre-commitment journal.

Most people who trade have no record of *why* they did anything. That is why
they cannot tell whether they have an edge, and why they cannot improve: there
is nothing to be right or wrong about afterwards.

This enforces the order of operations:

    1. Before entering, you write down the thesis, the exit, and what would
       prove you wrong. The entry is not permitted until you have.
    2. The plan is then frozen. You may close a trade early, but you may not
       rewrite what you predicted.
    3. The outcome is recorded against the original plan.

Point 2 is the whole value. A plan you can edit after the fact is a diary of
your memory, not of your decisions.
"""

from __future__ import annotations

import enum
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from ..domain.types import ZERO, Side, to_decimal

_QUANT = Decimal("0.00000001")

_SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS commitments (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ref            TEXT    NOT NULL UNIQUE,
    opened_at      TEXT    NOT NULL,
    symbol         TEXT    NOT NULL,
    side           TEXT    NOT NULL,
    entry          TEXT    NOT NULL,
    stop           TEXT    NOT NULL,
    target         TEXT,
    quantity       TEXT    NOT NULL,
    risk_amount    TEXT    NOT NULL,
    thesis         TEXT    NOT NULL,
    invalidation   TEXT    NOT NULL,
    signal_source  TEXT    NOT NULL,
    paper          INTEGER NOT NULL,
    -- Outcome columns, written once at close. Never updated afterwards.
    closed_at      TEXT,
    exit_price     TEXT,
    pnl            TEXT,
    exit_reason    TEXT,
    followed_plan  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_commitments_open ON commitments(closed_at);
"""


class ExitReason(enum.StrEnum):
    """Why the position was closed. ``DISCRETIONARY`` is the one to watch."""

    STOP_HIT = "STOP_HIT"
    TARGET_HIT = "TARGET_HIT"
    SIGNAL_EXIT = "SIGNAL_EXIT"
    INVALIDATED = "INVALIDATED"
    #: Closed for a reason that was not in the plan. Tracked separately,
    #: because deviation is usually where the losses are.
    DISCRETIONARY = "DISCRETIONARY"


class PlanIncomplete(ValueError):
    """Raised when a commitment is missing something it must state up front."""


class AlreadyClosed(RuntimeError):
    """Raised on an attempt to rewrite a recorded outcome."""


@dataclass(frozen=True, slots=True)
class Commitment:
    """What you said you would do, before you did it."""

    ref: str
    symbol: str
    side: Side
    entry: Decimal
    stop: Decimal
    quantity: Decimal
    risk_amount: Decimal
    #: Why you expect this to work. One or two sentences, in your words.
    thesis: str
    #: What would tell you the thesis is wrong — an observable, not a feeling.
    invalidation: str
    #: Which rule produced the signal, so discretionary trades are visible.
    signal_source: str
    target: Decimal | None = None
    paper: bool = True

    def __post_init__(self) -> None:
        for name in ("entry", "stop", "quantity", "risk_amount"):
            object.__setattr__(self, name, to_decimal(getattr(self, name)))
        if self.target is not None:
            object.__setattr__(self, "target", to_decimal(self.target))
        if self.quantity <= ZERO:
            raise PlanIncomplete("quantity must be > 0")
        if len(self.thesis.strip()) < 15:
            raise PlanIncomplete(
                "state the thesis in a sentence. 'looks good' is not a thesis, "
                "and in three months it will tell you nothing"
            )
        if len(self.invalidation.strip()) < 15:
            raise PlanIncomplete(
                "state what would prove this wrong, as something observable. "
                "Without it you cannot be wrong, only disappointed"
            )
        if not self.signal_source.strip():
            raise PlanIncomplete("record which rule produced this signal")

    @property
    def risk_reward(self) -> Decimal | None:
        """Reward-to-risk against the stated target. ``None`` without a target."""
        if self.target is None:
            return None
        risk = abs(self.entry - self.stop)
        if risk <= ZERO:
            return None
        return (abs(self.target - self.entry) / risk).quantize(Decimal("0.01"))


@dataclass(frozen=True, slots=True)
class Outcome:
    """What actually happened. Written once."""

    exit_price: Decimal
    exit_reason: ExitReason
    #: Whether the exit matched the plan. Computed, not self-assessed.
    followed_plan: bool
    pnl: Decimal


@dataclass(slots=True)
class Journal:
    """Durable record of commitments and outcomes."""

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
            raise RuntimeError("journal is closed")
        return self._conn

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()
        self._conn = None

    # --- writing -------------------------------------------------------
    def commit_plan(self, commitment: Commitment, *, now: datetime) -> None:
        """Record the plan. Fails if this reference already exists.

        Re-committing a reference is refused rather than updated: that is the
        mechanism that stops a plan being quietly rewritten to match the
        outcome.
        """
        try:
            self.conn.execute(
                "INSERT INTO commitments"
                "(ref, opened_at, symbol, side, entry, stop, target, quantity,"
                " risk_amount, thesis, invalidation, signal_source, paper)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    commitment.ref,
                    now.isoformat(),
                    commitment.symbol,
                    str(commitment.side),
                    str(commitment.entry),
                    str(commitment.stop),
                    str(commitment.target) if commitment.target is not None else None,
                    str(commitment.quantity),
                    str(commitment.risk_amount),
                    commitment.thesis.strip(),
                    commitment.invalidation.strip(),
                    commitment.signal_source.strip(),
                    1 if commitment.paper else 0,
                ),
            )
        except sqlite3.IntegrityError:
            raise AlreadyClosed(
                f"a plan already exists for {commitment.ref!r}; plans are not rewritten"
            ) from None
        self.conn.commit()

    def record_outcome(
        self,
        ref: str,
        *,
        exit_price: Decimal,
        exit_reason: ExitReason,
        now: datetime,
        fee_rate: Decimal = Decimal("0.0026"),
    ) -> Outcome:
        """Close a commitment against its original plan."""
        row = self.conn.execute(
            "SELECT * FROM commitments WHERE ref = ?", (ref,)
        ).fetchone()
        if row is None:
            raise KeyError(f"no commitment recorded for {ref!r}")
        if row["closed_at"] is not None:
            raise AlreadyClosed(f"{ref!r} is already closed; outcomes are written once")

        entry = Decimal(row["entry"])
        quantity = Decimal(row["quantity"])
        side = Side(row["side"])
        price = to_decimal(exit_price)

        gross = (price - entry) * quantity
        if side is Side.SELL:
            gross = -gross
        fees = ((entry + price) * quantity * to_decimal(fee_rate)).quantize(_QUANT)
        pnl = (gross - fees).quantize(_QUANT)
        followed = exit_reason is not ExitReason.DISCRETIONARY

        self.conn.execute(
            "UPDATE commitments SET closed_at = ?, exit_price = ?, pnl = ?,"
            " exit_reason = ?, followed_plan = ? WHERE ref = ?",
            (
                now.isoformat(),
                str(price),
                str(pnl),
                str(exit_reason),
                1 if followed else 0,
                ref,
            ),
        )
        self.conn.commit()
        return Outcome(
            exit_price=price, exit_reason=exit_reason, followed_plan=followed, pnl=pnl
        )

    # --- reading -------------------------------------------------------
    def open_positions(self) -> tuple[dict[str, str], ...]:
        rows = self.conn.execute(
            "SELECT ref, symbol, side, entry, stop, quantity, thesis"
            " FROM commitments WHERE closed_at IS NULL ORDER BY opened_at"
        ).fetchall()
        return tuple({k: str(r[k]) for k in r.keys()} for r in rows)

    def realised_pnl(self) -> Decimal:
        row = self.conn.execute(
            "SELECT pnl FROM commitments WHERE closed_at IS NOT NULL"
        ).fetchall()
        return sum((Decimal(r["pnl"]) for r in row), start=ZERO)

    def export(self) -> str:
        rows = self.conn.execute("SELECT * FROM commitments ORDER BY id").fetchall()
        return json.dumps([{k: r[k] for k in r.keys()} for r in rows], indent=2)
