"""Risk limits.

Every limit is data, never a literal buried in strategy code, so that a change
is a reviewable configuration diff (see docs/model-governance.md). Limits are
validated on load: a nonsensical configuration must fail at start-up, not at
the moment a trade is evaluated.

Sizes and losses are denominated in the accounting currency (USD by default);
fractions are expressed as decimals in [0, 1].
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..domain.types import ZERO, StrategyKind, to_decimal


class ConfigError(ValueError):
    """Raised when configuration is missing, malformed or internally inconsistent."""


@dataclass(frozen=True, slots=True)
class RiskLimits:
    """Hard limits. A limit is a wall, not a preference.

    No score, ranking or expected value may authorise crossing one of these.
    """

    # --- size and exposure ------------------------------------------------
    max_trade_size: Decimal
    max_total_exposure: Decimal
    max_asset_exposure: Decimal
    max_exchange_exposure: Decimal
    max_chain_exposure: Decimal
    # --- loss and activity ------------------------------------------------
    max_daily_loss: Decimal
    max_daily_trades: int
    max_concurrent_trades: int
    #: Separate daily budget for paper calibration trades. It does not weaken
    #: max_daily_trades: calibration is impossible outside paper mode, so this
    #: budget can never authorise real turnover. Without it, a 50-sample
    #: calibration target consumes a 50-trade daily limit exactly, and the
    #: strategy can never place a non-calibration trade on its first day.
    max_daily_calibration_trades: int
    # --- inventory --------------------------------------------------------
    max_inventory_imbalance: Decimal
    min_reserve_balance: Decimal
    max_rebalance_cost: Decimal
    # --- microstructure ---------------------------------------------------
    max_slippage: Decimal
    max_price_impact: Decimal
    max_quote_age_ms: int
    max_execution_latency_ms: int
    max_clock_drift_ms: int
    # --- economics --------------------------------------------------------
    #: Net expected profit must exceed this fraction of notional before a
    #: trade is even considered. Guards against fee/slippage model error.
    min_net_profit_margin: Decimal
    #: Multiplier applied to modelled costs when computing the safety-margin
    #: adjusted profit. >= 1; costs are assumed understated, never overstated.
    cost_safety_factor: Decimal
    #: Minimum data quality score (0..1) for an opportunity to be actionable.
    min_data_quality_score: Decimal
    #: Peg deviation beyond which stablecoin-dependent strategies are disabled.
    max_stablecoin_depeg: Decimal

    _FRACTIONS = (
        "max_slippage",
        "max_price_impact",
        "min_net_profit_margin",
        "min_data_quality_score",
        "max_stablecoin_depeg",
    )
    _POSITIVE_AMOUNTS = (
        "max_trade_size",
        "max_total_exposure",
        "max_asset_exposure",
        "max_exchange_exposure",
        "max_chain_exposure",
        "max_daily_loss",
        "max_inventory_imbalance",
        "max_rebalance_cost",
    )
    _POSITIVE_INTS = (
        "max_daily_trades",
        "max_daily_calibration_trades",
        "max_concurrent_trades",
        "max_quote_age_ms",
        "max_execution_latency_ms",
        "max_clock_drift_ms",
    )

    def __post_init__(self) -> None:
        for name in self._POSITIVE_AMOUNTS:
            value = to_decimal(getattr(self, name))
            object.__setattr__(self, name, value)
            if value <= ZERO:
                raise ConfigError(f"{name} must be > 0 (got {value})")
        for name in self._FRACTIONS:
            value = to_decimal(getattr(self, name))
            object.__setattr__(self, name, value)
            if not (ZERO <= value <= Decimal(1)):
                raise ConfigError(f"{name} must be within [0, 1] (got {value})")
        for name in self._POSITIVE_INTS:
            value_int = getattr(self, name)
            if not isinstance(value_int, int) or isinstance(value_int, bool) or value_int <= 0:
                raise ConfigError(f"{name} must be a positive integer (got {value_int!r})")

        min_reserve = to_decimal(self.min_reserve_balance)
        object.__setattr__(self, "min_reserve_balance", min_reserve)
        if min_reserve < ZERO:
            raise ConfigError("min_reserve_balance must be >= 0")

        safety = to_decimal(self.cost_safety_factor)
        object.__setattr__(self, "cost_safety_factor", safety)
        if safety < Decimal(1):
            raise ConfigError(
                f"cost_safety_factor must be >= 1; a value below 1 assumes costs are "
                f"overstated (got {safety})"
            )

        # Structural consistency: a single trade can never be allowed to breach
        # a portfolio-level ceiling.
        if self.max_trade_size > self.max_total_exposure:
            raise ConfigError("max_trade_size must not exceed max_total_exposure")
        for narrower in ("max_asset_exposure", "max_exchange_exposure", "max_chain_exposure"):
            if getattr(self, narrower) > self.max_total_exposure:
                raise ConfigError(f"{narrower} must not exceed max_total_exposure")

    def as_dict(self) -> dict[str, str]:
        """String-valued mapping suitable for audit records and diffing."""
        return {f.name: str(getattr(self, f.name)) for f in fields(self)}


@dataclass(frozen=True, slots=True)
class StrategyLimits:
    """Per-strategy overrides layered on top of the global limits.

    Overrides may only ever tighten a limit. A strategy module cannot grant
    itself more room than the platform-wide configuration allows.
    """

    strategy: StrategyKind
    enabled: bool
    overrides: Mapping[str, Decimal | int]

    _TIGHTEN_BY_INCREASING = frozenset({"min_net_profit_margin", "min_data_quality_score"})

    def apply(self, base: RiskLimits) -> RiskLimits:
        known = {f.name for f in fields(RiskLimits)}
        values: dict[str, Any] = {name: getattr(base, name) for name in known}
        for key, raw in self.overrides.items():
            if key not in known:
                raise ConfigError(f"unknown risk limit override: {key!r}")
            current = values[key]
            new = raw if isinstance(current, int) and not isinstance(current, Decimal) else to_decimal(raw)  # noqa: E501
            if key in self._TIGHTEN_BY_INCREASING:
                if new < current:
                    raise ConfigError(
                        f"strategy {self.strategy} override {key}={new} loosens the global "
                        f"minimum {current}; overrides may only tighten limits"
                    )
            elif new > current:
                raise ConfigError(
                    f"strategy {self.strategy} override {key}={new} loosens the global "
                    f"limit {current}; overrides may only tighten limits"
                )
            values[key] = new
        return RiskLimits(**values)


def limits_from_mapping(data: Mapping[str, Any]) -> RiskLimits:
    """Build limits from a plain mapping, rejecting unknown or missing keys.

    Unknown keys are an error rather than being ignored: a typo'd limit name
    that is silently dropped would leave the default in force while the
    operator believes their tighter value is applied.
    """
    known = {f.name for f in fields(RiskLimits)}
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f"unknown risk limit keys: {sorted(unknown)}")
    missing = known - set(data)
    if missing:
        raise ConfigError(f"missing risk limit keys: {sorted(missing)}")
    int_fields = set(RiskLimits._POSITIVE_INTS)
    kwargs: dict[str, Any] = {}
    for name in known:
        raw = data[name]
        kwargs[name] = int(raw) if name in int_fields else to_decimal(str(raw))
    return RiskLimits(**kwargs)


def load_limits(path: str | Path) -> RiskLimits:
    """Load limits from a YAML file containing a top-level ``risk_limits`` map."""
    import yaml

    text = Path(path).read_text(encoding="utf-8")
    document = yaml.safe_load(text)
    if not isinstance(document, dict) or "risk_limits" not in document:
        raise ConfigError(f"{path}: expected a top-level 'risk_limits' mapping")
    section = document["risk_limits"]
    if not isinstance(section, dict):
        raise ConfigError(f"{path}: 'risk_limits' must be a mapping")
    return limits_from_mapping(section)
