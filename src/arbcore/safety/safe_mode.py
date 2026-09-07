"""SAFE MODE — the system's default posture when reality is uncertain.

SAFE MODE prevents *new* risk. It does not, by itself, unwind existing
positions: blind unwinding under uncertain state is how a reconciliation
problem becomes a realised loss. It blocks new commitments and demands human
reconciliation.

Latching is deliberate: entering is automatic, leaving is manual.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import UTC, datetime


class SafeModeTrigger(enum.StrEnum):
    UNKNOWN_ORDER_STATE = "UNKNOWN_ORDER_STATE"
    UNKNOWN_TRANSACTION_STATE = "UNKNOWN_TRANSACTION_STATE"
    CORRUPTED_MARKET_DATA = "CORRUPTED_MARKET_DATA"
    RPC_DISAGREEMENT = "RPC_DISAGREEMENT"
    WALLET_MISMATCH = "WALLET_MISMATCH"
    BALANCE_MISMATCH = "BALANCE_MISMATCH"
    DATABASE_INCONSISTENCY = "DATABASE_INCONSISTENCY"
    EXCESSIVE_FAILURES = "EXCESSIVE_FAILURES"
    ABNORMAL_LATENCY = "ABNORMAL_LATENCY"
    ABNORMAL_SLIPPAGE = "ABNORMAL_SLIPPAGE"
    EXCHANGE_OUTAGE = "EXCHANGE_OUTAGE"
    CLOCK_DRIFT = "CLOCK_DRIFT"
    RECONCILIATION_MISMATCH = "RECONCILIATION_MISMATCH"
    STARTUP_UNRECONCILED = "STARTUP_UNRECONCILED"
    MANUAL = "MANUAL"


@dataclass(frozen=True, slots=True)
class SafeModeEvent:
    trigger: SafeModeTrigger
    detail: str
    at: datetime


class SafeModeError(RuntimeError):
    """Raised when an operation that creates new risk is attempted in SAFE MODE."""


@dataclass(slots=True)
class SafeMode:
    """Latching safe-mode controller.

    A fresh process starts in SAFE MODE: nothing may be committed before
    start-up reconciliation has completed successfully (see docs/recovery.md).
    """

    active: bool = True
    events: list[SafeModeEvent] = field(default_factory=list)
    cleared_at: datetime | None = None
    cleared_by: str | None = None

    def engage(
        self,
        trigger: SafeModeTrigger,
        detail: str,
        *,
        now: datetime | None = None,
    ) -> SafeModeEvent:
        """Enter (or re-affirm) SAFE MODE. Always succeeds; never throttled."""
        event = SafeModeEvent(trigger=trigger, detail=detail, at=now or datetime.now(UTC))
        self.events.append(event)
        self.active = True
        self.cleared_at = None
        self.cleared_by = None
        return event

    def clear(self, operator: str, *, now: datetime | None = None) -> None:
        """Leave SAFE MODE. Requires a named human operator.

        There is no automatic, timed or heuristic exit. Whatever caused the
        entry must have been investigated by someone accountable.
        """
        if not operator.strip():
            raise ValueError("clearing SAFE MODE requires an operator identity")
        self.active = False
        self.cleared_at = now or datetime.now(UTC)
        self.cleared_by = operator

    def assert_may_take_new_risk(self) -> None:
        if self.active:
            last = self.events[-1] if self.events else None
            cause = f"{last.trigger}: {last.detail}" if last else "start-up default"
            raise SafeModeError(f"SAFE MODE active ({cause}); new risk is not permitted")

    @property
    def last_event(self) -> SafeModeEvent | None:
        return self.events[-1] if self.events else None
