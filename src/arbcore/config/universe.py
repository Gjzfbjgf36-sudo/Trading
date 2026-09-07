"""The initial market universe.

Deliberately small. Every entry records the evidence behind it and its
epistemic status; nothing is listed because it is famous. Fields marked
``UNKNOWN`` are exactly that — they are filled in during the integration phases
(3 and 4) from current official documentation, and until then they block the
integrations that depend on them.

Nothing in this module implies an integration exists. These are admission
records, not adapters.
"""

from __future__ import annotations

from decimal import Decimal

from ..domain.types import AssetId, Chain, VenueId, VenueKind
from .whitelist import AssetSpec, Confidence, VenueSpec, Whitelist

BTC = AssetId("BTC", Chain.BITCOIN)
ETH = AssetId("ETH", Chain.ETHEREUM)
SOL = AssetId("SOL", Chain.SOLANA)
USDC_ETHEREUM = AssetId("USDC", Chain.ETHEREUM)
USDC_SOLANA = AssetId("USDC", Chain.SOLANA)
USDT_ETHEREUM = AssetId("USDT", Chain.ETHEREUM)

COINBASE = VenueId("coinbase", VenueKind.CEX)
KRAKEN = VenueId("kraken", VenueKind.CEX)
BINANCE = VenueId("binance", VenueKind.CEX)


def build_initial_whitelist(*, max_venue_balance: Decimal = Decimal("2000")) -> Whitelist:
    """The Phase 1 whitelist: majors only, with counterparty caps.

    ``max_venue_balance`` is the per-venue counterparty cap. It is a hard
    ceiling independent of the risk limits; the engine takes the tighter of the
    two.
    """
    wl = Whitelist()

    wl.add_asset(
        AssetSpec(
            asset=BTC,
            decimals=8,
            contract_address=None,
            is_stablecoin=False,
            liquidity_notes="Deepest spot books across all listed CEX venues.",
            known_risks=("withdrawal congestion during fee spikes", "10-min block settlement"),
            mint_authority=Confidence.CONFIRMED,
            freeze_authority=Confidence.CONFIRMED,
            transfer_restrictions=Confidence.CONFIRMED,
        )
    )
    wl.add_asset(
        AssetSpec(
            asset=ETH,
            decimals=18,
            contract_address=None,
            is_stablecoin=False,
            liquidity_notes="Deep CEX books; deep DEX liquidity on Ethereum and L2s.",
            known_risks=("gas spikes during congestion",),
            mint_authority=Confidence.CONFIRMED,
            freeze_authority=Confidence.CONFIRMED,
            transfer_restrictions=Confidence.CONFIRMED,
        )
    )
    wl.add_asset(
        AssetSpec(
            asset=SOL,
            decimals=9,
            contract_address=None,
            is_stablecoin=False,
            liquidity_notes="Deep CEX books; deep Solana DEX liquidity.",
            known_risks=("historical network degradation events", "priority-fee volatility"),
            mint_authority=Confidence.CONFIRMED,
            freeze_authority=Confidence.CONFIRMED,
            transfer_restrictions=Confidence.CONFIRMED,
        )
    )

    # Stablecoins are listed per chain and carry UNKNOWN token-authority
    # findings until Phase 4 verifies them against on-chain state. UNKNOWN is
    # recorded honestly rather than assumed benign.
    for asset, decimals in ((USDC_ETHEREUM, 6), (USDC_SOLANA, 6), (USDT_ETHEREUM, 6)):
        wl.add_asset(
            AssetSpec(
                asset=asset,
                decimals=decimals,
                contract_address="UNKNOWN",
                is_stablecoin=True,
                liquidity_notes="Primary quote asset; per-chain liquidity verified in Phase 4.",
                known_risks=(
                    "issuer freeze capability",
                    "peg deviation",
                    "chain-specific representation differs from other chains",
                ),
                mint_authority=Confidence.UNKNOWN,
                freeze_authority=Confidence.UNKNOWN,
                transfer_restrictions=Confidence.UNKNOWN,
            )
        )

    wl.add_venue(
        VenueSpec(
            venue=COINBASE,
            chains=frozenset({Chain.BITCOIN, Chain.ETHEREUM, Chain.SOLANA, Chain.BASE}),
            max_balance=max_venue_balance,
            api_docs_url="https://docs.cdp.coinbase.com/",
            api_version="UNKNOWN",
            rate_limit_notes="UNKNOWN — to be recorded from official docs in Phase 3.",
            notes="Candidate. No adapter exists yet.",
        )
    )
    wl.add_venue(
        VenueSpec(
            venue=KRAKEN,
            chains=frozenset({Chain.BITCOIN, Chain.ETHEREUM, Chain.SOLANA}),
            max_balance=max_venue_balance,
            api_docs_url="https://docs.kraken.com/api/",
            api_version="UNKNOWN",
            rate_limit_notes="UNKNOWN — to be recorded from official docs in Phase 3.",
            notes="Candidate. No adapter exists yet.",
        )
    )
    wl.add_venue(
        VenueSpec(
            venue=BINANCE,
            chains=frozenset({Chain.BITCOIN, Chain.ETHEREUM, Chain.SOLANA}),
            max_balance=max_venue_balance,
            api_docs_url="https://developers.binance.com/",
            api_version="UNKNOWN",
            rate_limit_notes="UNKNOWN — to be recorded from official docs in Phase 3.",
            notes=(
                "Candidate ONLY where legally and technically available to the operator. "
                "Jurisdictional eligibility is an open question flagged in docs/regulatory.md "
                "and must be resolved before any integration work."
            ),
        )
    )

    # DEX and bridge protocols are intentionally absent. Admitting a protocol
    # requires verified contract addresses, which Phase 4 supplies from
    # official documentation.
    return wl
