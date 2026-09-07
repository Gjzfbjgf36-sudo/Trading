"""Measured execution statistics.

Execution probabilities are **observed**, never assumed. This module is the
only source of `ExecutionProbabilities` in the system, and it refuses to
produce them until it has enough observations to mean anything.

That creates a genuine bootstrap problem: no data means no EV, no EV means no
trade, no trade means no data. It is resolved by an explicit, paper-only,
size-capped calibration mode (see `RiskEngine` and docs/paper-trading.md) whose
trades are recorded as data-gathering and excluded from performance — not by
inventing a prior and calling it a measurement.
"""

from __future__ import annotations

import enum
from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal

from ..domain.types import StrategyKind
from ..risk.proposal import ExecutionProbabilities

ONE = Decimal(1)
ZERO = Decimal(0)


class AttemptOutcome(enum.StrEnum):
    FULL_FILL = "FULL_FILL"
    PARTIAL_FILL = "PARTIAL_FILL"
    FAILED = "FAILED"
    #: Cancelled because the price moved through our limit before filling.
    DECAYED = "DECAYED"
    #: Filled, but the realised price was worse than the decision price by
    #: more than the tolerated slippage.
    ADVERSE_MOVE = "ADVERSE_MOVE"
    #: Outcome could not be established. Never counted as anything else.
    UNKNOWN = "UNKNOWN"


@dataclass(slots=True)
class ExecutionStatistics:
    """Rolling outcome counts for one strategy.

    ``min_samples`` is the point below which we decline to express an opinion.
    The window is rolling because execution quality changes: statistics from a
    thousand trades in a different regime are not evidence about now.
    """

    strategy: StrategyKind
    min_samples: int = 50
    window: int = 500
    outcomes: deque[AttemptOutcome] = field(default_factory=deque)

    def __post_init__(self) -> None:
        if self.min_samples < 10:
            raise ValueError("min_samples below 10 cannot support a probability estimate")
        self.outcomes = deque(self.outcomes, maxlen=self.window)

    def record(self, outcome: AttemptOutcome) -> None:
        self.outcomes.append(outcome)

    @property
    def samples(self) -> int:
        return len(self.outcomes)

    @property
    def sufficient(self) -> bool:
        return self.samples >= self.min_samples

    def count(self, outcome: AttemptOutcome) -> int:
        return sum(1 for o in self.outcomes if o is outcome)

    def probabilities(self) -> ExecutionProbabilities | None:
        """Measured probabilities, or ``None`` when there is not enough data.

        ``UNKNOWN`` outcomes are counted as failures. They are not neutral: an
        unresolved outcome cost us a reconciliation and may have cost us money,
        and treating it as "didn't happen" would flatter every estimate.
        """
        if not self.sufficient:
            return None
        n = Decimal(self.samples)
        unknown = self.count(AttemptOutcome.UNKNOWN)
        failed = self.count(AttemptOutcome.FAILED) + unknown
        return ExecutionProbabilities(
            full_execution=(Decimal(self.count(AttemptOutcome.FULL_FILL)) / n),
            partial_execution=(Decimal(self.count(AttemptOutcome.PARTIAL_FILL)) / n),
            failure=(Decimal(failed) / n),
            opportunity_decay=(Decimal(self.count(AttemptOutcome.DECAYED)) / n),
            adverse_price_move=(Decimal(self.count(AttemptOutcome.ADVERSE_MOVE)) / n),
            basis=(
                f"measured over {self.samples} attempts (rolling window {self.window}); "
                f"{unknown} unresolved outcomes counted as failures"
            ),
        )

    def summary(self) -> dict[str, str]:
        return {
            "samples": str(self.samples),
            "sufficient": str(self.sufficient),
            **{str(o): str(self.count(o)) for o in AttemptOutcome},
        }
