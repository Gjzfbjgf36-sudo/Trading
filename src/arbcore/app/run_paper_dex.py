"""CLI: DEX/DEX paper session.

Usage:
    python -m arbcore.app.run_paper_dex --chain ethereum --ticks 20000
    python -m arbcore.app.run_paper_dex --chain arbitrum --ticks 20000
"""

from __future__ import annotations

import argparse
import json
from decimal import Decimal

from ..adapters.synthetic_dex import DexMarketConfig, PoolConfig
from ..config.limits import load_limits
from ..config.universe import ETH, USDC_ETHEREUM, build_initial_whitelist
from ..config.whitelist import VenueSpec, Whitelist
from ..costs.gas import GasBook, GasModel, InclusionModel
from ..domain.types import Chain
from ..strategy.dex_dex import DexDexConfig, dex_venue
from .dex_session import DexPaperSession, DexSessionConfig, DexSessionOutcome

CHAINS = ("ethereum", "arbitrum", "base")

#: Gas assumptions per chain. ASSUMED, not measured and not from official
#: documentation — `confirmed=False` says so, and the report repeats it. They
#: are chosen to be pessimistic and are the single most influential input to the
#: viability question, so they must be replaced by measurement before any of
#: this informs a real decision.
_GAS_ASSUMPTIONS: dict[Chain, tuple[str, str, str]] = {
    # (gas units, base fee per unit in USD, priority fee per unit in USD)
    Chain.ETHEREUM: ("250000", "0.00008", "0.00004"),
    Chain.ARBITRUM: ("250000", "0.0000008", "0.0000004"),
    Chain.BASE: ("250000", "0.0000004", "0.0000002"),
}

#: Assumed time from pricing to inclusion, per chain. ASSUMED, like the gas
#: figures. Rollups settle far faster than an L1 block, which matters because
#: the opportunity decays over exactly this window.
_INCLUSION_MS: dict[Chain, int] = {
    Chain.ETHEREUM: 12_000,
    Chain.ARBITRUM: 2_000,
    Chain.BASE: 2_000,
}


def build_gas_book(chain: Chain) -> GasBook:
    units, base, priority = _GAS_ASSUMPTIONS[chain]
    return GasBook(
        {
            chain: GasModel(
                chain=chain,
                gas_units=Decimal(units),
                base_fee_per_unit=Decimal(base),
                priority_fee_per_unit=Decimal(priority),
                confirmed=False,
                source="ASSUMED — replace with measurement before relying on it",
            )
        }
    )


def dex_whitelist(protocols: tuple[str, ...], chain: Chain) -> Whitelist:
    """Whitelist with the two pool venues admitted.

    No *protocol* is admitted: that needs verified contract addresses, which
    Phase 4 must read from official sources. These entries admit the venues for
    accounting purposes only, and no contract interaction is implied.
    """
    wl = build_initial_whitelist()
    for protocol in protocols:
        wl.add_venue(
            VenueSpec(
                venue=dex_venue(protocol),
                chains=frozenset({chain}),
                max_balance=Decimal("5000"),
                api_docs_url="UNKNOWN",
                api_version="UNKNOWN",
                rate_limit_notes="UNKNOWN",
                notes="Synthetic pool for paper research. No adapter, no contract address.",
            )
        )
    return wl


def build_session(
    *,
    chain: Chain,
    ticks: int,
    seed: int,
    tick_ms: int,
    db_path: str | None,
    inject_faults: bool,
    limits_path: str,
    wallet: Decimal = Decimal("1800"),
    competitor_rate: Decimal,
    inclusion_probability: Decimal = Decimal("0.60"),
    revert_probability: Decimal = Decimal("0.25"),
) -> DexPaperSession:
    protocols = ("poolalpha", "poolbeta")
    market = DexMarketConfig(
        pools=(
            PoolConfig("poolalpha", Decimal("1000"), Decimal("3000000")),
            PoolConfig("poolbeta", Decimal("800"), Decimal("2400000")),
        ),
        competitor_arbitrage_rate=competitor_rate,
    )
    return DexPaperSession(
        config=DexSessionConfig(
            ticks=ticks,
            seed=seed,
            tick_ms=tick_ms,
            db_path=db_path,
            inject_faults=inject_faults,
            initial_quote=wallet,
        ),
        limits=load_limits(limits_path),
        whitelist=dex_whitelist(protocols, chain),
        market_config=market,
        strategy_config=DexDexConfig(
            chain=chain,
            base=ETH,
            quote=USDC_ETHEREUM,
            max_notional=Decimal("4000"),
            expected_inclusion_ms=_INCLUSION_MS[chain],
        ),
        gas=build_gas_book(chain),
        inclusion=InclusionModel(inclusion_probability, revert_probability),
        venues=tuple(dex_venue(p) for p in protocols),
    )


def render(outcome: DexSessionOutcome) -> str:
    return json.dumps(
        {
            "session_id": outcome.session_id,
            "report": outcome.report.as_dict(),
            "counters": outcome.counters.as_dict(),
            "chain": outcome.chain.summary(),
            "sizing_blocks": outcome.sizing_blocks,
            "pool_divergence": outcome.divergence.summary(),
            "operator_interventions": outcome.interventions,
            "execution_statistics": outcome.statistics,
        },
        indent=2,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a DEX/DEX paper session")
    parser.add_argument("--chain", choices=CHAINS, default="ethereum")
    parser.add_argument("--ticks", type=int, default=20_000)
    parser.add_argument("--tick-ms", type=int, default=12_000)
    parser.add_argument("--seed", type=int, default=20260907)
    parser.add_argument("--wallet", type=str, default="1800")
    parser.add_argument("--competitor-rate", type=str, default="0.35")
    parser.add_argument("--limits", type=str, default="config/risk_limits.paper.yaml")
    parser.add_argument("--db", type=str, default=None)
    parser.add_argument("--no-faults", action="store_true")
    parser.add_argument("--json", type=str, default=None)
    args = parser.parse_args()

    session = build_session(
        chain=Chain(args.chain.upper()),
        ticks=args.ticks,
        seed=args.seed,
        tick_ms=args.tick_ms,
        db_path=args.db,
        inject_faults=not args.no_faults,
        limits_path=args.limits,
        wallet=Decimal(args.wallet),
        competitor_rate=Decimal(args.competitor_rate),
    )
    session.notes.append(f"chain={args.chain}, competitor_arbitrage_rate={args.competitor_rate}")
    outcome = session.run()
    rendered = render(outcome)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            handle.write(rendered)
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
