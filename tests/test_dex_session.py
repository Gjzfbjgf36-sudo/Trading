"""DEX/DEX end-to-end session behaviour."""

from decimal import Decimal

import pytest

from arbcore.app.run_paper_dex import build_session
from arbcore.config.environment import TradingMode
from arbcore.domain.types import Chain
from arbcore.execution.chain import TxState
from arbcore.safety.safe_mode import SafeModeTrigger


def session(**overrides):
    kwargs = dict(
        chain=Chain.BASE,
        ticks=1_500,
        seed=101,
        tick_ms=12_000,
        db_path=None,
        inject_faults=True,
        limits_path="config/risk_limits.paper.yaml",
        wallet=Decimal("1800"),
        competitor_rate=Decimal("0.35"),
    )
    kwargs.update(overrides)
    return build_session(**kwargs)


def test_session_runs_and_stays_in_paper_mode():
    s = session()
    outcome = s.run()
    assert s.profile.trading_mode is TradingMode.PAPER
    assert not s.profile.uses_real_money
    assert outcome.report.opportunities_detected > 0


def test_session_is_deterministic():
    first = session().run()
    second = session().run()
    assert first.counters.as_dict() == second.counters.as_dict()
    assert first.chain.summary() == second.chain.summary()


def test_overfunded_wallet_refuses_to_start():
    """Every ceiling that applies to the wallet is checked, not only the total."""
    with pytest.raises(ValueError, match="max_asset_exposure|max_chain_exposure"):
        session(wallet=Decimal("4000"))


def test_l1_gas_makes_the_strategy_unviable_at_this_pool_depth():
    """The headline economic result, as a test."""
    outcome = session(chain=Chain.ETHEREUM, ticks=2_000).run()
    assert outcome.report.pnl.trades == 0
    assert outcome.sizing_blocks  # and it records *why*


def test_l2_gas_makes_the_same_market_tradeable():
    outcome = session(chain=Chain.BASE, ticks=4_000).run()
    assert outcome.chain.attempts > 0


def test_wallet_never_goes_negative():
    s = session(ticks=4_000)
    s.run()
    assert s.wallet >= Decimal(0)


def test_gas_is_paid_on_failures_too():
    outcome = session(ticks=4_000).run()
    if outcome.chain.reverted:
        assert outcome.chain.gas_spent > Decimal(0)


def test_unknown_transaction_engages_safe_mode_and_is_never_resent():
    # Fault injection is off so that the only outcome is the one under test.
    s = session(ticks=4_000, inject_faults=False)
    s.chain._unknown_probability = 1.0  # every submission goes unconfirmed
    outcome = s.run()
    if outcome.report.unknown_outcomes:
        assert SafeModeTrigger.UNKNOWN_TRANSACTION_STATE in {
            e.trigger for e in s.safe_mode.events
        }
        # One transaction object per key: nothing was submitted twice.
        keys = list(s.chain.transactions)
        assert len(keys) == len(set(keys))
        assert all(
            tx.state is TxState.UNKNOWN and tx.status_queries > 0
            for tx in s.chain.transactions.values()
        )


def test_never_included_means_no_gas_and_no_trades():
    s = session(ticks=3_000, inclusion_probability=Decimal("0"))
    outcome = s.run()
    assert outcome.chain.confirmed == 0
    assert outcome.chain.gas_spent == Decimal(0)
    assert outcome.report.pnl.realised == Decimal(0)


def test_fast_competitors_remove_the_strategy_entirely():
    """The result depends on an unmeasured parameter; this is the cliff."""
    slow = session(ticks=4_000, competitor_rate=Decimal("0.10")).run()
    fast = session(ticks=4_000, competitor_rate=Decimal("0.90")).run()
    assert slow.chain.attempts > 0
    assert fast.chain.attempts == 0


def test_every_unknown_outcome_costs_a_human_intervention():
    """The operability limit: one halt per unresolved transaction, by design."""
    s = session(ticks=4_000, inject_faults=False)
    s.chain._unknown_probability = 1.0
    outcome = s.run()
    assert outcome.interventions == outcome.report.unknown_outcomes > 0


def test_calibration_trades_are_kept_out_of_strategy_performance():
    outcome = session(ticks=4_000).run()
    if outcome.report.calibration_trades:
        combined = outcome.report.as_dict()["combined_pnl_all_trades"]
        assert combined != outcome.report.as_dict()["net_pnl_strategy"]


def test_sizing_blocks_record_why_nothing_traded():
    outcome = session(chain=Chain.ETHEREUM, ticks=2_000).run()
    assert set(outcome.sizing_blocks) <= {
        "no_positive_edge_after_pool_fees",
        "fixed_cost_exceeds_impact_cap",
        "wallet_below_required_size",
    }
