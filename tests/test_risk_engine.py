"""Risk engine behaviour.

Each test starts from a context and proposal that are *supposed* to be
accepted, then breaks exactly one thing. That isolates which rule produced the
rejection and catches a rule that silently stops firing.
"""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest
from helpers import NOW, complete_costs, probabilities

from arbcore.config.environment import Environment, RuntimeProfile, TradingMode
from arbcore.config.universe import BINANCE, BTC
from arbcore.domain.decision import RejectReason
from arbcore.domain.types import AssetId, Atomicity, Chain, StrategyKind, VenueId, VenueKind
from arbcore.risk.budget import RiskCategory, StrategyBudget
from arbcore.risk.exposure import ExposureSnapshot
from arbcore.safety.circuit_breaker import BreakerCondition
from arbcore.safety.safe_mode import SafeMode, SafeModeTrigger
from arbcore.strategy.state import StrategyState, StrategyStatus


def reasons(engine, proposal, ctx):
    return set(engine.evaluate(proposal, ctx).reasons)


def test_clean_proposal_is_accepted(engine, proposal, clean_context):
    decision = engine.evaluate(proposal, clean_context)
    assert decision.accepted, decision.failures
    # An accepted decision still records every check that was run.
    assert len(decision.checks) > 15


# --- system posture -------------------------------------------------------


def test_safe_mode_blocks_everything(engine, proposal, clean_context):
    clean_context.safe_mode.engage(SafeModeTrigger.WALLET_MISMATCH, "ledger mismatch")
    assert RejectReason.SAFE_MODE_ACTIVE in reasons(engine, proposal, clean_context)


def test_fresh_process_cannot_trade(engine, proposal, clean_context):
    ctx = replace(clean_context, safe_mode=SafeMode())
    assert RejectReason.SAFE_MODE_ACTIVE in reasons(engine, proposal, ctx)


def test_open_breaker_blocks_trading(engine, proposal, clean_context):
    clean_context.breakers.observe(
        BreakerCondition.EXECUTION_FAILURE_RATE, Decimal("0.5"), detail="failures"
    )
    assert RejectReason.CIRCUIT_BREAKER_OPEN in reasons(engine, proposal, clean_context)


def test_research_mode_never_produces_an_accept(engine, proposal, clean_context):
    ctx = replace(
        clean_context,
        profile=RuntimeProfile(Environment.PAPER, TradingMode.RESEARCH),
    )
    assert RejectReason.TRADING_MODE_FORBIDS in reasons(engine, proposal, ctx)


def test_paused_strategy_is_rejected(engine, proposal, clean_context):
    clean_context.strategy_status.set_state(StrategyState.PAUSED, "monitor", actor="monitor")
    assert RejectReason.STRATEGY_PAUSED in reasons(engine, proposal, clean_context)


def test_disabled_strategy_is_rejected(engine, proposal, clean_context):
    clean_context.strategy_status.disable("budget exhausted")
    assert RejectReason.STRATEGY_DISABLED in reasons(engine, proposal, clean_context)


def test_status_for_the_wrong_strategy_cannot_authorise(engine, proposal, clean_context):
    other = StrategyStatus(StrategyKind.DEX_DEX)
    other.set_state(StrategyState.NORMAL, "t", actor="t")
    ctx = replace(clean_context, strategy_status=other)
    assert RejectReason.CHECK_UNEVALUABLE in reasons(engine, proposal, ctx)


def test_unreconciled_exposure_blocks_trading(engine, proposal, clean_context):
    ctx = replace(clean_context, exposure=replace(clean_context.exposure, reconciled=False))
    assert RejectReason.UNKNOWN_STATE in reasons(engine, proposal, ctx)


# --- whitelists -----------------------------------------------------------


def test_unlisted_asset_is_rejected(engine, proposal, clean_context):
    p = replace(proposal, assets=(AssetId("PEPE", Chain.ETHEREUM),))
    assert RejectReason.ASSET_NOT_WHITELISTED in reasons(engine, p, clean_context)


def test_unlisted_venue_is_rejected(engine, proposal, clean_context):
    p = replace(proposal, venues=(VenueId("randomswap", VenueKind.DEX),))
    assert RejectReason.VENUE_NOT_WHITELISTED in reasons(engine, p, clean_context)


# --- data integrity -------------------------------------------------------


def test_stale_quote_is_rejected(engine, proposal, clean_context):
    p = replace(proposal, quote_age_ms=clean_context.limits.max_quote_age_ms + 1)
    assert RejectReason.QUOTE_EXPIRED in reasons(engine, p, clean_context)


def test_clock_drift_is_rejected_in_both_directions(engine, proposal, clean_context):
    limit = clean_context.limits.max_clock_drift_ms
    for drift in (limit + 1, -(limit + 1)):
        p = replace(proposal, clock_drift_ms=drift)
        assert RejectReason.CLOCK_DRIFT in reasons(engine, p, clean_context)


def test_excess_expected_latency_is_rejected(engine, proposal, clean_context):
    p = replace(
        proposal,
        expected_execution_latency_ms=clean_context.limits.max_execution_latency_ms + 1,
    )
    assert RejectReason.LATENCY_EXCEEDED in reasons(engine, p, clean_context)


def test_low_data_quality_is_rejected(engine, proposal, clean_context):
    p = replace(proposal, data_quality_score=Decimal("0.5"))
    assert RejectReason.DATA_QUALITY_BELOW_THRESHOLD in reasons(engine, p, clean_context)


def test_stale_exposure_snapshot_is_rejected(engine, proposal, clean_context):
    ctx = replace(clean_context, now=NOW + timedelta(seconds=30))
    assert RejectReason.DATA_STALE in reasons(engine, proposal, ctx)


# --- economics ------------------------------------------------------------


def test_any_unestimated_cost_line_rejects(engine, proposal, clean_context):
    p = replace(proposal, costs=replace(proposal.costs, bridge_fees=None))
    assert reasons(engine, p, clean_context) == {RejectReason.FEE_UNCERTAIN}


def test_unknown_cost_is_not_treated_as_zero(engine, proposal, clean_context):
    """The gross spread is comfortably positive; the unknown alone must reject."""
    p = replace(
        proposal,
        gross_expected_profit=Decimal("1000"),
        costs=replace(proposal.costs, gas=None),
    )
    assert not engine.evaluate(p, clean_context).accepted


def test_gross_spread_is_not_profit(engine, proposal, clean_context):
    """Positive gross, negative after costs, must be rejected."""
    p = replace(
        proposal,
        gross_expected_profit=Decimal("0.50"),
        costs=complete_costs(cex_trading_fees=Decimal("2.00")),
    )
    assert RejectReason.NEGATIVE_NET_PROFIT in reasons(engine, p, clean_context)


def test_thin_edge_fails_the_safety_margin(engine, proposal, clean_context):
    """Net-positive but not positive enough once costs are inflated."""
    p = replace(proposal, gross_expected_profit=Decimal("1.35"))
    result = reasons(engine, p, clean_context)
    assert RejectReason.BELOW_SAFETY_MARGIN in result
    assert RejectReason.NEGATIVE_NET_PROFIT not in result


def test_missing_probabilities_make_an_opportunity_research_only(
    engine, proposal, clean_context
):
    p = replace(proposal, probabilities=None)
    assert RejectReason.NEGATIVE_RISK_ADJUSTED_EV in reasons(engine, p, clean_context)


def test_high_failure_probability_kills_a_nominally_profitable_trade(
    engine, proposal, clean_context
):
    p = replace(
        proposal,
        gross_expected_profit=Decimal("2.00"),
        costs=complete_costs(expected_execution_loss=Decimal("40")),
        probabilities=probabilities(
            full_execution=Decimal("0.20"),
            partial_execution=Decimal("0.10"),
            failure=Decimal("0.50"),
            opportunity_decay=Decimal("0.15"),
            adverse_price_move=Decimal("0.05"),
        ),
    )
    assert RejectReason.NEGATIVE_RISK_ADJUSTED_EV in reasons(engine, p, clean_context)


# --- microstructure -------------------------------------------------------


def test_excess_slippage_is_rejected(engine, proposal, clean_context):
    p = replace(proposal, expected_slippage=clean_context.limits.max_slippage + Decimal("0.001"))
    assert RejectReason.SLIPPAGE_EXCEEDED in reasons(engine, p, clean_context)


def test_excess_price_impact_is_rejected(engine, proposal, clean_context):
    p = replace(
        proposal, expected_price_impact=clean_context.limits.max_price_impact + Decimal("0.001")
    )
    assert RejectReason.PRICE_IMPACT_EXCEEDED in reasons(engine, p, clean_context)


# --- exposure and activity ------------------------------------------------


def test_non_atomic_trades_get_a_tighter_size_ceiling(engine, proposal, clean_context):
    """Same notional: allowed when atomic, refused when non-atomic."""
    size = clean_context.limits.max_trade_size / 2
    non_atomic = replace(
        proposal,
        notional=size,
        atomicity=Atomicity.NON_ATOMIC,
        gross_expected_profit=Decimal("5"),
    )
    atomic = replace(non_atomic, atomicity=Atomicity.ATOMIC)
    assert RejectReason.TRADE_SIZE_EXCEEDED in reasons(engine, non_atomic, clean_context)
    assert RejectReason.TRADE_SIZE_EXCEEDED not in reasons(engine, atomic, clean_context)


def test_total_exposure_ceiling(engine, proposal, clean_context):
    ctx = replace(
        clean_context,
        exposure=replace(
            clean_context.exposure,
            total_exposure=clean_context.limits.max_total_exposure,
        ),
    )
    assert RejectReason.TOTAL_EXPOSURE_EXCEEDED in reasons(engine, proposal, ctx)


def test_asset_exposure_ceiling(engine, proposal, clean_context):
    ctx = replace(
        clean_context,
        exposure=replace(
            clean_context.exposure,
            per_asset={BTC: clean_context.limits.max_asset_exposure},
        ),
    )
    assert RejectReason.ASSET_EXPOSURE_EXCEEDED in reasons(engine, proposal, ctx)


def test_chain_exposure_ceiling(engine, proposal, clean_context):
    ctx = replace(
        clean_context,
        exposure=replace(
            clean_context.exposure,
            per_chain={Chain.BITCOIN: clean_context.limits.max_chain_exposure},
        ),
    )
    assert RejectReason.CHAIN_EXPOSURE_EXCEEDED in reasons(engine, proposal, ctx)


def test_venue_counterparty_cap_binds_when_tighter_than_the_risk_limit(
    engine, proposal, clean_context
):
    """The whitelist's per-venue cap is honoured even below the global limit."""
    tight = clean_context.whitelist.venues[BINANCE]
    clean_context.whitelist.venues[BINANCE] = replace(tight, max_balance=Decimal("50"))
    p = replace(proposal, venues=(BINANCE,))
    assert RejectReason.EXCHANGE_EXPOSURE_EXCEEDED in reasons(engine, p, clean_context)


def test_daily_loss_limit_stops_new_trades(engine, proposal, clean_context):
    ctx = replace(
        clean_context,
        exposure=replace(
            clean_context.exposure, daily_pnl=-clean_context.limits.max_daily_loss - 1
        ),
    )
    assert RejectReason.DAILY_LOSS_LIMIT_REACHED in reasons(engine, proposal, ctx)


def test_profitable_day_does_not_trip_the_loss_limit(engine, proposal, clean_context):
    ctx = replace(
        clean_context, exposure=replace(clean_context.exposure, daily_pnl=Decimal("10000"))
    )
    assert RejectReason.DAILY_LOSS_LIMIT_REACHED not in reasons(engine, proposal, ctx)


def test_daily_trade_count_limit(engine, proposal, clean_context):
    ctx = replace(
        clean_context,
        exposure=replace(
            clean_context.exposure, trades_today=clean_context.limits.max_daily_trades
        ),
    )
    assert RejectReason.DAILY_TRADE_LIMIT_REACHED in reasons(engine, proposal, ctx)


def test_concurrent_trade_limit(engine, proposal, clean_context):
    ctx = replace(
        clean_context,
        exposure=replace(
            clean_context.exposure,
            concurrent_trades=clean_context.limits.max_concurrent_trades,
        ),
    )
    assert RejectReason.CONCURRENT_TRADE_LIMIT_REACHED in reasons(engine, proposal, ctx)


# --- inventory ------------------------------------------------------------


def test_unverified_inventory_rejects(engine, proposal, clean_context):
    p = replace(proposal, inventory_sufficient=None)
    assert RejectReason.INSUFFICIENT_INVENTORY in reasons(engine, p, clean_context)


def test_inventory_imbalance_stops_new_trades(engine, proposal, clean_context):
    ctx = replace(
        clean_context,
        exposure=replace(
            clean_context.exposure,
            inventory_imbalance=clean_context.limits.max_inventory_imbalance + 1,
        ),
    )
    assert RejectReason.INVENTORY_IMBALANCE in reasons(engine, proposal, ctx)


# --- budgets --------------------------------------------------------------


def test_exhausted_budget_rejects(engine, proposal, clean_context):
    clean_context.strategy_budget.get(RiskCategory.EXECUTION).consume(Decimal("50"))
    assert RejectReason.RISK_BUDGET_EXHAUSTED in reasons(engine, proposal, clean_context)


def test_reserving_an_unallocated_category_rejects(engine, proposal, clean_context):
    p = replace(proposal, budget_reservation={RiskCategory.SMART_CONTRACT: Decimal("1")})
    assert RejectReason.RISK_BUDGET_EXHAUSTED in reasons(engine, p, clean_context)


def test_a_trade_that_reserves_nothing_is_rejected(engine, proposal, clean_context):
    p = replace(proposal, budget_reservation={})
    assert RejectReason.CHECK_UNEVALUABLE in reasons(engine, p, clean_context)


def test_budget_for_the_wrong_strategy_cannot_authorise(engine, proposal, clean_context):
    other = StrategyBudget(StrategyKind.DEX_DEX)
    other.allocate(RiskCategory.EXECUTION, Decimal("1000"))
    ctx = replace(clean_context, strategy_budget=other)
    assert RejectReason.CHECK_UNEVALUABLE in reasons(engine, proposal, ctx)


# --- cross-cutting properties --------------------------------------------


def test_all_failures_are_reported_not_just_the_first(engine, proposal, clean_context):
    p = replace(
        proposal,
        expected_slippage=Decimal("0.9"),
        expected_price_impact=Decimal("0.9"),
        data_quality_score=Decimal("0.1"),
    )
    result = reasons(engine, p, clean_context)
    assert {
        RejectReason.SLIPPAGE_EXCEEDED,
        RejectReason.PRICE_IMPACT_EXCEEDED,
        RejectReason.DATA_QUALITY_BELOW_THRESHOLD,
    } <= result


def test_decisions_are_reproducible(engine, proposal, clean_context):
    first = engine.evaluate(proposal, clean_context)
    second = engine.evaluate(proposal, clean_context)
    assert first.as_dict()["checks"] == second.as_dict()["checks"]
    assert first.verdict is second.verdict


def test_decision_record_never_carries_credentials(engine, proposal, clean_context):
    import json

    blob = json.dumps(engine.evaluate(proposal, clean_context).as_dict()).lower()
    for forbidden in ("secret", "api_key", "apikey", "private_key", "password", "token"):
        assert forbidden not in blob


def test_a_huge_expected_profit_cannot_buy_past_a_hard_limit(engine, proposal, clean_context):
    """No score, edge or expected value may override a hard limit."""
    p = replace(
        proposal,
        gross_expected_profit=Decimal("1000000"),
        notional=clean_context.limits.max_total_exposure * 10,
    )
    decision = engine.evaluate(p, clean_context)
    assert not decision.accepted
    assert RejectReason.TRADE_SIZE_EXCEEDED in set(decision.reasons)


@pytest.mark.parametrize(
    "field,value",
    [
        ("expected_slippage", Decimal("0.9")),
        ("data_quality_score", Decimal("0.1")),
        ("quote_age_ms", 999999),
    ],
)
def test_single_bad_input_never_yields_an_accept(engine, proposal, clean_context, field, value):
    p = replace(proposal, **{field: value})
    assert not engine.evaluate(p, clean_context).accepted


def test_empty_exposure_snapshot_still_evaluates(engine, proposal, clean_context):
    ctx = replace(
        clean_context,
        exposure=ExposureSnapshot(as_of=NOW, total_exposure=Decimal("0"), reconciled=True),
    )
    decision = engine.evaluate(proposal, ctx)
    assert decision.accepted, decision.failures
