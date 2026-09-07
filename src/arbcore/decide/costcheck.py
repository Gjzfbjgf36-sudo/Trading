"""Which strategies your fee rate and account size can actually support.

Two numbers decide this, and neither appears in the usual list of day-trading
strategies:

* **Fee share of risk.** A stop-out close to the entry is mostly fees. Above
  roughly a third, the account is trading for the exchange.
* **Fees per month.** Frequency multiplies a per-trade cost that looks small
  into one that is not. Scalping at 100 trades a day is a different business
  from a rule that signals twice a month, and the difference is arithmetic
  rather than opinion.

Nothing here says a strategy is good. It says whether it is affordable, which
is a prior question.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..domain.types import ZERO, to_decimal
from .sizing import fee_share_of_risk

_PCT = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class StrategyProfile:
    """A strategy class described by how often it trades and how far its stop sits."""

    name: str
    trades_per_month: Decimal
    #: Typical stop distance as a fraction of price.
    stop_fraction: Decimal
    note: str = ""


#: Rough shapes of the common day-trading approaches. These are order-of-
#: magnitude figures for comparison, not measurements of any particular rule —
#: the point is the ratio between rows, which does not depend on precision.
COMMON_PROFILES: tuple[StrategyProfile, ...] = (
    StrategyProfile("Scalping", Decimal("2000"), Decimal("0.0010"),
                    "Sekunden bis Minuten, sehr enge Stops"),
    StrategyProfile("Momentum", Decimal("120"), Decimal("0.0050"),
                    "schnelle Bewegungen, mittlere Stops"),
    StrategyProfile("News", Decimal("40"), Decimal("0.0100"),
                    "Gaps: der Stop hält oft nicht"),
    StrategyProfile("Breakout (Intraday)", Decimal("40"), Decimal("0.0100"), ""),
    StrategyProfile("Pullback", Decimal("20"), Decimal("0.0150"), ""),
    StrategyProfile("Trendfolge (Tageskerzen)", Decimal("2"), Decimal("0.0500"),
                    "wenige Signale, weite Stops"),
)


@dataclass(frozen=True, slots=True)
class Affordability:
    """What one strategy class costs against a given account and fee rate."""

    profile: StrategyProfile
    fee_share: Decimal
    monthly_fee_cost: Decimal
    monthly_fee_share_of_equity: Decimal

    @property
    def stop_is_mostly_fees(self) -> bool:
        return self.fee_share > Decimal("0.33")

    @property
    def verdict(self) -> str:
        if self.stop_is_mostly_fees:
            return "NEIN — der Stop-Verlust wäre überwiegend Gebühr"
        if self.monthly_fee_share_of_equity > Decimal("0.10"):
            return "NEIN — Gebühren fressen über 10 % des Kontos pro Monat"
        if self.monthly_fee_share_of_equity > Decimal("0.02"):
            return "grenzwertig"
        return "tragbar"


def assess(
    profile: StrategyProfile,
    *,
    equity: Decimal,
    fee_rate: Decimal,
    position_fraction: Decimal = Decimal("0.20"),
) -> Affordability:
    """Cost of running one strategy class for a month.

    ``position_fraction`` is how much of the account a typical position uses.
    Fees are charged on notional, so a larger position costs proportionally
    more — which is why the monthly figure is a share of equity rather than an
    absolute that flatters a big account.
    """
    equity = to_decimal(equity)
    rate = to_decimal(fee_rate)
    price = Decimal("100")  # any reference price; only ratios matter here
    stop = price * (Decimal(1) - profile.stop_fraction)

    share = fee_share_of_risk(entry=price, stop=stop, fee_rate=rate)
    notional = equity * to_decimal(position_fraction)
    # Both sides of every trade pay.
    monthly = (notional * rate * Decimal(2) * profile.trades_per_month).quantize(_PCT)
    return Affordability(
        profile=profile,
        fee_share=share,
        monthly_fee_cost=monthly,
        monthly_fee_share_of_equity=(monthly / equity).quantize(Decimal("0.0001"))
        if equity > ZERO
        else Decimal(1),
    )


def report(
    *,
    equity: Decimal,
    fee_rate: Decimal,
    profiles: tuple[StrategyProfile, ...] = COMMON_PROFILES,
) -> str:
    """Plain-text table. Answers 'why not scalping' with a number."""
    rate_pct = (to_decimal(fee_rate) * Decimal(100)).quantize(Decimal("0.001"))
    lines = [
        f"Konto {equity} · Gebühr {rate_pct} % pro Seite",
        "",
        f"{'Strategie':<26}{'Trades/Mon':>11}{'Gebühr/Mon':>12}{'% Konto':>9}  Urteil",
        "-" * 96,
    ]
    for profile in profiles:
        result = assess(profile, equity=equity, fee_rate=fee_rate)
        pct = (result.monthly_fee_share_of_equity * Decimal(100)).quantize(_PCT)
        lines.append(
            f"{profile.name:<26}{profile.trades_per_month:>11}"
            f"{result.monthly_fee_cost:>12}{pct:>8}%  {result.verdict}"
        )
    lines += [
        "",
        "Gebühren fallen an, bevor irgendein Kursgewinn entsteht. Eine Strategie,",
        "deren Gebühren über 10 % des Kontos pro Monat kosten, muss über 120 % im",
        "Jahr erwirtschaften, nur um bei null zu landen.",
        "",
        "Die Zeilen sind Größenordnungen zum Vergleich, keine Messung einer",
        "konkreten Regel. Das Verhältnis zwischen ihnen stimmt trotzdem.",
    ]
    return "\n".join(lines)
