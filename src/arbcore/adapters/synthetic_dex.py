"""Synthetic AMM pools.

Two pools on one chain holding the same pair, drifting apart and being pulled
back together. The pull-back matters more than the drift: pools that diverge
and stay diverged would make any arbitrage strategy look good, and real pools
are arbitraged back by whoever is fastest — usually not us.

Divergence here comes from two sources, both real:

* **Trade flow.** Independent traders push each pool's reserves around.
* **Our own trades.** A round trip moves both pools toward each other, which is
  the mechanism that closes the opportunity we just took.

As with every synthetic market in this repository: this is a parameter choice,
not a model of any real venue.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from decimal import Decimal

from ..domain.types import ZERO, AssetId
from ..pricing.amm import Pool, SwapQuote, apply_swap

_QUANT = Decimal("0.00000001")


@dataclass(frozen=True, slots=True)
class PoolConfig:
    """Shape of one synthetic pool."""

    protocol: str
    reserve_base: Decimal
    reserve_quote: Decimal
    fee: Decimal = Decimal("0.003")


@dataclass(frozen=True, slots=True)
class DexMarketConfig:
    """Shape of the synthetic pool market."""

    pools: tuple[PoolConfig, ...]
    #: Standard deviation of independent trade flow per tick, as a fraction of
    #: the pool's base reserve.
    flow_volatility: Decimal = Decimal("0.0020")
    #: Fraction of any divergence competitors arbitrage away each tick. This is
    #: the single most important parameter: it is how fast the opportunity
    #: disappears, and we are usually not the one taking it.
    competitor_arbitrage_rate: Decimal = Decimal("0.35")

    def __post_init__(self) -> None:
        if len(self.pools) < 2:
            raise ValueError("a DEX/DEX market needs at least two pools")
        if not (ZERO <= self.competitor_arbitrage_rate <= Decimal(1)):
            raise ValueError("competitor_arbitrage_rate must be within [0, 1]")


@dataclass
class SyntheticDexMarket:
    """Pools that drift apart on trade flow and are pulled back by competitors."""

    base: AssetId
    quote: AssetId
    config: DexMarketConfig
    seed: int
    pools: dict[str, Pool] = field(default_factory=dict)
    rng: random.Random = field(init=False)
    ticks: int = 0

    def __post_init__(self) -> None:
        self.rng = random.Random(self.seed)
        for spec in self.config.pools:
            self.pools[spec.protocol] = Pool(
                protocol=spec.protocol,
                base=self.base,
                quote=self.quote,
                reserve_base=spec.reserve_base,
                reserve_quote=spec.reserve_quote,
                fee=spec.fee,
            )

    def step(self) -> None:
        """One tick: independent flow, then competitor arbitrage."""
        for name, pool in list(self.pools.items()):
            shock = Decimal(str(round(self.rng.gauss(0.0, 1.0), 6)))
            delta_base = (pool.reserve_base * self.config.flow_volatility * shock).quantize(
                _QUANT
            )
            new_base = pool.reserve_base + delta_base
            if new_base <= ZERO:
                continue
            # Flow moves reserves along the curve, preserving the invariant.
            new_quote = (pool.invariant / new_base).quantize(_QUANT)
            if new_quote <= ZERO:
                continue
            self.pools[name] = Pool(
                protocol=pool.protocol,
                base=pool.base,
                quote=pool.quote,
                reserve_base=new_base,
                reserve_quote=new_quote,
                fee=pool.fee,
            )
        self._competitors_arbitrage()
        self.ticks += 1

    def _competitors_arbitrage(self) -> None:
        """Pull every pool a fraction of the way toward the mean price.

        The mechanism by which a visible opportunity stops being available. Set
        `competitor_arbitrage_rate` to zero and the strategy will look superb;
        that would be a statement about the parameter, not the strategy.
        """
        rate = self.config.competitor_arbitrage_rate
        if rate <= ZERO:
            return
        prices = [p.spot_price for p in self.pools.values()]
        mean_price = sum(prices, start=ZERO) / Decimal(len(prices))
        for name, pool in list(self.pools.items()):
            target = pool.spot_price + (mean_price - pool.spot_price) * rate
            if target <= ZERO:
                continue
            # Move reserves to the target price while preserving the invariant:
            #   base = sqrt(k / price), quote = k / base
            k = pool.invariant
            new_base = (k / target).sqrt().quantize(_QUANT)
            if new_base <= ZERO:
                continue
            new_quote = (k / new_base).quantize(_QUANT)
            self.pools[name] = Pool(
                protocol=pool.protocol,
                base=pool.base,
                quote=pool.quote,
                reserve_base=new_base,
                reserve_quote=new_quote,
                fee=pool.fee,
            )

    def apply_our_swap(self, protocol: str, quote: SwapQuote) -> None:
        """Apply our own fill so the opportunity we took is actually consumed.

        Without this the simulator would let us take the same spread every tick
        forever, which is the most flattering bug a backtest can have.
        """
        pool = self.pools[protocol]
        self.pools[protocol] = apply_swap(pool, quote)

    @property
    def divergence(self) -> Decimal:
        """Fractional spread between the cheapest and dearest pool."""
        prices = sorted(p.spot_price for p in self.pools.values())
        if prices[0] <= ZERO:
            return ZERO
        return ((prices[-1] - prices[0]) / prices[0]).quantize(_QUANT)
