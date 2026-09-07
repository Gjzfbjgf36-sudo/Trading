"""Strategy economics, measured statistics and the calibration gate."""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from arbcore.config.environment import Environment, RuntimeProfile, TradingMode
from arbcore.config.universe import BTC, COINBASE, KRAKEN, USDC_ETHEREUM
from arbcore.costs.fees import FeeBook, FeeUnknown, VenueFeeSchedule
from arbcore.domain.decision import RejectReason
from arbcore.domain.types import StrategyKind
from arbcore.inventory.manager import InventoryManager
from arbcore.marketdata.book import BookSnapshot, Level, OrderBook
from arbcore.strategy.cex_cex import CexCexConfig, CexCexStrategy
from arbcore.strategy.statistics import AttemptOutcome, ExecutionStatistics

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def book(venue, mid: str) -> OrderBook:
    centre = Decimal(mid)
    b = OrderBook(venue=venue, asset=BTC)
    b.apply_snapshot(
        BookSnapshot(
            venue=venue,
            asset=BTC,
            sequence=1,
            venue_time=NOW,
            received_at=NOW,
            bids=tuple(Level(centre - Decimal(i + 1), "5") for i in range(5)),
            asks=tuple(Level(centre + Decimal(i + 1), "5") for i in range(5)),
        )
    )
    return b


def fee_book(rate: str = "0.0005") -> FeeBook:
    return FeeBook(
        schedules={
            v: VenueFeeSchedule(
                venue=v,
                taker_rate=Decimal(rate),
                maker_rate=Decimal(rate),
                withdrawal_fees={BTC: Decimal("0.0001")},
            )
            for v in (COINBASE, KRAKEN)
        }
    )


def inventory() -> InventoryManager:
    inv = InventoryManager()
    for venue in (COINBASE, KRAKEN):
        inv.set_balance(venue, BTC, Decimal("5"))
        inv.set_balance(venue, USDC_ETHEREUM, Decimal("500000"))
    return inv


def strategy(fees: FeeBook | None = None) -> CexCexStrategy:
    return CexCexStrategy(
        CexCexConfig(
            base=BTC,
            quote=USDC_ETHEREUM,
            trade_size=Decimal("1"),
            calibration_size=Decimal("0.25"),
        ),
        fees or fee_book(),
    )


# --- detection -----------------------------------------------------------


def test_no_opportunity_without_a_dislocation():
    books = {COINBASE: book(COINBASE, "100"), KRAKEN: book(KRAKEN, "100")}
    assert strategy().detect(books, inventory(), now=NOW, opportunity_id="X") is None


def test_dislocation_produces_a_directed_opportunity():
    books = {COINBASE: book(COINBASE, "100"), KRAKEN: book(KRAKEN, "120")}
    found = strategy().detect(books, inventory(), now=NOW, opportunity_id="X")
    assert found is not None
    assert found.buy_venue == COINBASE  # cheap side
    assert found.sell_venue == KRAKEN
    assert found.gross_profit > Decimal(0)


def test_one_usable_book_is_not_an_opportunity():
    books = {COINBASE: book(COINBASE, "100"), KRAKEN: OrderBook(venue=KRAKEN, asset=BTC)}
    assert strategy().detect(books, inventory(), now=NOW, opportunity_id="X") is None


def test_inventory_limits_the_size():
    inv = InventoryManager()
    inv.set_balance(KRAKEN, BTC, Decimal("0.01"))
    inv.set_balance(COINBASE, USDC_ETHEREUM, Decimal("500000"))
    inv.set_balance(COINBASE, BTC, Decimal("0"))
    inv.set_balance(KRAKEN, USDC_ETHEREUM, Decimal("0"))
    books = {COINBASE: book(COINBASE, "100"), KRAKEN: book(KRAKEN, "120")}
    found = strategy().detect(books, inv, now=NOW, opportunity_id="X")
    assert found is not None
    assert found.size == Decimal("0.01")


def test_no_inventory_means_no_opportunity():
    books = {COINBASE: book(COINBASE, "100"), KRAKEN: book(KRAKEN, "120")}
    assert strategy().detect(books, InventoryManager(), now=NOW, opportunity_id="X") is None


def test_size_that_the_book_cannot_absorb_is_dropped_not_shrunk():
    """A smaller trade is a different opportunity with different economics."""
    deep_pockets = InventoryManager()
    for venue in (COINBASE, KRAKEN):
        deep_pockets.set_balance(venue, BTC, Decimal("10000"))
        deep_pockets.set_balance(venue, USDC_ETHEREUM, Decimal("100000000"))
    books = {COINBASE: book(COINBASE, "100"), KRAKEN: book(KRAKEN, "120")}
    # The books hold 25 units per side; asking for 1000 cannot be filled.
    huge = strategy().detect(
        books, deep_pockets, now=NOW, opportunity_id="X", size=Decimal("1000")
    )
    assert huge is None


# --- economics -----------------------------------------------------------


def opportunity_and_proposal(fees: FeeBook | None = None, sell_mid: str = "120", **kwargs):
    strat = strategy(fees)
    books = {COINBASE: book(COINBASE, "100"), KRAKEN: book(KRAKEN, sell_mid)}
    found = strat.detect(books, inventory(), now=NOW, opportunity_id="X")
    defaults = dict(
        data_quality=Decimal("0.99"),
        quote_age_ms=100,
        clock_drift_ms=10,
        expected_latency_ms=200,
        probabilities=None,
        calibration=False,
    )
    defaults.update(kwargs)
    return found, strat.to_proposal(found, **defaults)


def test_every_cost_line_is_estimated_so_the_trade_can_be_judged():
    _, proposal = opportunity_and_proposal()
    assert proposal.costs.complete
    assert proposal.costs.unknown_lines() == ()


def test_inapplicable_costs_are_explicit_zeros_not_omissions():
    _, proposal = opportunity_and_proposal()
    assert proposal.costs.gas == Decimal(0)
    assert proposal.costs.bridge_fees == Decimal(0)


def test_leg_risk_and_rebalancing_are_charged():
    """A strategy that ignores the cost of undoing itself is not profitable."""
    _, proposal = opportunity_and_proposal()
    assert proposal.costs.expected_execution_loss > Decimal(0)
    assert proposal.costs.expected_rebalancing_cost > Decimal(0)


def test_high_fees_turn_a_gross_profit_into_a_net_loss():
    """The finding that matters: a real spread that fees eat entirely."""
    # A 1-point spread on a ~101 notional: comfortably positive gross.
    _, cheap = opportunity_and_proposal(fee_book("0.0005"), sell_mid="103")
    _, dear = opportunity_and_proposal(fee_book("0.0300"), sell_mid="103")
    assert cheap.net_expected_profit() > Decimal(0)
    assert dear.net_expected_profit() < Decimal(0)
    assert dear.gross_expected_profit > Decimal(0)  # the spread was still there


def test_missing_fee_schedule_raises_rather_than_guessing():
    strat = strategy(FeeBook(schedules={}))
    books = {COINBASE: book(COINBASE, "100"), KRAKEN: book(KRAKEN, "120")}
    found = strat.detect(books, inventory(), now=NOW, opportunity_id="X")
    with pytest.raises(FeeUnknown):
        strat.to_proposal(
            found,
            data_quality=Decimal("0.99"),
            quote_age_ms=1,
            clock_drift_ms=0,
            expected_latency_ms=1,
            probabilities=None,
            calibration=False,
        )


def test_the_strategy_is_non_atomic():
    from arbcore.domain.types import Atomicity

    _, proposal = opportunity_and_proposal()
    assert proposal.atomicity is Atomicity.NON_ATOMIC


# --- measured statistics -------------------------------------------------


def test_no_probabilities_before_enough_samples():
    stats = ExecutionStatistics(strategy=StrategyKind.CEX_CEX, min_samples=50)
    for _ in range(49):
        stats.record(AttemptOutcome.FULL_FILL)
    assert stats.probabilities() is None
    stats.record(AttemptOutcome.FULL_FILL)
    assert stats.probabilities() is not None


def test_probabilities_record_their_basis():
    stats = ExecutionStatistics(strategy=StrategyKind.CEX_CEX, min_samples=10)
    for _ in range(10):
        stats.record(AttemptOutcome.FULL_FILL)
    assert "measured over 10 attempts" in stats.probabilities().basis


def test_unknown_outcomes_count_as_failures_not_as_nothing():
    stats = ExecutionStatistics(strategy=StrategyKind.CEX_CEX, min_samples=10)
    for _ in range(5):
        stats.record(AttemptOutcome.FULL_FILL)
    for _ in range(5):
        stats.record(AttemptOutcome.UNKNOWN)
    probabilities = stats.probabilities()
    assert probabilities.failure == Decimal("0.5")
    assert "unresolved" in probabilities.basis


def test_statistics_window_is_rolling():
    stats = ExecutionStatistics(strategy=StrategyKind.CEX_CEX, min_samples=10, window=20)
    for _ in range(30):
        stats.record(AttemptOutcome.FULL_FILL)
    assert stats.samples == 20


def test_min_samples_below_ten_is_refused():
    with pytest.raises(ValueError):
        ExecutionStatistics(strategy=StrategyKind.CEX_CEX, min_samples=3)


# --- the calibration gate ------------------------------------------------


def test_calibration_is_rejected_outside_paper_mode(engine, proposal, clean_context):
    """Real capital is never committed to gather statistics."""
    calibration = replace(proposal, probabilities=None, calibration=True)
    live = replace(
        clean_context,
        profile=RuntimeProfile(
            Environment.PRODUCTION,
            TradingMode.CANARY,
            live_trading_enabled=True,
            manual_approval_reference="T-1",
            readiness=__import__(
                "arbcore.config.environment", fromlist=["ReadinessChecklist"]
            ).ReadinessChecklist(*[True] * 8),
        ),
    )
    decision = engine.evaluate(calibration, live)
    assert RejectReason.TRADING_MODE_FORBIDS in set(decision.reasons)


def test_calibration_is_size_capped_below_normal_trades(engine, proposal, clean_context):
    cap = clean_context.limits.max_trade_size * Decimal("0.10")
    oversized = replace(
        proposal, probabilities=None, calibration=True, notional=cap + Decimal("1")
    )
    assert RejectReason.TRADE_SIZE_EXCEEDED in set(
        engine.evaluate(oversized, clean_context).reasons
    )


def test_small_paper_calibration_trade_is_permitted(engine, proposal, clean_context):
    small = replace(
        proposal, probabilities=None, calibration=True, notional=Decimal("20")
    )
    decision = engine.evaluate(small, clean_context)
    assert decision.accepted, decision.failures


def test_without_the_calibration_flag_missing_probabilities_still_reject(
    engine, proposal, clean_context
):
    unflagged = replace(proposal, probabilities=None, calibration=False)
    assert RejectReason.NEGATIVE_RISK_ADJUSTED_EV in set(
        engine.evaluate(unflagged, clean_context).reasons
    )
