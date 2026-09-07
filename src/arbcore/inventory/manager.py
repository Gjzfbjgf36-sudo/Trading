"""Inventory manager.

The CEX/CEX strategy trades against **pre-funded** balances: buy on the venue
holding quote currency, sell on the venue holding base, and rebalance later.
That makes inventory, not speed, the binding constraint — and makes reserving
balance before an order the difference between two strategies double-spending
the same funds and not.

Rebalancing is a separate operation with its own decision and cost budget. It
never happens as a side effect of a trade.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from ..domain.types import ZERO, AssetId, VenueId, to_decimal


class InsufficientBalance(RuntimeError):
    """Raised when a reservation exceeds the free balance."""


@dataclass(slots=True)
class Position:
    """One asset on one venue.

    ``free`` is spendable now; ``reserved`` is committed to an in-flight order.
    Only ``free`` may fund a new leg — treating reserved balance as available
    is how two opportunities spend the same coins.
    """

    venue: VenueId
    asset: AssetId
    free: Decimal
    reserved: Decimal = ZERO

    def __post_init__(self) -> None:
        self.free = to_decimal(self.free)
        self.reserved = to_decimal(self.reserved)
        if self.free < ZERO or self.reserved < ZERO:
            raise ValueError(f"negative position for {self.asset} on {self.venue}")

    @property
    def total(self) -> Decimal:
        return self.free + self.reserved


@dataclass(slots=True)
class InventoryManager:
    """Tracks every asset on every venue, with explicit reservations."""

    positions: dict[tuple[VenueId, AssetId], Position] = field(default_factory=dict)
    #: Reserved amounts keyed by the reservation's idempotency key, so a
    #: crash-recovered process can tell which reservations are still real.
    reservations: dict[str, tuple[VenueId, AssetId, Decimal]] = field(default_factory=dict)
    #: Minimum balance that may never be spent, per asset.
    min_reserve: dict[AssetId, Decimal] = field(default_factory=dict)

    # --- balances ------------------------------------------------------
    def set_balance(self, venue: VenueId, asset: AssetId, free: Decimal) -> Position:
        position = Position(venue=venue, asset=asset, free=to_decimal(free))
        self.positions[(venue, asset)] = position
        return position

    def position(self, venue: VenueId, asset: AssetId) -> Position:
        try:
            return self.positions[(venue, asset)]
        except KeyError:
            # Fail closed: an untracked position is zero, not unlimited.
            raise KeyError(f"no tracked position for {asset} on {venue}") from None

    def free(self, venue: VenueId, asset: AssetId) -> Decimal:
        try:
            return self.position(venue, asset).free
        except KeyError:
            return ZERO

    def spendable(self, venue: VenueId, asset: AssetId) -> Decimal:
        """Free balance less the untouchable reserve. Never negative."""
        reserve = self.min_reserve.get(asset, ZERO)
        return max(ZERO, self.free(venue, asset) - reserve)

    def total_of(self, asset: AssetId) -> Decimal:
        return sum(
            (p.total for (_, a), p in self.positions.items() if a == asset), start=ZERO
        )

    # --- reservations --------------------------------------------------
    def reserve(
        self, key: str, venue: VenueId, asset: AssetId, amount: Decimal
    ) -> Decimal:
        """Move ``amount`` from free to reserved under an idempotency key.

        Re-reserving the same key is a no-op rather than a double reservation,
        which is what makes a retried request safe.
        """
        if not key.strip():
            raise ValueError("a reservation requires an idempotency key")
        if key in self.reservations:
            return self.reservations[key][2]
        value = to_decimal(amount)
        if value <= ZERO:
            raise ValueError("reservation amount must be > 0")
        available = self.spendable(venue, asset)
        if value > available:
            raise InsufficientBalance(
                f"{venue}/{asset}: requested {value}, spendable {available} "
                f"(reserve {self.min_reserve.get(asset, ZERO)} withheld)"
            )
        position = self.position(venue, asset)
        position.free -= value
        position.reserved += value
        self.reservations[key] = (venue, asset, value)
        return value

    def release(self, key: str) -> Decimal:
        """Return an unused reservation to free balance. Idempotent."""
        entry = self.reservations.pop(key, None)
        if entry is None:
            return ZERO
        venue, asset, value = entry
        position = self.position(venue, asset)
        position.reserved -= value
        position.free += value
        return value

    def settle(
        self,
        key: str,
        *,
        spent: Decimal,
        received_venue: VenueId,
        received_asset: AssetId,
        received: Decimal,
    ) -> None:
        """Consume a reservation and credit the proceeds.

        ``spent`` may be less than the reservation (a partial fill); the
        remainder returns to free balance rather than vanishing.
        """
        entry = self.reservations.pop(key, None)
        if entry is None:
            raise KeyError(f"unknown reservation {key!r}; refusing to settle blind")
        venue, asset, reserved = entry
        outlay = to_decimal(spent)
        if outlay > reserved:
            raise ValueError(
                f"settlement spends {outlay} but only {reserved} was reserved under {key!r}"
            )
        position = self.position(venue, asset)
        position.reserved -= reserved
        position.free += reserved - outlay

        credit = to_decimal(received)
        if credit > ZERO:
            target = self.positions.get((received_venue, received_asset))
            if target is None:
                target = self.set_balance(received_venue, received_asset, ZERO)
            target.free += credit

    # --- safety --------------------------------------------------------
    def imbalance(self, asset: AssetId, targets: dict[VenueId, Decimal]) -> Decimal:
        """Largest absolute deviation from the per-venue target for one asset.

        The maximum, not the sum: one badly misallocated venue is the problem,
        and averaging it away is how it stays unnoticed.
        """
        worst = ZERO
        for venue, target in targets.items():
            actual = self.positions.get((venue, asset))
            deviation = abs((actual.total if actual else ZERO) - to_decimal(target))
            worst = max(worst, deviation)
        return worst

    def reserve_breached(self) -> tuple[tuple[VenueId, AssetId], ...]:
        """Positions that have fallen below their untouchable minimum."""
        return tuple(
            key
            for key, position in self.positions.items()
            if position.total < self.min_reserve.get(position.asset, ZERO)
        )
