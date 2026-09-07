"""DEX/DEX paper session.

Same decision architecture as the CEX/CEX session — the risk engine, safe mode,
circuit breakers, measured statistics, persistence and metrics are identical.
What differs is everything the strategy owns: pool pricing instead of order
books, a single wallet instead of per-venue inventory, and atomic transactions
instead of two independent legs.

That is the point of keeping strategies as separate modules. The shared parts
are genuinely shared; the different parts are not forced to look alike.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from ..adapters.synthetic_dex import DexMarketConfig, SyntheticDexMarket
from ..config.environment import Environment, RuntimeProfile, TradingMode
from ..config.limits import RiskLimits
from ..config.whitelist import Whitelist
from ..costs.gas import GasBook, GasUnknown, InclusionModel
from ..domain.decision import CheckResult, Decision, RejectReason, decide
from ..domain.state import OpportunityLifecycle, OpportunityState
from ..domain.types import ZERO, VenueId, to_decimal
from ..execution.chain import ArbitrageTx, ChainStats, PaperChain, TxState, simulate
from ..monitoring.metrics import (
    Counter,
    InfrastructureCost,
    PerformanceReport,
    PnLTracker,
    Series,
)
from ..persistence.store import Store
from ..pricing.amm import quote_swap
from ..risk.budget import RiskCategory, StrategyBudget
from ..risk.engine import CALIBRATION_SIZE_FACTOR, RiskContext, RiskEngine
from ..risk.exposure import ExposureSnapshot
from ..safety.circuit_breaker import BreakerCondition, BreakerPanel, CircuitBreaker
from ..safety.safe_mode import SafeMode, SafeModeTrigger
from ..strategy.dex_dex import DexDexConfig, DexDexOpportunity, DexDexStrategy
from ..strategy.state import StrategyState, StrategyStatus
from ..strategy.statistics import AttemptOutcome, ExecutionStatistics
from .operator import SimulatedOperator

_QUANT = Decimal("0.00000001")

DEX_CAVEAT = (
    "Synthetic AMM pools. The divergence process and the competitor-arbitrage "
    "rate are parameter choices, not measurements. These numbers describe the "
    "SYSTEM, not real DEX profitability."
)


@dataclass(frozen=True, slots=True)
class DexSessionConfig:
    ticks: int = 20_000
    seed: int = 20260907
    tick_ms: int = 12_000
    db_path: str | None = None
    inject_faults: bool = True
    fault_every: int = 500
    infrastructure_cost_per_day: Decimal = Decimal("8")
    #: Wallet balance in quote units. A single wallet, not per-venue inventory.
    initial_quote: Decimal = Decimal("4000")


@dataclass(slots=True)
class DexSessionOutcome:
    report: PerformanceReport
    counters: Counter
    chain: ChainStats
    interventions: int
    session_id: str
    statistics: dict[str, str]
    divergence: Series
    sizing_blocks: dict[str, int]


class DexPaperSession:
    """One reproducible DEX/DEX paper run."""

    def __init__(
        self,
        *,
        config: DexSessionConfig,
        limits: RiskLimits,
        whitelist: Whitelist,
        market_config: DexMarketConfig,
        strategy_config: DexDexConfig,
        gas: GasBook,
        inclusion: InclusionModel,
        venues: tuple[VenueId, ...],
    ) -> None:
        self.config = config
        self.limits = limits
        self.whitelist = whitelist
        self.venues = venues
        self.session_id = uuid.uuid4().hex[:16]
        self.start = datetime(2026, 1, 1, tzinfo=UTC)
        self.profile = RuntimeProfile(
            environment=Environment.PAPER, trading_mode=TradingMode.PAPER
        )

        self.market = SyntheticDexMarket(
            base=strategy_config.base,
            quote=strategy_config.quote,
            config=market_config,
            seed=config.seed,
        )
        self.strategy = DexDexStrategy(strategy_config, gas, inclusion, atomic=True)
        self.chain = PaperChain(gas.get(strategy_config.chain), inclusion, seed=config.seed + 13)
        self.chain_stats = ChainStats()

        self.wallet = to_decimal(config.initial_quote)
        self.statistics = ExecutionStatistics(strategy=self.strategy.kind, min_samples=50)
        self.status = StrategyStatus(self.strategy.kind)
        self.status.set_state(StrategyState.NORMAL, "session start", actor="session")
        self.budget = StrategyBudget(self.strategy.kind)
        self.budget.allocate(RiskCategory.EXECUTION, limits.max_daily_loss)
        self.budget.allocate(RiskCategory.SMART_CONTRACT, limits.max_total_exposure)

        self.safe_mode = SafeMode()
        self.breakers = BreakerPanel()
        self.breakers.add(CircuitBreaker(BreakerCondition.EXECUTION_FAILURE_RATE, Decimal("0.75")))
        self.breakers.add(CircuitBreaker(BreakerCondition.DRAWDOWN, limits.max_daily_loss))
        self.breakers.add(CircuitBreaker(BreakerCondition.RPC_FAILURE, Decimal("5")))
        self.operator = SimulatedOperator()
        self.engine = RiskEngine()

        self.counters = Counter()
        self.pnl = PnLTracker()
        self.calibration_pnl = PnLTracker()
        self.slippage = Series("curve_impact")
        self.latency = Series("inclusion_latency_ms")
        self.divergence = Series("pool_divergence")
        self.sizing_blocks: dict[str, int] = {}
        self.trades_today = 0
        self.calibration_trades_today = 0
        self.current_day = self.start.date()
        self.pnl_at_day_start = ZERO
        self.notes: list[str] = [DEX_CAVEAT]

        self._validate_wallet()

        path = config.db_path or ":memory:"
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.store = Store(path)
        self.store.start_session(
            self.session_id,
            started_at=self.start,
            environment=str(self.profile.environment),
            trading_mode=str(self.profile.trading_mode),
            config={"ticks": config.ticks, "seed": config.seed, "limits": limits.as_dict()},
        )

    def _validate_wallet(self) -> None:
        """Fail fast if the wallet cannot legally trade under these limits.

        The whole wallet sits in one asset on one chain, so it is checked
        against every ceiling that applies to it — not only the total. Learned
        from the CEX session, where a session funded past its limits rejected
        every opportunity for thousands of ticks instead of refusing to start.
        """
        ceilings = {
            "max_total_exposure": self.limits.max_total_exposure,
            "max_asset_exposure": self.limits.max_asset_exposure,
            "max_chain_exposure": self.limits.max_chain_exposure,
        }
        for name, ceiling in ceilings.items():
            if self.wallet > ceiling:
                raise ValueError(
                    f"wallet of {self.wallet} exceeds {name} {ceiling}. "
                    f"Fund the session within its limits or raise them deliberately."
                )
        for venue in self.venues:
            self.whitelist.venue(venue)

    # ------------------------------------------------------------------
    def run(self) -> DexSessionOutcome:
        for tick in range(self.config.ticks):
            now = self.start + timedelta(milliseconds=self.config.tick_ms * tick)
            self._roll_day(now)
            self.market.step()
            self.divergence.observe(self.market.divergence)
            self._maybe_inject(tick)
            self._operator_review(tick, now)
            self._trade(tick, now)
        self.store.commit()
        outcome = self._finish()
        self.store.end_session(
            self.session_id,
            ended_at=self.start
            + timedelta(milliseconds=self.config.tick_ms * self.config.ticks),
            summary=outcome.report.as_dict(),
        )
        return outcome

    def _roll_day(self, now: datetime) -> None:
        if now.date() == self.current_day:
            return
        self.current_day = now.date()
        self.trades_today = 0
        self.calibration_trades_today = 0
        self.pnl_at_day_start = self.pnl.realised + self.calibration_pnl.realised
        self.counters.increment("day_rollovers")

    def _maybe_inject(self, tick: int) -> None:
        if not self.config.inject_faults or self.config.fault_every <= 0 or tick == 0:
            return
        if tick % self.config.fault_every:
            return
        fault = ("revert", "dropped", "unknown", "congestion")[
            (tick // self.config.fault_every) % 4
        ]
        self.chain.inject_failure(fault)
        self.counters.increment(f"injected_{fault}")

    def _operator_review(self, tick: int, now: datetime) -> None:
        if tick == 0:
            self.safe_mode.clear("session-startup-reconciliation", now=now)
            return
        if self.safe_mode.active or self.breakers.any_open:
            self.counters.increment("halted_ticks")
            self.operator.review(
                self.profile, self.safe_mode, self.breakers, tick=tick, now=now
            )

    # ------------------------------------------------------------------
    def _trade(self, tick: int, now: datetime) -> None:
        if self.safe_mode.active or self.breakers.any_open:
            return
        if self.wallet <= ZERO:
            return

        opportunity_id = f"DEX-{self.session_id}-{tick:06d}"
        calibrating = not self.statistics.sufficient
        # The strategy cannot see the risk limits, so the session sizes to
        # them. Same lesson as the CEX session: proposing a size that is
        # structurally guaranteed to be rejected wastes the opportunity and
        # buries the real reason in the rejection histogram.
        budget = min(self.wallet, self.limits.max_trade_size)
        if calibrating:
            budget = min(budget, self.limits.max_trade_size * CALIBRATION_SIZE_FACTOR)

        opportunity = self.strategy.detect(
            self.market.pools,
            now=now,
            opportunity_id=opportunity_id,
            available_quote=budget,
        )
        if opportunity is None:
            self._record_sizing_block()
            return

        self.counters.increment("opportunities_detected")
        lifecycle = OpportunityLifecycle(opportunity_id)
        lifecycle.transition(OpportunityState.VALIDATING, reason="round trip priced")
        lifecycle.transition(OpportunityState.VALIDATED, reason="pools usable")

        try:
            proposal = self.strategy.to_proposal(
                opportunity,
                venues=self.venues,
                data_quality=Decimal("0.99"),
                quote_age_ms=0,
                clock_drift_ms=0,
                expected_latency_ms=self.strategy.config.expected_inclusion_ms,
                probabilities=self.statistics.probabilities(),
                calibration=calibrating,
            )
        except GasUnknown as exc:
            self._record(
                decide(
                    opportunity_id,
                    [CheckResult.fail("gas_model", RejectReason.FEE_UNCERTAIN, detail=str(exc))],
                    at=now,
                )
            )
            lifecycle.transition(OpportunityState.REJECTED, reason="no gas model")
            return

        lifecycle.transition(OpportunityState.RISK_CHECK, reason="submitting to risk engine")
        decision = self.engine.evaluate(proposal, self._risk_context(now))
        self._record(decision)
        if not decision.accepted:
            self.counters.increment("opportunities_rejected")
            lifecycle.transition(
                OpportunityState.REJECTED,
                reason=", ".join(str(r) for r in decision.reasons) or "rejected",
            )
            return

        self.counters.increment("opportunities_accepted")
        if calibrating:
            self.counters.increment("calibration_trades")
        lifecycle.transition(OpportunityState.EXECUTION_READY, reason="risk checks passed")
        self._execute(opportunity, lifecycle, now, calibration=calibrating)

    def _record_sizing_block(self) -> None:
        """Record *why* no trade was possible, not merely that none was."""
        pools = list(self.market.pools.values())
        if len(pools) < 2:
            return
        cheap = min(pools, key=lambda p: p.spot_price)
        dear = max(pools, key=lambda p: p.spot_price)
        probe = min(self.wallet, self.strategy.config.max_notional) / Decimal(100)
        edge = self.strategy.round_trip_edge(cheap, dear, probe)
        if edge is None or edge <= ZERO:
            key = "no_positive_edge_after_pool_fees"
        else:
            window = self.strategy.size_window(cheap, dear, edge)
            key = (
                "fixed_cost_exceeds_impact_cap"
                if window.chosen <= ZERO
                else "wallet_below_required_size"
            )
        self.sizing_blocks[key] = self.sizing_blocks.get(key, 0) + 1

    def _risk_context(self, now: datetime) -> RiskContext:
        exposure = ExposureSnapshot(
            as_of=now,
            total_exposure=self.wallet,
            per_asset={self.strategy.config.quote: self.wallet},
            # A DEX protocol never custodies our balance: at rest our exposure
            # to it is zero, and the engine adds the notional at risk during
            # the swap. Reporting the whole wallet against each pool would
            # double-count it once per venue.
            per_venue={v: ZERO for v in self.venues},
            per_chain={self.strategy.config.chain: self.wallet},
            daily_pnl=(
                self.pnl.realised + self.calibration_pnl.realised - self.pnl_at_day_start
            ),
            trades_today=self.trades_today,
            calibration_trades_today=self.calibration_trades_today,
            concurrent_trades=0,
            inventory_imbalance=ZERO,
            reconciled=True,
        )
        return RiskContext(
            profile=self.profile,
            limits=self.limits,
            whitelist=self.whitelist,
            exposure=exposure,
            safe_mode=self.safe_mode,
            breakers=self.breakers,
            strategy_status=self.status,
            strategy_budget=self.budget,
            now=now,
        )

    # ------------------------------------------------------------------
    def _execute(
        self,
        opportunity: DexDexOpportunity,
        lifecycle: OpportunityLifecycle,
        now: datetime,
        *,
        calibration: bool,
    ) -> None:
        cfg = self.strategy.config
        # Minimum output enforced on chain: we accept the modelled slippage and
        # nothing worse. Below this the transaction reverts rather than filling
        # at a price we never agreed to.
        min_output = (
            opportunity.output_quote * (Decimal(1) - self.limits.max_slippage)
        ).quantize(_QUANT)

        pre_check = simulate(
            opportunity.buy_pool,
            opportunity.sell_pool,
            cfg.quote,
            cfg.base,
            opportunity.input_quote,
            min_output,
        )
        if not pre_check.succeeds:
            # A failing simulation is a rejection, never a "try anyway".
            self.counters.increment("simulation_rejected")
            self.statistics.record(AttemptOutcome.DECAYED)
            lifecycle.transition(
                OpportunityState.REJECTED, reason=f"simulation failed: {pre_check.reason}"
            )
            return

        lifecycle.transition(OpportunityState.EXECUTING, reason="submitting transaction")
        if calibration:
            self.calibration_trades_today += 1
        else:
            self.trades_today += 1

        tx = ArbitrageTx(
            idempotency_key=_tx_key(opportunity.opportunity_id),
            opportunity_id=opportunity.opportunity_id,
            input_amount=opportunity.input_quote,
            min_output=min_output,
        )
        self.chain.submit(
            tx,
            opportunity.buy_pool,
            opportunity.sell_pool,
            cfg.quote,
            cfg.base,
            now=now,
        )

        if tx.state is TxState.UNKNOWN:
            lifecycle.transition(OpportunityState.UNKNOWN, reason="no receipt")
            self.safe_mode.engage(
                SafeModeTrigger.UNKNOWN_TRANSACTION_STATE,
                f"{opportunity.opportunity_id}: no receipt",
                now=now,
            )
            self.statistics.record(AttemptOutcome.UNKNOWN)
            self.counters.increment("unknown_outcomes")
            # Resolved by querying the chain, never by resending.
            receipt = self.chain.receipt(tx.idempotency_key)
            self.chain_stats.record(tx)
            if receipt is not None and receipt.state is TxState.UNKNOWN:
                self.wallet -= receipt.gas_paid
            lifecycle.transition(OpportunityState.RECONCILIATION, reason="receipt queried")
            lifecycle.transition(OpportunityState.CLOSED, reason="resolved: no state change")
            return

        self.chain_stats.record(tx)
        self.slippage.observe(opportunity.curve_impact)
        self.latency.observe(Decimal(self.strategy.config.expected_inclusion_ms))

        tracker = self.calibration_pnl if calibration else self.pnl
        if tx.state is TxState.CONFIRMED:
            lifecycle.transition(OpportunityState.FILLED, reason="transaction confirmed")
            # Our own swaps move the pools, closing the opportunity we took.
            first = quote_swap(opportunity.buy_pool, cfg.quote, opportunity.input_quote)
            self.market.apply_our_swap(opportunity.buy_pool.protocol, first)
            second = quote_swap(opportunity.sell_pool, cfg.base, first.output_amount)
            self.market.apply_our_swap(opportunity.sell_pool.protocol, second)
            self.wallet += tx.profit
            tracker.record(tx.profit, tx.gas_paid)
            self.statistics.record(AttemptOutcome.FULL_FILL)
            lifecycle.transition(OpportunityState.SETTLEMENT, reason="swaps settled")
        else:
            reason = "reverted" if tx.state is TxState.REVERTED else "not included"
            lifecycle.transition(OpportunityState.FAILED, reason=reason)
            self.wallet -= tx.gas_paid
            tracker.record(-tx.gas_paid, tx.gas_paid)
            self.statistics.record(
                AttemptOutcome.FAILED
                if tx.state is TxState.REVERTED
                else AttemptOutcome.DECAYED
            )
            self.counters.increment(f"tx_{tx.state}".lower())

        self.breakers.observe(
            BreakerCondition.DRAWDOWN,
            self.pnl.max_drawdown + self.calibration_pnl.max_drawdown,
            detail="session drawdown",
        )
        self._update_failure_breaker()
        lifecycle.transition(OpportunityState.RECONCILIATION, reason="post-trade check")
        lifecycle.transition(OpportunityState.CLOSED, reason="complete")

    def _update_failure_breaker(self) -> None:
        """Failure rate over recent attempts.

        The threshold is high (0.75) because on chain a *majority* of attempts
        are expected to fail: losing the race is normal, not a malfunction. It
        trips only when almost nothing is landing, which is a real signal.
        """
        window = self.chain_stats.history[-40:]
        if len(window) < 20:
            return
        failures = sum(1 for s in window if s is not TxState.CONFIRMED)
        rate = Decimal(failures) / Decimal(len(window))
        self.breakers.observe(
            BreakerCondition.EXECUTION_FAILURE_RATE,
            rate,
            detail=f"{rate} of the last {len(window)} transactions did not confirm",
        )

    def _record(self, decision: Decision) -> None:
        self.store.record_decision(decision, session_id=self.session_id)

    def _finish(self) -> DexSessionOutcome:
        days = Decimal(self.config.ticks * self.config.tick_ms) / Decimal(86_400_000)
        report = PerformanceReport(
            opportunities_detected=self.counters.get("opportunities_detected"),
            opportunities_rejected=self.counters.get("opportunities_rejected"),
            opportunities_accepted=self.counters.get("opportunities_accepted"),
            rejection_reasons=self.store.rejection_histogram(self.session_id),
            pnl=self.pnl,
            infrastructure=InfrastructureCost(
                per_day=self.config.infrastructure_cost_per_day, days_elapsed=days
            ),
            execution_failures=self.chain_stats.reverted + self.chain_stats.dropped,
            partial_fills=0,  # atomic: a swap either completes or reverts
            unknown_outcomes=self.counters.get("unknown_outcomes"),
            slippage=self.slippage,
            latency=self.latency,
            calibration_trades=self.counters.get("calibration_trades"),
            calibration_pnl=self.calibration_pnl,
            notes=tuple(self.notes),
        )
        return DexSessionOutcome(
            report=report,
            counters=self.counters,
            chain=self.chain_stats,
            interventions=self.operator.count,
            session_id=self.session_id,
            statistics=self.statistics.summary(),
            divergence=self.divergence,
            sizing_blocks=dict(sorted(self.sizing_blocks.items(), key=lambda kv: -kv[1])),
        )


def _tx_key(opportunity_id: str) -> str:
    """Deterministic idempotency key, so a restart recognises its own tx."""
    return "arbtx-" + hashlib.sha256(opportunity_id.encode()).hexdigest()[:24]
