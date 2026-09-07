"""Simulated operator.

SAFE MODE and circuit breakers require a *human* to clear them. That is correct
and non-negotiable, but it means an unattended paper session stops permanently
the first time anything goes wrong — which would tell us nothing about the
hundred ticks after that.

This stands in for the human, and is deliberately visible: every intervention
is counted and reported. A session that needed forty interventions has not
demonstrated that the system works; it has demonstrated that the system is
unoperable. The count is a headline metric for exactly that reason.

Paper mode only. It is never wired into a real-money path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from ..config.environment import RuntimeProfile, TradingMode
from ..safety.circuit_breaker import BreakerPanel
from ..safety.safe_mode import SafeMode


@dataclass(slots=True)
class InterventionRecord:
    at: datetime
    what: str
    cause: str


@dataclass(slots=True)
class SimulatedOperator:
    """Stands in for the human who reviews and clears halts."""

    name: str = "simulated-operator"
    #: Ticks of "investigation" before a halt may be cleared. Not cosmetic: it
    #: makes downtime visible in the session's timeline.
    review_ticks: int = 5
    interventions: list[InterventionRecord] = field(default_factory=list)
    _pending_since: int | None = None

    def review(
        self,
        profile: RuntimeProfile,
        safe_mode: SafeMode,
        breakers: BreakerPanel,
        *,
        tick: int,
        now: datetime,
    ) -> bool:
        """Attempt to clear halts. Returns True if the system is clear afterwards.

        Refuses outright outside paper mode: a simulated human must never be
        able to clear a halt that guards real capital.
        """
        if profile.trading_mode is not TradingMode.PAPER:
            raise RuntimeError(
                "the simulated operator is paper-only; real halts need a real human"
            )
        halted = safe_mode.active or breakers.any_open
        if not halted:
            self._pending_since = None
            return True

        if self._pending_since is None:
            self._pending_since = tick
            return False
        if tick - self._pending_since < self.review_ticks:
            return False

        if safe_mode.active:
            event = safe_mode.last_event
            cause = f"{event.trigger}: {event.detail}" if event else "start-up default"
            safe_mode.clear(self.name, now=now)
            self.interventions.append(
                InterventionRecord(at=now, what="cleared SAFE MODE", cause=cause)
            )
        for breaker in breakers.open_breakers:
            trip = breaker.trips[-1] if breaker.trips else None
            breaker.reset(self.name)
            self.interventions.append(
                InterventionRecord(
                    at=now,
                    what=f"reset breaker {breaker.condition}",
                    cause=trip.detail if trip else "no trip record",
                )
            )
        self._pending_since = None
        return True

    @property
    def count(self) -> int:
        return len(self.interventions)
