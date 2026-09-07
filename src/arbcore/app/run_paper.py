"""CLI: run a paper session and print the report.

Usage:
    python -m arbcore.app.run_paper --ticks 20000 --seed 20260907 --json out.json
"""

from __future__ import annotations

import argparse
import json
from decimal import Decimal

from ..adapters.synthetic import SyntheticConfig
from ..config.limits import load_limits
from ..config.universe import BTC, COINBASE, KRAKEN, USDC_ETHEREUM, build_initial_whitelist
from ..costs.fees import FeeBook, VenueFeeSchedule
from ..execution.paper import PaperMarketModel
from ..strategy.cex_cex import CexCexConfig
from .paper_session import PaperSession, SessionConfig, SessionOutcome

SCENARIOS = ("realistic", "mechanics")


def build_fee_book() -> FeeBook:
    """Placeholder fee schedules.

    ``confirmed=False`` is load-bearing: these numbers are plausible taker
    rates, not documented ones. They are adequate for exercising the machinery
    and must never back real capital.
    """
    return FeeBook(
        schedules={
            COINBASE: VenueFeeSchedule(
                venue=COINBASE,
                taker_rate=Decimal("0.0060"),
                maker_rate=Decimal("0.0040"),
                withdrawal_fees={BTC: Decimal("0.0001")},
                confirmed=False,
            ),
            KRAKEN: VenueFeeSchedule(
                venue=KRAKEN,
                taker_rate=Decimal("0.0026"),
                maker_rate=Decimal("0.0016"),
                withdrawal_fees={BTC: Decimal("0.00005")},
                confirmed=False,
            ),
        }
    )


def high_tier_fee_book() -> FeeBook:
    """Fee schedule for the mechanics scenario only.

    Represents a high-volume tier. It is NOT the rate a new account pays, and
    using it is a deliberate assumption made to exercise the execution path —
    never a claim that these are our fees.
    """
    return FeeBook(
        schedules={
            COINBASE: VenueFeeSchedule(
                venue=COINBASE,
                taker_rate=Decimal("0.0005"),
                maker_rate=Decimal("0.0000"),
                withdrawal_fees={BTC: Decimal("0.0001")},
                confirmed=False,
                source="ASSUMED high-volume tier; mechanics scenario only",
            ),
            KRAKEN: VenueFeeSchedule(
                venue=KRAKEN,
                taker_rate=Decimal("0.0004"),
                maker_rate=Decimal("0.0000"),
                withdrawal_fees={BTC: Decimal("0.00005")},
                confirmed=False,
                source="ASSUMED high-volume tier; mechanics scenario only",
            ),
        }
    )


def scenario_settings(scenario: str) -> tuple[SyntheticConfig, FeeBook]:
    """Market process and fee book for a named scenario.

    ``realistic``  retail taker fees and a calm major-pair spread process. This
                   is the honest configuration and the one whose economic
                   conclusion should be believed.
    ``mechanics``  wide dislocations and high-tier fees, chosen so that trades
                   actually execute and the execution, failure and
                   reconciliation paths get exercised. Its P/L is an artefact
                   of these parameters and means nothing economically.
    """
    if scenario == "realistic":
        return SyntheticConfig(), build_fee_book()
    if scenario == "mechanics":
        return (
            SyntheticConfig(offset_volatility=Decimal("0.0025")),
            high_tier_fee_book(),
        )
    raise ValueError(f"unknown scenario {scenario!r}; expected one of {SCENARIOS}")


def build_session(
    *,
    ticks: int,
    seed: int,
    tick_ms: int,
    db_path: str | None,
    inject_faults: bool,
    limits_path: str,
    trade_size: Decimal,
    initial_base: Decimal = Decimal("0.009"),
    initial_quote: Decimal = Decimal("700"),
    market: SyntheticConfig | None = None,
    execution: PaperMarketModel | None = None,
    fees: FeeBook | None = None,
) -> PaperSession:
    limits = load_limits(limits_path)
    return PaperSession(
        config=SessionConfig(
            ticks=ticks,
            seed=seed,
            tick_ms=tick_ms,
            db_path=db_path,
            inject_faults=inject_faults,
            initial_base=initial_base,
            initial_quote=initial_quote,
        ),
        limits=limits,
        whitelist=build_initial_whitelist(),
        fees=fees or build_fee_book(),
        base=BTC,
        quote=USDC_ETHEREUM,
        venues=(COINBASE, KRAKEN),
        strategy_config=CexCexConfig(
            base=BTC,
            quote=USDC_ETHEREUM,
            trade_size=trade_size,
            calibration_size=trade_size / Decimal(4),
        ),
        market_config=market,
        execution_model=execution,
    )


def render(outcome: SessionOutcome) -> str:
    payload = {
        "session_id": outcome.session_id,
        "report": outcome.report.as_dict(),
        "counters": outcome.counters.as_dict(),
        "operator_interventions": outcome.interventions,
        "reconciliations": outcome.reconciliations,
        "reconciliation_failures": outcome.reconciliation_failures,
        "book_resyncs": outcome.book_resyncs,
        "execution_statistics": outcome.statistics,
    }
    return json.dumps(payload, indent=2)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a paper trading session")
    parser.add_argument("--ticks", type=int, default=5_000)
    parser.add_argument("--tick-ms", type=int, default=250)
    parser.add_argument("--seed", type=int, default=20260907)
    parser.add_argument("--db", type=str, default=None)
    parser.add_argument("--limits", type=str, default="config/risk_limits.paper.yaml")
    parser.add_argument("--trade-size", type=str, default="0.004")
    parser.add_argument("--no-faults", action="store_true")
    parser.add_argument("--scenario", choices=SCENARIOS, default="realistic")
    parser.add_argument("--json", type=str, default=None)
    args = parser.parse_args()

    market, fees = scenario_settings(args.scenario)
    session = build_session(
        ticks=args.ticks,
        seed=args.seed,
        tick_ms=args.tick_ms,
        db_path=args.db,
        inject_faults=not args.no_faults,
        limits_path=args.limits,
        trade_size=Decimal(args.trade_size),
        market=market,
        fees=fees,
    )
    session.notes.append(f"scenario={args.scenario}")
    if args.scenario == "mechanics":
        session.notes.append(
            "MECHANICS SCENARIO: dislocations and fees were chosen to make trades "
            "happen so the execution path could be tested. The P/L is an artefact "
            "of those parameters and is not an economic result."
        )
    outcome = session.run()
    rendered = render(outcome)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            handle.write(rendered)
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
