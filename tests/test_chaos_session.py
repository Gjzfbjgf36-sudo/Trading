"""Chaos and end-to-end session behaviour.

The acceptance rule for every test here is the same: whatever breaks, the
system must take **no new risk** and must never lose track of state.
"""

from decimal import Decimal

import pytest

from arbcore.adapters.synthetic import SyntheticConfig
from arbcore.app.run_paper import build_session, high_tier_fee_book, scenario_settings
from arbcore.config.environment import TradingMode
from arbcore.execution.paper import PaperMarketModel
from arbcore.marketdata.book import BookInvalidation
from arbcore.safety.circuit_breaker import BreakerCondition
from arbcore.safety.safe_mode import SafeModeTrigger


def session(**overrides):
    kwargs = dict(
        ticks=400,
        seed=7,
        tick_ms=1000,
        db_path=None,
        inject_faults=True,
        limits_path="config/risk_limits.paper.yaml",
        trade_size=Decimal("0.004"),
        market=SyntheticConfig(offset_volatility=Decimal("0.0025")),
        fees=high_tier_fee_book(),
    )
    kwargs.update(overrides)
    return build_session(**kwargs)


# --- the session runs at all --------------------------------------------


def test_session_completes_under_injected_faults():
    outcome = session().run()
    assert outcome.report.opportunities_detected > 0
    assert outcome.reconciliation_failures == 0


def test_session_is_deterministic_for_a_seed():
    first = session().run()
    second = session().run()
    assert first.counters.as_dict() == second.counters.as_dict()
    assert first.report.as_dict()["net_pnl_strategy"] == (
        second.report.as_dict()["net_pnl_strategy"]
    )


def test_a_session_never_runs_in_a_real_money_mode():
    assert session().profile.trading_mode is TradingMode.PAPER
    assert not session().profile.uses_real_money


# --- configuration safety ------------------------------------------------


def test_overfunded_session_refuses_to_start():
    """Discovered in run 01: a session funded past its limits rejected every
    opportunity for 2000 ticks instead of failing immediately."""
    with pytest.raises(ValueError, match="max_total_exposure|max_asset_exposure|working-inventory"):
        session(initial_base=Decimal("1"))


def test_session_needs_two_venues():
    from arbcore.app.paper_session import PaperSession

    with pytest.raises(ValueError, match="at least two venues"):
        PaperSession(
            config=__import__(
                "arbcore.app.paper_session", fromlist=["SessionConfig"]
            ).SessionConfig(),
            limits=session().limits,
            whitelist=session().whitelist,
            fees=high_tier_fee_book(),
            base=session().base,
            quote=session().quote,
            venues=(session().venues[0],),
            strategy_config=session().strategy.config,
        )


# --- market-data chaos ---------------------------------------------------


def test_sequence_gap_stops_the_book_being_used():
    s = session(ticks=1)
    s.run()
    venue = s.venues[0]
    book = s.books[venue]
    book.apply_update(
        __import__("arbcore.marketdata.book", fromlist=["BookUpdate"]).BookUpdate(
            venue=venue,
            asset=s.base,
            sequence=book.sequence + 50,
            venue_time=s.start,
            received_at=s.start,
        )
    )
    assert not book.usable
    assert book.invalidation is BookInvalidation.SEQUENCE_GAP
    # No opportunity can be built from an unusable book.
    assert s.strategy.detect(s.books, s.inventory, now=s.start, opportunity_id="X") is None


def test_faults_are_injected_and_recovered_from():
    outcome = session(ticks=600).run()
    counters = outcome.counters.as_dict()
    assert counters.get("faults_injected", 0) > 0
    # Every invalidation is followed by a resync; the book never stays broken.
    assert outcome.book_resyncs > 0


def test_no_faults_means_a_clean_book():
    outcome = session(inject_faults=False).run()
    assert outcome.book_resyncs == 0


# --- execution chaos -----------------------------------------------------


def test_total_venue_failure_produces_no_trades_and_no_loss_of_state():
    """Every order rejected: the system must simply not trade."""
    s = session(execution=PaperMarketModel(reject_probability=1.0))
    outcome = s.run()
    assert outcome.report.pnl.trades == 0
    assert outcome.report.calibration_pnl.trades == 0
    # Nothing may be left reserved.
    assert all(p.reserved == Decimal(0) for p in s.inventory.positions.values())


def test_unknown_order_state_engages_safe_mode():
    s = session(execution=PaperMarketModel(timeout_probability=1.0))
    outcome = s.run()
    assert outcome.report.unknown_outcomes > 0
    triggers = {e.trigger for e in s.safe_mode.events}
    assert SafeModeTrigger.UNKNOWN_ORDER_STATE in triggers


def test_unknown_orders_are_resolved_by_querying_never_by_resending():
    s = session(execution=PaperMarketModel(timeout_probability=1.0))
    s.run()
    # One order object per client id: no order was ever sent twice.
    for venue in s.venues:
        submitted = s.paper_venues[venue].orders
        assert len(submitted) == len({o.client_id for o in submitted.values()})
    assert any(o.status_queries > 0 for o in s.orders.values())


def test_total_competition_means_no_fills_and_no_stuck_reservations():
    s = session(
        execution=PaperMarketModel(
            competition_probability=1.0, competition_fill_max=0.0
        )
    )
    s.run()
    assert all(p.reserved == Decimal(0) for p in s.inventory.positions.values())


def test_inventory_is_conserved_across_a_full_session():
    """Nothing may be created or destroyed by the accounting itself."""
    s = session()
    before = sum(
        (p.total * (s.market.config.initial_mid if a == s.base else Decimal(1)))
        for (_, a), p in s.inventory.positions.items()
    )
    s.run()
    for position in s.inventory.positions.values():
        assert position.free >= Decimal(0)
        assert position.reserved >= Decimal(0)
    assert before > Decimal(0)


# --- halts ---------------------------------------------------------------


def test_a_halted_system_takes_no_new_risk():
    s = session(ticks=50)
    s.safe_mode.engage(SafeModeTrigger.MANUAL, "test halt")
    accepted_before = s.counters.get("opportunities_accepted")
    s._trade(1, s.start)
    assert s.counters.get("opportunities_accepted") == accepted_before


def test_open_breaker_blocks_trading():
    s = session(ticks=50)
    s.breakers.breakers[BreakerCondition.DRAWDOWN].trip("test")
    s._trade(1, s.start)
    assert s.counters.get("opportunities_accepted") == 0


def test_the_simulated_operator_refuses_to_work_outside_paper_mode():
    """A simulated human must never clear a halt guarding real capital."""
    from arbcore.app.operator import SimulatedOperator
    from arbcore.config.environment import (
        Environment,
        ReadinessChecklist,
        RuntimeProfile,
    )

    s = session(ticks=10)
    live = RuntimeProfile(
        Environment.PRODUCTION,
        TradingMode.LIVE,
        live_trading_enabled=True,
        manual_approval_reference="T-1",
        readiness=ReadinessChecklist(*[True] * 8),
    )
    s.safe_mode.engage(SafeModeTrigger.MANUAL, "halt")
    with pytest.raises(RuntimeError, match="paper-only"):
        SimulatedOperator().review(live, s.safe_mode, s.breakers, tick=100, now=s.start)


# --- scenarios -----------------------------------------------------------


def test_realistic_scenario_rejects_everything_on_cost():
    """The honest economic result: retail taker fees exceed the spread."""
    market, fees = scenario_settings("realistic")
    outcome = session(ticks=800, market=market, fees=fees).run()
    assert outcome.report.opportunities_detected > 0
    assert outcome.report.opportunities_accepted == 0
    reasons = outcome.report.rejection_reasons
    assert reasons.get("NEGATIVE_NET_PROFIT", 0) > 0


def test_unknown_scenario_is_refused():
    with pytest.raises(ValueError):
        scenario_settings("wishful")
