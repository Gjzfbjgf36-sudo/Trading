"""Asset, venue and protocol whitelists.

Nothing trades unless it was deliberately added. Lookups fail closed: an
unknown symbol is not "probably fine", it is a rejection. Whitelist entries
carry the evidence that justified admission, so an entry can be re-reviewed
later without archaeology.
"""

from __future__ import annotations

import enum
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from ..domain.types import ZERO, AssetId, Chain, VenueId, VenueKind, to_decimal


class Confidence(enum.StrEnum):
    """Epistemic status of a recorded fact (docs/assumptions.md).

    ``UNKNOWN`` never silently becomes ``CONFIRMED``: promotion is an explicit,
    reviewed configuration change.
    """

    CONFIRMED = "CONFIRMED"
    LIKELY = "LIKELY"
    UNCERTAIN = "UNCERTAIN"
    UNKNOWN = "UNKNOWN"


class WhitelistError(LookupError):
    """Raised when an unlisted asset, venue or protocol is referenced."""


@dataclass(frozen=True, slots=True)
class AssetSpec:
    """An admitted asset on one chain."""

    asset: AssetId
    decimals: int
    #: Contract/mint address. ``None`` only for a chain's native asset.
    contract_address: str | None
    is_stablecoin: bool
    #: Evidence recorded at admission time.
    liquidity_notes: str
    known_risks: tuple[str, ...] = ()
    #: Token authority findings; UNKNOWN blocks admission for non-native assets.
    mint_authority: Confidence = Confidence.UNKNOWN
    freeze_authority: Confidence = Confidence.UNKNOWN
    transfer_restrictions: Confidence = Confidence.UNKNOWN
    reviewed_on: date | None = None

    def __post_init__(self) -> None:
        if self.decimals < 0 or self.decimals > 36:
            raise ValueError(f"implausible decimals for {self.asset}: {self.decimals}")
        if self.contract_address is None and self.asset.chain not in _NATIVE_ASSETS.get(
            self.asset.symbol, frozenset()
        ):
            raise ValueError(
                f"{self.asset} has no contract address but is not the native asset of its chain"
            )


#: Symbols that are native (no contract) on the given chains.
_NATIVE_ASSETS: Mapping[str, frozenset[Chain]] = {
    "BTC": frozenset({Chain.BITCOIN}),
    "ETH": frozenset({Chain.ETHEREUM, Chain.BASE, Chain.ARBITRUM}),
    "SOL": frozenset({Chain.SOLANA}),
}


@dataclass(frozen=True, slots=True)
class VenueSpec:
    """An admitted venue and the counterparty facts we hold about it."""

    venue: VenueId
    #: Chains on which this venue can settle deposits/withdrawals.
    chains: frozenset[Chain]
    #: Counterparty cap: never concentrate unlimited capital on one venue.
    max_balance: Decimal
    api_docs_url: str
    api_version: str = "UNKNOWN"
    #: Documented rate limits; UNKNOWN means we throttle conservatively.
    rate_limit_notes: str = "UNKNOWN"
    withdrawal_permissions_required: bool = False
    notes: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "max_balance", to_decimal(self.max_balance))
        if self.max_balance <= ZERO:
            raise ValueError(f"{self.venue}: max_balance must be > 0")


@dataclass(frozen=True, slots=True)
class ProtocolSpec:
    """An admitted on-chain protocol (DEX, router, bridge)."""

    name: str
    chain: Chain
    version: str
    #: Addresses we are permitted to interact with. Anything else is refused.
    contract_addresses: frozenset[str]
    max_exposure: Decimal
    documentation_url: str
    audit_notes: str = "UNKNOWN"
    known_risks: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "max_exposure", to_decimal(self.max_exposure))
        if self.max_exposure <= ZERO:
            raise ValueError(f"{self.name}: max_exposure must be > 0")
        if not self.contract_addresses:
            raise ValueError(f"{self.name}: at least one contract address is required")


@dataclass(slots=True)
class Whitelist:
    """The single authority on what may be touched.

    Read paths raise :class:`WhitelistError` rather than returning ``None`` so
    that a caller cannot accidentally proceed with a missing entry.
    """

    assets: dict[AssetId, AssetSpec] = field(default_factory=dict)
    venues: dict[VenueId, VenueSpec] = field(default_factory=dict)
    protocols: dict[tuple[str, Chain], ProtocolSpec] = field(default_factory=dict)

    # --- registration ----------------------------------------------------
    def add_asset(self, spec: AssetSpec) -> None:
        if spec.asset in self.assets:
            raise ValueError(f"duplicate asset entry: {spec.asset}")
        self.assets[spec.asset] = spec

    def add_venue(self, spec: VenueSpec) -> None:
        if spec.venue in self.venues:
            raise ValueError(f"duplicate venue entry: {spec.venue}")
        self.venues[spec.venue] = spec

    def add_protocol(self, spec: ProtocolSpec) -> None:
        key = (spec.name, spec.chain)
        if key in self.protocols:
            raise ValueError(f"duplicate protocol entry: {key}")
        self.protocols[key] = spec

    # --- fail-closed lookups ---------------------------------------------
    def asset(self, asset: AssetId) -> AssetSpec:
        try:
            return self.assets[asset]
        except KeyError:
            raise WhitelistError(f"asset not whitelisted: {asset}") from None

    def venue(self, venue: VenueId) -> VenueSpec:
        try:
            return self.venues[venue]
        except KeyError:
            raise WhitelistError(f"venue not whitelisted: {venue}") from None

    def protocol(self, name: str, chain: Chain) -> ProtocolSpec:
        try:
            return self.protocols[(name, chain)]
        except KeyError:
            raise WhitelistError(f"protocol not whitelisted: {name} on {chain}") from None

    def contract_permitted(self, address: str, chain: Chain) -> bool:
        """True only if the exact address belongs to an admitted protocol."""
        return any(
            address in spec.contract_addresses
            for (_, spec_chain), spec in self.protocols.items()
            if spec_chain is chain
        )

    def is_stablecoin(self, asset: AssetId) -> bool:
        return self.asset(asset).is_stablecoin

    def venues_for(self, kind: VenueKind) -> tuple[VenueSpec, ...]:
        return tuple(spec for spec in self.venues.values() if spec.venue.kind is kind)

    def require_all(self, assets: Iterable[AssetId], venues: Iterable[VenueId]) -> None:
        """Assert every referenced asset and venue is admitted, or raise."""
        for asset in assets:
            self.asset(asset)
        for venue in venues:
            self.venue(venue)
