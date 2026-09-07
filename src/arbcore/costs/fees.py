"""Fee schedules.

Fees are configuration, never constants in strategy code, and a venue with no
recorded schedule has *no* fee estimate — not a default one. `estimate` raises
rather than substituting a plausible number, and the caller turns that into a
`FEE_UNCERTAIN` rejection.

The shipped schedules are explicitly placeholders (`CONFIRMED = False`) until
Phase 3/4 records real numbers from official documentation.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..domain.types import ZERO, AssetId, VenueId, to_decimal


class FeeUnknown(LookupError):
    """Raised when no fee schedule exists for a venue. Never a default."""


@dataclass(frozen=True, slots=True)
class VenueFeeSchedule:
    """Taker/maker rates and fixed transfer costs for one venue.

    ``confirmed`` records whether these numbers came from official
    documentation. Unconfirmed schedules are usable for paper research and are
    reported as such; they must not back real capital.
    """

    venue: VenueId
    taker_rate: Decimal
    maker_rate: Decimal
    #: Withdrawal fee per asset, in units of that asset.
    withdrawal_fees: dict[AssetId, Decimal]
    deposit_fee: Decimal = ZERO
    confirmed: bool = False
    source: str = "PLACEHOLDER — not from official documentation"

    def __post_init__(self) -> None:
        for name in ("taker_rate", "maker_rate", "deposit_fee"):
            value = to_decimal(getattr(self, name))
            object.__setattr__(self, name, value)
            if value < ZERO or value > Decimal("0.05"):
                raise ValueError(f"{name}={value} is outside a plausible fee range [0, 0.05]")

    def taker_cost(self, notional: Decimal) -> Decimal:
        return (to_decimal(notional) * self.taker_rate).quantize(Decimal("0.00000001"))

    def withdrawal_fee(self, asset: AssetId) -> Decimal:
        try:
            return self.withdrawal_fees[asset]
        except KeyError:
            raise FeeUnknown(
                f"{self.venue}: no recorded withdrawal fee for {asset}"
            ) from None


@dataclass(slots=True)
class FeeBook:
    """All known fee schedules. Lookups fail closed."""

    schedules: dict[VenueId, VenueFeeSchedule]

    def get(self, venue: VenueId) -> VenueFeeSchedule:
        try:
            return self.schedules[venue]
        except KeyError:
            raise FeeUnknown(f"no fee schedule recorded for {venue}") from None

    @property
    def all_confirmed(self) -> bool:
        return all(s.confirmed for s in self.schedules.values())

    def unconfirmed(self) -> tuple[VenueId, ...]:
        return tuple(v for v, s in self.schedules.items() if not s.confirmed)
