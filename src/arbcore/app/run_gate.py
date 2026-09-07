"""CLI: check a signal against your risk limits, and record the plan.

Nothing here places an order. It answers "may this proceed, at what size" and
writes down what you committed to, so that in three months your own hit rate is
a number rather than a feeling.

    # Is this trade allowed, and how big?
    python -m arbcore.app.run_gate check \\
        --symbol BTCUSD --side BUY --entry 60000 --stop 57000 \\
        --equity 1000 --source donchian_55_20

    # Record the plan (only possible for a green verdict)
    python -m arbcore.app.run_gate commit \\
        --symbol BTCUSD --side BUY --entry 60000 --stop 57000 \\
        --equity 1000 --source donchian_55_20 \\
        --thesis "..." --invalidation "..."

    # Close it out
    python -m arbcore.app.run_gate close --ref BTCUSD-123 --exit 62000 --reason TARGET_HIT

    # What do my own decisions say?
    python -m arbcore.app.run_gate review
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from decimal import Decimal

from ..decide.gate import AccountState, GateConfig, Signal, SignalGate
from ..decide.journal import ExitReason, Journal
from ..decide.sizing import RiskProfile
from ..domain.types import Side
from ..review.performance import review_journal

DEFAULT_JOURNAL = "journal/decisions.sqlite"


def _profile(args: argparse.Namespace) -> RiskProfile:
    return RiskProfile(
        risk_per_trade=Decimal(args.risk_per_trade),
        daily_loss_limit=Decimal(args.daily_loss),
        max_drawdown=Decimal(args.max_drawdown),
    )


def _signal(args: argparse.Namespace, now: datetime) -> Signal:
    return Signal(
        ref=args.ref or f"{args.symbol}-{int(now.timestamp())}",
        symbol=args.symbol,
        side=Side(args.side),
        entry=Decimal(args.entry),
        stop=Decimal(args.stop),
        target=Decimal(args.target) if args.target else None,
        source=args.source,
        emitted_at=now,
    )


def _account(args: argparse.Namespace) -> AccountState:
    equity = Decimal(args.equity)
    return AccountState(
        equity=equity,
        peak_equity=Decimal(args.peak_equity) if args.peak_equity else equity,
        pnl_today=Decimal(args.pnl_today),
        open_positions=args.open_positions,
        paper=not args.real,
    )


def _add_trade_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--side", choices=["BUY", "SELL"], default="BUY")
    parser.add_argument("--entry", required=True)
    parser.add_argument("--stop", required=True)
    parser.add_argument("--target", default=None)
    parser.add_argument("--source", required=True, help="which rule produced this signal")
    parser.add_argument("--ref", default=None)
    parser.add_argument("--equity", required=True)
    parser.add_argument("--peak-equity", dest="peak_equity", default=None)
    parser.add_argument("--pnl-today", dest="pnl_today", default="0")
    parser.add_argument("--open-positions", dest="open_positions", type=int, default=0)
    parser.add_argument("--fee-rate", dest="fee_rate", default="0.0026")
    parser.add_argument("--risk-per-trade", dest="risk_per_trade", default="0.01")
    parser.add_argument("--daily-loss", dest="daily_loss", default="0.03")
    parser.add_argument("--max-drawdown", dest="max_drawdown", default="0.15")
    parser.add_argument(
        "--real",
        action="store_true",
        help="mark this as a real-money trade in the journal (default: paper)",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Decision gate — no orders are placed")
    parser.add_argument("--journal", default=DEFAULT_JOURNAL)
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="may this trade proceed, and how big?")
    _add_trade_args(check)

    commit = sub.add_parser("commit", help="record the plan for a green signal")
    _add_trade_args(commit)
    commit.add_argument("--thesis", required=True, help="why you expect this to work")
    commit.add_argument(
        "--invalidation", required=True, help="what observable would prove it wrong"
    )

    close = sub.add_parser("close", help="record the outcome")
    close.add_argument("--ref", required=True)
    close.add_argument("--exit", dest="exit_price", required=True)
    close.add_argument(
        "--reason", choices=[str(r) for r in ExitReason], default=str(ExitReason.STOP_HIT)
    )
    close.add_argument("--fee-rate", dest="fee_rate", default="0.0026")

    sub.add_parser("open", help="list open positions")
    sub.add_parser("review", help="what your own decisions say so far")

    args = parser.parse_args()
    now = datetime.now(UTC)

    if args.command in ("check", "commit"):
        gate = SignalGate(
            GateConfig(risk=_profile(args), fee_rate=Decimal(args.fee_rate))
        )
        signal = _signal(args, now)
        verdict = gate.evaluate(signal, _account(args), now=now)
        print(verdict.explain())
        if args.command == "check":
            return 0 if verdict.green else 1
        if not verdict.green:
            print("\nNothing recorded: the gate said no.")
            return 1
        journal = Journal(args.journal)
        journal.commit_plan(
            verdict.to_commitment(args.thesis, args.invalidation), now=now
        )
        journal.close()
        print(f"\nPlan recorded as {signal.ref}. It cannot be edited from here on.")
        return 0

    journal = Journal(args.journal)
    try:
        if args.command == "close":
            outcome = journal.record_outcome(
                args.ref,
                exit_price=Decimal(args.exit_price),
                exit_reason=ExitReason(args.reason),
                now=now,
                fee_rate=Decimal(args.fee_rate),
            )
            print(f"Closed {args.ref}: P/L {outcome.pnl} ({outcome.exit_reason})")
            if not outcome.followed_plan:
                print("Recorded as a deviation from the plan.")
        elif args.command == "open":
            rows = journal.open_positions()
            if not rows:
                print("No open positions.")
            for row in rows:
                print(f"{row['ref']}  {row['symbol']} {row['side']}  "
                      f"entry {row['entry']}  stop {row['stop']}  qty {row['quantity']}")
                print(f"    thesis: {row['thesis']}")
        elif args.command == "review":
            print(review_journal(journal))
    finally:
        journal.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
