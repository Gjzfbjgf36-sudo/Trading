"""CLI: parameter sensitivity for the DEX/DEX strategy.

A single profitable run says almost nothing. What matters is *which assumption*
the result depends on, and how hard. This sweeps one parameter at a time and
prints the result next to it, so a cliff is visible rather than buried.

The competitor-arbitrage rate is the one to watch: it is unmeasured, and the
strategy goes from clearly profitable to placing no trades at all across a
range no one can currently distinguish.

Usage:
    python -m arbcore.app.run_sensitivity --ticks 12000
"""

from __future__ import annotations

import argparse
import json
from decimal import Decimal
from typing import Any

from ..domain.types import Chain
from .run_paper_dex import build_session

_QUANT = Decimal("0.01")


def one_run(
    chain: Chain,
    seed: int,
    competitor_rate: str,
    inclusion: str,
    revert: str,
    ticks: int,
    wallet: str,
) -> dict[str, Any]:
    session = build_session(
        chain=chain,
        ticks=ticks,
        seed=seed,
        tick_ms=12_000,
        db_path=None,
        inject_faults=True,
        limits_path="config/risk_limits.paper.yaml",
        wallet=Decimal(wallet),
        competitor_rate=Decimal(competitor_rate),
        inclusion_probability=Decimal(inclusion),
        revert_probability=Decimal(revert),
    )
    outcome = session.run()
    report = outcome.report
    return {
        "trades": report.pnl.trades,
        "net_pnl": str(report.pnl.realised.quantize(_QUANT)),
        "net_after_infrastructure": str(report.net_after_infrastructure.quantize(_QUANT)),
        "max_drawdown": str(report.pnl.max_drawdown.quantize(_QUANT)),
        "confirmed": outcome.chain.confirmed,
        "attempts": outcome.chain.attempts,
        "gas_spent": str(outcome.chain.gas_spent.quantize(_QUANT)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="DEX/DEX parameter sensitivity")
    parser.add_argument("--chain", default="base")
    parser.add_argument("--ticks", type=int, default=12_000)
    parser.add_argument("--wallet", type=str, default="1800")
    parser.add_argument("--seed", type=int, default=101)
    parser.add_argument("--json", type=str, default=None)
    args = parser.parse_args()
    chain = Chain(args.chain.upper())

    results: dict[str, Any] = {
        "chain": args.chain,
        "ticks": args.ticks,
        "wallet": args.wallet,
        "caveat": (
            "Synthetic pools. Each sweep varies ONE assumed parameter. Where the "
            "outcome swings between profitable and no-trades across a range that "
            "cannot currently be distinguished by measurement, the result is a "
            "statement about the assumption, not about the strategy."
        ),
    }

    sweeps: list[tuple[str, str, tuple[str, ...]]] = [
        ("seed", "seed", ("101", "202", "303", "404", "505")),
        ("competitor_arbitrage_rate", "comp", ("0.10", "0.25", "0.35", "0.50", "0.70", "0.90")),
        ("inclusion_probability", "incl", ("0.20", "0.40", "0.60", "0.80", "1.00")),
        ("revert_probability", "rev", ("0.10", "0.25", "0.50", "0.75")),
    ]

    for name, kind, values in sweeps:
        rows: dict[str, Any] = {}
        for value in values:
            seed = int(value) if kind == "seed" else args.seed
            comp = value if kind == "comp" else "0.35"
            incl = value if kind == "incl" else "0.60"
            rev = value if kind == "rev" else "0.25"
            rows[value] = one_run(
                chain, seed, comp, incl, rev, args.ticks, args.wallet
            )
        results[name] = rows
        print(f"--- {name} ---")
        for value, row in rows.items():
            print(
                f"  {value:>6}: trades {row['trades']:3d}  "
                f"net {row['net_pnl']:>8}  after_infra {row['net_after_infrastructure']:>8}  "
                f"confirm {row['confirmed']}/{row['attempts']}"
            )

    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(results, handle, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
