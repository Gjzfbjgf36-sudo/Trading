"""A synthetic venue.

Its purpose is to exercise the *machinery* — sequencing, integrity handling,
decision flow, execution paths, reconciliation — deterministically and without
touching a real exchange. It is explicitly **not** a market model: any P/L it
produces is a property of these parameters, not evidence about real markets.
That caveat is repeated in every report the paper runner writes.

Fault injection lives here rather than in tests so that the same faults can be
driven through a full session, not only a unit test.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from ..domain.types import ZERO, AssetId, VenueId
from ..marketdata.book import BookSnapshot, BookUpdate, Level

_QUANT = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class SyntheticConfig:
    """Shape of the generated market."""

    #: Starting mid price.
    initial_mid: Decimal = Decimal("60000")
    #: Per-tick volatility as a fraction of mid.
    tick_volatility: Decimal = Decimal("0.0004")
    #: Half-spread as a fraction of mid.
    half_spread: Decimal = Decimal("0.00008")
    #: Number of price levels per side.
    depth_levels: int = 12
    #: Size at the top level, growing linearly deeper into the book.
    top_level_size: Decimal = Decimal("0.15")
    #: Price step between levels, as a fraction of mid.
    level_step: Decimal = Decimal("0.00005")
    #: Venue-specific mean-reverting offset, as a fraction of mid.
    offset_volatility: Decimal = Decimal("0.00015")
    offset_reversion: Decimal = Decimal("0.25")


@dataclass
class SyntheticVenue:
    """Generates a book for one venue that drifts around a shared mid.

    The shared mid is owned by :class:`SyntheticMarket`; each venue adds its own
    mean-reverting offset. That is what creates (and closes) cross-venue
    spreads, and the mean reversion is what makes them decay — the single most
    important property to model, because a spread that never decays makes any
    strategy look good.
    """

    venue: VenueId
    asset: AssetId
    config: SyntheticConfig
    rng: random.Random
    offset: Decimal = ZERO
    sequence: int = 0
    #: Faults to inject on the next poll, consumed one per call.
    pending_faults: list[str] = field(default_factory=list)
    #: Last published levels, so updates can carry the deletions a real
    #: incremental feed carries. Without them the consumer accumulates stale
    #: levels and the book crosses — which is exactly what happened the first
    #: time this ran.
    _last_bids: dict[Decimal, Decimal] = field(default_factory=dict)
    _last_asks: dict[Decimal, Decimal] = field(default_factory=dict)

    def step(self) -> None:
        """Advance the venue's private offset one tick (mean-reverting)."""
        shock = Decimal(str(round(self.rng.gauss(0.0, 1.0), 6)))
        self.offset += (
            self.config.offset_volatility * shock
            - self.config.offset_reversion * self.offset
        )

    def mid(self, shared_mid: Decimal) -> Decimal:
        return (shared_mid * (Decimal(1) + self.offset)).quantize(_QUANT)

    def build_levels(self, shared_mid: Decimal) -> tuple[tuple[Level, ...], tuple[Level, ...]]:
        mid = self.mid(shared_mid)
        half = mid * self.config.half_spread
        step = mid * self.config.level_step
        bids: list[Level] = []
        asks: list[Level] = []
        for i in range(self.config.depth_levels):
            size = self.config.top_level_size * (Decimal(1) + Decimal(i) / Decimal(2))
            bids.append(Level((mid - half - step * i).quantize(_QUANT), size))
            asks.append(Level((mid + half + step * i).quantize(_QUANT), size))
        return tuple(bids), tuple(asks)

    def snapshot(self, shared_mid: Decimal, *, now: datetime) -> BookSnapshot:
        bids, asks = self.build_levels(shared_mid)
        self._last_bids = {level.price: level.size for level in bids}
        self._last_asks = {level.price: level.size for level in asks}
        return BookSnapshot(
            venue=self.venue,
            asset=self.asset,
            sequence=self.sequence,
            venue_time=now,
            received_at=now,
            bids=bids,
            asks=asks,
        )

    def poll(self, shared_mid: Decimal, *, now: datetime) -> tuple[BookUpdate, ...]:
        """One update, with any queued fault applied.

        Faults are the point of this method: `gap`, `duplicate`, `out_of_order`
        and `stale` each drive a different integrity path in `OrderBook`.
        """
        fault = self.pending_faults.pop(0) if self.pending_faults else None
        self.sequence += 1
        sequence = self.sequence
        venue_time = now

        if fault == "gap":
            self.sequence += 3
            sequence = self.sequence
        elif fault == "duplicate":
            sequence = self.sequence - 1
            self.sequence -= 1
        elif fault == "out_of_order":
            sequence = max(0, self.sequence - 5)
        elif fault == "stale":
            venue_time = datetime.fromtimestamp(now.timestamp() - 3600, tz=now.tzinfo)

        bids, asks = self.build_levels(shared_mid)
        bid_delta = _diff(self._last_bids, bids)
        ask_delta = _diff(self._last_asks, asks)
        if fault not in ("duplicate", "out_of_order"):
            # A duplicated or replayed message must not advance our idea of
            # what the consumer knows, or the next diff would be wrong.
            self._last_bids = {level.price: level.size for level in bids}
            self._last_asks = {level.price: level.size for level in asks}
        return (
            BookUpdate(
                venue=self.venue,
                asset=self.asset,
                sequence=sequence,
                venue_time=venue_time,
                received_at=now,
                bids=bid_delta,
                asks=ask_delta,
            ),
        )

    def inject(self, fault: str) -> None:
        if fault not in ("gap", "duplicate", "out_of_order", "stale"):
            raise ValueError(f"unknown fault: {fault!r}")
        self.pending_faults.append(fault)


def _diff(previous: dict[Decimal, Decimal], current: tuple[Level, ...]) -> tuple[Level, ...]:
    """Levels that changed, plus zero-size deletions for levels that vanished.

    This is what a real incremental feed sends. Publishing only the new levels
    is the bug that floods a consumer with crossed books.
    """
    now_map = {level.price: level.size for level in current}
    delta: list[Level] = [
        Level(price, size) for price, size in now_map.items() if previous.get(price) != size
    ]
    delta.extend(Level(price, ZERO) for price in previous if price not in now_map)
    return tuple(delta)


@dataclass
class SyntheticMarket:
    """A shared mid price plus one book per venue.

    Every venue sees the same underlying asset, which is what makes cross-venue
    spreads meaningful rather than two unrelated random walks.
    """

    asset: AssetId
    config: SyntheticConfig
    seed: int
    mid: Decimal = field(init=False)
    rng: random.Random = field(init=False)
    venues: dict[VenueId, SyntheticVenue] = field(default_factory=dict)
    ticks: int = 0

    def __post_init__(self) -> None:
        self.rng = random.Random(self.seed)
        self.mid = self.config.initial_mid

    def add_venue(self, venue: VenueId) -> SyntheticVenue:
        if venue in self.venues:
            raise ValueError(f"duplicate synthetic venue {venue}")
        # A per-venue generator keeps one venue's draws from shifting another's,
        # so injecting a fault on one venue does not change the other's path.
        sub = random.Random(self.seed ^ (hash(venue.name) & 0xFFFFFFFF))
        self.venues[venue] = SyntheticVenue(
            venue=venue, asset=self.asset, config=self.config, rng=sub
        )
        return self.venues[venue]

    def step(self) -> None:
        shock = Decimal(str(round(self.rng.gauss(0.0, 1.0), 6)))
        self.mid = (self.mid * (Decimal(1) + self.config.tick_volatility * shock)).quantize(
            _QUANT
        )
        if self.mid <= ZERO:
            raise RuntimeError("synthetic mid collapsed; parameters are unusable")
        for venue in self.venues.values():
            venue.step()
        self.ticks += 1
