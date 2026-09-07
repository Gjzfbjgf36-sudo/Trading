"""Alert routing and walk-forward discipline."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from arbcore.backtest.walkforward import (
    DataSplit,
    OutOfSampleExhausted,
    OutOfSampleLedger,
    Split,
    SplitOverlap,
    evaluate_split,
    parameter_hash,
    walk_forward,
)
from arbcore.monitoring.alerts import AlertKind, AlertRouter, Severity

NOW = datetime(2026, 1, 1, tzinfo=UTC)


# --- alerts --------------------------------------------------------------


def test_severity_is_fixed_per_kind_not_chosen_at_the_call_site():
    router = AlertRouter()
    assert router.emit(AlertKind.SAFE_MODE, "x", now=NOW).severity is Severity.CRITICAL
    assert (
        router.emit(AlertKind.TRADE_EXECUTED, "x", now=NOW).severity is Severity.INFO
    )


def test_repeats_are_suppressed_so_alerts_stay_meaningful():
    router = AlertRouter(suppression_window=timedelta(minutes=5))
    assert router.emit(AlertKind.EXECUTION_FAILURE, "a", now=NOW) is not None
    assert router.emit(AlertKind.EXECUTION_FAILURE, "b", now=NOW) is None
    assert router.suppressed == 1


def test_suppression_expires():
    router = AlertRouter(suppression_window=timedelta(minutes=5))
    router.emit(AlertKind.EXECUTION_FAILURE, "a", now=NOW)
    later = NOW + timedelta(minutes=6)
    assert router.emit(AlertKind.EXECUTION_FAILURE, "b", now=later) is not None


def test_critical_alerts_are_never_suppressed():
    """A repeated CRITICAL means the condition is still live."""
    router = AlertRouter()
    for _ in range(5):
        assert router.emit(AlertKind.WALLET_MISMATCH, "still wrong", now=NOW) is not None
    assert len(router.criticals) == 5
    assert router.suppressed == 0


def test_every_alert_kind_has_a_severity():
    router = AlertRouter()
    for kind in AlertKind:
        assert router.emit(kind, "x", now=NOW) is not None


# --- walk-forward --------------------------------------------------------


def constant_run(pnl: str):
    def run(seed: int) -> tuple[int, int, int, Decimal, Decimal, int]:
        return (100, 10, 5, Decimal(pnl), Decimal("1"), 1)

    return run


def a_split() -> DataSplit:
    return DataSplit(train=(1, 2), validation=(3,), out_of_sample=(4,))


def test_overlapping_splits_are_refused():
    """Overlapping splits are not a split."""
    with pytest.raises(SplitOverlap):
        DataSplit(train=(1, 2), validation=(2,), out_of_sample=(4,))


def test_empty_split_is_refused():
    with pytest.raises(ValueError):
        DataSplit(train=(), validation=(3,), out_of_sample=(4,))


def test_parameter_hash_changes_with_any_parameter():
    assert parameter_hash({"a": 1}) != parameter_hash({"a": 2})
    assert parameter_hash({"a": 1}) == parameter_hash({"a": 1})


def test_out_of_sample_is_not_touched_by_default(tmp_path):
    result = walk_forward(
        constant_run("10"),
        a_split(),
        {"p": 1},
        ledger=OutOfSampleLedger(tmp_path / "ledger.json"),
        now=NOW,
    )
    assert result.out_of_sample is None
    assert "not touched" in " ".join(result.notes)


def test_out_of_sample_budget_is_spent_once(tmp_path):
    ledger = OutOfSampleLedger(tmp_path / "ledger.json")
    walk_forward(
        constant_run("10"),
        a_split(),
        {"p": 1},
        ledger=ledger,
        now=NOW,
        evaluate_out_of_sample=True,
    )
    with pytest.raises(OutOfSampleExhausted):
        walk_forward(
            constant_run("10"),
            a_split(),
            {"p": 1},
            ledger=ledger,
            now=NOW,
            evaluate_out_of_sample=True,
        )


def test_the_budget_survives_a_restart(tmp_path):
    """An in-memory budget is reset by whoever least wants to be limited."""
    path = tmp_path / "ledger.json"
    first = OutOfSampleLedger(path)
    walk_forward(
        constant_run("10"),
        a_split(),
        {"p": 1},
        ledger=first,
        now=NOW,
        evaluate_out_of_sample=True,
    )
    reopened = OutOfSampleLedger(path)
    assert reopened.spent(parameter_hash({"p": 1})) == 1


def test_a_changed_parameter_set_has_its_own_budget(tmp_path):
    ledger = OutOfSampleLedger(tmp_path / "ledger.json")
    for value in (1, 2):
        walk_forward(
            constant_run("10"),
            a_split(),
            {"p": value},
            ledger=ledger,
            now=NOW,
            evaluate_out_of_sample=True,
        )
    assert len(ledger.entries) == 2


def test_degradation_compares_out_of_sample_to_training(tmp_path):
    result = walk_forward(
        constant_run("10"),
        a_split(),
        {"p": 1},
        ledger=OutOfSampleLedger(tmp_path / "ledger.json"),
        now=NOW,
        evaluate_out_of_sample=True,
    )
    # Identical behaviour across splits: no degradation.
    assert result.degradation == Decimal(1)


def test_pnl_per_trade_is_none_without_trades():
    def no_trades(seed: int) -> tuple[int, int, int, Decimal, Decimal, int]:
        return (10, 0, 0, Decimal(0), Decimal(0), 0)

    result = evaluate_split(no_trades, (1, 2), Split.TRAIN)
    assert result.pnl_per_trade is None
    assert result.acceptance_rate == Decimal(0)
