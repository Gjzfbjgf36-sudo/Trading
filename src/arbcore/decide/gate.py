"""The signal gate.

Takes a signal produced by a rule you wrote, and answers one question:

    may this trade proceed, at what size, and if not, exactly why not

It never produces a market view of its own and never places an order. It has no
opinion about whether the rule is any good — that is what the backtest and the
journal are for.

Every rejection names a specific check, so "no" is always actionable. The
checks reuse the same fail-closed discipline as the rest of the platform: a
check that cannot be evaluated rejects.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from ..domain.decision import CheckResult, Decision, RejectReason, decide
from ..domain.types import ZERO, Side, to_decimal
from .journal import Commitment
from .sizing import (
    PositionSize,
    RiskProfile,
    SizingImpossible,
    fee_share_of_risk,
    size_position,
)

#: Above this fraction, a stop-out is mostly fees rather than market move. The
#: account is then trading for the exchange, which no rule can fix.
MAX_FEE_SHARE_OF_RISK = Decimal("0.33")


@dataclass(frozen=True, slots=True)
class Signal:
    """What a rule emitted. Data, not advice."""

    #: Stable identifier, so a duplicate webhook delivery is recognised.
    ref: str
    symbol: str
    side: Side
    entry: Decimal
    stop: Decimal
    #: Which rule fired, including its parameters. Recorded verbatim.
    source: str
    emitted_at: datetime
    target: Decimal | None = None

    def __post_init__(self) -> None:
        for name in ("entry", "stop"):
            object.__setattr__(self, name, to_decimal(getattr(self, name)))
        if self.target is not None:
            object.__setattr__(self, "target", to_decimal(self.target))


@dataclass(frozen=True, slots=True)
class AccountState:
    """Where the account stands right now. Supplied by the caller.

    Passing it in rather than fetching it keeps the gate pure and its decisions
    reproducible from the record — the same property the risk engine has.
    """

    equity: Decimal
    #: Peak equity ever reached, for the drawdown check.
    peak_equity: Decimal
    #: Realised profit or loss so far today (negative is a loss).
    pnl_today: Decimal
    open_positions: int
    #: True only if this account is paper. Real money changes nothing about the
    #: checks; it is recorded so the journal can separate the two.
    paper: bool = True

    def __post_init__(self) -> None:
        for name in ("equity", "peak_equity", "pnl_today"):
            object.__setattr__(self, name, to_decimal(getattr(self, name)))

    @property
    def drawdown(self) -> Decimal:
        if self.peak_equity <= ZERO:
            return ZERO
        return max(ZERO, (self.peak_equity - self.equity) / self.peak_equity)


@dataclass(frozen=True, slots=True)
class GateConfig:
    """Everything the gate needs that is not the signal or the account."""

    risk: RiskProfile
    fee_rate: Decimal = Decimal("0.0026")
    min_notional: Decimal = Decimal("10")
    #: A signal older than this is not acted on. Alerts can arrive late, and a
    #: late alert prices a market that has moved.
    max_signal_age: timedelta = timedelta(minutes=30)
    max_open_positions: int = 3


@dataclass(frozen=True, slots=True)
class GateVerdict:
    """Green or not, with the size and the full reasoning."""

    decision: Decision
    size: PositionSize | None
    signal: Signal

    @property
    def green(self) -> bool:
        return self.decision.accepted and self.size is not None and self.size.viable

    def explain(self) -> str:
        """One screen of plain text: what to do, or why not."""
        if self.green:
            assert self.size is not None
            lines = [
                f"GREEN  {self.signal.symbol}  {self.signal.side}",
                f"  size          {self.size.quantity}  (~{self.size.notional})",
                f"  entry         {self.signal.entry}",
                f"  stop          {self.signal.stop}   <- place this immediately",
                f"  risking       {self.size.risk_amount}",
                f"  limited by    {self.size.binding_constraint}",
                f"  rule          {self.signal.source}",
                "",
                "Before entering, write the thesis and the invalidation into the",
                "journal. The gate has checked the arithmetic, not the idea.",
            ]
            return "\n".join(lines)
        lines = [f"NO  {self.signal.symbol}  {self.signal.side}", ""]
        for check in self.decision.failures:
            detail = f" — {check.detail}" if check.detail else ""
            lines.append(f"  blocked by {check.name} [{check.reason}]{detail}")
        return "\n".join(lines)

    def to_commitment(self, thesis: str, invalidation: str) -> Commitment:
        """Turn a green verdict into a journal entry.

        Requires the thesis and invalidation, so the plan cannot be recorded
        without them.
        """
        if not self.green or self.size is None:
            raise ValueError("only a green verdict can become a commitment")
        return Commitment(
            ref=self.signal.ref,
            symbol=self.signal.symbol,
            side=self.signal.side,
            entry=self.signal.entry,
            stop=self.signal.stop,
            target=self.signal.target,
            quantity=self.size.quantity,
            risk_amount=self.size.risk_amount,
            thesis=thesis,
            invalidation=invalidation,
            signal_source=self.signal.source,
        )


class SignalGate:
    """Evaluates one signal against the account and the risk profile."""

    def __init__(self, config: GateConfig) -> None:
        self.config = config

    def evaluate(
        self, signal: Signal, account: AccountState, *, now: datetime
    ) -> GateVerdict:
        checks: list[CheckResult] = []
        cfg = self.config

        # --- is the signal still worth acting on --------------------------
        age = now - signal.emitted_at
        if age < timedelta(0):
            checks.append(
                CheckResult.fail(
                    "signal_time",
                    RejectReason.CLOCK_DRIFT,
                    detail=f"signal is timestamped {abs(age)} in the future",
                )
            )
        elif age > cfg.max_signal_age:
            checks.append(
                CheckResult.fail(
                    "signal_age",
                    RejectReason.QUOTE_EXPIRED,
                    detail=f"signal is {age} old; the market has moved since",
                )
            )
        else:
            checks.append(CheckResult.ok("signal_age", detail=str(age)))

        # --- account-level stops -----------------------------------------
        drawdown = account.drawdown
        if drawdown > cfg.risk.max_drawdown:
            checks.append(
                CheckResult.fail(
                    "max_drawdown",
                    RejectReason.DAILY_LOSS_LIMIT_REACHED,
                    observed=drawdown,
                    limit=cfg.risk.max_drawdown,
                    detail=(
                        "stop trading and review. This limit exists so that a bad "
                        "run ends while the account still exists"
                    ),
                )
            )
        else:
            checks.append(CheckResult.ok("max_drawdown", detail=str(drawdown)))

        loss_today = -account.pnl_today if account.pnl_today < ZERO else ZERO
        daily_limit = account.equity * cfg.risk.daily_loss_limit
        if loss_today >= daily_limit:
            checks.append(
                CheckResult.fail(
                    "daily_loss_limit",
                    RejectReason.DAILY_LOSS_LIMIT_REACHED,
                    observed=loss_today,
                    limit=daily_limit,
                    detail="done for today. Trading after this point is where revenge starts",
                )
            )
        else:
            checks.append(CheckResult.ok("daily_loss_limit", detail=str(loss_today)))

        if account.open_positions >= cfg.max_open_positions:
            checks.append(
                CheckResult.fail(
                    "open_positions",
                    RejectReason.CONCURRENT_TRADE_LIMIT_REACHED,
                    observed=Decimal(account.open_positions),
                    limit=Decimal(cfg.max_open_positions),
                )
            )
        else:
            checks.append(CheckResult.ok("open_positions"))

        # --- can this be sized at all ------------------------------------
        size: PositionSize | None = None
        try:
            size = size_position(
                equity=account.equity,
                entry=signal.entry,
                stop=signal.stop,
                side=signal.side,
                profile=cfg.risk,
                fee_rate=cfg.fee_rate,
                min_notional=cfg.min_notional,
            )
        except SizingImpossible as exc:
            checks.append(
                CheckResult.fail("position_sizing", RejectReason.CHECK_UNEVALUABLE, detail=str(exc))
            )

        if size is not None:
            if not size.viable:
                checks.append(
                    CheckResult.fail(
                        "minimum_size",
                        RejectReason.TRADE_SIZE_EXCEEDED,
                        detail=size.binding_constraint,
                    )
                )
            else:
                checks.append(
                    CheckResult.ok(
                        "position_sizing",
                        detail=f"{size.quantity} ({size.binding_constraint})",
                    )
                )

            # --- do fees leave anything of the risk ----------------------
            share = fee_share_of_risk(
                entry=signal.entry, stop=signal.stop, fee_rate=cfg.fee_rate
            )
            if share > MAX_FEE_SHARE_OF_RISK:
                checks.append(
                    CheckResult.fail(
                        "fee_share_of_risk",
                        RejectReason.NEGATIVE_NET_PROFIT,
                        observed=share,
                        limit=MAX_FEE_SHARE_OF_RISK,
                        detail=(
                            "most of what you would lose at the stop is fees, not the "
                            "market. Use a wider stop or a bigger account, or do not "
                            "trade this setup"
                        ),
                    )
                )
            else:
                checks.append(CheckResult.ok("fee_share_of_risk", detail=str(share)))

            # --- is the reward worth the risk ----------------------------
            if signal.target is not None:
                risk_distance = abs(signal.entry - signal.stop)
                reward = abs(signal.target - signal.entry)
                ratio = reward / risk_distance if risk_distance > ZERO else ZERO
                if ratio < Decimal(1):
                    checks.append(
                        CheckResult.fail(
                            "reward_to_risk",
                            RejectReason.BELOW_SAFETY_MARGIN,
                            observed=ratio,
                            limit=Decimal(1),
                            detail=(
                                "risking more than the target pays. This needs a win "
                                "rate above 50% to break even, which few rules have"
                            ),
                        )
                    )
                else:
                    checks.append(CheckResult.ok("reward_to_risk", detail=str(ratio)))

        decision = decide(
            signal.ref,
            checks,
            context={
                "symbol": signal.symbol,
                "side": str(signal.side),
                "source": signal.source,
                "equity": str(account.equity),
                "paper": str(account.paper),
            },
            at=now,
        )
        return GateVerdict(decision=decision, size=size, signal=signal)
