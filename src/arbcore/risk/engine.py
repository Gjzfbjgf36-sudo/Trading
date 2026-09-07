"""The centralised risk engine.

Design rules this file exists to enforce:

* **Every** proposal passes through here. Strategy modules cannot self-approve.
* Checks are pure functions of an explicit snapshot, so a decision replays
  identically from its audit record.
* All checks run, even after the first failure, so the record shows everything
  that was wrong rather than only the first thing noticed.
* A check that cannot be evaluated fails. There is no "assume fine" path.
* Scores never override limits: the engine has no notion of a score at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from ..config.environment import RuntimeProfile, TradingMode
from ..config.limits import RiskLimits
from ..config.whitelist import Whitelist, WhitelistError
from ..domain.decision import CheckResult, Decision, RejectReason, decide
from ..domain.types import ZERO, Atomicity, to_decimal
from ..safety.circuit_breaker import BreakerPanel
from ..safety.safe_mode import SafeMode
from ..strategy.state import StrategyState, StrategyStatus
from .budget import StrategyBudget
from .exposure import ExposureSnapshot
from .proposal import TradeProposal

#: Non-atomic strategies carry leg risk, so their size ceiling is a fraction of
#: the configured maximum. Tightening only; never a widening factor.
NON_ATOMIC_SIZE_FACTOR: Decimal = Decimal("0.25")


@dataclass(frozen=True, slots=True)
class RiskContext:
    """Everything the engine needs, supplied by the caller.

    Passing state in (rather than reaching out for it) is what makes decisions
    reproducible and testable, including the failure paths.
    """

    profile: RuntimeProfile
    limits: RiskLimits
    whitelist: Whitelist
    exposure: ExposureSnapshot
    safe_mode: SafeMode
    breakers: BreakerPanel
    strategy_status: StrategyStatus
    strategy_budget: StrategyBudget
    now: datetime


class RiskEngine:
    """Evaluates proposals against hard limits and system posture."""

    def evaluate(self, proposal: TradeProposal, ctx: RiskContext) -> Decision:
        checks: list[CheckResult] = []
        checks += self._posture_checks(proposal, ctx)
        checks += self._whitelist_checks(proposal, ctx)
        checks += self._data_integrity_checks(proposal, ctx)
        checks += self._economics_checks(proposal, ctx)
        checks += self._microstructure_checks(proposal, ctx)
        checks += self._exposure_checks(proposal, ctx)
        checks += self._inventory_checks(proposal, ctx)
        checks += self._budget_checks(proposal, ctx)
        return decide(
            proposal.proposal_id,
            checks,
            context={
                "strategy": str(proposal.strategy),
                "atomicity": str(proposal.atomicity),
                "environment": str(ctx.profile.environment),
                "trading_mode": str(ctx.profile.trading_mode),
                "notional": str(proposal.notional),
                "exposure_as_of": ctx.exposure.as_of.isoformat(),
            },
            at=ctx.now,
        )

    # ------------------------------------------------------------------
    # system posture
    # ------------------------------------------------------------------
    def _posture_checks(self, proposal: TradeProposal, ctx: RiskContext) -> list[CheckResult]:
        checks: list[CheckResult] = []

        if ctx.profile.trading_mode is TradingMode.RESEARCH:
            checks.append(
                CheckResult.fail(
                    "trading_mode",
                    RejectReason.TRADING_MODE_FORBIDS,
                    detail="research mode observes and records only; no execution path",
                )
            )
        else:
            checks.append(CheckResult.ok("trading_mode", detail=str(ctx.profile.trading_mode)))

        if ctx.safe_mode.active:
            last = ctx.safe_mode.last_event
            checks.append(
                CheckResult.fail(
                    "safe_mode",
                    RejectReason.SAFE_MODE_ACTIVE,
                    detail=f"{last.trigger}: {last.detail}" if last else "start-up default",
                )
            )
        else:
            checks.append(CheckResult.ok("safe_mode"))

        if ctx.breakers.any_open:
            open_names = ", ".join(str(b.condition) for b in ctx.breakers.open_breakers)
            checks.append(
                CheckResult.fail(
                    "circuit_breakers",
                    RejectReason.CIRCUIT_BREAKER_OPEN,
                    detail=f"open breakers: {open_names}",
                )
            )
        else:
            checks.append(CheckResult.ok("circuit_breakers"))

        status = ctx.strategy_status
        if status.strategy is not proposal.strategy:
            checks.append(
                CheckResult.fail(
                    "strategy_status",
                    RejectReason.CHECK_UNEVALUABLE,
                    detail=f"status is for {status.strategy}, proposal is {proposal.strategy}",
                )
            )
        elif not status.may_open_trades:
            reason = (
                RejectReason.STRATEGY_DISABLED
                if status.state is StrategyState.DISABLED
                else RejectReason.STRATEGY_PAUSED
            )
            checks.append(
                CheckResult.fail("strategy_status", reason, detail=str(status.state))
            )
        else:
            checks.append(CheckResult.ok("strategy_status", detail=str(status.state)))

        if not ctx.exposure.reconciled:
            checks.append(
                CheckResult.fail(
                    "exposure_reconciled",
                    RejectReason.UNKNOWN_STATE,
                    detail="exposure snapshot is not backed by a successful reconciliation",
                )
            )
        else:
            checks.append(CheckResult.ok("exposure_reconciled"))

        return checks

    # ------------------------------------------------------------------
    # whitelists
    # ------------------------------------------------------------------
    def _whitelist_checks(self, proposal: TradeProposal, ctx: RiskContext) -> list[CheckResult]:
        checks: list[CheckResult] = []
        try:
            for asset in proposal.assets:
                ctx.whitelist.asset(asset)
            checks.append(CheckResult.ok("assets_whitelisted"))
        except WhitelistError as exc:
            checks.append(
                CheckResult.fail(
                    "assets_whitelisted", RejectReason.ASSET_NOT_WHITELISTED, detail=str(exc)
                )
            )
        try:
            for venue in proposal.venues:
                ctx.whitelist.venue(venue)
            checks.append(CheckResult.ok("venues_whitelisted"))
        except WhitelistError as exc:
            checks.append(
                CheckResult.fail(
                    "venues_whitelisted", RejectReason.VENUE_NOT_WHITELISTED, detail=str(exc)
                )
            )
        return checks

    # ------------------------------------------------------------------
    # data integrity
    # ------------------------------------------------------------------
    def _data_integrity_checks(
        self, proposal: TradeProposal, ctx: RiskContext
    ) -> list[CheckResult]:
        limits = ctx.limits
        checks = [
            _threshold(
                "quote_age",
                Decimal(proposal.quote_age_ms),
                Decimal(limits.max_quote_age_ms),
                RejectReason.QUOTE_EXPIRED,
                unit="ms",
            ),
            _threshold(
                "clock_drift",
                Decimal(abs(proposal.clock_drift_ms)),
                Decimal(limits.max_clock_drift_ms),
                RejectReason.CLOCK_DRIFT,
                unit="ms",
            ),
            _threshold(
                "execution_latency",
                Decimal(proposal.expected_execution_latency_ms),
                Decimal(limits.max_execution_latency_ms),
                RejectReason.LATENCY_EXCEEDED,
                unit="ms",
            ),
        ]
        if proposal.data_quality_score < limits.min_data_quality_score:
            checks.append(
                CheckResult.fail(
                    "data_quality",
                    RejectReason.DATA_QUALITY_BELOW_THRESHOLD,
                    observed=proposal.data_quality_score,
                    limit=limits.min_data_quality_score,
                )
            )
        else:
            checks.append(CheckResult.ok("data_quality"))

        # The exposure snapshot itself must not be stale.
        snapshot_age = Decimal(ctx.exposure.age_ms(ctx.now))
        checks.append(
            _threshold(
                "exposure_snapshot_age",
                snapshot_age,
                Decimal(limits.max_quote_age_ms),
                RejectReason.DATA_STALE,
                unit="ms",
            )
        )
        return checks

    # ------------------------------------------------------------------
    # economics
    # ------------------------------------------------------------------
    def _economics_checks(self, proposal: TradeProposal, ctx: RiskContext) -> list[CheckResult]:
        limits = ctx.limits
        unknown = proposal.costs.unknown_lines()
        if unknown:
            # Without a complete cost model there is no profit number to test.
            return [
                CheckResult.fail(
                    "cost_model_complete",
                    RejectReason.FEE_UNCERTAIN,
                    detail=f"unestimated cost lines: {', '.join(unknown)}",
                )
            ]

        checks = [CheckResult.ok("cost_model_complete")]
        net = proposal.net_expected_profit()
        if net <= ZERO:
            checks.append(
                CheckResult.fail(
                    "net_expected_profit",
                    RejectReason.NEGATIVE_NET_PROFIT,
                    observed=net,
                    limit=ZERO,
                )
            )
        else:
            checks.append(CheckResult.ok("net_expected_profit", detail=str(net)))

        adjusted = proposal.safety_adjusted_profit(limits.cost_safety_factor)
        required = limits.min_net_profit_margin * proposal.notional
        if adjusted < required:
            checks.append(
                CheckResult.fail(
                    "safety_margin",
                    RejectReason.BELOW_SAFETY_MARGIN,
                    observed=adjusted,
                    limit=required,
                    detail=f"cost_safety_factor={limits.cost_safety_factor}",
                )
            )
        else:
            checks.append(CheckResult.ok("safety_margin", detail=str(adjusted)))

        ev = proposal.risk_adjusted_ev(limits.cost_safety_factor)
        if ev is None:
            checks.append(
                CheckResult.fail(
                    "risk_adjusted_ev",
                    RejectReason.NEGATIVE_RISK_ADJUSTED_EV,
                    detail=(
                        "no measured execution probabilities; opportunity is research-only "
                        "until an empirical basis exists"
                    ),
                )
            )
        elif ev <= ZERO:
            checks.append(
                CheckResult.fail(
                    "risk_adjusted_ev",
                    RejectReason.NEGATIVE_RISK_ADJUSTED_EV,
                    observed=ev,
                    limit=ZERO,
                )
            )
        else:
            checks.append(CheckResult.ok("risk_adjusted_ev", detail=str(ev)))
        return checks

    # ------------------------------------------------------------------
    # microstructure
    # ------------------------------------------------------------------
    def _microstructure_checks(
        self, proposal: TradeProposal, ctx: RiskContext
    ) -> list[CheckResult]:
        return [
            _threshold(
                "slippage",
                proposal.expected_slippage,
                ctx.limits.max_slippage,
                RejectReason.SLIPPAGE_EXCEEDED,
            ),
            _threshold(
                "price_impact",
                proposal.expected_price_impact,
                ctx.limits.max_price_impact,
                RejectReason.PRICE_IMPACT_EXCEEDED,
            ),
        ]

    # ------------------------------------------------------------------
    # exposure and activity
    # ------------------------------------------------------------------
    def _exposure_checks(self, proposal: TradeProposal, ctx: RiskContext) -> list[CheckResult]:
        limits = ctx.limits
        exposure = ctx.exposure
        notional = proposal.notional

        size_limit = limits.max_trade_size
        if proposal.atomicity is Atomicity.NON_ATOMIC:
            size_limit = size_limit * NON_ATOMIC_SIZE_FACTOR

        checks = [
            _threshold(
                "trade_size", notional, size_limit, RejectReason.TRADE_SIZE_EXCEEDED
            ),
            _threshold(
                "total_exposure",
                exposure.total_exposure + notional,
                limits.max_total_exposure,
                RejectReason.TOTAL_EXPOSURE_EXCEEDED,
            ),
            _threshold(
                "daily_trades",
                Decimal(exposure.trades_today + 1),
                Decimal(limits.max_daily_trades),
                RejectReason.DAILY_TRADE_LIMIT_REACHED,
            ),
            _threshold(
                "concurrent_trades",
                Decimal(exposure.concurrent_trades + 1),
                Decimal(limits.max_concurrent_trades),
                RejectReason.CONCURRENT_TRADE_LIMIT_REACHED,
            ),
        ]

        # Daily loss: a loss is a negative P/L, tested against a positive limit.
        loss = -exposure.daily_pnl if exposure.daily_pnl < ZERO else ZERO
        checks.append(
            _threshold(
                "daily_loss", loss, limits.max_daily_loss, RejectReason.DAILY_LOSS_LIMIT_REACHED
            )
        )

        # Per-dimension exposure. The proposal's notional is attributed in full
        # to each dimension it touches; that over-counts a multi-leg trade on
        # purpose, since the conservative reading is the safe one here.
        for asset in proposal.assets:
            checks.append(
                _threshold(
                    f"asset_exposure[{asset}]",
                    exposure.asset_exposure(asset) + notional,
                    limits.max_asset_exposure,
                    RejectReason.ASSET_EXPOSURE_EXCEEDED,
                )
            )
        for venue in proposal.venues:
            venue_cap = limits.max_exchange_exposure
            try:
                venue_cap = min(venue_cap, ctx.whitelist.venue(venue).max_balance)
            except WhitelistError:
                pass  # already reported by the whitelist check
            checks.append(
                _threshold(
                    f"venue_exposure[{venue}]",
                    exposure.venue_exposure(venue) + notional,
                    venue_cap,
                    RejectReason.EXCHANGE_EXPOSURE_EXCEEDED,
                )
            )
        for chain in proposal.chains:
            checks.append(
                _threshold(
                    f"chain_exposure[{chain}]",
                    exposure.chain_exposure(chain) + notional,
                    limits.max_chain_exposure,
                    RejectReason.CHAIN_EXPOSURE_EXCEEDED,
                )
            )
        return checks

    # ------------------------------------------------------------------
    # inventory
    # ------------------------------------------------------------------
    def _inventory_checks(self, proposal: TradeProposal, ctx: RiskContext) -> list[CheckResult]:
        checks: list[CheckResult] = []
        if proposal.inventory_sufficient is not True:
            checks.append(
                CheckResult.fail(
                    "inventory_sufficient",
                    RejectReason.INSUFFICIENT_INVENTORY,
                    detail=(
                        "required free balances not verified against a reconciled snapshot"
                        if proposal.inventory_sufficient is None
                        else "required free balances unavailable"
                    ),
                )
            )
        else:
            checks.append(CheckResult.ok("inventory_sufficient"))

        checks.append(
            _threshold(
                "inventory_imbalance",
                ctx.exposure.inventory_imbalance,
                ctx.limits.max_inventory_imbalance,
                RejectReason.INVENTORY_IMBALANCE,
            )
        )
        return checks

    # ------------------------------------------------------------------
    # budgets
    # ------------------------------------------------------------------
    def _budget_checks(self, proposal: TradeProposal, ctx: RiskContext) -> list[CheckResult]:
        budget = ctx.strategy_budget
        if budget.strategy is not proposal.strategy:
            return [
                CheckResult.fail(
                    "risk_budget",
                    RejectReason.CHECK_UNEVALUABLE,
                    detail=f"budget is for {budget.strategy}, proposal is {proposal.strategy}",
                )
            ]
        if not proposal.budget_reservation:
            return [
                CheckResult.fail(
                    "risk_budget",
                    RejectReason.CHECK_UNEVALUABLE,
                    detail="proposal reserves no risk budget; every trade must consume budget",
                )
            ]
        checks: list[CheckResult] = []
        for category, amount in proposal.budget_reservation.items():
            requested = to_decimal(amount)
            try:
                allocation = budget.get(category)
            except KeyError as exc:
                checks.append(
                    CheckResult.fail(
                        f"risk_budget[{category}]",
                        RejectReason.RISK_BUDGET_EXHAUSTED,
                        detail=str(exc),
                    )
                )
                continue
            checks.append(
                _threshold(
                    f"risk_budget[{category}]",
                    requested,
                    allocation.remaining,
                    RejectReason.RISK_BUDGET_EXHAUSTED,
                )
            )
        return checks


def _threshold(
    name: str,
    observed: Decimal,
    limit: Decimal,
    reason: RejectReason,
    *,
    unit: str = "",
) -> CheckResult:
    """Pass iff ``observed <= limit``. Limits are inclusive ceilings."""
    if observed > limit:
        return CheckResult.fail(
            name,
            reason,
            observed=observed,
            limit=limit,
            detail=f"{observed}{unit} exceeds {limit}{unit}",
        )
    return CheckResult.ok(name, detail=f"{observed}{unit} <= {limit}{unit}")
