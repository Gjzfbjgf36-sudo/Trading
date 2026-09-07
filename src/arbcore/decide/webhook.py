"""TradingView webhook receiver.

TradingView alerts POST a JSON body to a URL. This receives them, runs the gate,
and **queues the verdict for you**. It deliberately does not create a
commitment: the thesis and the invalidation are yours to write, and a system
that files them on your behalf would be recording a plan nobody made.

So the flow stays: rule fires → gate checks → *you* decide and commit.

Security notes, because this endpoint is reachable from the internet:

* A shared token is required. TradingView alerts cannot send custom HTTP
  headers, so the token may also travel as a ``token`` field in the JSON body —
  that is the form the integration actually uses. Either way it is compared in
  constant time and is never logged, echoed in a response, or written to the
  queue. Because the body form stores the token inside your TradingView alert,
  treat it as a shared secret for this endpoint only and rotate it if the alert
  is ever shared.
* Unknown senders get an identical, uninformative response, so the endpoint
  cannot be probed for whether a token is close.
* Bodies are size-limited before parsing.
* A repeated ``ref`` is recognised as a duplicate delivery, not a second signal.

Alert payloads are attacker-controllable in principle. They are parsed
strictly, and every numeric field goes through ``Decimal`` validation before it
reaches anything that sizes a position.
"""

from __future__ import annotations

import hmac
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from ..domain.types import Side
from .gate import GateVerdict, Signal, SignalGate

#: Alert bodies are small. Anything larger is not from TradingView.
MAX_BODY_BYTES = 8_192

_QUEUE_SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS pending_signals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ref         TEXT    NOT NULL UNIQUE,
    received_at TEXT    NOT NULL,
    symbol      TEXT    NOT NULL,
    side        TEXT    NOT NULL,
    entry       TEXT    NOT NULL,
    stop        TEXT    NOT NULL,
    target      TEXT,
    source      TEXT    NOT NULL,
    verdict     TEXT    NOT NULL,
    explanation TEXT    NOT NULL,
    acted_on    INTEGER NOT NULL DEFAULT 0
);
"""


class InvalidPayload(ValueError):
    """Raised when an alert body is not something we will act on."""


def extract_token(body: bytes) -> str:
    """Read the ``token`` field out of an alert body, if there is one.

    Returns an empty string when absent or unparseable — never raises, because
    a malformed body must fail authentication rather than reveal that it was
    malformed before the token was checked.
    """
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    value = payload.get("token", "")
    return value if isinstance(value, str) else ""


def parse_alert(body: bytes, *, now: datetime) -> Signal:
    """Parse a TradingView alert body into a signal.

    Strict on purpose. Every field is required and validated; nothing is
    defaulted. A malformed alert is not a slightly worse signal, it is not a
    signal.
    """
    if len(body) > MAX_BODY_BYTES:
        raise InvalidPayload("body too large")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidPayload(f"body is not JSON: {exc}") from None
    if not isinstance(payload, dict):
        raise InvalidPayload("body must be a JSON object")

    # The token, if present, is authentication rather than signal data. It is
    # removed here so it cannot reach the queue, a log line or a response.
    payload.pop("token", None)

    required = ("ref", "symbol", "side", "entry", "stop", "source")
    missing = [key for key in required if key not in payload]
    if missing:
        raise InvalidPayload(f"missing fields: {', '.join(missing)}")

    side_raw = str(payload["side"]).upper()
    if side_raw not in ("BUY", "SELL"):
        raise InvalidPayload(f"side must be BUY or SELL, got {side_raw!r}")

    def number(key: str) -> Decimal:
        try:
            value = Decimal(str(payload[key]))
        except (InvalidOperation, TypeError) as exc:
            raise InvalidPayload(f"{key} is not a number: {payload[key]!r}") from exc
        if not value.is_finite() or value <= 0:
            raise InvalidPayload(f"{key} must be a positive finite number")
        return value

    ref = str(payload["ref"]).strip()
    if not ref or len(ref) > 128:
        raise InvalidPayload("ref must be a non-empty string of at most 128 characters")

    target = None
    if payload.get("target") not in (None, ""):
        target = number("target")

    return Signal(
        ref=ref,
        symbol=str(payload["symbol"])[:32],
        side=Side(side_raw),
        entry=number("entry"),
        stop=number("stop"),
        target=target,
        source=str(payload["source"])[:128],
        emitted_at=now,
    )


@dataclass(slots=True)
class SignalQueue:
    """Durable queue of received signals and their verdicts."""

    path: str | Path
    _conn: sqlite3.Connection | None = None

    def __post_init__(self) -> None:
        self.path = str(self.path)
        parent = Path(self.path).parent
        if str(parent) not in ("", "."):
            parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        with closing(conn.cursor()) as cur:
            cur.executescript(_QUEUE_SCHEMA)
        conn.commit()
        self._conn = conn

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("queue is closed")
        return self._conn

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()
        self._conn = None

    def record(self, verdict: GateVerdict, *, now: datetime) -> bool:
        """Store a verdict. Returns False if this ref was already seen."""
        signal = verdict.signal
        try:
            self.conn.execute(
                "INSERT INTO pending_signals"
                "(ref, received_at, symbol, side, entry, stop, target, source,"
                " verdict, explanation)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    signal.ref,
                    now.isoformat(),
                    signal.symbol,
                    str(signal.side),
                    str(signal.entry),
                    str(signal.stop),
                    str(signal.target) if signal.target is not None else None,
                    signal.source,
                    "GREEN" if verdict.green else "NO",
                    verdict.explain(),
                ),
            )
        except sqlite3.IntegrityError:
            return False
        self.conn.commit()
        return True

    def pending(self, *, green_only: bool = False) -> tuple[dict[str, str], ...]:
        query = "SELECT * FROM pending_signals WHERE acted_on = 0"
        if green_only:
            query += " AND verdict = 'GREEN'"
        rows = self.conn.execute(query + " ORDER BY received_at DESC").fetchall()
        return tuple({k: str(r[k]) for k in r.keys()} for r in rows)

    def mark_acted(self, ref: str) -> None:
        self.conn.execute("UPDATE pending_signals SET acted_on = 1 WHERE ref = ?", (ref,))
        self.conn.commit()


class _Handler(BaseHTTPRequestHandler):
    """Minimal, deliberately boring HTTP handler."""

    server_version = "arbcore"
    sys_version = ""

    # Injected by make_server.
    token: str = ""
    gate: SignalGate
    queue: SignalQueue
    account_provider: Any

    def do_POST(self) -> None:  # noqa: N802 (http.server API)
        if self.path.rstrip("/") != "/tradingview":
            self._respond(404, "not found")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._respond(400, "bad length")
            return
        if length <= 0 or length > MAX_BODY_BYTES:
            self._respond(400, "bad length")
            return
        body = self.rfile.read(length)

        # TradingView cannot send custom headers, so the token may arrive in the
        # body instead. Both are compared in constant time: a timing difference
        # would let the token be discovered one character at a time.
        header_token = self.headers.get("X-Arbcore-Token", "")
        body_token = extract_token(body)
        authorised = hmac.compare_digest(header_token, self.token) or hmac.compare_digest(
            body_token, self.token
        )
        if not authorised:
            self._respond(401, "unauthorised")
            return

        now = datetime.now(UTC)
        try:
            signal = parse_alert(body, now=now)
        except InvalidPayload as exc:
            # The reason is safe to return: it describes the caller's own body.
            self._respond(400, str(exc))
            return

        verdict = self.gate.evaluate(signal, self.account_provider(), now=now)
        fresh = self.queue.record(verdict, now=now)
        if not fresh:
            self._respond(200, "duplicate")
            return
        self._respond(202, "GREEN" if verdict.green else "NO")

    def _respond(self, code: int, message: str) -> None:
        body = message.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        """Log without echoing headers, so a token can never reach the log."""
        print(f"[webhook] {self.address_string()} {format % args}")


def make_server(
    *,
    gate: SignalGate,
    queue: SignalQueue,
    account_provider: Any,
    token: str,
    host: str = "127.0.0.1",
    port: int = 8787,
) -> HTTPServer:
    """Build the receiver.

    Binds to localhost by default. Exposing it to the internet is a deliberate
    act that should go through a reverse proxy with TLS — an unencrypted
    endpoint would put the token on the wire in clear text.
    """
    if len(token) < 24:
        raise ValueError(
            "use a token of at least 24 random characters; this endpoint is "
            "reachable by anyone who finds the URL"
        )

    handler = type(
        "BoundHandler",
        (_Handler,),
        {
            "token": token,
            "gate": gate,
            "queue": queue,
            "account_provider": staticmethod(account_provider),
        },
    )
    return HTTPServer((host, port), handler)
