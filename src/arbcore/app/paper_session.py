"""Paper trading session.

Runs the complete pipeline — market data ingestion, integrity validation,
quality scoring, opportunity detection, executable pricing, the full cost
stack, the risk engine, inventory reservation, simulated execution,
reconciliation, persistence and metrics — against a synthetic market.

**What this validates and what it does not.** It exercises the machinery under
faults, and it measures how the system behaves. It says *nothing* about
profitability in real markets: the price process is a parameter choice, not a
market. Every report this module writes repeats that.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from ..adapters.synthetic import SyntheticConfig, SyntheticMarket
from ..config.environment import Environment, RuntimeProfile, TradingMode
from ..config.limits import RiskLimits, StrategyLimits
from ..config.whitelist import Whitelist
from ..costs.fees import FeeBook, FeeUnknown
from ..domain.decision import CheckResult, Decision, RejectReason, decide
from ..domain.state import OpportunityLifecycle, OpportunityState
from ..domain.types import ZERO, AssetId, Side, StrategyKind, VenueId, to_decimal
from ..execution.order import Order, OrderState, client_order_id
from ..execution.paper import PaperMarketModel, PaperVenue, limit_price_for
from ..inventory.manager import InsufficientBalance, InventoryManager
from ..marketdata.book import BookStatus, OrderBook
from ..marketdata.clock import ClockTracker
from ..marketdata.feed import FeedHealth, FeedRegistry
from ..marketdata.quality import score_book
from ..monitoring.metrics import (
    Counter,
    InfrastructureCost,
    PerformanceReport,
    PnLTracker,
    Series,
)
from ..persistence.store import Store
from ..recovery.reconciliation import Reconciler
from ..risk.budget import RiskCategory, StrategyBudget
from ..risk.engine import (
    CALIBRATION_SIZE_FACTOR,
    NON_ATOMIC_SIZE_FACTOR,
    RiskContext,
    RiskEngine,
)
from ..risk.exposure import ExposureSnapshot
from ..safety.circuit_breaker import (
    BreakerCondition,
    BreakerPanel,
    CircuitBreaker,
)
from ..safety.safe_mode import SafeMode, SafeModeTrigger
from ..strategy.cex_cex import CexCexConfig, CexCexOpportunity, CexCexStrategy
from ..strategy.state import StrategyState, StrategyStatus
from ..strategy.statistics import AttemptOutcome, ExecutionStatistics
from .operator import SimulatedOperator

_QUANT = Decimal("0.00000001")

#: Fraction of max_asset_exposure that opening working inventory may occupy.
#: The rest is headroom for mark-to-market drift.
WORKING_INVENTORY_HEADROOM = Decimal("0.60")

#: Partial-fill rate that degrades the strategy, and the lower rate at which it
#: recovers. The gap is deliberate hysteresis: a single threshold makes the
#: strategy oscillate across it.
PARTIAL_RATE_DEGRADE = Decimal("0.40")
PARTIAL_RATE_RECOVER = Decimal("0.20")

#: Size multiplier applied while DEGRADED.
DEGRADED_SIZE_FACTOR = Decimal("0.5")

SESSION_CAVEAT = (
    "Synthetic market. These numbers describe the SYSTEM's behaviour under a "
    "chosen price process; they are NOT evidence about real-market profitability."
)


@dataclass(frozen=True, slots=True)
class SessionConfig:
    """Everything that makes a session reproducible."""

    ticks: int = 5_000
    seed: int = 20260907
    tick_ms: int = 250
    db_path: str | None = None
    #: Inject market-data faults on a schedule (chaos mode).
    inject_faults: bool = True
    fault_every: int = 250
    #: Reconcile every N ticks.
    reconcile_every: int = 200
    infrastructure_cost_per_day: Decimal = Decimal("8")
    #: Starting balances, per venue, in base and quote units.
    initial_base: Decimal = Decimal("0.5")
    initial_quote: Decimal = Decimal("30000")


@dataclass(slots=True)
class SessionOutcome:
    report: PerformanceReport
    counters: Counter
    interventions: int
    reconciliations: int
    reconciliation_failures: int
    book_resyncs: int
    session_id: str
    statistics: dict[str, str]


class PaperSession:
    """One reproducible paper-trading run."""

    def __init__(
        self,
        *,
        config: SessionConfig,
        limits: RiskLimits,
        whitelist: Whitelist,
        fees: FeeBook,
        base: AssetId,
        quote: AssetId,
        venues: tuple[VenueId, ...],
        strategy_config: CexCexConfig,
        market_config: SyntheticConfig | None = None,
        execution_model: PaperMarketModel | None = None,
    ) -> None:
        if len(venues) < 2:
            raise ValueError("a CEX/CEX session needs at least two venues")
        self.config = config
        # A CEX order round-trip has no business taking seconds. The global
        # limit is the loosest across all strategies (on-chain inclusion);
        # this tightens it back for CEX/CEX. Overrides may only tighten.
        self.limits = StrategyLimits(
            strategy=StrategyKind.CEX_CEX,
            enabled=True,
            overrides={"max_execution_latency_ms": 2000},
        ).apply(limits)
        self.whitelist = whitelist
        self.fees = fees
        self.base = base
        self.quote = quote
        self.venues = venues
        self.session_id = uuid.uuid4().hex[:16]
        self.start = datetime(2026, 1, 1, tzinfo=UTC)

        self.profile = RuntimeProfile(
            environment=Environment.PAPER, trading_mode=TradingMode.PAPER
        )
        self.market = SyntheticMarket(
            asset=base, config=market_config or SyntheticConfig(), seed=config.seed
        )
        self.books: dict[VenueId, OrderBook] = {}
        self.feeds = FeedRegistry()
        self.clocks: dict[VenueId, ClockTracker] = {}
        self.paper_venues: dict[VenueId, PaperVenue] = {}
        model = execution_model or PaperMarketModel()
        for index, venue in enumerate(venues):
            self.market.add_venue(venue)
            self.books[venue] = OrderBook(venue=venue, asset=base)
            self.feeds.add(FeedHealth(venue=venue, heartbeat_timeout_ms=2_000))
            self.clocks[venue] = ClockTracker(venue=venue.name)
            self.paper_venues[venue] = PaperVenue(model, seed=config.seed + index * 7919)

        self.inventory = InventoryManager()
        self.inventory.min_reserve = {quote: limits.min_reserve_balance}
        for venue in venues:
            self.inventory.set_balance(venue, base, config.initial_base)
            self.inventory.set_balance(venue, quote, config.initial_quote)

        self.strategy = CexCexStrategy(strategy_config, fees)
        self.statistics = ExecutionStatistics(strategy=self.strategy.kind, min_samples=50)
        self.status = StrategyStatus(self.strategy.kind)
        self.status.set_state(StrategyState.NORMAL, "session start", actor="session")
        self.budget = StrategyBudget(self.strategy.kind)
        self.budget.allocate(RiskCategory.EXECUTION, limits.max_daily_loss)
        self.budget.allocate(RiskCategory.COUNTERPARTY, limits.max_total_exposure)
        self.budget.allocate(RiskCategory.MARKET, limits.max_daily_loss)

        self.safe_mode = SafeMode()
        self.breakers = BreakerPanel()
        self.breakers.add(CircuitBreaker(BreakerCondition.EXECUTION_FAILURE_RATE, Decimal("0.35")))
        self.breakers.add(CircuitBreaker(BreakerCondition.DRAWDOWN, limits.max_daily_loss))
        # Rate over a rolling window, not a cumulative count: a total only ever
        # grows, so a fixed threshold against it guarantees a permanent trip.
        self.breakers.add(
            CircuitBreaker(BreakerCondition.MARKET_DATA_INCONSISTENT, Decimal("0.25"))
        )
        self.breakers.add(CircuitBreaker(BreakerCondition.ABNORMAL_SLIPPAGE, limits.max_slippage))
        self.operator = SimulatedOperator()
        self.reconciler = Reconciler()
        self.engine = RiskEngine()

        self.counters = Counter()
        self.pnl = PnLTracker()
        self.calibration_pnl = PnLTracker()
        self.slippage = Series("slippage")
        self.latency = Series("latency_ms")
        self.orders: dict[str, Order] = {}
        self.lifecycles: list[OpportunityLifecycle] = []
        # Reliability and sizing are different problems with different
        # responses. Counting a partial fill as an execution failure made the
        # failure breaker fire on 48% partial rate, halting the system 81 times
        # in two simulated days. Partials now drive strategy degradation
        # (smaller size); only genuine failures drive the breaker (stop).
        self.recent_failures: list[bool] = []
        self.recent_partials: list[bool] = []
        self._integrity_window: list[int] = []
        self._integrity_window_size = 200
        self.reconciliations = 0
        self.reconciliation_failures = 0
        # Daily limits need a day. Without a rollover the trade counter only
        # grows, and the process locks itself out permanently once it reaches
        # max_daily_trades — observed in run 04, where 11,414 of 11,596
        # rejections were a daily limit that could never reset.
        self.trades_today = 0
        self.calibration_trades_today = 0
        self.current_day = self.start.date()
        self.pnl_at_day_start = ZERO
        self.days_elapsed = 0
        self.notes: list[str] = [SESSION_CAVEAT]

        self._validate_initial_inventory()

        path = config.db_path or ":memory:"
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.store = Store(path)
        self.store.start_session(
            self.session_id,
            started_at=self.start,
            environment=str(self.profile.environment),
            trading_mode=str(self.profile.trading_mode),
            config={
                "ticks": config.ticks,
                "seed": config.seed,
                "inject_faults": config.inject_faults,
                "limits": limits.as_dict(),
            },
        )

    def _validate_initial_inventory(self) -> None:
        """Fail fast if the opening book of business cannot legally trade.

        Discovered the hard way: a session funded with 1 BTC against a $5,000
        total-exposure limit rejected every single opportunity for the whole
        run. The limits were doing their job; the session had no business
        starting.
        """
        mid = self.market.mid
        directional = self.inventory.total_of(self.base) * mid
        if directional > self.limits.max_total_exposure:
            raise ValueError(
                f"opening inventory of {self.inventory.total_of(self.base)} {self.base} "
                f"is worth {directional} at {mid}, above max_total_exposure "
                f"{self.limits.max_total_exposure}. Fund the session within its limits "
                f"or raise the limits deliberately."
            )
        if directional > self.limits.max_asset_exposure:
            raise ValueError(
                f"opening {self.base} exposure {directional} exceeds max_asset_exposure "
                f"{self.limits.max_asset_exposure}"
            )
        # Working inventory is marked to market, so its value drifts even when
        # we do not trade. Funding it to the cap means a few percent of upward
        # drift halts the strategy indefinitely — observed in run 03, where
        # 95% of all rejections were ASSET_EXPOSURE_EXCEEDED caused purely by
        # mark drift. Leave headroom for it.
        headroom_limit = self.limits.max_asset_exposure * WORKING_INVENTORY_HEADROOM
        if directional > headroom_limit:
            raise ValueError(
                f"opening {self.base} inventory is worth {directional}, above the "
                f"working-inventory ceiling {headroom_limit} "
                f"({WORKING_INVENTORY_HEADROOM} of max_asset_exposure). The remainder is "
                f"headroom for mark-price drift; without it the exposure cap halts "
                f"trading as soon as the price rises."
            )
        for venue in self.venues:
            value = (
                self.inventory.free(venue, self.base) * mid
                + self.inventory.free(venue, self.quote)
            )
            cap = min(
                self.limits.max_exchange_exposure, self.whitelist.venue(venue).max_balance
            )
            if value > cap:
                raise ValueError(
                    f"opening balance on {venue} is worth {value}, above its cap {cap}"
                )

    def _max_size_for_limits(self, calibrating: bool) -> Decimal:
        """Largest size that can satisfy every notional cap simultaneously.

        Three caps stack here — max_trade_size, the non-atomic factor, and the
        calibration cap — and the strategy has no visibility of any of them.
        Sizing here rather than in the strategy is what stopped the runner
        proposing trades that were structurally guaranteed to be rejected.
        """
        cap = self.limits.max_trade_size * NON_ATOMIC_SIZE_FACTOR
        if calibrating:
            cap = min(cap, self.limits.max_trade_size * CALIBRATION_SIZE_FACTOR)
        prices = [
            book.best_ask.price
            for book in self.books.values()
            if book.usable and book.best_ask is not None
        ]
        if not prices:
            return ZERO
        # Price the cap at the worst ask we can see, so the realised notional
        # after walking the book still lands under the limit.
        return (cap / max(prices)).quantize(Decimal("0.00000001"))

    # ------------------------------------------------------------------
    def run(self) -> SessionOutcome:
        for tick in range(self.config.ticks):
            now = self.start + timedelta(milliseconds=self.config.tick_ms * tick)
            self._roll_day(now)
            self._ingest(tick, now)
            self._maybe_reconcile(tick, now)
            self._operator_review(tick, now)
            self._trade(tick, now)
        self.store.commit()
        outcome = self._finish()
        self.store.end_session(
            self.session_id,
            ended_at=self.start + timedelta(milliseconds=self.config.tick_ms * self.config.ticks),
            summary=outcome.report.as_dict(),
        )
        return outcome

    def _roll_day(self, now: datetime) -> None:
        """Reset per-day counters at the UTC day boundary.

        Daily limits are limits *per day*; a counter that never resets is a
        lifetime limit wearing a daily label.
        """
        if now.date() == self.current_day:
            return
        self.current_day = now.date()
        self.days_elapsed += 1
        self.trades_today = 0
        self.calibration_trades_today = 0
        self.pnl_at_day_start = self.pnl.realised + self.calibration_pnl.realised
        self.counters.increment("day_rollovers")
        self.store.record_event(
            "DAY_ROLLOVER",
            "INFO",
            f"daily counters reset for {self.current_day.isoformat()}",
            at=now,
            session_id=self.session_id,
        )

    # --- market data ---------------------------------------------------
    def _ingest(self, tick: int, now: datetime) -> None:
        self.market.step()
        tick_had_invalidation = 0
        inject = (
            self.config.inject_faults
            and self.config.fault_every > 0
            and tick > 0
            and tick % self.config.fault_every == 0
        )
        faults = ("gap", "duplicate", "out_of_order", "stale")
        for index, venue in enumerate(self.venues):
            source = self.market.venues[venue]
            book = self.books[venue]
            feed = self.feeds.get(venue)

            if inject and index == (tick // self.config.fault_every) % len(self.venues):
                source.inject(faults[(tick // self.config.fault_every) % len(faults)])
                self.counters.increment("faults_injected")

            if feed.state.name != "LIVE":
                feed.connecting()
                feed.connected(now)

            if not book.usable:
                # Resync is the only way back. Deliberately costs a tick, so a
                # feed that breaks constantly shows up as lost trading time.
                recovering = book.status is BookStatus.INVALID
                book.apply_snapshot(source.snapshot(self.market.mid, now=now))
                # Only a recovery counts as a resync. Counting the first
                # snapshot of an uninitialised book inflates the recovery rate
                # and hides how often the feed actually breaks.
                self.counters.increment(
                    "book_resyncs" if recovering else "initial_snapshots"
                )
                source.sequence = book.sequence
                feed.message(now)
                continue

            for update in source.poll(self.market.mid, now=now):
                applied = book.apply_update(update)
                feed.message(now)
                self.clocks[venue].observe(now, update.venue_time)
                if not applied:
                    tick_had_invalidation = 1
                    self.counters.increment(f"book_invalid_{book.invalidation}")
                    self.store.record_event(
                        "BOOK_INVALIDATED",
                        "WARN",
                        f"{venue}: {book.invalidation} {book.invalidation_detail}",
                        at=now,
                        session_id=self.session_id,
                    )

        self._integrity_window.append(tick_had_invalidation)
        if len(self._integrity_window) > self._integrity_window_size:
            self._integrity_window.pop(0)
        if len(self._integrity_window) == self._integrity_window_size:
            rate = Decimal(sum(self._integrity_window)) / Decimal(len(self._integrity_window))
            self.breakers.observe(
                BreakerCondition.MARKET_DATA_INCONSISTENT,
                rate,
                detail=f"{rate} of the last {self._integrity_window_size} ticks invalidated a book",
            )

    # --- reconciliation -------------------------------------------------
    def _maybe_reconcile(self, tick: int, now: datetime) -> None:
        if tick == 0 or tick % self.config.reconcile_every != 0:
            return
        self.reconciliations += 1
        venue_balances = {
            key: position.total for key, position in self.inventory.positions.items()
        }
        venue_orders: dict[str, Order] = {}
        for venue in self.venues:
            for client_id, order in self.paper_venues[venue].orders.items():
                venue_orders[client_id] = order
        result = self.reconciler.reconcile(
            self.inventory, venue_balances, self.orders, venue_orders, now=now
        )
        if not result.clean:
            self.reconciliation_failures += 1
            self.store.record_event(
                "RECONCILIATION_MISMATCH",
                "ERROR",
                "; ".join(f"{m.kind}: {m.detail}" for m in result.mismatches[:5]),
                at=now,
                session_id=self.session_id,
            )
        self.reconciler.enforce(result, self.safe_mode)

    def _operator_review(self, tick: int, now: datetime) -> None:
        if tick == 0:
            # Start-up reconciliation: balances are known and no orders exist.
            self.safe_mode.clear("session-startup-reconciliation", now=now)
            return
        if self.safe_mode.active or self.breakers.any_open:
            self.counters.increment("halted_ticks")
            self.operator.review(
                self.profile, self.safe_mode, self.breakers, tick=tick, now=now
            )

    # --- trading --------------------------------------------------------
    def _trade(self, tick: int, now: datetime) -> None:
        if self.safe_mode.active or self.breakers.any_open:
            return

        opportunity_id = f"OPP-{self.session_id}-{tick:06d}"
        calibrating = not self.statistics.sufficient
        configured = (
            self.strategy.config.calibration_size
            if calibrating
            else self.strategy.config.trade_size
        )
        if self.status.state is StrategyState.DEGRADED:
            configured = (configured * DEGRADED_SIZE_FACTOR).quantize(_QUANT)
        size = min(configured, self._max_size_for_limits(calibrating))
        if size <= ZERO:
            return
        opportunity = self.strategy.detect(
            self.books, self.inventory, now=now, opportunity_id=opportunity_id, size=size
        )
        if opportunity is None:
            return

        self.counters.increment("opportunities_detected")
        lifecycle = OpportunityLifecycle(opportunity_id)
        lifecycle.transition(OpportunityState.VALIDATING, reason="candidate detected")

        quality = self._data_quality(opportunity.buy_venue, opportunity.sell_venue, now)
        lifecycle.transition(OpportunityState.VALIDATED, reason="inputs scored")

        try:
            proposal = self.strategy.to_proposal(
                opportunity,
                data_quality=quality,
                quote_age_ms=max(
                    self.books[opportunity.buy_venue].age_ms(now),
                    self.books[opportunity.sell_venue].age_ms(now),
                ),
                clock_drift_ms=max(
                    abs(self.clocks[opportunity.buy_venue].drift_ms),
                    abs(self.clocks[opportunity.sell_venue].drift_ms),
                ),
                expected_latency_ms=400,
                probabilities=self.statistics.probabilities(),
                calibration=calibrating,
            )
        except FeeUnknown as exc:
            self._record(
                decide(
                    opportunity_id,
                    [CheckResult.fail("fee_schedule", RejectReason.FEE_UNCERTAIN, detail=str(exc))],
                    at=now,
                ),
                now,
            )
            lifecycle.transition(OpportunityState.REJECTED, reason="fee schedule missing")
            return

        lifecycle.transition(OpportunityState.RISK_CHECK, reason="submitting to risk engine")
        decision = self.engine.evaluate(proposal, self._risk_context(now))
        self._record(decision, now)

        if not decision.accepted:
            self.counters.increment("opportunities_rejected")
            lifecycle.transition(
                OpportunityState.REJECTED,
                reason=", ".join(str(r) for r in decision.reasons) or "rejected",
            )
            self.lifecycles.append(lifecycle)
            return

        self.counters.increment("opportunities_accepted")
        if proposal.calibration:
            self.counters.increment("calibration_trades")
        lifecycle.transition(OpportunityState.EXECUTION_READY, reason="risk checks passed")
        self._execute(opportunity, lifecycle, now, calibration=proposal.calibration)

    def _data_quality(self, buy: VenueId, sell: VenueId, now: datetime) -> Decimal:
        """Worst of the two venues' quality. A pair is only as good as its weaker leg."""
        scores = []
        for venue in (buy, sell):
            factors = score_book(
                self.books[venue],
                self.feeds.get(venue),
                self.clocks[venue],
                now=now,
                max_quote_age_ms=self.limits.max_quote_age_ms,
                max_clock_drift_ms=self.limits.max_clock_drift_ms,
            )
            scores.append(factors.score)
        return min(scores)

    def _risk_context(self, now: datetime) -> RiskContext:
        mid = self.market.mid
        per_asset: dict[AssetId, Decimal] = {}
        per_venue: dict[VenueId, Decimal] = {}
        total = ZERO
        for (venue, asset), position in self.inventory.positions.items():
            value = position.total * (mid if asset == self.base else Decimal(1))
            per_asset[asset] = per_asset.get(asset, ZERO) + value
            per_venue[venue] = per_venue.get(venue, ZERO) + value
            total += value
        # Only the base leg is directional exposure; quote balances are the
        # accounting currency and are not marked as market risk.
        directional = per_asset.get(self.base, ZERO)
        exposure = ExposureSnapshot(
            as_of=now,
            total_exposure=directional,
            per_asset={self.base: directional},
            per_venue={v: per_venue.get(v, ZERO) for v in self.venues},
            per_chain={},
            daily_pnl=(
                self.pnl.realised + self.calibration_pnl.realised - self.pnl_at_day_start
            ),
            trades_today=self.trades_today,
            calibration_trades_today=self.calibration_trades_today,
            concurrent_trades=0,
            inventory_imbalance=self._imbalance(mid),
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

    def _imbalance(self, mid: Decimal) -> Decimal:
        """Deviation from an even split of base across venues, valued at mid."""
        total_base = self.inventory.total_of(self.base)
        target = total_base / Decimal(len(self.venues))
        return self.inventory.imbalance(self.base, {v: target for v in self.venues}) * mid

    # --- execution ------------------------------------------------------
    def _execute(
        self,
        opportunity: CexCexOpportunity,
        lifecycle: OpportunityLifecycle,
        now: datetime,
        *,
        calibration: bool = False,
    ) -> None:
        # Reserve BEFORE declaring the opportunity EXECUTING. Reserving after
        # the transition left no legal state to fall back to when the balance
        # was unavailable, and the lifecycle correctly refused the illegal
        # EXECUTING -> REJECTED move rather than letting it pass.
        buy_key = client_order_id(opportunity.opportunity_id, "buy", opportunity.buy_venue)
        sell_key = client_order_id(opportunity.opportunity_id, "sell", opportunity.sell_venue)

        # Reserve at the LIMIT price, not the decision price. The fill can land
        # anywhere up to the limit once latency drift is applied, and a
        # reservation that only covers the decision price is short by exactly
        # the slippage — which a real venue reports as insufficient funds.
        buy_limit = limit_price_for(
            opportunity.buy_quote.vwap, Side.BUY, self.limits.max_slippage
        )
        buy_cost = (buy_limit * opportunity.size).quantize(_QUANT)
        try:
            self.inventory.reserve(buy_key, opportunity.buy_venue, self.quote, buy_cost)
            self.inventory.reserve(sell_key, opportunity.sell_venue, self.base, opportunity.size)
        except InsufficientBalance as exc:
            self.inventory.release(buy_key)
            self.inventory.release(sell_key)
            self.counters.increment("inventory_blocked")
            self.store.record_event(
                "INVENTORY_BLOCKED", "WARN", str(exc), at=now, session_id=self.session_id
            )
            lifecycle.transition(OpportunityState.REJECTED, reason="inventory unavailable")
            self.lifecycles.append(lifecycle)
            return

        lifecycle.transition(OpportunityState.EXECUTING, reason="dispatching legs")
        if calibration:
            self.calibration_trades_today += 1
        else:
            self.trades_today += 1

        buy_order = Order(
            client_id=buy_key,
            opportunity_id=opportunity.opportunity_id,
            venue=opportunity.buy_venue,
            asset=self.base,
            side=Side.BUY,
            quantity=opportunity.size,
            decision_price=opportunity.buy_quote.vwap,
            limit_price=buy_limit,
            created_at=now,
        )
        sell_order = Order(
            client_id=sell_key,
            opportunity_id=opportunity.opportunity_id,
            venue=opportunity.sell_venue,
            asset=self.base,
            side=Side.SELL,
            quantity=opportunity.size,
            decision_price=opportunity.sell_quote.vwap,
            limit_price=limit_price_for(
                opportunity.sell_quote.vwap, Side.SELL, self.limits.max_slippage
            ),
            created_at=now,
        )
        self.orders[buy_key] = buy_order
        self.orders[sell_key] = sell_order

        buy_outcome = self.paper_venues[opportunity.buy_venue].submit(
            buy_order, self.books[opportunity.buy_venue], now=now
        )
        sell_outcome = self.paper_venues[opportunity.sell_venue].submit(
            sell_order, self.books[opportunity.sell_venue], now=now
        )
        self.latency.observe(Decimal(max(buy_outcome.latency_ms, sell_outcome.latency_ms)))

        for order in (buy_order, sell_order):
            self.store.record_order(order, at=now, session_id=self.session_id)

        self._settle(
            opportunity, buy_order, sell_order, lifecycle, now, calibration=calibration
        )

    def _settle(
        self,
        opportunity: CexCexOpportunity,
        buy_order: Order,
        sell_order: Order,
        lifecycle: OpportunityLifecycle,
        now: datetime,
        *,
        calibration: bool = False,
    ) -> None:
        unknown = any(o.state is OrderState.UNKNOWN for o in (buy_order, sell_order))
        buy_filled = buy_order.filled_quantity
        sell_filled = sell_order.filled_quantity

        if unknown:
            # We do not know what happened. Reservations stay held, the
            # opportunity goes to UNKNOWN, and SAFE MODE stops new risk until a
            # reconciliation resolves it.
            lifecycle.transition(OpportunityState.UNKNOWN, reason="unconfirmed order state")
            self.safe_mode.engage(
                SafeModeTrigger.UNKNOWN_ORDER_STATE,
                f"{opportunity.opportunity_id}: venue did not confirm",
                now=now,
            )
            self.statistics.record(AttemptOutcome.UNKNOWN)
            self.counters.increment("unknown_outcomes")
            self._resolve_unknown(buy_order, sell_order, lifecycle, now)
            return

        state = (
            OpportunityState.FILLED
            if buy_filled >= buy_order.quantity and sell_filled >= sell_order.quantity
            else OpportunityState.PARTIAL
            if (buy_filled > ZERO or sell_filled > ZERO)
            else OpportunityState.FAILED
        )
        lifecycle.transition(state, reason=f"buy {buy_filled}, sell {sell_filled}")

        # Settle inventory against what actually filled.
        buy_spent = (
            buy_order.average_price * buy_filled if buy_order.average_price else ZERO
        )
        self.inventory.settle(
            buy_order.client_id,
            spent=buy_spent,
            received_venue=buy_order.venue,
            received_asset=self.base,
            received=buy_filled,
        )
        sell_proceeds = (
            sell_order.average_price * sell_filled if sell_order.average_price else ZERO
        )
        self.inventory.settle(
            sell_order.client_id,
            spent=sell_filled,
            received_venue=sell_order.venue,
            received_asset=self.quote,
            received=sell_proceeds,
        )

        fees = buy_order.fees_paid + sell_order.fees_paid
        matched = min(buy_filled, sell_filled)
        unmatched = abs(buy_filled - sell_filled)

        realised = ZERO
        if matched > ZERO and buy_order.average_price and sell_order.average_price:
            realised = (sell_order.average_price - buy_order.average_price) * matched
        # An unmatched leg is naked exposure. Charge it at the modelled leg-risk
        # rate immediately rather than carrying an unpriced position: pretending
        # it is flat is how leg risk disappears from a report.
        if unmatched > ZERO:
            leg_cost = (
                self.market.mid * unmatched * self.strategy.config.leg_risk_fraction
            ).quantize(_QUANT)
            realised -= leg_cost
            self.counters.increment("unmatched_legs")

        tracker = self.calibration_pnl if calibration else self.pnl
        tracker.record((realised - fees).quantize(_QUANT), fees)
        self.breakers.observe(
            BreakerCondition.DRAWDOWN,
            self.pnl.max_drawdown + self.calibration_pnl.max_drawdown,
            detail="session drawdown (strategy + calibration; both spend real risk budget)",
        )

        self._record_slippage(buy_order)
        self._record_slippage(sell_order)

        if state is OpportunityState.FILLED:
            self.statistics.record(AttemptOutcome.FULL_FILL)
            self.recent_failures.append(False)
            self.recent_partials.append(False)
        elif state is OpportunityState.PARTIAL:
            self.statistics.record(AttemptOutcome.PARTIAL_FILL)
            self.counters.increment("partial_fills")
            # Not a failure: we traded, just not all of it.
            self.recent_failures.append(False)
            self.recent_partials.append(True)
        else:
            outcome = (
                AttemptOutcome.DECAYED
                if any(o.state is OrderState.CANCELLED for o in (buy_order, sell_order))
                else AttemptOutcome.FAILED
            )
            self.statistics.record(outcome)
            self.counters.increment("execution_failures")
            self.recent_failures.append(True)
            self.recent_partials.append(False)

        self._update_failure_breaker()
        self._update_degradation(now)
        for order in (buy_order, sell_order):
            self.store.record_order(order, at=now, session_id=self.session_id)
        # A wholly failed attempt has nothing to settle, but it still owns
        # whatever it reserved, so it reconciles rather than being discarded.
        # Only adding the competition model made this path reachable, and the
        # lifecycle rejected the illegal FAILED -> SETTLEMENT move immediately.
        if state is not OpportunityState.FAILED:
            lifecycle.transition(OpportunityState.SETTLEMENT, reason="legs settled")
        lifecycle.transition(OpportunityState.RECONCILIATION, reason="post-trade check")
        lifecycle.transition(OpportunityState.CLOSED, reason="complete")
        self.lifecycles.append(lifecycle)

    def _resolve_unknown(
        self,
        buy_order: Order,
        sell_order: Order,
        lifecycle: OpportunityLifecycle,
        now: datetime,
    ) -> None:
        """Query the venue for the true state of an unconfirmed order.

        This is the only permitted resolution: never a resend, never an
        assumption that nothing happened.
        """
        for order in (buy_order, sell_order):
            if order.state is not OrderState.UNKNOWN:
                continue
            venue_view = self.paper_venues[order.venue].query(order.client_id)
            if venue_view is None:
                continue
            # The synthetic venue holds the same object, so an UNKNOWN order
            # genuinely has no fills: nothing was executed.
            if not venue_view.fills:
                order.transition(OrderState.CANCELLED)
        self.inventory.release(buy_order.client_id)
        self.inventory.release(sell_order.client_id)
        lifecycle.transition(OpportunityState.RECONCILIATION, reason="venue state queried")
        lifecycle.transition(OpportunityState.CLOSED, reason="resolved flat")
        self.lifecycles.append(lifecycle)
        self.store.record_event(
            "UNKNOWN_RESOLVED",
            "WARN",
            f"{buy_order.opportunity_id}: resolved by venue query, no fills",
            at=now,
            session_id=self.session_id,
        )

    def _record_slippage(self, order: Order) -> None:
        average = order.average_price
        if average is None or order.decision_price <= ZERO:
            return
        raw = (average - order.decision_price) / order.decision_price
        adverse = raw if order.side is Side.BUY else -raw
        value = max(ZERO, adverse)
        self.slippage.observe(value)
        self.breakers.observe(
            BreakerCondition.ABNORMAL_SLIPPAGE, value, detail=f"{order.client_id} slippage"
        )

    def _update_failure_breaker(self) -> None:
        """Trip on genuine execution failures only — not on partial fills."""
        window = self.recent_failures[-40:]
        if len(window) < 20:
            return
        rate = Decimal(sum(1 for failed in window if failed)) / Decimal(len(window))
        self.breakers.observe(
            BreakerCondition.EXECUTION_FAILURE_RATE,
            rate,
            detail=f"{rate} failures over last {len(window)} attempts",
        )

    def _update_degradation(self, now: datetime) -> None:
        """Degrade or recover the strategy based on partial-fill rate.

        A high partial rate means we are asking for more size than the book
        gives us. The response is to ask for less, not to stop: stopping wastes
        the opportunities we *can* fill, and halting on a sizing problem is how
        a system becomes unoperable.
        """
        window = self.recent_partials[-40:]
        if len(window) < 20:
            return
        rate = Decimal(sum(1 for partial in window if partial)) / Decimal(len(window))
        if rate > PARTIAL_RATE_DEGRADE and self.status.state is StrategyState.NORMAL:
            self.status.set_state(
                StrategyState.DEGRADED,
                f"partial-fill rate {rate} over last {len(window)} attempts",
                actor="session-monitor",
                now=now,
            )
            self.counters.increment("degradations")
        elif rate < PARTIAL_RATE_RECOVER and self.status.state is StrategyState.DEGRADED:
            self.status.set_state(
                StrategyState.NORMAL,
                f"partial-fill rate recovered to {rate}",
                actor="session-monitor",
                now=now,
            )
            self.counters.increment("recoveries")

    # --- output ---------------------------------------------------------
    def _record(self, decision: Decision, now: datetime) -> None:
        self.store.record_decision(decision, session_id=self.session_id)

    def _finish(self) -> SessionOutcome:
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
            execution_failures=self.counters.get("execution_failures"),
            partial_fills=self.counters.get("partial_fills"),
            unknown_outcomes=self.counters.get("unknown_outcomes"),
            slippage=self.slippage,
            latency=self.latency,
            calibration_trades=self.counters.get("calibration_trades"),
            calibration_pnl=self.calibration_pnl,
            notes=tuple(self.notes),
        )
        return SessionOutcome(
            report=report,
            counters=self.counters,
            interventions=self.operator.count,
            reconciliations=self.reconciliations,
            reconciliation_failures=self.reconciliation_failures,
            book_resyncs=self.counters.get("book_resyncs"),
            session_id=self.session_id,
            statistics=self.statistics.summary(),
        )


def to_decimal_safe(value: object) -> Decimal:
    """Convenience for report rendering; never used on the decision path."""
    return to_decimal(str(value))
