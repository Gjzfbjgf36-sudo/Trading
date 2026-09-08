"""Chart reads: recorded before the fact, scored against the outcome."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from arbcore.decide.journal import Commitment, ExitReason, Journal
from arbcore.decide.reads import (
    ChartRead,
    Conviction,
    ReadLog,
    RuleVerdict,
    calibration,
)
from arbcore.domain.types import Side

NOW = datetime(2026, 1, 1, 12, tzinfo=UTC)
OBSERVED = "Kurs 59800, ueber EMA200, gestern rote Kerze mit langem Docht"
VIEW = "Nimmt die naechste Kerze das gestrige Hoch, ist das ein Engulfing im Trend"
CONDITION = "naechste Tageskerze schliesst gruen ueber 60000"


@pytest.fixture
def log(tmp_path):
    log = ReadLog(tmp_path / "reads.sqlite")
    yield log
    log.close()


def a_read(ref: str = "R-1", **overrides) -> ChartRead:
    kwargs = dict(
        ref=ref,
        symbol="BTCUSD",
        timeframe="1D",
        observed=OBSERVED,
        rule_says=RuleVerdict.DOES_NOT_FIRE,
        claude_view=VIEW,
        conviction=Conviction.MEDIUM,
    )
    kwargs.update(overrides)
    return ChartRead(**kwargs)


# --- what must be written down -------------------------------------------


def test_an_unspecific_observation_is_refused():
    """Without it the read cannot be checked against the screenshot later."""
    with pytest.raises(ValueError, match="visible"):
        a_read(observed="chart")


def test_an_unspecific_view_is_refused():
    with pytest.raises(ValueError, match="score"):
        a_read(claude_view="gut")


def test_a_vague_condition_is_refused():
    """A condition arguable after the fact defeats the point of writing it down."""
    with pytest.raises(ValueError, match="observable"):
        a_read(trigger_condition="wenn es gut aussieht")


def test_a_read_without_a_condition_applies_now():
    assert not a_read().is_armed
    assert a_read(trigger_condition=CONDITION).is_armed


# --- the record cannot be revised ----------------------------------------


def test_a_read_is_written_once(log):
    log.record(a_read(), now=NOW)
    with pytest.raises(ValueError, match="not rewritten"):
        log.record(a_read(claude_view="etwas ganz anderes als vorher gesagt"), now=NOW)


def test_a_condition_triggers_once(log):
    log.record(a_read(trigger_condition=CONDITION), now=NOW)
    log.mark_triggered("R-1", now=NOW)
    with pytest.raises(ValueError, match="already triggered"):
        log.mark_triggered("R-1", now=NOW)


def test_only_an_armed_read_can_trigger(log):
    log.record(a_read(), now=NOW)
    with pytest.raises(ValueError, match="nothing to trigger"):
        log.mark_triggered("R-1", now=NOW)


def test_triggering_an_unknown_read_is_refused(log):
    with pytest.raises(KeyError):
        log.mark_triggered("ghost", now=NOW)


def test_armed_reads_are_listed_until_they_trigger(log):
    log.record(a_read("R-1", trigger_condition=CONDITION), now=NOW)
    log.record(a_read("R-2"), now=NOW)  # applies now, not armed
    assert [r["ref"] for r in log.armed()] == ["R-1"]
    log.mark_triggered("R-1", now=NOW)
    assert log.armed() == ()


def test_counts_separate_armed_from_triggered(log):
    log.record(a_read("R-1", trigger_condition=CONDITION), now=NOW)
    log.record(a_read("R-2", trigger_condition=CONDITION), now=NOW)
    log.mark_triggered("R-1", now=NOW)
    total, acted, armed, triggered = log.counts()
    assert (total, armed, triggered) == (2, 2, 1)
    assert acted == 0


# --- calibration ---------------------------------------------------------


def _traded(journal: Journal, ref: str, exit_price: str, *, when: datetime) -> None:
    journal.commit_plan(
        Commitment(
            ref=ref,
            symbol="BTCUSD",
            side=Side.BUY,
            entry=Decimal("60000"),
            stop=Decimal("57000"),
            quantity=Decimal("0.003"),
            risk_amount=Decimal("10"),
            thesis="Bullish Engulfing ueber dem EMA200, Bedingung eingetreten",
            invalidation="Schlusskurs unter 57000 beendet die These",
            signal_source="claude_read",
        ),
        now=when,
    )
    journal.record_outcome(
        ref,
        exit_price=Decimal(exit_price),
        exit_reason=ExitReason.STOP_HIT,
        now=when,
        fee_rate=Decimal("0"),
    )


def test_calibration_without_reads_says_so(log, tmp_path):
    journal = Journal(tmp_path / "j.sqlite")
    assert "Noch keine" in calibration(log, str(tmp_path / "j.sqlite"))
    journal.close()


def test_a_read_with_no_trade_yet_has_no_outcome(log, tmp_path):
    Journal(tmp_path / "j.sqlite").close()
    log.record(a_read(), now=NOW)
    text = calibration(log, str(tmp_path / "j.sqlite"))
    assert "Noch kein abgeschlossener Trade" in text


def test_outcomes_are_grouped_by_conviction(log, tmp_path):
    journal = Journal(tmp_path / "j.sqlite")
    log.record(a_read("H-1", conviction=Conviction.HIGH), now=NOW)
    _traded(journal, "H-1", "63000", when=NOW)
    log.record(a_read("L-1", conviction=Conviction.LOW), now=NOW)
    _traded(journal, "L-1", "57000", when=NOW + timedelta(days=1))
    journal.close()

    text = calibration(log, str(tmp_path / "j.sqlite"))
    assert "HIGH     1 Trades" in text
    assert "LOW      1 Trades" in text
    assert "Zu wenig" in text  # two trades is not a verdict


def test_unacted_reads_are_counted_but_not_scored(log, tmp_path):
    """Scoring only the ones that became trades would grade a selected sample."""
    journal = Journal(tmp_path / "j.sqlite")
    log.record(a_read("T-1"), now=NOW)
    _traded(journal, "T-1", "63000", when=NOW)
    log.record(a_read("N-1"), now=NOW)  # never traded
    journal.close()

    text = calibration(log, str(tmp_path / "j.sqlite"))
    assert "Einschätzungen gesamt   2" in text
    assert "davon abgeschlossen     1" in text


def test_recent_reads_come_back_newest_first(log):
    log.record(a_read("A"), now=NOW)
    log.record(a_read("B"), now=NOW + timedelta(hours=1))
    assert [r["ref"] for r in log.recent()] == ["B", "A"]


@pytest.mark.parametrize(
    "condition",
    [
        "naechste Tageskerze schliesst gruen ueber 60000",
        "naechste Kerze schliesst gruen",
        "Kurs bricht das gestrige Hoch",
        "Schlusskurs unter 57000",
    ],
)
def test_concrete_conditions_are_accepted(condition):
    assert a_read(trigger_condition=condition).is_armed


@pytest.mark.parametrize(
    "condition",
    ["wenn es gut aussieht", "wenn der Markt stark wirkt", "sobald es sich lohnt"],
)
def test_conditions_that_could_be_argued_afterwards_are_refused(condition):
    with pytest.raises(ValueError, match="observable"):
        a_read(trigger_condition=condition)


# --- backup and restore --------------------------------------------------


def test_a_journal_survives_being_deleted(tmp_path):
    """The container is ephemeral; a record that dies with it is not a record."""
    from arbcore.decide.journal import Journal

    original = Journal(tmp_path / "j.sqlite")
    original.commit_plan(
        Commitment(
            ref="T-1",
            symbol="BTCUSD",
            side=Side.BUY,
            entry=Decimal("60000"),
            stop=Decimal("57000"),
            quantity=Decimal("0.003"),
            risk_amount=Decimal("10"),
            thesis="Ausbruch ueber das 55-Tage-Hoch, Trend intakt",
            invalidation="Schlusskurs unter dem 20-Tage-Tief beendet die These",
            signal_source="donchian_55_20",
        ),
        now=NOW,
    )
    rows = original.export_rows()
    original.close()

    restored = Journal(tmp_path / "fresh.sqlite")
    imported, skipped = restored.import_rows(rows)
    assert (imported, skipped) == (1, 0)
    assert restored.open_positions()[0]["ref"] == "T-1"
    restored.close()


def test_restoring_twice_never_overwrites(tmp_path):
    """A restore must not be able to rewrite a plan or an outcome."""
    from arbcore.decide.journal import Journal

    journal = Journal(tmp_path / "j.sqlite")
    journal.commit_plan(
        Commitment(
            ref="T-1",
            symbol="BTCUSD",
            side=Side.BUY,
            entry=Decimal("60000"),
            stop=Decimal("57000"),
            quantity=Decimal("0.003"),
            risk_amount=Decimal("10"),
            thesis="Ausbruch ueber das 55-Tage-Hoch, Trend intakt",
            invalidation="Schlusskurs unter dem 20-Tage-Tief beendet die These",
            signal_source="donchian_55_20",
        ),
        now=NOW,
    )
    rows = journal.export_rows()
    tampered = [dict(rows[0], thesis="etwas ganz anderes, nachtraeglich geschrieben")]
    imported, skipped = journal.import_rows(tampered)
    assert (imported, skipped) == (0, 1)
    assert journal.open_positions()[0]["thesis"].startswith("Ausbruch")
    journal.close()


def test_reads_survive_the_same_way(log, tmp_path):
    from arbcore.decide.reads import export_reads, import_reads

    log.record(a_read("R-1", trigger_condition=CONDITION), now=NOW)
    log.mark_triggered("R-1", now=NOW)
    rows = export_reads(log)

    fresh = ReadLog(tmp_path / "fresh.sqlite")
    imported, skipped = import_reads(fresh, rows)
    assert (imported, skipped) == (1, 0)
    # The triggered state comes back too, not just the text.
    assert fresh.armed() == ()
    assert fresh.counts() == (1, 0, 1, 1)
    fresh.close()
