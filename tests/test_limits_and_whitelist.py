from decimal import Decimal

import pytest

from arbcore.config.limits import (
    ConfigError,
    RiskLimits,
    StrategyLimits,
    limits_from_mapping,
    load_limits,
)
from arbcore.config.universe import BTC, COINBASE
from arbcore.config.whitelist import Confidence, WhitelistError
from arbcore.domain.types import AssetId, Chain, StrategyKind, VenueId, VenueKind


def test_shipped_paper_config_loads_and_validates():
    limits = load_limits("config/risk_limits.paper.yaml")
    assert limits.max_trade_size <= limits.max_total_exposure
    assert limits.cost_safety_factor >= Decimal(1)


def test_typo_in_a_limit_name_is_an_error_not_a_silent_default(limits):
    data = limits.as_dict()
    data["max_slipage"] = data.pop("max_slippage")
    with pytest.raises(ConfigError, match="unknown risk limit keys"):
        limits_from_mapping(data)


def test_missing_limits_are_rejected(limits):
    data = limits.as_dict()
    del data["max_daily_loss"]
    with pytest.raises(ConfigError, match="missing risk limit keys"):
        limits_from_mapping(data)


def test_trade_size_above_total_exposure_is_rejected(limits):
    data = limits.as_dict()
    data["max_trade_size"] = str(Decimal(data["max_total_exposure"]) + 1)
    with pytest.raises(ConfigError, match="max_trade_size"):
        limits_from_mapping(data)


def test_cost_safety_factor_below_one_is_rejected(limits):
    data = limits.as_dict()
    data["cost_safety_factor"] = "0.9"
    with pytest.raises(ConfigError, match="cost_safety_factor"):
        limits_from_mapping(data)


def test_fraction_limits_must_stay_in_range(limits):
    data = limits.as_dict()
    data["max_slippage"] = "1.5"
    with pytest.raises(ConfigError):
        limits_from_mapping(data)


def test_strategy_override_may_tighten(limits):
    tighter = StrategyLimits(
        StrategyKind.CROSS_CHAIN, enabled=True, overrides={"max_trade_size": Decimal("50")}
    ).apply(limits)
    assert tighter.max_trade_size == Decimal("50")


def test_strategy_override_may_not_loosen(limits):
    with pytest.raises(ConfigError, match="loosens"):
        StrategyLimits(
            StrategyKind.CROSS_CHAIN,
            enabled=True,
            overrides={"max_trade_size": limits.max_trade_size + 1},
        ).apply(limits)


def test_minimum_style_override_loosening_is_also_caught(limits):
    """Lowering a minimum is loosening, even though the number goes down."""
    with pytest.raises(ConfigError, match="loosens"):
        StrategyLimits(
            StrategyKind.DEX_DEX,
            enabled=True,
            overrides={"min_data_quality_score": Decimal("0.10")},
        ).apply(limits)


def test_unknown_override_key_is_rejected(limits):
    with pytest.raises(ConfigError, match="unknown risk limit override"):
        StrategyLimits(
            StrategyKind.DEX_DEX, enabled=True, overrides={"max_yolo": Decimal("1")}
        ).apply(limits)


# --- whitelist -----------------------------------------------------------


def test_unknown_asset_raises_rather_than_returning_none(whitelist):
    with pytest.raises(WhitelistError):
        whitelist.asset(AssetId("DOGE", Chain.ETHEREUM))


def test_unknown_venue_raises(whitelist):
    with pytest.raises(WhitelistError):
        whitelist.venue(VenueId("someswap", VenueKind.DEX))


def test_same_symbol_other_chain_is_not_admitted_implicitly(whitelist):
    whitelist.asset(AssetId("SOL", Chain.SOLANA))
    with pytest.raises(WhitelistError):
        whitelist.asset(AssetId("SOL", Chain.ETHEREUM))


def test_no_protocols_are_admitted_in_phase_one(whitelist):
    assert whitelist.protocols == {}
    assert not whitelist.contract_permitted("0xdeadbeef", Chain.ETHEREUM)


def test_stablecoin_authority_findings_start_unknown(whitelist):
    usdc = whitelist.asset(AssetId("USDC", Chain.ETHEREUM))
    assert usdc.is_stablecoin
    assert usdc.freeze_authority is Confidence.UNKNOWN


def test_venue_carries_a_counterparty_cap(whitelist):
    assert whitelist.venue(COINBASE).max_balance > 0


def test_btc_is_admitted_as_native(whitelist):
    assert whitelist.asset(BTC).contract_address is None


def test_duplicate_registration_is_refused(whitelist):
    spec = whitelist.asset(BTC)
    with pytest.raises(ValueError):
        whitelist.add_asset(spec)


def test_require_all_reports_the_first_missing_entry(whitelist):
    with pytest.raises(WhitelistError):
        whitelist.require_all([AssetId("PEPE", Chain.ETHEREUM)], [])


def test_limits_as_dict_round_trips(limits):
    assert limits_from_mapping(limits.as_dict()) == limits


def test_risk_limits_reject_zero_sizes():
    with pytest.raises(ConfigError):
        RiskLimits(
            max_trade_size=Decimal(0),
            max_total_exposure=Decimal(1),
            max_asset_exposure=Decimal(1),
            max_exchange_exposure=Decimal(1),
            max_chain_exposure=Decimal(1),
            max_daily_loss=Decimal(1),
            max_daily_trades=1,
            max_concurrent_trades=1,
            max_inventory_imbalance=Decimal(1),
            min_reserve_balance=Decimal(0),
            max_rebalance_cost=Decimal(1),
            max_slippage=Decimal("0.1"),
            max_price_impact=Decimal("0.1"),
            max_quote_age_ms=1,
            max_execution_latency_ms=1,
            max_clock_drift_ms=1,
            min_net_profit_margin=Decimal("0.1"),
            cost_safety_factor=Decimal(1),
            min_data_quality_score=Decimal("0.5"),
            max_stablecoin_depeg=Decimal("0.01"),
        )
