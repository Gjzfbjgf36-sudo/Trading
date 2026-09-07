"""Alerting.

Alerts exist to get a human's attention, which means the scarce resource is
attention, not messages. Two rules follow:

* Severity is fixed per event kind, not inferred at the call site, so the same
  condition always alerts the same way.
* Repeats are suppressed within a window. An alert that fires two hundred times
  trains the operator to ignore it, which is worse than not alerting.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timedelta


class Severity(enum.StrEnum):
    INFO = "INFO"
    WARN = "WARN"
    #: Requires a human now: capital or state integrity is in question.
    CRITICAL = "CRITICAL"


class AlertKind(enum.StrEnum):
    HIGH_QUALITY_OPPORTUNITY = "HIGH_QUALITY_OPPORTUNITY"
    TRADE_EXECUTED = "TRADE_EXECUTED"
    EXECUTION_FAILURE = "EXECUTION_FAILURE"
    CIRCUIT_BREAKER = "CIRCUIT_BREAKER"
    SAFE_MODE = "SAFE_MODE"
    DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
    API_FAILURE = "API_FAILURE"
    RPC_FAILURE = "RPC_FAILURE"
    ABNORMAL_SLIPPAGE = "ABNORMAL_SLIPPAGE"
    WALLET_MISMATCH = "WALLET_MISMATCH"
    INVENTORY_IMBALANCE = "INVENTORY_IMBALANCE"
    UNUSUAL_PNL = "UNUSUAL_PNL"
    RECONCILIATION_MISMATCH = "RECONCILIATION_MISMATCH"
    UNKNOWN_STATE = "UNKNOWN_STATE"
    STRATEGY_DEGRADED = "STRATEGY_DEGRADED"


_SEVERITY: dict[AlertKind, Severity] = {
    AlertKind.HIGH_QUALITY_OPPORTUNITY: Severity.INFO,
    AlertKind.TRADE_EXECUTED: Severity.INFO,
    AlertKind.STRATEGY_DEGRADED: Severity.WARN,
    AlertKind.EXECUTION_FAILURE: Severity.WARN,
    AlertKind.ABNORMAL_SLIPPAGE: Severity.WARN,
    AlertKind.INVENTORY_IMBALANCE: Severity.WARN,
    AlertKind.API_FAILURE: Severity.WARN,
    AlertKind.RPC_FAILURE: Severity.WARN,
    AlertKind.UNUSUAL_PNL: Severity.WARN,
    # Everything below means capital or state integrity is in question.
    AlertKind.CIRCUIT_BREAKER: Severity.CRITICAL,
    AlertKind.SAFE_MODE: Severity.CRITICAL,
    AlertKind.DAILY_LOSS_LIMIT: Severity.CRITICAL,
    AlertKind.WALLET_MISMATCH: Severity.CRITICAL,
    AlertKind.RECONCILIATION_MISMATCH: Severity.CRITICAL,
    AlertKind.UNKNOWN_STATE: Severity.CRITICAL,
}


@dataclass(frozen=True, slots=True)
class Alert:
    kind: AlertKind
    severity: Severity
    detail: str
    at: datetime

    def __str__(self) -> str:
        return f"[{self.severity}] {self.kind}: {self.detail}"


@dataclass(slots=True)
class AlertRouter:
    """Deduplicating alert sink.

    Critical alerts are never suppressed. A repeated CRITICAL is not noise: it
    means the condition is still live and nobody has dealt with it.
    """

    suppression_window: timedelta = timedelta(minutes=5)
    emitted: list[Alert] = field(default_factory=list)
    suppressed: int = 0
    _last_seen: dict[AlertKind, datetime] = field(default_factory=dict)

    def emit(self, kind: AlertKind, detail: str, *, now: datetime) -> Alert | None:
        severity = _SEVERITY[kind]
        last = self._last_seen.get(kind)
        if (
            severity is not Severity.CRITICAL
            and last is not None
            and now - last < self.suppression_window
        ):
            self.suppressed += 1
            return None
        alert = Alert(kind=kind, severity=severity, detail=detail, at=now)
        self._last_seen[kind] = now
        self.emitted.append(alert)
        return alert

    def by_severity(self, severity: Severity) -> tuple[Alert, ...]:
        return tuple(a for a in self.emitted if a.severity is severity)

    @property
    def criticals(self) -> tuple[Alert, ...]:
        return self.by_severity(Severity.CRITICAL)

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {"suppressed": self.suppressed}
        for alert in self.emitted:
            counts[str(alert.kind)] = counts.get(str(alert.kind), 0) + 1
        return counts
