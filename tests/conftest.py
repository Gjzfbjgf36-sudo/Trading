"""Shared fixtures.

``clean_context`` is the one configuration in which a proposal is *supposed*
to be accepted. Every rejection test starts from it and breaks exactly one
thing, so a test failure names the check that changed behaviour.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from helpers import NOW, complete_costs, probabilities

from arbcore.config.environment import Environment, RuntimeProfile, TradingMode
from arbcore.config.limits import load_limits
from arbcore.config.universe import BTC, COINBASE, KRAKEN, USDC_ETHEREUM, build_initial_whitelist
from arbcore.domain.types import Atomicity, Chain, StrategyKind
from arbcore.risk.budget import RiskCategory, StrategyBudget
from arbcore.risk.engine import RiskContext, RiskEngine
from arbcore.risk.exposure import ExposureSnapshot
from arbcore.risk.proposal import TradeProposal
from arbcore.safety.circuit_breaker import (
    BreakerCondition,
    BreakerPanel,
    CircuitBreaker,
)
from arbcore.safety.safe_mode import SafeMode
from arbcore.strategy.state import StrategyState, StrategyStatus

LIMITS_PATH = "config/risk_limits.paper.yaml"


@pytest.fixture
def limits():
    return load_limits(LIMITS_PATH)


@pytest.fixture
def whitelist():
    return build_initial_whitelist()


@pytest.fixture
def open_safe_mode() -> SafeMode:
    sm = SafeMode()
    sm.clear("test-operator", now=NOW)
    return sm


@pytest.fixture
def breakers() -> BreakerPanel:
    panel = BreakerPanel()
    panel.add(
        CircuitBreaker(BreakerCondition.EXECUTION_FAILURE_RATE, Decimal("0.10"))
    )
    panel.add(CircuitBreaker(BreakerCondition.DRAWDOWN, Decimal("100")))
    return panel


@pytest.fixture
def budget() -> StrategyBudget:
    b = StrategyBudget(StrategyKind.CEX_CEX)
    b.allocate(RiskCategory.EXECUTION, Decimal("50"))
    b.allocate(RiskCategory.MARKET, Decimal("50"))
    return b


@pytest.fixture
def status() -> StrategyStatus:
    s = StrategyStatus(StrategyKind.CEX_CEX)
    s.set_state(StrategyState.NORMAL, "test setup", actor="test")
    return s


@pytest.fixture
def exposure() -> ExposureSnapshot:
    return ExposureSnapshot(
        as_of=NOW,
        total_exposure=Decimal("1000"),
        per_asset={BTC: Decimal("500"), USDC_ETHEREUM: Decimal("500")},
        per_venue={COINBASE: Decimal("500"), KRAKEN: Decimal("500")},
        per_chain={Chain.BITCOIN: Decimal("500")},
        daily_pnl=Decimal("10"),
        trades_today=3,
        concurrent_trades=0,
        inventory_imbalance=Decimal("100"),
        reconciled=True,
    )


@pytest.fixture
def profile() -> RuntimeProfile:
    return RuntimeProfile(environment=Environment.PAPER, trading_mode=TradingMode.PAPER)


@pytest.fixture
def clean_context(profile, limits, whitelist, exposure, open_safe_mode, breakers, status, budget):
    return RiskContext(
        profile=profile,
        limits=limits,
        whitelist=whitelist,
        exposure=exposure,
        safe_mode=open_safe_mode,
        breakers=breakers,
        strategy_status=status,
        strategy_budget=budget,
        now=NOW,
    )


@pytest.fixture
def proposal() -> TradeProposal:
    """A modest, fully-specified CEX/CEX proposal that should be accepted."""
    return TradeProposal(
        proposal_id="OPP-TEST-1",
        strategy=StrategyKind.CEX_CEX,
        atomicity=Atomicity.NON_ATOMIC,
        notional=Decimal("100"),
        assets=(BTC,),
        venues=(COINBASE, KRAKEN),
        chains=(Chain.BITCOIN,),
        gross_expected_profit=Decimal("3.00"),
        costs=complete_costs(),
        expected_slippage=Decimal("0.0005"),
        expected_price_impact=Decimal("0.0010"),
        quote_age_ms=200,
        expected_execution_latency_ms=400,
        clock_drift_ms=20,
        data_quality_score=Decimal("0.97"),
        inventory_sufficient=True,
        probabilities=probabilities(),
        budget_reservation={RiskCategory.EXECUTION: Decimal("5")},
        created_at=NOW,
    )


@pytest.fixture
def engine() -> RiskEngine:
    return RiskEngine()
