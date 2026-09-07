"""Local order book with integrity enforcement.

The book is the thing most likely to be quietly wrong. Every failure mode we
know about — sequence gaps, duplicates, out-of-order messages, crossed books,
staleness — invalidates the book rather than being repaired in place. An
invalid book is unusable until an authoritative snapshot replaces it: guessing
at the missing delta is how a stale price becomes a real loss.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from ..domain.types import ZERO, AssetId, VenueId, to_decimal


class BookStatus(enum.StrEnum):
    #: Never received a snapshot. Unusable.
    UNINITIALISED = "UNINITIALISED"
    OK = "OK"
    #: Integrity broken. Unusable until re-snapshotted.
    INVALID = "INVALID"


class BookInvalidation(enum.StrEnum):
    SEQUENCE_GAP = "SEQUENCE_GAP"
    OUT_OF_ORDER = "OUT_OF_ORDER"
    CROSSED_BOOK = "CROSSED_BOOK"
    EMPTY_SIDE = "EMPTY_SIDE"
    NEGATIVE_SIZE = "NEGATIVE_SIZE"
    TIMESTAMP_REGRESSION = "TIMESTAMP_REGRESSION"
    HEARTBEAT_LOST = "HEARTBEAT_LOST"
    MANUAL = "MANUAL"


@dataclass(frozen=True, slots=True)
class Level:
    price: Decimal
    size: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "price", to_decimal(self.price))
        object.__setattr__(self, "size", to_decimal(self.size))
        if self.price <= ZERO:
            raise ValueError(f"level price must be > 0 (got {self.price})")
        if self.size < ZERO:
            raise ValueError(f"level size must be >= 0 (got {self.size})")


@dataclass(frozen=True, slots=True)
class BookSnapshot:
    """An authoritative point-in-time book, as published by a venue."""

    venue: VenueId
    asset: AssetId
    sequence: int
    venue_time: datetime
    received_at: datetime
    bids: tuple[Level, ...]
    asks: tuple[Level, ...]


@dataclass(frozen=True, slots=True)
class BookUpdate:
    """An incremental update. ``size == 0`` removes the level."""

    venue: VenueId
    asset: AssetId
    sequence: int
    venue_time: datetime
    received_at: datetime
    bids: tuple[Level, ...] = ()
    asks: tuple[Level, ...] = ()


@dataclass(slots=True)
class OrderBook:
    """A local book that refuses to be subtly wrong.

    Counters (``gaps_detected`` etc.) are kept because the *rate* of integrity
    failures is itself a circuit-breaker input: a book that resyncs constantly
    is not a healthy book even if each individual snapshot is fine.
    """

    venue: VenueId
    asset: AssetId
    status: BookStatus = BookStatus.UNINITIALISED
    sequence: int = 0
    venue_time: datetime | None = None
    received_at: datetime | None = None
    _bids: dict[Decimal, Decimal] = field(default_factory=dict)
    _asks: dict[Decimal, Decimal] = field(default_factory=dict)
    invalidation: BookInvalidation | None = None
    invalidation_detail: str = ""
    gaps_detected: int = 0
    duplicates_ignored: int = 0
    out_of_order_ignored: int = 0
    resyncs: int = 0

    # --- ingestion -----------------------------------------------------
    def apply_snapshot(self, snapshot: BookSnapshot) -> None:
        """Replace the book wholesale. The only way out of ``INVALID``."""
        self._require_identity(snapshot.venue, snapshot.asset)
        self._bids = {level.price: level.size for level in snapshot.bids if level.size > ZERO}
        self._asks = {level.price: level.size for level in snapshot.asks if level.size > ZERO}
        self.sequence = snapshot.sequence
        self.venue_time = snapshot.venue_time
        self.received_at = snapshot.received_at
        if self.status is BookStatus.INVALID:
            self.resyncs += 1
        self.status = BookStatus.OK
        self.invalidation = None
        self.invalidation_detail = ""
        self._validate_shape()

    def apply_update(self, update: BookUpdate) -> bool:
        """Apply an incremental update.

        Returns True if the book is usable afterwards. Updates against an
        uninitialised or invalid book are refused outright — applying a delta
        to a book we do not trust produces a book we trust even less.
        """
        self._require_identity(update.venue, update.asset)

        if self.status is not BookStatus.OK:
            return False

        if update.sequence <= self.sequence:
            # Duplicate or replayed message. Dropping it is safe; the state it
            # describes is already incorporated.
            if update.sequence == self.sequence:
                self.duplicates_ignored += 1
            else:
                self.out_of_order_ignored += 1
            return True

        if update.sequence != self.sequence + 1:
            self.gaps_detected += 1
            self._invalidate(
                BookInvalidation.SEQUENCE_GAP,
                f"expected sequence {self.sequence + 1}, received {update.sequence}",
            )
            return False

        if self.venue_time is not None and update.venue_time < self.venue_time:
            self._invalidate(
                BookInvalidation.TIMESTAMP_REGRESSION,
                f"venue time went backwards: {update.venue_time} < {self.venue_time}",
            )
            return False

        for level in update.bids:
            _apply_level(self._bids, level)
        for level in update.asks:
            _apply_level(self._asks, level)

        self.sequence = update.sequence
        self.venue_time = update.venue_time
        self.received_at = update.received_at
        return self._validate_shape()

    def invalidate(self, reason: BookInvalidation, detail: str) -> None:
        """Externally invalidate, e.g. on a lost heartbeat."""
        self._invalidate(reason, detail)

    # --- reads ---------------------------------------------------------
    @property
    def usable(self) -> bool:
        return self.status is BookStatus.OK

    @property
    def bids(self) -> tuple[Level, ...]:
        return tuple(
            Level(price, size) for price, size in sorted(self._bids.items(), reverse=True)
        )

    @property
    def asks(self) -> tuple[Level, ...]:
        return tuple(Level(price, size) for price, size in sorted(self._asks.items()))

    @property
    def best_bid(self) -> Level | None:
        return self.bids[0] if self._bids else None

    @property
    def best_ask(self) -> Level | None:
        return self.asks[0] if self._asks else None

    def age_ms(self, now: datetime) -> int:
        """Age since receipt. An uninitialised book is infinitely old."""
        if self.received_at is None:
            return 2**31 - 1
        return int((now - self.received_at).total_seconds() * 1000)

    # --- internals -----------------------------------------------------
    def _require_identity(self, venue: VenueId, asset: AssetId) -> None:
        if venue != self.venue or asset != self.asset:
            raise ValueError(
                f"message for {venue}/{asset} routed to book for {self.venue}/{self.asset}"
            )

    def _invalidate(self, reason: BookInvalidation, detail: str) -> None:
        self.status = BookStatus.INVALID
        self.invalidation = reason
        self.invalidation_detail = detail

    def _validate_shape(self) -> bool:
        if not self._bids or not self._asks:
            self._invalidate(BookInvalidation.EMPTY_SIDE, "one side of the book is empty")
            return False
        best_bid = max(self._bids)
        best_ask = min(self._asks)
        if best_bid >= best_ask:
            self._invalidate(
                BookInvalidation.CROSSED_BOOK,
                f"best bid {best_bid} >= best ask {best_ask}",
            )
            return False
        return True


def _apply_level(side: dict[Decimal, Decimal], level: Level) -> None:
    if level.size == ZERO:
        side.pop(level.price, None)
    else:
        side[level.price] = level.size
