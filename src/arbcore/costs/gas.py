"""Gas and priority fees.

The structurally important property, and the one that decides whether DEX
arbitrage is viable at a given size: **gas is a fixed cost per attempt.** It
does not scale down with the trade. A 20 USD transaction fee is 2% of a 1,000
USD trade and 0.02% of a 100,000 USD one, and it is charged whether the
transaction succeeds or reverts.

That single fact produces a hard minimum viable trade size, computed here
rather than discovered after the fact.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..domain.types import ZERO, Chain, to_decimal

_QUANT = Decimal("0.00000001")


class GasUnknown(LookupError):
    """Raised when no gas model exists for a chain. Never a default."""


@dataclass(frozen=True, slots=True)
class GasModel:
    """Cost of getting one transaction included on one chain.

    ``confirmed`` records whether these figures came from live measurement or
    official documentation. Unconfirmed models are usable for research and are
    reported as such; they must never back real capital.
    """

    chain: Chain
    #: Units of gas (or compute) a two-swap arbitrage transaction consumes.
    gas_units: Decimal
    #: Base fee per unit, denominated in the accounting currency.
    base_fee_per_unit: Decimal
    #: Priority fee per unit paid to compete for inclusion.
    priority_fee_per_unit: Decimal
    #: Fixed per-transaction overhead (L1 data cost on rollups, etc.).
    fixed_overhead: Decimal = ZERO
    confirmed: bool = False
    source: str = "ASSUMED — not from measurement or official documentation"

    def __post_init__(self) -> None:
        for name in (
            "gas_units",
            "base_fee_per_unit",
            "priority_fee_per_unit",
            "fixed_overhead",
        ):
            value = to_decimal(getattr(self, name))
            object.__setattr__(self, name, value)
            if value < ZERO:
                raise ValueError(f"{name} must be >= 0")
        if self.gas_units <= ZERO:
            raise ValueError("gas_units must be > 0")

    @property
    def base_cost(self) -> Decimal:
        """Cost of inclusion at the base fee, before competing for priority."""
        return (self.gas_units * self.base_fee_per_unit + self.fixed_overhead).quantize(
            _QUANT
        )

    @property
    def priority_cost(self) -> Decimal:
        return (self.gas_units * self.priority_fee_per_unit).quantize(_QUANT)

    @property
    def total_cost(self) -> Decimal:
        """What one *attempt* costs, successful or not."""
        return self.base_cost + self.priority_cost

    def with_congestion(self, multiplier: Decimal) -> GasModel:
        """The same model under a fee spike.

        Congestion multiplies the base fee and, worse, the priority fee we must
        pay to stay competitive — which is why gas spikes do not merely reduce
        the edge, they invert it.
        """
        factor = to_decimal(multiplier)
        if factor < ZERO:
            raise ValueError("congestion multiplier must be >= 0")
        return GasModel(
            chain=self.chain,
            gas_units=self.gas_units,
            base_fee_per_unit=self.base_fee_per_unit * factor,
            priority_fee_per_unit=self.priority_fee_per_unit * factor,
            fixed_overhead=self.fixed_overhead * factor,
            confirmed=self.confirmed,
            source=f"{self.source} (congestion x{factor})",
        )

    def minimum_viable_notional(self, edge_fraction: Decimal) -> Decimal:
        """Smallest notional at which the edge covers one attempt's gas.

        Raises if the edge is non-positive: there is no size at which a
        negative edge covers a fixed cost, and returning a large number would
        imply one exists.
        """
        edge = to_decimal(edge_fraction)
        if edge <= ZERO:
            raise ValueError(
                "a non-positive edge is never covered by any trade size; "
                "there is no minimum viable notional"
            )
        return (self.total_cost / edge).quantize(_QUANT)


@dataclass(slots=True)
class GasBook:
    """Gas models per chain. Lookups fail closed."""

    models: dict[Chain, GasModel]

    def get(self, chain: Chain) -> GasModel:
        try:
            return self.models[chain]
        except KeyError:
            raise GasUnknown(f"no gas model recorded for {chain}") from None

    def unconfirmed(self) -> tuple[Chain, ...]:
        return tuple(c for c, m in self.models.items() if not m.confirmed)


@dataclass(frozen=True, slots=True)
class InclusionModel:
    """Probability that our transaction lands, and what it costs when it does not.

    A profitable arbitrage transaction is visible in the mempool before it is
    included. Others can see the same opportunity, and some can reorder around
    us. Two consequences are modelled:

    * We may simply lose the race, and the state we priced against is gone.
    * We still pay gas on a reverted transaction.

    These figures are assumptions, not measurements, and are marked as such in
    docs/assumptions.md. They are chosen to be pessimistic.
    """

    #: Probability our transaction is included in a timely block at all.
    inclusion_probability: Decimal
    #: Given inclusion, probability the state moved and the swap reverts.
    revert_probability: Decimal
    #: Fraction of gas still paid on a revert (usually most of it).
    revert_gas_fraction: Decimal = Decimal("0.9")

    def __post_init__(self) -> None:
        for name in (
            "inclusion_probability",
            "revert_probability",
            "revert_gas_fraction",
        ):
            value = to_decimal(getattr(self, name))
            object.__setattr__(self, name, value)
            if not (ZERO <= value <= Decimal(1)):
                raise ValueError(f"{name} must be a probability in [0, 1]")

    @property
    def success_probability(self) -> Decimal:
        return self.inclusion_probability * (Decimal(1) - self.revert_probability)

    def expected_gas_cost(self, gas: GasModel) -> Decimal:
        """Gas cost per *attempt*, averaging over inclusion and revert.

        A transaction that is never included costs nothing on chain; one that
        is included and reverts costs almost the full amount. Both outcomes are
        priced, because both happen.
        """
        included = self.inclusion_probability
        reverted = included * self.revert_probability
        succeeded = self.success_probability
        return (
            succeeded * gas.total_cost + reverted * gas.total_cost * self.revert_gas_fraction
        ).quantize(_QUANT)
