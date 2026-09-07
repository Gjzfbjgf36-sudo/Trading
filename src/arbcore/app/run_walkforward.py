"""CLI: walk-forward evaluation across disjoint seed ranges.

Usage:
    python -m arbcore.app.run_walkforward --scenario mechanics
    python -m arbcore.app.run_walkforward --scenario mechanics --out-of-sample
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from decimal import Decimal

from ..backtest.walkforward import DataSplit, OutOfSampleLedger, walk_forward
from .run_paper import SCENARIOS, build_session, scenario_settings


def main() -> int:
    parser = argparse.ArgumentParser(description="Walk-forward evaluation")
    parser.add_argument("--scenario", choices=SCENARIOS, default="mechanics")
    parser.add_argument("--ticks", type=int, default=4_000)
    parser.add_argument("--tick-ms", type=int, default=5_000)
    parser.add_argument("--trade-size", type=str, default="0.004")
    parser.add_argument("--ledger", type=str, default="artifacts/oos_ledger.json")
    parser.add_argument(
        "--out-of-sample",
        action="store_true",
        help="Consume one out-of-sample evaluation from this parameter set's budget",
    )
    args = parser.parse_args()

    market, fees = scenario_settings(args.scenario)

    def run(seed: int) -> tuple[int, int, int, Decimal, Decimal, int]:
        session = build_session(
            ticks=args.ticks,
            seed=seed,
            tick_ms=args.tick_ms,
            db_path=None,
            inject_faults=True,
            limits_path="config/risk_limits.paper.yaml",
            trade_size=Decimal(args.trade_size),
            market=market,
            fees=fees,
        )
        outcome = session.run()
        report = outcome.report
        return (
            report.opportunities_detected,
            report.opportunities_accepted,
            report.pnl.trades,
            report.pnl.realised,
            report.pnl.max_drawdown,
            report.execution_failures,
        )

    split = DataSplit(
        train=(101, 102, 103, 104),
        validation=(201, 202, 203),
        out_of_sample=(301, 302, 303),
    )
    parameters = {
        "scenario": args.scenario,
        "ticks": args.ticks,
        "tick_ms": args.tick_ms,
        "trade_size": args.trade_size,
        "limits": "config/risk_limits.paper.yaml",
    }
    result = walk_forward(
        run,
        split,
        parameters,
        ledger=OutOfSampleLedger(args.ledger),
        now=datetime.now(UTC),
        evaluate_out_of_sample=args.out_of_sample,
        note=f"CLI walk-forward, scenario={args.scenario}",
    )
    print(json.dumps(result.as_dict(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
