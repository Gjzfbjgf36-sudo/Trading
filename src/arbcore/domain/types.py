"""Core value types.

All monetary and quantity arithmetic uses :class:`decimal.Decimal`. Binary
floats are never used for prices, sizes, fees or balances: repeated float
arithmetic silently changes value, and a silently wrong balance is a capital
loss, not a rounding curiosity.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Final

#: Decimal context is not mutated globally; callers quantize explicitly at the
#: venue's tick/lot precision when a value leaves the system.
ZERO: Final[Decimal] = Decimal(0)


def to_decimal(value: Decimal | int | str) -> Decimal:
    """Convert to ``Decimal`` without ever passing through ``float``.

    ``float`` inputs are rejected rather than coerced: accepting them would let
    a caller inject a value that is already wrong before we see it.
    """
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, bool):  # bool is an int subclass; never a quantity
        raise TypeError("bool is not a numeric quantity")
    elif isinstance(value, int):
        result = Decimal(value)
    elif isinstance(value, str):
        try:
            result = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError(f"not a decimal literal: {value!r}") from exc
    else:
        raise TypeError(
            f"refusing to convert {type(value).__name__} to Decimal; "
            "pass Decimal, int or str (float loses precision)"
        )
    if not result.is_finite():
        raise ValueError(f"non-finite decimal: {value!r}")
    return result


class Side(enum.StrEnum):
    """Direction of a single execution leg."""

    BUY = "BUY"
    SELL = "SELL"

    @property
    def opposite(self) -> Side:
        return Side.SELL if self is Side.BUY else Side.BUY


class VenueKind(enum.StrEnum):
    CEX = "CEX"
    DEX = "DEX"
    BRIDGE = "BRIDGE"


class Chain(enum.StrEnum):
    """Settlement layers in the initial universe."""

    BITCOIN = "BITCOIN"
    ETHEREUM = "ETHEREUM"
    SOLANA = "SOLANA"
    BASE = "BASE"
    ARBITRUM = "ARBITRUM"


class StrategyKind(enum.StrEnum):
    """Each kind is an independent module with its own models.

    There is deliberately no generic "arbitrage" strategy: opportunity,
    execution, cost, risk, settlement and failure semantics differ per kind.
    """

    CEX_CEX = "CEX_CEX"
    DEX_DEX = "DEX_DEX"
    CEX_DEX = "CEX_DEX"
    CROSS_CHAIN = "CROSS_CHAIN"


class Atomicity(enum.StrEnum):
    """Whether all legs settle together.

    ``NON_ATOMIC`` strategies carry leg risk (one side fills, the other does
    not) and are subject to stricter limits.
    """

    ATOMIC = "ATOMIC"
    NON_ATOMIC = "NON_ATOMIC"


@dataclass(frozen=True, slots=True)
class AssetId:
    """An asset as it exists on one specific chain.

    ``USDC`` on Ethereum and ``USDC`` on Solana are different assets with
    different transfer, bridge and depeg risk; they are never interchangeable
    in inventory accounting.
    """

    symbol: str
    chain: Chain

    def __post_init__(self) -> None:
        if not self.symbol or self.symbol != self.symbol.upper():
            raise ValueError(f"asset symbol must be non-empty upper case: {self.symbol!r}")

    def __str__(self) -> str:
        return f"{self.symbol}@{self.chain}"


@dataclass(frozen=True, slots=True)
class VenueId:
    """A trading venue: an exchange account, a DEX deployment or a bridge."""

    name: str
    kind: VenueKind

    def __post_init__(self) -> None:
        if not self.name or self.name != self.name.lower():
            raise ValueError(f"venue name must be non-empty lower case: {self.name!r}")

    def __str__(self) -> str:
        return f"{self.name}({self.kind})"


@dataclass(frozen=True, slots=True)
class Amount:
    """A quantity of one asset. Arithmetic across different assets is refused."""

    asset: AssetId
    quantity: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "quantity", to_decimal(self.quantity))

    def _check_same_asset(self, other: Amount) -> None:
        if self.asset != other.asset:
            raise ValueError(f"cannot combine {self.asset} with {other.asset}")

    def __add__(self, other: Amount) -> Amount:
        self._check_same_asset(other)
        return Amount(self.asset, self.quantity + other.quantity)

    def __sub__(self, other: Amount) -> Amount:
        self._check_same_asset(other)
        return Amount(self.asset, self.quantity - other.quantity)

    @property
    def is_negative(self) -> bool:
        return self.quantity < ZERO

    def __str__(self) -> str:
        return f"{self.quantity} {self.asset}"
