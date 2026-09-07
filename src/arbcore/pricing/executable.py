"""Executable prices.

The distinction this module exists to enforce: **the best bid/ask is a price
for an infinitesimal size, not for ours.** Walking the book gives the price we
would actually pay, the impact we would cause, and — crucially — whether the
size fills at all.

A partial walk is never silently returned as if it were a full fill. If the
book cannot absorb the size, the result says so and the opportunity dies.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..domain.types import ZERO, Side, to_decimal
from ..marketdata.book import Level, OrderBook

_QUANT = Decimal("0.00000001")


class NotExecutable(ValueError):
    """Raised when a fill cannot be computed at all (empty or unusable book)."""


@dataclass(frozen=True, slots=True)
class ExecutableQuote:
    """The result of walking a book for a specific size."""

    side: Side
    requested_size: Decimal
    filled_size: Decimal
    #: Volume-weighted average price of the walk.
    vwap: Decimal
    #: Top-of-book price, for reference only — never the execution assumption.
    reference_price: Decimal
    #: Deepest price touched.
    worst_price: Decimal
    levels_consumed: int

    @property
    def complete(self) -> bool:
        return self.filled_size >= self.requested_size

    @property
    def notional(self) -> Decimal:
        return self.vwap * self.filled_size

    @property
    def price_impact(self) -> Decimal:
        """Fractional distance from top-of-book to the VWAP of our own walk.

        This is the cost we inflict on ourselves by being large relative to the
        book. Always >= 0 by construction of the walk.
        """
        if self.reference_price <= ZERO:
            return ZERO
        raw = (self.vwap - self.reference_price) / self.reference_price
        signed = raw if self.side is Side.BUY else -raw
        return max(ZERO, signed).quantize(_QUANT)


def walk_book(book: OrderBook, side: Side, size: Decimal) -> ExecutableQuote:
    """Compute the executable price for ``size`` on ``side``.

    ``side`` is our action: ``BUY`` consumes asks, ``SELL`` consumes bids.
    An unusable book raises rather than returning a pessimistic guess — a
    corrupt book has no price, not a bad one.
    """
    if not book.usable:
        raise NotExecutable(
            f"book {book.venue}/{book.asset} is {book.status} "
            f"({book.invalidation}: {book.invalidation_detail})"
        )
    requested = to_decimal(size)
    if requested <= ZERO:
        raise NotExecutable("size must be > 0")

    levels: tuple[Level, ...] = book.asks if side is Side.BUY else book.bids
    if not levels:
        raise NotExecutable(f"no {'ask' if side is Side.BUY else 'bid'} liquidity")

    remaining = requested
    cost = ZERO
    filled = ZERO
    consumed = 0
    worst = levels[0].price
    for level in levels:
        if remaining <= ZERO:
            break
        take = min(remaining, level.size)
        if take <= ZERO:
            continue
        cost += take * level.price
        filled += take
        remaining -= take
        worst = level.price
        consumed += 1

    if filled <= ZERO:
        raise NotExecutable("book has levels but no size")

    return ExecutableQuote(
        side=side,
        requested_size=requested,
        filled_size=filled,
        vwap=(cost / filled).quantize(_QUANT),
        reference_price=levels[0].price,
        worst_price=worst,
        levels_consumed=consumed,
    )


def expected_slippage(quote: ExecutableQuote, decision_price: Decimal) -> Decimal:
    """Fractional adverse difference between the price we decided on and the VWAP.

    Favourable differences are floored at zero: an unexpectedly good fill is not
    a risk budget to spend elsewhere.
    """
    reference = to_decimal(decision_price)
    if reference <= ZERO:
        return ZERO
    raw = (quote.vwap - reference) / reference
    signed = raw if quote.side is Side.BUY else -raw
    return max(ZERO, signed).quantize(_QUANT)


def depth_at_price(book: OrderBook, side: Side, limit_price: Decimal) -> Decimal:
    """Total size available at or better than ``limit_price``.

    Used to answer "is there exit liquidity", which daily volume does not
    answer.
    """
    if not book.usable:
        raise NotExecutable(f"book {book.venue}/{book.asset} is {book.status}")
    bound = to_decimal(limit_price)
    levels = book.asks if side is Side.BUY else book.bids
    total = ZERO
    for level in levels:
        better = level.price <= bound if side is Side.BUY else level.price >= bound
        if not better:
            break
        total += level.size
    return total
