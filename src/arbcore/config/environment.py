"""Environment separation and the live-trading gate.

Two orthogonal axes:

``Environment``  where the process runs (development .. production).
``TradingMode``  what it is allowed to do (research .. live).

Live trading is disabled by default and cannot be enabled implicitly. Enabling
it requires a production environment, an explicit configuration flag, an
explicit human approval reference, and a fully satisfied readiness checklist.
The gate is deliberately annoying to pass.
"""

from __future__ import annotations

import enum
import os
from collections.abc import Mapping
from dataclasses import dataclass, field, fields


class Environment(enum.StrEnum):
    DEVELOPMENT = "development"
    TESTING = "testing"
    PAPER = "paper"
    CANARY = "canary"
    PRODUCTION = "production"


class TradingMode(enum.StrEnum):
    #: Detect and record only. No order objects are ever constructed.
    RESEARCH = "research"
    #: Full decision pipeline, simulated execution, no real funds.
    PAPER = "paper"
    #: Real funds, hard-capped at canary size, manual supervision.
    CANARY = "canary"
    #: Real funds under normal limits.
    LIVE = "live"


#: Modes that move real capital.
REAL_MONEY_MODES: frozenset[TradingMode] = frozenset({TradingMode.CANARY, TradingMode.LIVE})

#: Which trading modes each environment may ever host.
_PERMITTED_MODES: Mapping[Environment, frozenset[TradingMode]] = {
    Environment.DEVELOPMENT: frozenset({TradingMode.RESEARCH, TradingMode.PAPER}),
    Environment.TESTING: frozenset({TradingMode.RESEARCH, TradingMode.PAPER}),
    Environment.PAPER: frozenset({TradingMode.RESEARCH, TradingMode.PAPER}),
    Environment.CANARY: frozenset({TradingMode.RESEARCH, TradingMode.PAPER, TradingMode.CANARY}),
    Environment.PRODUCTION: frozenset(TradingMode),
}


class LiveGateError(RuntimeError):
    """Raised when real-money operation is requested but not authorised."""


@dataclass(frozen=True, slots=True)
class ReadinessChecklist:
    """Preconditions for real-money operation.

    Every field defaults to ``False``: a checklist that was never filled in
    blocks live trading rather than permitting it.
    """

    critical_tests_passing: bool = False
    security_review_complete: bool = False
    risk_limits_configured: bool = False
    monitoring_operational: bool = False
    reconciliation_operational: bool = False
    kill_switch_tested: bool = False
    paper_trading_completed: bool = False
    out_of_sample_validation_completed: bool = False

    def unmet(self) -> tuple[str, ...]:
        return tuple(f.name for f in fields(self) if getattr(self, f.name) is not True)

    @staticmethod
    def criteria() -> tuple[str, ...]:
        return tuple(f.name for f in fields(ReadinessChecklist))

    @property
    def satisfied(self) -> bool:
        return not self.unmet()


@dataclass(frozen=True, slots=True)
class RuntimeProfile:
    """The resolved answer to "what is this process allowed to do right now".

    Constructed once at start-up and treated as immutable. Nothing downstream
    may widen it.
    """

    environment: Environment
    trading_mode: TradingMode
    #: Must be True *in addition* to a real-money mode. Two independent knobs
    #: so that a single mistyped setting cannot arm live execution.
    live_trading_enabled: bool = False
    #: Free-form reference to the human approval (ticket, signed note). Its
    #: presence is checked; its content is never interpreted by the system.
    manual_approval_reference: str | None = None
    readiness: ReadinessChecklist = field(default_factory=ReadinessChecklist)

    def __post_init__(self) -> None:
        permitted = _PERMITTED_MODES[self.environment]
        if self.trading_mode not in permitted:
            raise LiveGateError(
                f"trading mode {self.trading_mode} is not permitted in "
                f"environment {self.environment} (permitted: {sorted(permitted)})"
            )
        if self.trading_mode in REAL_MONEY_MODES:
            self._assert_real_money_authorised()

    def _assert_real_money_authorised(self) -> None:
        if not self.live_trading_enabled:
            raise LiveGateError(
                f"trading mode {self.trading_mode} requires live_trading_enabled=True; "
                "real-money execution is disabled by default"
            )
        if not (self.manual_approval_reference or "").strip():
            raise LiveGateError(
                f"trading mode {self.trading_mode} requires a manual approval reference"
            )
        unmet = self.readiness.unmet()
        if unmet:
            raise LiveGateError(
                "real-money execution blocked; unmet readiness criteria: " + ", ".join(unmet)
            )

    @property
    def uses_real_money(self) -> bool:
        return self.trading_mode in REAL_MONEY_MODES

    @property
    def may_place_orders(self) -> bool:
        """Research mode observes only; every other mode runs the order path."""
        return self.trading_mode is not TradingMode.RESEARCH


def _env_flag(mapping: Mapping[str, str], key: str) -> bool:
    """Strict flag parsing: anything but an explicit true-value is False."""
    return mapping.get(key, "").strip().lower() in {"1", "true", "yes", "on"}


def profile_from_env(environ: Mapping[str, str] | None = None) -> RuntimeProfile:
    """Build the runtime profile from environment variables.

    Defaults are the safe ones: development environment, research mode, live
    trading off. Missing configuration therefore yields the least-privileged
    profile rather than an error-free but dangerous one.
    """
    env = environ if environ is not None else os.environ
    environment = Environment(env.get("ARBCORE_ENV", Environment.DEVELOPMENT).strip().lower())
    mode = TradingMode(env.get("ARBCORE_TRADING_MODE", TradingMode.RESEARCH).strip().lower())
    checklist_prefix = "ARBCORE_READY_"
    readiness = ReadinessChecklist(
        **{
            name: _env_flag(env, checklist_prefix + name.upper())
            for name in ReadinessChecklist.criteria()
        }
    )
    return RuntimeProfile(
        environment=environment,
        trading_mode=mode,
        live_trading_enabled=_env_flag(env, "ARBCORE_LIVE_TRADING_ENABLED"),
        manual_approval_reference=env.get("ARBCORE_MANUAL_APPROVAL_REF") or None,
        readiness=readiness,
    )
