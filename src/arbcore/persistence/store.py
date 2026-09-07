"""Durable store (SQLite).

Everything that could be needed to explain a decision, reconstruct exposure
after a crash, or answer "what did we actually do" is written here. Two rules
shape the schema:

* **Append-only where it matters.** Decisions and fills are never updated in
  place; a corrected view is a new row, so the record of what we believed at
  the time survives.
* **No secrets.** Only typed, enumerated fields are persisted. There is no
  free-form capture of request or response payloads, because that is how
  credentials end up in a database.

SQLite is chosen for a single-operator system: it is durable, transactional,
has no server to misconfigure, and backs up by copying a file.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..domain.decision import Decision
from ..execution.order import Order

SCHEMA_VERSION = 1

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS decisions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id    TEXT    NOT NULL,
    verdict       TEXT    NOT NULL,
    at            TEXT    NOT NULL,
    reasons       TEXT    NOT NULL,
    checks_json   TEXT    NOT NULL,
    context_json  TEXT    NOT NULL,
    session_id    TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_decisions_subject ON decisions(subject_id);
CREATE INDEX IF NOT EXISTS idx_decisions_verdict ON decisions(verdict);

CREATE TABLE IF NOT EXISTS orders (
    client_id       TEXT PRIMARY KEY,
    opportunity_id  TEXT NOT NULL,
    venue           TEXT NOT NULL,
    asset           TEXT NOT NULL,
    side            TEXT NOT NULL,
    quantity        TEXT NOT NULL,
    decision_price  TEXT NOT NULL,
    limit_price     TEXT NOT NULL,
    state           TEXT NOT NULL,
    filled_quantity TEXT NOT NULL,
    average_price   TEXT,
    fees_paid       TEXT NOT NULL,
    reject_reason   TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    session_id      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_orders_state ON orders(state);
CREATE INDEX IF NOT EXISTS idx_orders_opportunity ON orders(opportunity_id);

CREATE TABLE IF NOT EXISTS fills (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id  TEXT NOT NULL REFERENCES orders(client_id),
    quantity   TEXT NOT NULL,
    price      TEXT NOT NULL,
    fee        TEXT NOT NULL,
    at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    at         TEXT NOT NULL,
    kind       TEXT NOT NULL,
    severity   TEXT NOT NULL,
    detail     TEXT NOT NULL,
    session_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_kind ON events(kind);

CREATE TABLE IF NOT EXISTS balances (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    at         TEXT NOT NULL,
    venue      TEXT NOT NULL,
    asset      TEXT NOT NULL,
    free       TEXT NOT NULL,
    reserved   TEXT NOT NULL,
    session_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id   TEXT PRIMARY KEY,
    started_at   TEXT NOT NULL,
    environment  TEXT NOT NULL,
    trading_mode TEXT NOT NULL,
    config_json  TEXT NOT NULL,
    ended_at     TEXT,
    summary_json TEXT
);
"""


@dataclass(frozen=True, slots=True)
class OpenOrderRow:
    """An order the database believes is still live — a recovery input."""

    client_id: str
    opportunity_id: str
    venue: str
    state: str
    filled_quantity: Decimal


class Store:
    """Thin, explicit persistence layer. No ORM, no lazy magic."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        with closing(self._conn.cursor()) as cur:
            cur.executescript(_SCHEMA)
            cur.execute("SELECT COUNT(*) AS n FROM schema_version")
            if cur.fetchone()["n"] == 0:
                cur.execute("INSERT INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION,))
        self._conn.commit()

    # --- lifecycle -----------------------------------------------------
    def close(self) -> None:
        self._conn.commit()
        self._conn.close()

    def start_session(
        self,
        session_id: str,
        *,
        started_at: datetime,
        environment: str,
        trading_mode: str,
        config: dict[str, Any],
    ) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO sessions"
            "(session_id, started_at, environment, trading_mode, config_json)"
            " VALUES (?, ?, ?, ?, ?)",
            (session_id, started_at.isoformat(), environment, trading_mode, json.dumps(config)),
        )
        self._conn.commit()

    def end_session(
        self, session_id: str, *, ended_at: datetime, summary: dict[str, Any]
    ) -> None:
        self._conn.execute(
            "UPDATE sessions SET ended_at = ?, summary_json = ? WHERE session_id = ?",
            (ended_at.isoformat(), json.dumps(summary), session_id),
        )
        self._conn.commit()

    # --- writes --------------------------------------------------------
    def record_decision(self, decision: Decision, *, session_id: str) -> None:
        payload = decision.as_dict()
        self._conn.execute(
            "INSERT INTO decisions"
            "(subject_id, verdict, at, reasons, checks_json, context_json, session_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                payload["subject_id"],
                payload["verdict"],
                payload["at"],
                ",".join(payload["reasons"]),
                json.dumps(payload["checks"]),
                json.dumps(payload["context"]),
                session_id,
            ),
        )

    def record_order(self, order: Order, *, at: datetime, session_id: str) -> None:
        average = order.average_price
        self._conn.execute(
            "INSERT INTO orders"
            "(client_id, opportunity_id, venue, asset, side, quantity, decision_price,"
            " limit_price, state, filled_quantity, average_price, fees_paid, reject_reason,"
            " updated_at, session_id)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(client_id) DO UPDATE SET"
            "  state=excluded.state, filled_quantity=excluded.filled_quantity,"
            "  average_price=excluded.average_price, fees_paid=excluded.fees_paid,"
            "  reject_reason=excluded.reject_reason, updated_at=excluded.updated_at",
            (
                order.client_id,
                order.opportunity_id,
                str(order.venue),
                str(order.asset),
                str(order.side),
                str(order.quantity),
                str(order.decision_price),
                str(order.limit_price),
                str(order.state),
                str(order.filled_quantity),
                str(average) if average is not None else None,
                str(order.fees_paid),
                order.reject_reason,
                at.isoformat(),
                session_id,
            ),
        )
        # Fills are append-only; re-recording an order must not duplicate them.
        existing = self._conn.execute(
            "SELECT COUNT(*) AS n FROM fills WHERE client_id = ?", (order.client_id,)
        ).fetchone()["n"]
        for fill in order.fills[existing:]:
            self._conn.execute(
                "INSERT INTO fills(client_id, quantity, price, fee, at) VALUES (?,?,?,?,?)",
                (
                    order.client_id,
                    str(fill.quantity),
                    str(fill.price),
                    str(fill.fee),
                    fill.at.isoformat(),
                ),
            )

    def record_event(
        self, kind: str, severity: str, detail: str, *, at: datetime, session_id: str
    ) -> None:
        self._conn.execute(
            "INSERT INTO events(at, kind, severity, detail, session_id) VALUES (?,?,?,?,?)",
            (at.isoformat(), kind, severity, detail, session_id),
        )

    def record_balances(
        self,
        rows: Iterable[tuple[str, str, Decimal, Decimal]],
        *,
        at: datetime,
        session_id: str,
    ) -> None:
        self._conn.executemany(
            "INSERT INTO balances(at, venue, asset, free, reserved, session_id)"
            " VALUES (?,?,?,?,?,?)",
            [
                (at.isoformat(), venue, asset, str(free), str(reserved), session_id)
                for venue, asset, free, reserved in rows
            ],
        )

    def commit(self) -> None:
        self._conn.commit()

    # --- reads ---------------------------------------------------------
    def open_orders(self) -> tuple[OpenOrderRow, ...]:
        """Orders that are neither terminal nor resolved — what recovery must chase."""
        rows = self._conn.execute(
            "SELECT client_id, opportunity_id, venue, state, filled_quantity FROM orders"
            " WHERE state NOT IN ('FILLED', 'CANCELLED', 'REJECTED')"
        ).fetchall()
        return tuple(
            OpenOrderRow(
                client_id=r["client_id"],
                opportunity_id=r["opportunity_id"],
                venue=r["venue"],
                state=r["state"],
                filled_quantity=Decimal(r["filled_quantity"]),
            )
            for r in rows
        )

    def rejection_histogram(self, session_id: str | None = None) -> dict[str, int]:
        """Counts per rejection reason — the core research output of a session."""
        query = "SELECT reasons FROM decisions WHERE verdict = 'REJECT'"
        params: tuple[str, ...] = ()
        if session_id is not None:
            query += " AND session_id = ?"
            params = (session_id,)
        histogram: dict[str, int] = {}
        for row in self._conn.execute(query, params):
            for reason in filter(None, row["reasons"].split(",")):
                histogram[reason] = histogram.get(reason, 0) + 1
        return dict(sorted(histogram.items(), key=lambda kv: -kv[1]))

    def counts(self, session_id: str | None = None) -> dict[str, int]:
        where = " WHERE session_id = ?" if session_id else ""
        params: tuple[str, ...] = (session_id,) if session_id else ()
        out: dict[str, int] = {}
        for table in ("decisions", "orders", "events"):
            row = self._conn.execute(
                f"SELECT COUNT(*) AS n FROM {table}{where}", params
            ).fetchone()
            out[table] = row["n"]
        out["fills"] = self._conn.execute("SELECT COUNT(*) AS n FROM fills").fetchone()["n"]
        return out

    def integrity_check(self) -> bool:
        """SQLite's own consistency check — a corrupted database must be detected."""
        row = self._conn.execute("PRAGMA integrity_check").fetchone()
        return bool(row[0] == "ok")
