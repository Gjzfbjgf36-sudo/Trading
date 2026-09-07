from decimal import Decimal

import pytest

from arbcore.domain.types import Amount, AssetId, Chain, Side, VenueId, VenueKind, to_decimal


def test_float_is_refused_everywhere():
    """Floats must never enter monetary arithmetic, even 'harmless' ones."""
    with pytest.raises(TypeError):
        to_decimal(0.1)  # type: ignore[arg-type]


def test_bool_is_not_a_quantity():
    with pytest.raises(TypeError):
        to_decimal(True)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", ["nan", "Infinity", "-Infinity"])
def test_non_finite_rejected(bad):
    with pytest.raises(ValueError):
        to_decimal(bad)


def test_same_symbol_on_different_chains_is_a_different_asset():
    assert AssetId("USDC", Chain.ETHEREUM) != AssetId("USDC", Chain.SOLANA)


def test_amounts_of_different_assets_cannot_be_combined():
    eth_usdc = Amount(AssetId("USDC", Chain.ETHEREUM), Decimal("1"))
    sol_usdc = Amount(AssetId("USDC", Chain.SOLANA), Decimal("1"))
    with pytest.raises(ValueError):
        _ = eth_usdc + sol_usdc


def test_amount_arithmetic_is_exact():
    asset = AssetId("BTC", Chain.BITCOIN)
    total = Amount(asset, "0.1") + Amount(asset, "0.2")
    assert total.quantity == Decimal("0.3")


def test_side_opposite():
    assert Side.BUY.opposite is Side.SELL
    assert Side.SELL.opposite is Side.BUY


def test_identifier_normalisation_is_enforced():
    with pytest.raises(ValueError):
        AssetId("btc", Chain.BITCOIN)
    with pytest.raises(ValueError):
        VenueId("Coinbase", VenueKind.CEX)
