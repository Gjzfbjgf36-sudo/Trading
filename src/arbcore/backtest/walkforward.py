"""Walk-forward evaluation.

Out-of-sample data is a consumable resource. Every time a parameter set is
evaluated against it and then changed in response, that dataset stops being
out-of-sample — the information has leaked into the parameters via the
researcher. The usual defence is a rule in a document that nobody enforces.

This module enforces it: the ledger records every out-of-sample evaluation
against a hash of the exact parameters used, and refuses repeated evaluations
beyond a small budget. Burning through the budget is meant to be uncomfortable.
"""

from __future__ import annotations

import enum
import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..domain.types import ZERO


class Split(enum.StrEnum):
    TRAIN = "TRAIN"
    VALIDATION = "VALIDATION"
    OUT_OF_SAMPLE = "OUT_OF_SAMPLE"


class OutOfSampleExhausted(RuntimeError):
    """Raised when the out-of-sample budget for a parameter set is spent."""


class SplitOverlap(ValueError):
    """Raised when datasets overlap, which destroys the separation entirely."""


def parameter_hash(parameters: Mapping[str, Any]) -> str:
    """Stable hash of a parameter set, so a changed parameter is a new identity."""
    payload = json.dumps(parameters, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class DataSplit:
    """Disjoint seed ranges standing in for disjoint time periods.

    In a real backtest these are date ranges over recorded market data. The
    contract is the same either way: the three sets never overlap, and the
    out-of-sample set is touched last and rarely.
    """

    train: tuple[int, ...]
    validation: tuple[int, ...]
    out_of_sample: tuple[int, ...]

    def __post_init__(self) -> None:
        sets = {
            Split.TRAIN: set(self.train),
            Split.VALIDATION: set(self.validation),
            Split.OUT_OF_SAMPLE: set(self.out_of_sample),
        }
        for name, values in sets.items():
            if not values:
                raise ValueError(f"{name} split is empty")
        names = list(sets)
        for i, first in enumerate(names):
            for second in names[i + 1 :]:
                shared = sets[first] & sets[second]
                if shared:
                    raise SplitOverlap(
                        f"{first} and {second} share seeds {sorted(shared)}; "
                        "overlapping splits are not a split"
                    )

    def seeds(self, split: Split) -> tuple[int, ...]:
        return {
            Split.TRAIN: self.train,
            Split.VALIDATION: self.validation,
            Split.OUT_OF_SAMPLE: self.out_of_sample,
        }[split]


@dataclass(slots=True)
class OutOfSampleLedger:
    """Records and limits out-of-sample evaluations.

    Persisted to disk so the budget survives a restart — an in-memory budget is
    reset by the researcher who least wants to be limited by it.
    """

    path: Path
    budget_per_parameter_set: int = 1
    entries: list[dict[str, str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        if self.path.exists():
            self.entries = json.loads(self.path.read_text(encoding="utf-8"))

    def spent(self, param_hash: str) -> int:
        return sum(1 for e in self.entries if e["parameter_hash"] == param_hash)

    def assert_available(self, param_hash: str) -> None:
        used = self.spent(param_hash)
        if used >= self.budget_per_parameter_set:
            raise OutOfSampleExhausted(
                f"parameter set {param_hash} has already been evaluated out-of-sample "
                f"{used} time(s). Evaluating again and then tuning is how an "
                f"out-of-sample dataset silently becomes a training set. Use the "
                f"validation split, or accept the result you already have."
            )

    def record(self, param_hash: str, *, at: datetime, note: str) -> None:
        self.entries.append(
            {"parameter_hash": param_hash, "at": at.isoformat(), "note": note}
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.entries, indent=2), encoding="utf-8")


@dataclass(frozen=True, slots=True)
class SplitResult:
    """Aggregate across every run in one split."""

    split: Split
    runs: int
    trades: int
    net_pnl: Decimal
    max_drawdown: Decimal
    accepted: int
    detected: int
    failures: int

    @property
    def acceptance_rate(self) -> Decimal | None:
        if self.detected == 0:
            return None
        return Decimal(self.accepted) / Decimal(self.detected)

    @property
    def pnl_per_trade(self) -> Decimal | None:
        """``None`` with no trades — an undefined average, not a zero one."""
        if self.trades == 0:
            return None
        return self.net_pnl / Decimal(self.trades)

    def as_dict(self) -> dict[str, object]:
        return {
            "split": str(self.split),
            "runs": self.runs,
            "detected": self.detected,
            "accepted": self.accepted,
            "trades": self.trades,
            "net_pnl": str(self.net_pnl),
            "max_drawdown": str(self.max_drawdown),
            "failures": self.failures,
            "pnl_per_trade": str(self.pnl_per_trade) if self.pnl_per_trade else None,
            "acceptance_rate": (
                str(self.acceptance_rate) if self.acceptance_rate is not None else None
            ),
        }


#: A run function takes a seed and returns (detected, accepted, trades, net_pnl,
#: max_drawdown, failures).
RunFn = Callable[[int], tuple[int, int, int, Decimal, Decimal, int]]


def evaluate_split(run: RunFn, seeds: Sequence[int], split: Split) -> SplitResult:
    detected = accepted = trades = failures = 0
    net = ZERO
    worst_drawdown = ZERO
    for seed in seeds:
        d, a, t, pnl, drawdown, f = run(seed)
        detected += d
        accepted += a
        trades += t
        failures += f
        net += pnl
        worst_drawdown = max(worst_drawdown, drawdown)
    return SplitResult(
        split=split,
        runs=len(seeds),
        trades=trades,
        net_pnl=net,
        max_drawdown=worst_drawdown,
        accepted=accepted,
        detected=detected,
        failures=failures,
    )


@dataclass(frozen=True, slots=True)
class WalkForwardResult:
    train: SplitResult
    validation: SplitResult
    out_of_sample: SplitResult | None
    parameter_hash: str
    notes: tuple[str, ...]

    @property
    def degradation(self) -> Decimal | None:
        """Out-of-sample P/L per trade relative to training.

        Below 1 means the result did not hold up. Well above 1 is not good news
        either; it usually means the splits differ in some way that has nothing
        to do with the strategy.
        """
        if self.out_of_sample is None:
            return None
        baseline = self.train.pnl_per_trade
        actual = self.out_of_sample.pnl_per_trade
        if baseline is None or actual is None or baseline == ZERO:
            return None
        return actual / baseline

    def as_dict(self) -> dict[str, object]:
        return {
            "parameter_hash": self.parameter_hash,
            "train": self.train.as_dict(),
            "validation": self.validation.as_dict(),
            "out_of_sample": (
                self.out_of_sample.as_dict() if self.out_of_sample else None
            ),
            "out_of_sample_degradation": (
                str(self.degradation) if self.degradation is not None else None
            ),
            "notes": list(self.notes),
        }


def walk_forward(
    run: RunFn,
    split: DataSplit,
    parameters: Mapping[str, Any],
    *,
    ledger: OutOfSampleLedger,
    now: datetime,
    evaluate_out_of_sample: bool = False,
    note: str = "",
) -> WalkForwardResult:
    """Run train and validation always; out-of-sample only on explicit request.

    The default is *not* to touch the out-of-sample set. Making it opt-in is the
    point: it should take a deliberate act, not a default argument.
    """
    param_hash = parameter_hash(dict(parameters))
    train = evaluate_split(run, split.train, Split.TRAIN)
    validation = evaluate_split(run, split.validation, Split.VALIDATION)
    notes = [
        "Parameters were not fitted to any split; the strategy is rule-based.",
        "Synthetic data: this measures consistency across regimes, not real profitability.",
    ]

    out_of_sample: SplitResult | None = None
    if evaluate_out_of_sample:
        ledger.assert_available(param_hash)
        out_of_sample = evaluate_split(run, split.out_of_sample, Split.OUT_OF_SAMPLE)
        ledger.record(param_hash, at=now, note=note or "walk-forward evaluation")
        notes.append(
            f"Out-of-sample budget for {param_hash} is now spent. Changing any "
            f"parameter creates a new set with a fresh budget — which is exactly "
            f"the loophole this ledger makes visible rather than prevents."
        )
    else:
        notes.append("Out-of-sample split was not touched.")

    return WalkForwardResult(
        train=train,
        validation=validation,
        out_of_sample=out_of_sample,
        parameter_hash=param_hash,
        notes=tuple(notes),
    )
