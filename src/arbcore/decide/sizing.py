"""Position sizing from risk, not from account size.

The single most common way a small account dies is sizing by "how much can I
afford to buy" instead of "how much can I afford to lose". Those give wildly
different answers, and only the second one survives a losing streak.

Size follows from three things you decide in advance:

    risk per trade (a fraction of equity)
    entry price
    stop price

    quantity = (equity x risk fraction) / |entry - stop|

The stop is therefore not optional. Without one there is no risk to size
against, and this module refuses rather than inventing a default.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..domain.types import ZERO, Side, to_decimal

_QUANT = Decimal("0.00000001")


class SizingImpossible(ValueError):
    """Raised when a position cannot be sized safely."""


@dataclass(frozen=True, slots=True)
class RiskProfile:
    """How much you are prepared to lose, decided while calm.

    Defaults are conservative on purpose. A beginner's first instinct is to
    size too large; nothing here will help with that except the numbers.
    """

    #: Fraction of equity risked on a single trade.
    risk_per_trade: Decimal = Decimal("0.01")
    #: Loss in one day that stops new trades until tomorrow.
    daily_loss_limit: Decimal = Decimal("0.03")
    #: Drawdown from the equity peak that stops everything pending review.
    max_drawdown: Decimal = Decimal("0.15")
    #: Ceiling on one position as a fraction of equity, independent of the
    #: stop distance. A very tight stop would otherwise justify an enormous
    #: position, and a gap through that stop would then be catastrophic.
    max_position_fraction: Decimal = Decimal("0.25")

    def __post_init__(self) -> None:
        for name in (
            "risk_per_trade",
            "daily_loss_limit",
            "max_drawdown",
            "max_position_fraction",
        ):
            value = to_decimal(getattr(self, name))
            object.__setattr__(self, name, value)
            if not (ZERO < value <= Decimal(1)):
                raise ValueError(f"{name} must be within (0, 1] (got {value})")
        if self.risk_per_trade > Decimal("0.02"):
            raise ValueError(
                f"risk_per_trade of {self.risk_per_trade} is above 2% of equity. "
                "At that size a normal losing streak is an account-ending event."
            )
        if self.risk_per_trade > self.daily_loss_limit:
            raise ValueError("a single trade may not risk more than the daily limit")


@dataclass(frozen=True, slots=True)
class PositionSize:
    """The computed size, with everything needed to check the arithmetic."""

    quantity: Decimal
    notional: Decimal
    risk_amount: Decimal
    stop_distance: Decimal
    #: Which constraint actually decided the size.
    binding_constraint: str

    @property
    def viable(self) -> bool:
        return self.quantity > ZERO


def size_position(
    *,
    equity: Decimal,
    entry: Decimal,
    stop: Decimal,
    side: Side,
    profile: RiskProfile,
    fee_rate: Decimal = Decimal("0.0026"),
    min_notional: Decimal = Decimal("10"),
) -> PositionSize:
    """Largest position whose stop-out loses no more than the risk budget.

    Fees are charged on the way in *and* out and are part of the loss, so they
    are added to the stop distance rather than ignored. On a small account with
    a tight stop they can be most of the risk, which is exactly the case a
    beginner does not see coming.
    """
    equity = to_decimal(equity)
    entry = to_decimal(entry)
    stop = to_decimal(stop)
    if equity <= ZERO:
        raise SizingImpossible("equity must be > 0")
    if entry <= ZERO or stop <= ZERO:
        raise SizingImpossible("entry and stop must be > 0")

    # The stop must be on the losing side of the entry, or it is not a stop.
    if side is Side.BUY and stop >= entry:
        raise SizingImpossible(
            f"a long's stop ({stop}) must be below its entry ({entry}); "
            "as written this order would close immediately"
        )
    if side is Side.SELL and stop <= entry:
        raise SizingImpossible(
            f"a short's stop ({stop}) must be above its entry ({entry})"
        )

    price_distance = abs(entry - stop)
    # Round-trip fees are part of what a stop-out costs.
    fee_distance = (entry + stop) * to_decimal(fee_rate)
    loss_per_unit = price_distance + fee_distance
    if loss_per_unit <= ZERO:
        raise SizingImpossible("stop distance is zero after fees; no size is safe")

    risk_amount = (equity * profile.risk_per_trade).quantize(_QUANT)
    by_risk = risk_amount / loss_per_unit
    by_cap = (equity * profile.max_position_fraction) / entry

    if by_cap < by_risk:
        quantity, binding = by_cap, "max_position_fraction"
    else:
        quantity, binding = by_risk, "risk_per_trade"
    quantity = quantity.quantize(_QUANT)
    notional = (quantity * entry).quantize(_QUANT)

    if notional < to_decimal(min_notional):
        # Not a failure of nerve: below the exchange minimum there is no trade,
        # and forcing one means abandoning the risk limit to get there.
        return PositionSize(
            quantity=ZERO,
            notional=ZERO,
            risk_amount=risk_amount,
            stop_distance=loss_per_unit,
            binding_constraint=(
                f"position of {notional} is below the {min_notional} minimum; "
                f"at {equity} equity and a {price_distance} stop, a correctly "
                f"sized trade is too small to place"
            ),
        )

    return PositionSize(
        quantity=quantity,
        notional=notional,
        risk_amount=risk_amount,
        stop_distance=loss_per_unit,
        binding_constraint=binding,
    )


def fee_share_of_risk(
    *, entry: Decimal, stop: Decimal, fee_rate: Decimal
) -> Decimal:
    """Fraction of a stop-out loss that is fees rather than market move.

    Above roughly a third, the account is trading for the exchange. This is the
    number that decides whether a small account can trade a given stop distance
    at all, and it is computed before the trade rather than discovered in the
    statement.
    """
    entry = to_decimal(entry)
    stop = to_decimal(stop)
    price_distance = abs(entry - stop)
    fee_distance = (entry + stop) * to_decimal(fee_rate)
    total = price_distance + fee_distance
    if total <= ZERO:
        return Decimal(1)
    return (fee_distance / total).quantize(Decimal("0.0001"))
