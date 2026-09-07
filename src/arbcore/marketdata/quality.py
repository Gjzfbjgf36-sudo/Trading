"""Data quality scoring.

One number in [0, 1] summarising how much we trust the inputs behind an
opportunity. It is a *gate*, not a ranking: a high score never authorises
anything, a low score always blocks.

The score is the product of independent factors, not their average. A single
disqualifying input (an invalid book) must drive the score to zero rather than
being outvoted by four healthy ones — averaging is how a corrupt feed gets
traded on.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime
from decimal import Decimal

from ..domain.types import ZERO, to_decimal
from .book import OrderBook
from .clock import ClockTracker
from .feed import FeedHealth

ONE = Decimal(1)


@dataclass(frozen=True, slots=True)
class QualityFactors:
    """Independent [0, 1] factors. Any zero disqualifies the opportunity."""

    book_integrity: Decimal
    quote_freshness: Decimal
    feed_health: Decimal
    clock_confidence: Decimal
    cross_source_agreement: Decimal
    liquidity_confidence: Decimal

    def __post_init__(self) -> None:
        for f in fields(self):
            value = to_decimal(getattr(self, f.name))
            object.__setattr__(self, f.name, value)
            if not (ZERO <= value <= ONE):
                raise ValueError(f"{f.name} must be within [0, 1] (got {value})")

    @property
    def score(self) -> Decimal:
        product = ONE
        for f in fields(self):
            product *= to_decimal(getattr(self, f.name))
        return product

    def zero_factors(self) -> tuple[str, ...]:
        return tuple(f.name for f in fields(self) if getattr(self, f.name) == ZERO)

    def as_dict(self) -> dict[str, str]:
        return {f.name: str(getattr(self, f.name)) for f in fields(self)}


def _decay(age_ms: int, budget_ms: int) -> Decimal:
    """Linear decay from 1 at age 0 to 0 at the budget. Never negative."""
    if budget_ms <= 0:
        return ZERO
    if age_ms <= 0:
        return ONE
    if age_ms >= budget_ms:
        return ZERO
    return (Decimal(budget_ms - age_ms) / Decimal(budget_ms)).quantize(Decimal("0.0001"))


def score_book(
    book: OrderBook,
    feed: FeedHealth,
    clock: ClockTracker,
    *,
    now: datetime,
    max_quote_age_ms: int,
    max_clock_drift_ms: int,
    liquidity_confidence: Decimal = ONE,
    cross_source_agreement: Decimal = ONE,
) -> QualityFactors:
    """Derive quality factors from observed feed state.

    Deliberately harsh: an unusable book, a stalled feed or an unmeasured clock
    each produce a zero factor, which zeroes the whole score.
    """
    book_integrity = ONE if book.usable else ZERO
    freshness = _decay(book.age_ms(now), max_quote_age_ms)
    feed_factor = ONE if feed.check(now).name == "LIVE" else ZERO
    if not clock.has_samples:
        # An unmeasured clock is an unknown clock, not a good one.
        clock_factor = ZERO
    else:
        clock_factor = _decay(abs(clock.drift_ms), max_clock_drift_ms)
    return QualityFactors(
        book_integrity=book_integrity,
        quote_freshness=freshness,
        feed_health=feed_factor,
        clock_confidence=clock_factor,
        cross_source_agreement=cross_source_agreement,
        liquidity_confidence=liquidity_confidence,
    )
