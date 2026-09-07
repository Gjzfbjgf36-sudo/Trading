"""Constant-product AMM pricing.

The `x * y = k` invariant is public mathematics, not a vendor API, so this can
be implemented exactly without guessing at anyone's documentation. What is
*not* implemented here is any specific protocol's router, encoding or contract
address — those are integration facts that must be read from official sources
(see docs/assumptions.md B4/B5), and no protocol is whitelisted.

The point of this module is the same as `pricing/executable.py` for order
books: **the pool's spot price is not the price we get.** Output is computed
from reserves at our actual input size, after the pool fee.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from ..domain.types import ZERO, AssetId, to_decimal

_QUANT = Decimal("0.000000000000000001")
ONE = Decimal(1)


class PoolUnusable(ValueError):
    """Raised when a pool cannot price a swap at all."""


@dataclass(frozen=True, slots=True)
class Pool:
    """A constant-product pool between two assets.

    ``fee`` is the protocol's swap fee as a fraction, taken from the input
    amount before it touches the invariant — which is how the common
    implementations do it, and why the fee compounds with price impact rather
    than simply adding to it.
    """

    protocol: str
    base: AssetId
    quote: AssetId
    reserve_base: Decimal
    reserve_quote: Decimal
    fee: Decimal

    def __post_init__(self) -> None:
        for name in ("reserve_base", "reserve_quote", "fee"):
            object.__setattr__(self, name, to_decimal(getattr(self, name)))
        if self.reserve_base <= ZERO or self.reserve_quote <= ZERO:
            raise PoolUnusable(f"{self.protocol}: a pool with an empty side has no price")
        if not (ZERO <= self.fee < ONE):
            raise ValueError(f"{self.protocol}: fee must be within [0, 1)")

    @property
    def spot_price(self) -> Decimal:
        """Marginal price of base in quote. Reference only — never executable."""
        return (self.reserve_quote / self.reserve_base).quantize(_QUANT)

    @property
    def invariant(self) -> Decimal:
        return self.reserve_base * self.reserve_quote


@dataclass(frozen=True, slots=True)
class SwapQuote:
    """What a specific input amount actually yields."""

    pool: Pool
    input_asset: AssetId
    input_amount: Decimal
    output_amount: Decimal
    #: Effective price paid, in quote per base, for comparison across venues.
    effective_price: Decimal
    spot_price: Decimal
    fee_paid: Decimal
    #: Loss from the constant-product curve alone, excluding the pool fee.
    curve_impact: Decimal

    @property
    def fee_fraction(self) -> Decimal:
        """The pool's fee. A known, quoted cost — not a liquidity risk."""
        return self.pool.fee

    @property
    def price_impact(self) -> Decimal:
        """Total adverse distance from spot: pool fee plus curve impact.

        Reported for transparency. It is **not** what the risk engine's
        price-impact limit is compared against — see `curve_impact`. Conflating
        the two makes any impact limit below the pool fee unsatisfiable at every
        size, since the fee is paid on the first wei.
        """
        return (self.fee_fraction + self.curve_impact).quantize(_QUANT)

    @property
    def total_cost_fraction(self) -> Decimal:
        return self.price_impact


def quote_swap(pool: Pool, input_asset: AssetId, input_amount: Decimal) -> SwapQuote:
    """Exact constant-product output for ``input_amount``.

    ``output = (reserve_out * input_after_fee) / (reserve_in + input_after_fee)``

    The result is always strictly less than the spot-price equivalent, and the
    gap widens with size. That gap is the whole reason a "spread" between two
    pools is not the profit.
    """
    amount = to_decimal(input_amount)
    if amount <= ZERO:
        raise PoolUnusable("swap input must be > 0")
    if input_asset == pool.base:
        reserve_in, reserve_out = pool.reserve_base, pool.reserve_quote
    elif input_asset == pool.quote:
        reserve_in, reserve_out = pool.reserve_quote, pool.reserve_base
    else:
        raise PoolUnusable(f"{input_asset} is not in pool {pool.protocol}")

    try:
        fee_paid = (amount * pool.fee).quantize(_QUANT)
        after_fee = amount - fee_paid
        output = ((reserve_out * after_fee) / (reserve_in + after_fee)).quantize(_QUANT)
    except InvalidOperation as exc:
        # An input so large the arithmetic overflows the decimal context is not
        # a numeric edge case to be papered over — it is a size this pool
        # cannot serve, and it must surface as a domain refusal rather than an
        # opaque decimal error somewhere upstream.
        raise PoolUnusable(
            f"{pool.protocol}: input {amount} is beyond what this pool can price"
        ) from exc

    # Curve impact is what the *shape* of the pool costs us, measured with the
    # fee removed. It is the quantity that grows with size and that a liquidity
    # limit should bound; the fee is a flat, known charge.
    try:
        ideal_output = amount * reserve_out / reserve_in
        no_fee_output = reserve_out * amount / (reserve_in + amount)
        curve_impact = (
            ((ideal_output - no_fee_output) / ideal_output).quantize(_QUANT)
            if ideal_output > ZERO
            else ZERO
        )
    except InvalidOperation as exc:
        raise PoolUnusable(f"{pool.protocol}: cannot measure impact at size {amount}") from exc
    if output <= ZERO or output >= reserve_out:
        raise PoolUnusable(
            f"{pool.protocol}: input {amount} cannot be filled from reserves"
        )

    try:
        if input_asset == pool.quote:
            # Bought base with quote: price is quote paid per base received.
            effective = (amount / output).quantize(_QUANT)
        else:
            # Sold base for quote: price is quote received per base sold.
            effective = (output / amount).quantize(_QUANT)
    except InvalidOperation as exc:
        raise PoolUnusable(f"{pool.protocol}: cannot price size {amount}") from exc

    return SwapQuote(
        pool=pool,
        input_asset=input_asset,
        input_amount=amount,
        output_amount=output,
        effective_price=effective,
        spot_price=pool.spot_price,
        fee_paid=fee_paid,
        curve_impact=max(ZERO, curve_impact),
    )


def apply_swap(pool: Pool, quote: SwapQuote) -> Pool:
    """The pool after a swap, so consecutive swaps see the state they create.

    Used by the simulator: a round trip through two pools must not price the
    second leg against reserves the first leg already moved.
    """
    if quote.input_asset == pool.base:
        return Pool(
            protocol=pool.protocol,
            base=pool.base,
            quote=pool.quote,
            reserve_base=pool.reserve_base + quote.input_amount,
            reserve_quote=pool.reserve_quote - quote.output_amount,
            fee=pool.fee,
        )
    return Pool(
        protocol=pool.protocol,
        base=pool.base,
        quote=pool.quote,
        reserve_base=pool.reserve_base - quote.output_amount,
        reserve_quote=pool.reserve_quote + quote.input_amount,
        fee=pool.fee,
    )


def max_input_for_impact(
    pool: Pool, input_asset: AssetId, max_impact: Decimal, *, precision: int = 24
) -> Decimal:
    """Largest input whose **curve impact** stays within ``max_impact``.

    Bounds the curve, not the fee. A limit below the pool fee would otherwise
    be unsatisfiable at any size, since the fee is charged on the first unit —
    which would silently make the strategy impossible rather than constrained.

    Bisection rather than a closed form: being approximately right on the safe
    side is what matters, and the returned size always satisfies the bound.
    """
    limit = to_decimal(max_impact)
    if limit <= ZERO:
        return ZERO
    reserve_in = pool.reserve_base if input_asset == pool.base else pool.reserve_quote
    low, high = ZERO, reserve_in
    for _ in range(precision):
        mid = (low + high) / Decimal(2)
        if mid <= ZERO:
            break
        try:
            impact = quote_swap(pool, input_asset, mid).curve_impact
        except PoolUnusable:
            high = mid
            continue
        if impact > limit:
            high = mid
        else:
            low = mid
    return low.quantize(_QUANT)
