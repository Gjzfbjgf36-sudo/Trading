"""AMM pricing, gas economics and the fixed-cost floor."""

from decimal import Decimal

import pytest

from arbcore.config.universe import ETH, USDC_ETHEREUM
from arbcore.costs.gas import GasBook, GasModel, GasUnknown, InclusionModel
from arbcore.domain.types import Chain
from arbcore.pricing.amm import (
    Pool,
    PoolUnusable,
    apply_swap,
    max_input_for_impact,
    quote_swap,
)


def pool(base="1000", quote="3000000", fee="0.003") -> Pool:
    return Pool("p", ETH, USDC_ETHEREUM, Decimal(base), Decimal(quote), Decimal(fee))


# --- AMM -----------------------------------------------------------------


def test_spot_price_is_the_reserve_ratio():
    assert pool().spot_price == Decimal("3000")


def test_empty_pool_has_no_price():
    with pytest.raises(PoolUnusable):
        Pool("p", ETH, USDC_ETHEREUM, Decimal("0"), Decimal("1"), Decimal("0.003"))


def test_output_is_always_worse_than_the_spot_equivalent():
    """The pool's spot price is not the price we get."""
    q = quote_swap(pool(), ETH, Decimal("1"))
    assert q.output_amount < Decimal("3000")


def test_impact_grows_with_size():
    impacts = [quote_swap(pool(), ETH, Decimal(s)).curve_impact for s in ("1", "10", "100")]
    assert impacts[0] < impacts[1] < impacts[2]


def test_curve_impact_excludes_the_fee_and_price_impact_includes_it():
    """Conflating them makes any impact limit below the pool fee unsatisfiable."""
    q = quote_swap(pool(fee="0.003"), ETH, Decimal("1"))
    assert q.fee_fraction == Decimal("0.003")
    assert q.curve_impact < Decimal("0.003")
    assert q.price_impact == (q.fee_fraction + q.curve_impact).quantize(
        Decimal("0.000000000000000001")
    )


def test_a_zero_fee_pool_still_has_curve_impact():
    q = quote_swap(pool(fee="0"), ETH, Decimal("50"))
    assert q.fee_fraction == Decimal(0)
    assert q.curve_impact > Decimal(0)


def test_impact_ceiling_is_respected():
    limit = Decimal("0.003")
    size = max_input_for_impact(pool(), ETH, limit)
    assert size > Decimal(0)
    assert quote_swap(pool(), ETH, size).curve_impact <= limit


def test_impact_ceiling_below_the_fee_is_still_satisfiable():
    """Because it bounds the curve, not the fee."""
    assert max_input_for_impact(pool(fee="0.003"), ETH, Decimal("0.001")) > Decimal(0)


def test_swap_of_a_foreign_asset_is_refused():
    from arbcore.config.universe import BTC

    with pytest.raises(PoolUnusable):
        quote_swap(pool(), BTC, Decimal("1"))


def test_zero_and_negative_inputs_are_refused():
    for bad in ("0", "-1"):
        with pytest.raises(PoolUnusable):
            quote_swap(pool(), ETH, Decimal(bad))


def test_applying_a_swap_moves_the_reserves_the_right_way():
    p = pool()
    q = quote_swap(p, ETH, Decimal("10"))
    after = apply_swap(p, q)
    assert after.reserve_base == p.reserve_base + Decimal("10")
    assert after.reserve_quote < p.reserve_quote
    # Selling base into the pool makes base cheaper.
    assert after.spot_price < p.spot_price


def test_a_swap_that_would_drain_the_pool_is_refused():
    with pytest.raises(PoolUnusable):
        quote_swap(pool(), USDC_ETHEREUM, Decimal("10") ** 30)


# --- gas -----------------------------------------------------------------


def gas_model(base="0.00008", priority="0.00004") -> GasModel:
    return GasModel(
        chain=Chain.ETHEREUM,
        gas_units=Decimal("250000"),
        base_fee_per_unit=Decimal(base),
        priority_fee_per_unit=Decimal(priority),
    )


def test_gas_is_a_fixed_cost_per_attempt():
    assert gas_model().total_cost == Decimal("30")


def test_minimum_viable_notional_falls_as_the_edge_grows():
    gas = gas_model()
    assert gas.minimum_viable_notional(Decimal("0.001")) == Decimal("30000")
    assert gas.minimum_viable_notional(Decimal("0.01")) == Decimal("3000")


def test_no_size_covers_a_non_positive_edge():
    """Returning a large number would imply a viable size exists."""
    for edge in ("0", "-0.01"):
        with pytest.raises(ValueError):
            gas_model().minimum_viable_notional(Decimal(edge))


def test_congestion_multiplies_base_and_priority_together():
    spiked = gas_model().with_congestion(Decimal("5"))
    assert spiked.total_cost == Decimal("150")


def test_unknown_chain_has_no_gas_estimate():
    with pytest.raises(GasUnknown):
        GasBook({}).get(Chain.SOLANA)


def test_placeholder_gas_models_are_flagged_unconfirmed():
    assert not gas_model().confirmed
    assert GasBook({Chain.ETHEREUM: gas_model()}).unconfirmed() == (Chain.ETHEREUM,)


# --- inclusion -----------------------------------------------------------


def test_success_requires_both_inclusion_and_no_revert():
    model = InclusionModel(Decimal("0.5"), Decimal("0.2"))
    assert model.success_probability == Decimal("0.40")


def test_expected_gas_prices_reverts_as_well_as_successes():
    """A reverted transaction still costs gas; pretending otherwise is free money."""
    gas = gas_model()
    certain = InclusionModel(Decimal("1"), Decimal("0"))
    reverting = InclusionModel(Decimal("1"), Decimal("1"))
    assert certain.expected_gas_cost(gas) == gas.total_cost
    assert reverting.expected_gas_cost(gas) > Decimal(0)


def test_a_transaction_never_included_costs_nothing_on_chain():
    never = InclusionModel(Decimal("0"), Decimal("0"))
    assert never.expected_gas_cost(gas_model()) == Decimal(0)


def test_probabilities_outside_range_are_refused():
    with pytest.raises(ValueError):
        InclusionModel(Decimal("1.5"), Decimal("0"))
