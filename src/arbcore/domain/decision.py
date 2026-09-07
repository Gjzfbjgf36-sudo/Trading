"""Deterministic decision records.

Every accept and every reject is recorded with its inputs and its reason.
Decisions are produced by pure functions over an explicit snapshot so that
replaying the same snapshot reproduces the same decision exactly — a decision
that cannot be reproduced cannot be audited.
"""

from __future__ import annotations

import enum
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any


class Verdict(enum.StrEnum):
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"


class RejectReason(enum.StrEnum):
    """Closed vocabulary of rejection causes.

    A closed set makes rejections countable: "why did we not trade today" is a
    query, not an investigation. New causes are added deliberately, never as
    free text.
    """

    # --- data integrity -------------------------------------------------
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
    DATA_STALE = "DATA_STALE"
    DATA_INCONSISTENT = "DATA_INCONSISTENT"
    SEQUENCE_GAP = "SEQUENCE_GAP"
    CLOCK_DRIFT = "CLOCK_DRIFT"
    DATA_QUALITY_BELOW_THRESHOLD = "DATA_QUALITY_BELOW_THRESHOLD"
    # --- economics ------------------------------------------------------
    NEGATIVE_NET_PROFIT = "NEGATIVE_NET_PROFIT"
    BELOW_SAFETY_MARGIN = "BELOW_SAFETY_MARGIN"
    NEGATIVE_RISK_ADJUSTED_EV = "NEGATIVE_RISK_ADJUSTED_EV"
    FEE_UNCERTAIN = "FEE_UNCERTAIN"
    # --- market microstructure -----------------------------------------
    SLIPPAGE_EXCEEDED = "SLIPPAGE_EXCEEDED"
    PRICE_IMPACT_EXCEEDED = "PRICE_IMPACT_EXCEEDED"
    INSUFFICIENT_LIQUIDITY = "INSUFFICIENT_LIQUIDITY"
    QUOTE_EXPIRED = "QUOTE_EXPIRED"
    LATENCY_EXCEEDED = "LATENCY_EXCEEDED"
    OPPORTUNITY_DECAYED = "OPPORTUNITY_DECAYED"
    # --- risk limits ----------------------------------------------------
    TRADE_SIZE_EXCEEDED = "TRADE_SIZE_EXCEEDED"
    TOTAL_EXPOSURE_EXCEEDED = "TOTAL_EXPOSURE_EXCEEDED"
    ASSET_EXPOSURE_EXCEEDED = "ASSET_EXPOSURE_EXCEEDED"
    EXCHANGE_EXPOSURE_EXCEEDED = "EXCHANGE_EXPOSURE_EXCEEDED"
    CHAIN_EXPOSURE_EXCEEDED = "CHAIN_EXPOSURE_EXCEEDED"
    DAILY_LOSS_LIMIT_REACHED = "DAILY_LOSS_LIMIT_REACHED"
    DAILY_TRADE_LIMIT_REACHED = "DAILY_TRADE_LIMIT_REACHED"
    CONCURRENT_TRADE_LIMIT_REACHED = "CONCURRENT_TRADE_LIMIT_REACHED"
    RISK_BUDGET_EXHAUSTED = "RISK_BUDGET_EXHAUSTED"
    # --- inventory ------------------------------------------------------
    INSUFFICIENT_INVENTORY = "INSUFFICIENT_INVENTORY"
    INVENTORY_IMBALANCE = "INVENTORY_IMBALANCE"
    RESERVE_BREACH = "RESERVE_BREACH"
    REBALANCE_COST_EXCEEDED = "REBALANCE_COST_EXCEEDED"
    # --- security / governance -----------------------------------------
    ASSET_NOT_WHITELISTED = "ASSET_NOT_WHITELISTED"
    VENUE_NOT_WHITELISTED = "VENUE_NOT_WHITELISTED"
    PROTOCOL_NOT_WHITELISTED = "PROTOCOL_NOT_WHITELISTED"
    STABLECOIN_DEPEG = "STABLECOIN_DEPEG"
    # --- system posture -------------------------------------------------
    SAFE_MODE_ACTIVE = "SAFE_MODE_ACTIVE"
    CIRCUIT_BREAKER_OPEN = "CIRCUIT_BREAKER_OPEN"
    STRATEGY_DISABLED = "STRATEGY_DISABLED"
    STRATEGY_PAUSED = "STRATEGY_PAUSED"
    TRADING_MODE_FORBIDS = "TRADING_MODE_FORBIDS"
    SIMULATION_FAILED = "SIMULATION_FAILED"
    UNKNOWN_STATE = "UNKNOWN_STATE"
    #: Used when a check itself could not be evaluated. Fail-closed: an
    #: unevaluable check is a rejection, never a pass.
    CHECK_UNEVALUABLE = "CHECK_UNEVALUABLE"


@dataclass(frozen=True, slots=True)
class CheckResult:
    """Outcome of one named, individually auditable check."""

    name: str
    passed: bool
    reason: RejectReason | None = None
    detail: str = ""
    observed: Decimal | None = None
    limit: Decimal | None = None

    def __post_init__(self) -> None:
        if not self.passed and self.reason is None:
            raise ValueError(f"failed check {self.name!r} must carry a RejectReason")
        if self.passed and self.reason is not None:
            raise ValueError(f"passed check {self.name!r} must not carry a RejectReason")

    @classmethod
    def ok(cls, name: str, *, detail: str = "") -> CheckResult:
        return cls(name=name, passed=True, detail=detail)

    @classmethod
    def fail(
        cls,
        name: str,
        reason: RejectReason,
        *,
        detail: str = "",
        observed: Decimal | None = None,
        limit: Decimal | None = None,
    ) -> CheckResult:
        return cls(
            name=name,
            passed=False,
            reason=reason,
            detail=detail,
            observed=observed,
            limit=limit,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "reason": str(self.reason) if self.reason else None,
            "detail": self.detail,
            "observed": str(self.observed) if self.observed is not None else None,
            "limit": str(self.limit) if self.limit is not None else None,
        }


@dataclass(frozen=True, slots=True)
class Decision:
    """The auditable outcome of a full evaluation.

    ``checks`` holds *all* evaluated checks, not only the failing one, so the
    record answers both "why was this rejected" and "what was true when this
    was accepted".
    """

    subject_id: str
    verdict: Verdict
    checks: tuple[CheckResult, ...]
    context: Mapping[str, str] = field(default_factory=dict)
    at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        failed = [c for c in self.checks if not c.passed]
        if self.verdict is Verdict.ACCEPT and failed:
            raise ValueError("ACCEPT decision cannot contain failed checks")
        if self.verdict is Verdict.REJECT and not failed:
            raise ValueError("REJECT decision must contain at least one failed check")

    @property
    def failures(self) -> tuple[CheckResult, ...]:
        return tuple(c for c in self.checks if not c.passed)

    @property
    def reasons(self) -> tuple[RejectReason, ...]:
        return tuple(c.reason for c in self.failures if c.reason is not None)

    @property
    def accepted(self) -> bool:
        return self.verdict is Verdict.ACCEPT

    def as_dict(self) -> dict[str, Any]:
        """Serialisable audit record. Contains no credentials by construction."""
        return {
            "subject_id": self.subject_id,
            "verdict": str(self.verdict),
            "at": self.at.isoformat(),
            "reasons": [str(r) for r in self.reasons],
            "checks": [c.as_dict() for c in self.checks],
            "context": dict(self.context),
        }


def decide(
    subject_id: str,
    checks: list[CheckResult],
    *,
    context: Mapping[str, str] | None = None,
    at: datetime | None = None,
) -> Decision:
    """Fold checks into a decision. Any failure rejects; an empty list rejects.

    An empty check list means nothing was actually verified, which is not the
    same as everything passing.
    """
    if not checks:
        checks = [
            CheckResult.fail(
                "checks_present",
                RejectReason.CHECK_UNEVALUABLE,
                detail="no checks were evaluated; refusing to accept by default",
            )
        ]
    verdict = Verdict.ACCEPT if all(c.passed for c in checks) else Verdict.REJECT
    return Decision(
        subject_id=subject_id,
        verdict=verdict,
        checks=tuple(checks),
        context=dict(context or {}),
        at=at or datetime.now(UTC),
    )
