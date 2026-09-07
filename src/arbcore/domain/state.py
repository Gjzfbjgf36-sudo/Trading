"""Opportunity lifecycle state machine.

Every opportunity walks the same path. No component may skip a state: the
machine is the enforcement point, not a convention. An illegal transition is a
programming error and raises rather than being silently coerced, because a
skipped RISK_CHECK is exactly the bug that costs capital.
"""

from __future__ import annotations

import enum
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType


class OpportunityState(enum.StrEnum):
    DETECTED = "DETECTED"
    VALIDATING = "VALIDATING"
    VALIDATED = "VALIDATED"
    RISK_CHECK = "RISK_CHECK"
    EXECUTION_READY = "EXECUTION_READY"
    EXECUTING = "EXECUTING"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    FAILED = "FAILED"
    SETTLEMENT = "SETTLEMENT"
    RECONCILIATION = "RECONCILIATION"
    CLOSED = "CLOSED"
    #: Terminal state for anything rejected before execution. Rejected
    #: opportunities are still recorded with a reason (auditability).
    REJECTED = "REJECTED"
    #: Terminal-for-automation state: the true outcome could not be
    #: established. Requires human reconciliation; never auto-resolved.
    UNKNOWN = "UNKNOWN"


S = OpportunityState

#: Allowed transitions. Rejection is reachable from every pre-execution state.
#: UNKNOWN is reachable from any state in which an external system may have
#: acted on our behalf without us learning the outcome.
_TRANSITIONS: Mapping[OpportunityState, frozenset[OpportunityState]] = MappingProxyType(
    {
        S.DETECTED: frozenset({S.VALIDATING, S.REJECTED}),
        S.VALIDATING: frozenset({S.VALIDATED, S.REJECTED}),
        S.VALIDATED: frozenset({S.RISK_CHECK, S.REJECTED}),
        S.RISK_CHECK: frozenset({S.EXECUTION_READY, S.REJECTED}),
        # An opportunity may still be dropped after being cleared: quotes decay,
        # and a stale EXECUTION_READY must not become an order.
        S.EXECUTION_READY: frozenset({S.EXECUTING, S.REJECTED}),
        S.EXECUTING: frozenset({S.PARTIAL, S.FILLED, S.FAILED, S.UNKNOWN}),
        S.PARTIAL: frozenset({S.FILLED, S.FAILED, S.SETTLEMENT, S.UNKNOWN}),
        S.FILLED: frozenset({S.SETTLEMENT, S.UNKNOWN}),
        # A failed attempt still owns whatever partially settled; it must be
        # reconciled rather than discarded.
        S.FAILED: frozenset({S.RECONCILIATION, S.UNKNOWN}),
        S.SETTLEMENT: frozenset({S.RECONCILIATION, S.UNKNOWN}),
        S.RECONCILIATION: frozenset({S.CLOSED, S.UNKNOWN}),
        S.UNKNOWN: frozenset({S.RECONCILIATION}),
        S.CLOSED: frozenset(),
        S.REJECTED: frozenset(),
    }
)

TERMINAL_STATES: frozenset[OpportunityState] = frozenset({S.CLOSED, S.REJECTED})

#: States in which capital may be at risk on a venue or chain.
AT_RISK_STATES: frozenset[OpportunityState] = frozenset(
    {S.EXECUTING, S.PARTIAL, S.FILLED, S.FAILED, S.SETTLEMENT, S.UNKNOWN}
)


class IllegalTransition(RuntimeError):
    """Raised when code attempts to skip or reverse a lifecycle state."""


@dataclass(frozen=True, slots=True)
class TransitionRecord:
    """One audited step of an opportunity's life."""

    from_state: OpportunityState
    to_state: OpportunityState
    reason: str
    at: datetime


def allowed_transitions(state: OpportunityState) -> frozenset[OpportunityState]:
    return _TRANSITIONS[state]


@dataclass(slots=True)
class OpportunityLifecycle:
    """Tracks one opportunity through its states, keeping the full history.

    The history is the audit trail required by docs/architecture.md §Audit:
    every state change carries a reason, so both accepted and rejected
    opportunities can be explained after the fact.
    """

    opportunity_id: str
    state: OpportunityState = OpportunityState.DETECTED
    history: list[TransitionRecord] = field(default_factory=list)

    def can_transition_to(self, target: OpportunityState) -> bool:
        return target in _TRANSITIONS[self.state]

    def transition(
        self,
        target: OpportunityState,
        reason: str,
        *,
        now: datetime | None = None,
    ) -> TransitionRecord:
        if not reason.strip():
            raise ValueError("every transition needs a recorded reason")
        if not self.can_transition_to(target):
            raise IllegalTransition(
                f"{self.opportunity_id}: {self.state} -> {target} is not permitted "
                f"(allowed: {sorted(_TRANSITIONS[self.state])})"
            )
        record = TransitionRecord(
            from_state=self.state,
            to_state=target,
            reason=reason,
            at=now or datetime.now(UTC),
        )
        self.state = target
        self.history.append(record)
        return record

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    @property
    def capital_at_risk(self) -> bool:
        return self.state in AT_RISK_STATES
