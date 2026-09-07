"""Exposure accounting.

The snapshot is an explicit input to the risk engine rather than something the
engine fetches. That keeps risk decisions pure and replayable, and forces the
caller to state how fresh the numbers are — stale exposure data is itself a
reason to refuse.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from ..domain.types import ZERO, AssetId, Chain, VenueId, to_decimal


@dataclass(frozen=True, slots=True)
class VenueBalance:
    """Balance of one asset on one venue, split by availability.

    ``free`` is what could be traded right now; ``reserved`` is committed to
    open orders or in-flight settlement. Only ``free`` may fund a new leg.
    """

    venue: VenueId
    asset: AssetId
    free: Decimal
    reserved: Decimal = ZERO

    def __post_init__(self) -> None:
        object.__setattr__(self, "free", to_decimal(self.free))
        object.__setattr__(self, "reserved", to_decimal(self.reserved))
        if self.free < ZERO or self.reserved < ZERO:
            raise ValueError(f"negative balance for {self.asset} on {self.venue}")

    @property
    def total(self) -> Decimal:
        return self.free + self.reserved


@dataclass(frozen=True, slots=True)
class ExposureSnapshot:
    """A point-in-time view of capital at risk, valued in the accounting currency.

    Valuation is supplied by the caller (a marking service), not computed here:
    the risk engine must not depend on a price source, or a bad price would
    both create and approve a bad trade.
    """

    #: When the underlying balances were last reconciled with the venues.
    as_of: datetime
    total_exposure: Decimal
    per_asset: dict[AssetId, Decimal] = field(default_factory=dict)
    per_venue: dict[VenueId, Decimal] = field(default_factory=dict)
    per_chain: dict[Chain, Decimal] = field(default_factory=dict)
    #: Realised + unrealised P/L for the current trading day (negative = loss).
    daily_pnl: Decimal = ZERO
    trades_today: int = 0
    #: Paper calibration trades placed today, counted separately.
    calibration_trades_today: int = 0
    concurrent_trades: int = 0
    #: Largest absolute inventory deviation from target, in accounting currency.
    inventory_imbalance: Decimal = ZERO
    #: True only if a successful reconciliation backs these numbers.
    reconciled: bool = False

    def asset_exposure(self, asset: AssetId) -> Decimal:
        return self.per_asset.get(asset, ZERO)

    def venue_exposure(self, venue: VenueId) -> Decimal:
        return self.per_venue.get(venue, ZERO)

    def chain_exposure(self, chain: Chain) -> Decimal:
        return self.per_chain.get(chain, ZERO)

    def age_ms(self, now: datetime) -> int:
        return int((now - self.as_of).total_seconds() * 1000)
