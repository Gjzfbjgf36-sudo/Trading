"""Per-strategy operational state.

Degradation is a first-class state, not an alert. A strategy whose execution
quality has slipped is throttled automatically; deciding *why* it slipped —
regime, fees, competition, latency, liquidity, data quality, execution quality,
inventory or a software bug — is human work, and optimisation before that
diagnosis is forbidden (docs/risk-management.md §Strategy degradation).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ..domain.types import StrategyKind


class StrategyState(enum.StrEnum):
    NORMAL = "NORMAL"
    #: Still permitted, but at reduced size and with tighter thresholds.
    DEGRADED = "DEGRADED"
    #: No new trades; existing commitments are seen through.
    PAUSED = "PAUSED"
    #: Budget exhausted or governance decision. Requires manual re-enable.
    DISABLED = "DISABLED"


#: Only NORMAL and DEGRADED may open new trades.
TRADING_STATES: frozenset[StrategyState] = frozenset(
    {StrategyState.NORMAL, StrategyState.DEGRADED}
)


@dataclass(frozen=True, slots=True)
class StateChange:
    from_state: StrategyState
    to_state: StrategyState
    reason: str
    actor: str
    at: datetime


@dataclass(slots=True)
class StrategyStatus:
    """Tracks one strategy's operational state and why it got there."""

    strategy: StrategyKind
    state: StrategyState = StrategyState.PAUSED
    history: list[StateChange] = field(default_factory=list)

    def set_state(
        self,
        target: StrategyState,
        reason: str,
        *,
        actor: str,
        now: datetime | None = None,
    ) -> StateChange:
        if not reason.strip():
            raise ValueError("a state change must record a reason")
        if not actor.strip():
            raise ValueError("a state change must record an actor")
        if target is not StrategyState.DISABLED and self.state is StrategyState.DISABLED:
            # Re-enabling a disabled strategy is a governance action, expressed
            # explicitly via enable() so it cannot happen as a side effect.
            raise RuntimeError(
                f"{self.strategy} is DISABLED; use enable() with an operator identity"
            )
        change = StateChange(
            from_state=self.state,
            to_state=target,
            reason=reason,
            actor=actor,
            at=now or datetime.now(UTC),
        )
        self.state = target
        self.history.append(change)
        return change

    def disable(self, reason: str, *, actor: str = "system") -> StateChange:
        return self.set_state(StrategyState.DISABLED, reason, actor=actor)

    def enable(self, operator: str, reason: str) -> StateChange:
        """Manually re-enable a disabled strategy, in PAUSED state.

        Re-enabling never lands directly in NORMAL: the operator must
        separately decide that it may trade again.
        """
        if not operator.strip():
            raise ValueError("enabling a strategy requires an operator identity")
        change = StateChange(
            from_state=self.state,
            to_state=StrategyState.PAUSED,
            reason=reason,
            actor=operator,
            at=datetime.now(UTC),
        )
        self.state = StrategyState.PAUSED
        self.history.append(change)
        return change

    @property
    def may_open_trades(self) -> bool:
        return self.state in TRADING_STATES
