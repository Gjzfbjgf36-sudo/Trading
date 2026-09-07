"""Configuration for the decision system.

One file holds the things you decided while calm: how much capital, what fee
your venue charges, how much you are prepared to lose. Retyping those on every
command is how a wrong number reaches the risk checks — so they are stored once
and read, not passed in.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..domain.types import ZERO, to_decimal
from .sizing import RiskProfile


class SettingsError(ValueError):
    """Raised when the configuration is missing or implausible."""


@dataclass(frozen=True, slots=True)
class DecideSettings:
    """Everything the gate needs that is not the signal itself."""

    #: Capital the account started with. Realised P/L is added to it.
    starting_capital: Decimal
    #: Your venue's taker fee as a fraction. Look it up; do not guess.
    fee_rate: Decimal
    #: Smallest order the venue accepts, in quote currency.
    min_notional: Decimal
    risk: RiskProfile
    symbol: str = "BTCUSD"
    journal_path: str = "journal/decisions.sqlite"
    max_open_positions: int = 3
    #: Minimum reward-to-risk. See GateConfig for why 1.0 is not enough.
    min_reward_to_risk: Decimal = Decimal("1.5")
    #: False until you have met every condition in docs/ANLEITUNG.md.
    real_money: bool = False

    def __post_init__(self) -> None:
        for name in ("starting_capital", "fee_rate", "min_notional"):
            value = to_decimal(getattr(self, name))
            object.__setattr__(self, name, value)
        if self.starting_capital <= ZERO:
            raise SettingsError("starting_capital must be > 0")
        if not (ZERO <= self.fee_rate < Decimal("0.05")):
            raise SettingsError(
                f"fee_rate of {self.fee_rate} is outside a plausible range. "
                "It is a fraction: 0.26% is 0.0026, not 0.26"
            )
        if self.min_notional < ZERO:
            raise SettingsError("min_notional must be >= 0")
        ratio = to_decimal(self.min_reward_to_risk)
        object.__setattr__(self, "min_reward_to_risk", ratio)
        if ratio < Decimal(1):
            raise SettingsError(
                f"min_reward_to_risk of {ratio} means the target pays less than "
                "the trade risks. No win rate a rule-based system achieves "
                "recovers from that."
            )

    def as_dict(self) -> dict[str, str]:
        return {
            "starting_capital": str(self.starting_capital),
            "fee_rate": str(self.fee_rate),
            "min_notional": str(self.min_notional),
            "symbol": self.symbol,
            "risk_per_trade": str(self.risk.risk_per_trade),
            "daily_loss_limit": str(self.risk.daily_loss_limit),
            "max_drawdown": str(self.risk.max_drawdown),
            "min_reward_to_risk": str(self.min_reward_to_risk),
            "real_money": str(self.real_money),
        }


DEFAULT_PATH = "config/decide.yaml"


def load_settings(path: str | Path = DEFAULT_PATH) -> DecideSettings:
    """Load settings from YAML, rejecting unknown keys.

    A mistyped key is an error rather than being ignored: silently keeping the
    default while the operator believes their value applies is exactly the
    failure this file exists to prevent.
    """
    import yaml

    file = Path(path)
    if not file.exists():
        raise SettingsError(
            f"{path} does not exist. Copy config/decide.example.yaml to {path} "
            "and fill in your own numbers."
        )
    document = yaml.safe_load(file.read_text(encoding="utf-8"))
    if not isinstance(document, Mapping):
        raise SettingsError(f"{path}: expected a mapping at the top level")
    return settings_from_mapping(document)


def settings_from_mapping(data: Mapping[str, Any]) -> DecideSettings:
    known = {
        "starting_capital",
        "fee_rate",
        "min_notional",
        "symbol",
        "journal_path",
        "max_open_positions",
        "min_reward_to_risk",
        "real_money",
        "risk_per_trade",
        "daily_loss_limit",
        "max_drawdown",
        "max_position_fraction",
    }
    unknown = set(data) - known
    if unknown:
        raise SettingsError(f"unknown settings: {sorted(unknown)}")

    risk = RiskProfile(
        risk_per_trade=to_decimal(str(data.get("risk_per_trade", "0.01"))),
        daily_loss_limit=to_decimal(str(data.get("daily_loss_limit", "0.03"))),
        max_drawdown=to_decimal(str(data.get("max_drawdown", "0.15"))),
        max_position_fraction=to_decimal(str(data.get("max_position_fraction", "0.25"))),
    )
    try:
        starting = data["starting_capital"]
        fee = data["fee_rate"]
    except KeyError as exc:
        raise SettingsError(f"missing required setting: {exc.args[0]}") from None

    return DecideSettings(
        starting_capital=to_decimal(str(starting)),
        fee_rate=to_decimal(str(fee)),
        min_notional=to_decimal(str(data.get("min_notional", "10"))),
        risk=risk,
        symbol=str(data.get("symbol", "BTCUSD")),
        journal_path=str(data.get("journal_path", "journal/decisions.sqlite")),
        max_open_positions=int(data.get("max_open_positions", 3)),
        min_reward_to_risk=to_decimal(str(data.get("min_reward_to_risk", "1.5"))),
        real_money=bool(data.get("real_money", False)),
    )
