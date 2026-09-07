"""Adapter interfaces.

Defined now so that Phase 3/4 adapters slot in without the rest of the system
learning anything venue-specific. Every method that could plausibly be faked
with a guess instead raises in the base: a `NotImplementedError` in paper mode
is a bug report; an invented endpoint in live mode is a loss.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from ..domain.types import AssetId, VenueId
from ..execution.order import Order
from ..marketdata.book import BookSnapshot, BookUpdate


@runtime_checkable
class MarketDataSource(Protocol):
    """Streams book state for one venue."""

    venue: VenueId

    def snapshot(self, asset: AssetId, *, now: datetime) -> BookSnapshot:
        """Authoritative full book. Must be reachable at any time to resync."""

    def poll(self, asset: AssetId, *, now: datetime) -> tuple[BookUpdate, ...]:
        """Incremental updates since the last poll, in sequence order."""


@runtime_checkable
class ExecutionVenue(Protocol):
    """Places and reports on orders for one venue."""

    venue: VenueId

    def place(self, order: Order, *, now: datetime) -> Order:
        """Submit an order. Must be idempotent on the client order id."""

    def status(self, client_id: str) -> Order | None:
        """Authoritative order state. The only way to resolve UNKNOWN."""


@runtime_checkable
class BalanceSource(Protocol):
    """Reports venue-side balances for reconciliation."""

    venue: VenueId

    def balances(self) -> dict[AssetId, object]:
        """Venue-reported balances; the truth our records are checked against."""
