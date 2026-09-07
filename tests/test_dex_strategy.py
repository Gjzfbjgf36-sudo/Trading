"""DEX/DEX strategy: sizing, atomicity and on-chain execution semantics."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from arbcore.adapters.synthetic_dex import DexMarketConfig, PoolConfig, SyntheticDexMarket
from arbcore.config.universe import ETH, USDC_ETHEREUM
from arbcore.costs.gas import GasBook, GasModel, GasUnknown, InclusionModel
from arbcore.domain.types import Atomicity, Chain
from arbcore.execution.chain import ArbitrageTx, PaperChain, TxState, simulate
from arbcore.pricing.amm import Pool
from arbcore.strategy.dex_dex import DexDexConfig, DexDexStrategy, dex_venue

NOW = datetime(2026, 1, 1, tzinfo=UTC)
VENUES = (dex_venue("poola"), dex_venue("poolb"))


def gas_book(base="0.00008", priority="0.00004") -> GasBook:
    return GasBook(
        {
            Chain.ETHEREUM: GasModel(
                chain=Chain.ETHEREUM,
                gas_units=Decimal("250000"),
                base_fee_per_unit=Decimal(base),
                priority_fee_per_unit=Decimal(priority),
            )
        }
    )


def pools(dislocation: str) -> dict[str, Pool]:
    quote_b = (Decimal("3000000") * (Decimal(1) + Decimal(dislocation))).quantize(Decimal("1"))
    return {
        "a": Pool("a", ETH, USDC_ETHEREUM, Decimal("1000"), Decimal("3000000"), Decimal("0.003")),
        "b": Pool("b", ETH, USDC_ETHEREUM, Decimal("1000"), quote_b, Decimal("0.003")),
    }


def strategy(gas: GasBook | None = None, atomic: bool = True) -> DexDexStrategy:
    return DexDexStrategy(
        DexDexConfig(
            chain=Chain.ETHEREUM,
            base=ETH,
            quote=USDC_ETHEREUM,
            max_notional=Decimal("200000"),
        ),
        gas or gas_book(),
        InclusionModel(Decimal("0.6"), Decimal("0.25")),
        atomic=atomic,
    )


def detect(strat: DexDexStrategy, dislocation: str, budget: str = "500000"):
    return strat.detect(
        pools(dislocation),
        now=NOW,
        opportunity_id="X",
        available_quote=Decimal(budget),
    )


# --- the fixed-cost floor ------------------------------------------------


def test_a_small_dislocation_is_not_tradeable_at_l1_gas():
    """30 USD per attempt needs an edge the pool curve will not permit."""
    assert detect(strategy(), "0.005") is None


def test_the_same_dislocation_is_tradeable_at_l2_gas():
    """The only thing that changed is the fixed cost."""
    cheap = GasBook(
        {
            Chain.ETHEREUM: GasModel(
                chain=Chain.ETHEREUM,
                gas_units=Decimal("250000"),
                base_fee_per_unit=Decimal("0.0000008"),
                priority_fee_per_unit=Decimal("0.0000004"),
            )
        }
    )
    assert detect(strategy(cheap), "0.01") is not None


def test_sizing_window_explains_why_no_size_works():
    strat = strategy()
    window = strat.size_window(pools("0.005")["a"], pools("0.005")["b"], Decimal("0.002"))
    assert not window.viable
    assert "no size satisfies both" in window.reason


def test_size_is_the_profit_maximum_not_the_impact_ceiling():
    """Sizing at the ceiling deliberately picks the most expensive permitted trade."""
    strat = strategy()
    opportunity = detect(strat, "0.02")
    assert opportunity is not None
    window = strat.size_window(
        opportunity.buy_pool, opportunity.sell_pool, opportunity.edge_fraction
    )
    assert opportunity.input_quote <= window.maximum
    # A larger trade would earn less after impact.
    bigger = strat._price(
        opportunity.buy_pool,
        opportunity.sell_pool,
        opportunity.input_quote * Decimal("1.8"),
        "bigger",
        NOW,
    )
    assert bigger is None or bigger.gross_profit < opportunity.gross_profit


def test_no_gas_model_raises_rather_than_guessing():
    strat = DexDexStrategy(
        DexDexConfig(Chain.SOLANA, ETH, USDC_ETHEREUM, Decimal("1000")),
        gas_book(),
        InclusionModel(Decimal("1"), Decimal("0")),
    )
    with pytest.raises(GasUnknown):
        strat.size_window(pools("0.02")["a"], pools("0.02")["b"], Decimal("0.01"))


# --- atomicity -----------------------------------------------------------


def proposal_for(strat: DexDexStrategy, dislocation: str = "0.02"):
    opportunity = detect(strat, dislocation)
    assert opportunity is not None
    return opportunity, strat.to_proposal(
        opportunity,
        venues=VENUES,
        data_quality=Decimal("0.99"),
        quote_age_ms=0,
        clock_drift_ms=0,
        expected_latency_ms=12_000,
        probabilities=None,
        calibration=False,
    )


def test_atomic_execution_has_no_leg_risk():
    """A reverted round trip leaves the position untouched."""
    _, proposal = proposal_for(strategy(atomic=True))
    assert proposal.atomicity is Atomicity.ATOMIC
    assert proposal.costs.expected_execution_loss == Decimal(0)
    assert proposal.costs.partial_fill_cost == Decimal(0)


def test_non_atomic_execution_is_charged_for_leg_risk():
    _, proposal = proposal_for(strategy(atomic=False))
    assert proposal.atomicity is Atomicity.NON_ATOMIC
    assert proposal.costs.expected_execution_loss > Decimal(0)


def test_only_atomic_strategies_may_declare_a_reduced_exposure_delta():
    """A non-atomic failure is a one-sided position: the full notional."""
    _, atomic = proposal_for(strategy(atomic=True))
    _, non_atomic = proposal_for(strategy(atomic=False))
    assert atomic.worst_case_exposure_delta() < atomic.notional
    assert non_atomic.worst_case_exposure_delta() == non_atomic.notional


def test_cex_and_bridge_cost_lines_are_explicit_zeros():
    _, proposal = proposal_for(strategy())
    assert proposal.costs.cex_trading_fees == Decimal(0)
    assert proposal.costs.bridge_fees == Decimal(0)
    assert proposal.costs.withdrawal_fees == Decimal(0)
    assert proposal.costs.complete


def test_gas_is_charged_even_though_it_is_not_proportional_to_size():
    _, proposal = proposal_for(strategy())
    assert proposal.costs.gas > Decimal(0)


# --- on-chain execution --------------------------------------------------


def a_tx(key: str = "k1", min_output: str = "1") -> ArbitrageTx:
    return ArbitrageTx(
        idempotency_key=key,
        opportunity_id="X",
        input_amount=Decimal("1000"),
        min_output=Decimal(min_output),
    )


def chain(seed: int = 1, incl="1", rev="0", unknown: float = 0.0) -> PaperChain:
    return PaperChain(
        gas_book().get(Chain.ETHEREUM),
        InclusionModel(Decimal(incl), Decimal(rev)),
        seed=seed,
        unknown_probability=unknown,
    )


def submit(c: PaperChain, tx: ArbitrageTx, dislocation: str = "0.02") -> ArbitrageTx:
    p = pools(dislocation)
    return c.submit(tx, p["a"], p["b"], USDC_ETHEREUM, ETH, now=NOW)


def test_profit_is_output_minus_input_minus_gas():
    tx = submit(chain(), a_tx())
    assert tx.state is TxState.CONFIRMED
    assert tx.gas_paid > Decimal(0)
    expected = (tx.realised_output - tx.input_amount - tx.gas_paid).quantize(
        Decimal("0.00000001")
    )
    assert tx.profit == expected


def test_a_loose_minimum_output_lets_a_losing_trade_confirm():
    """Why min_output must be derived from the expected output, not set low.

    With min_output = 1 the on-chain guard permits any outcome, and gas alone
    turns a thin round trip into a loss that the chain happily confirms. The
    guard is the only thing standing between us and paying to trade.
    """
    tx = submit(chain(), a_tx(min_output="1"))
    assert tx.state is TxState.CONFIRMED
    assert tx.profit < Decimal(0)


def test_a_correctly_derived_minimum_output_reverts_that_same_trade():
    p = pools("0.02")
    expected = simulate(
        p["a"], p["b"], USDC_ETHEREUM, ETH, Decimal("1000"), Decimal("0")
    ).expected_output
    # Require the round trip to at least return the capital plus gas.
    tx = a_tx(key="k2", min_output=str(Decimal("1000") + Decimal("30")))
    assert expected < Decimal("1030")
    submit(chain(), tx)
    assert tx.state is TxState.REVERTED


def test_a_revert_still_costs_gas():
    """The cost of being wrong, and it is charged."""
    c = chain()
    c.inject_failure("revert")
    tx = submit(c, a_tx())
    assert tx.state is TxState.REVERTED
    assert tx.gas_paid > Decimal(0)
    assert tx.profit < Decimal(0)


def test_a_dropped_transaction_costs_nothing_on_chain():
    c = chain()
    c.inject_failure("dropped")
    tx = submit(c, a_tx())
    assert tx.state is TxState.DROPPED
    assert tx.gas_paid == Decimal(0)


def test_minimum_output_makes_an_unprofitable_round_trip_revert():
    """No half-executed arbitrage: it reverts as a whole."""
    tx = submit(chain(), a_tx(min_output="99999999"))
    assert tx.state is TxState.REVERTED


def test_simulation_catches_the_same_condition_as_the_revert():
    p = pools("0.02")
    result = simulate(p["a"], p["b"], USDC_ETHEREUM, ETH, Decimal("1000"), Decimal("99999999"))
    assert not result.succeeds
    assert "below minimum" in result.reason


def test_unknown_transaction_state_is_never_resent():
    c = chain(unknown=1.0)
    tx = submit(c, a_tx())
    assert tx.state is TxState.UNKNOWN
    # The only permitted next step is asking the chain.
    assert c.receipt(tx.idempotency_key) is tx
    assert tx.status_queries == 1
    assert len(c.transactions) == 1


def test_duplicate_submission_is_refused_by_idempotency_key():
    c = chain()
    tx = a_tx()
    submit(c, tx)
    again = submit(c, a_tx())  # same key, new object
    assert again is tx
    assert "duplicate" in again.detail


def test_congestion_multiplies_the_cost_of_an_attempt():
    c = chain()
    c.inject_failure("congestion")
    tx = submit(c, a_tx(min_output="99999999"))  # forced revert to expose gas
    assert tx.gas_paid > gas_book().get(Chain.ETHEREUM).total_cost


# --- synthetic pool market ----------------------------------------------


def test_competitors_close_divergence():
    """Set the rate to zero and any strategy looks superb; that is the point."""
    config = DexMarketConfig(
        pools=(
            PoolConfig("a", Decimal("1000"), Decimal("3000000")),
            PoolConfig("b", Decimal("800"), Decimal("2600000")),
        ),
        flow_volatility=Decimal("0"),
        competitor_arbitrage_rate=Decimal("0.5"),
    )
    market = SyntheticDexMarket(ETH, USDC_ETHEREUM, config, seed=1)
    before = market.divergence
    for _ in range(5):
        market.step()
    assert market.divergence < before


def test_our_own_swap_consumes_the_opportunity():
    """Without this a backtest can take the same spread forever."""
    from arbcore.pricing.amm import quote_swap

    config = DexMarketConfig(
        pools=(
            PoolConfig("a", Decimal("1000"), Decimal("3000000")),
            PoolConfig("b", Decimal("1000"), Decimal("3060000")),
        ),
        competitor_arbitrage_rate=Decimal("0"),
    )
    market = SyntheticDexMarket(ETH, USDC_ETHEREUM, config, seed=1)
    before = market.divergence
    swap = quote_swap(market.pools["a"], USDC_ETHEREUM, Decimal("20000"))
    market.apply_our_swap("a", swap)
    assert market.divergence < before


def test_a_market_needs_two_pools():
    with pytest.raises(ValueError):
        DexMarketConfig(pools=(PoolConfig("a", Decimal("1"), Decimal("1")),))
