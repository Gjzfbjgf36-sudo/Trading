"""Position sizing, the pre-commitment journal and the signal gate."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from arbcore.decide.gate import AccountState, GateConfig, Signal, SignalGate
from arbcore.decide.journal import (
    AlreadyClosed,
    Commitment,
    ExitReason,
    Journal,
    PlanIncomplete,
)
from arbcore.decide.sizing import (
    RiskProfile,
    SizingImpossible,
    fee_share_of_risk,
    size_position,
)
from arbcore.domain.types import Side
from arbcore.review.performance import MIN_TRADES_FOR_A_VERDICT, collect, review_journal

NOW = datetime(2026, 1, 1, 12, tzinfo=UTC)
THESIS = "Ausbruch ueber das 55-Tage-Hoch, Trend seit Oktober intakt"
INVALIDATION = "Schlusskurs unter dem 20-Tage-Tief beendet die These"


# --- sizing --------------------------------------------------------------


def test_size_comes_from_risk_not_from_account_size():
    """1% of 1000 is 10; a 3000 stop distance therefore buys very little."""
    size = size_position(
        equity=Decimal("1000"),
        entry=Decimal("60000"),
        stop=Decimal("57000"),
        side=Side.BUY,
        profile=RiskProfile(),
    )
    assert size.viable
    assert size.risk_amount == Decimal("10")
    # Loss at the stop must not exceed the risk budget.
    assert size.quantity * size.stop_distance <= Decimal("10.01")


def test_a_missing_or_wrong_side_stop_is_refused():
    """Without a stop there is no risk to size against."""
    for side, stop in ((Side.BUY, "61000"), (Side.SELL, "59000")):
        with pytest.raises(SizingImpossible):
            size_position(
                equity=Decimal("1000"),
                entry=Decimal("60000"),
                stop=Decimal(stop),
                side=side,
                profile=RiskProfile(),
            )


def test_fees_are_part_of_the_loss_not_ignored():
    with_fees = size_position(
        equity=Decimal("1000"),
        entry=Decimal("60000"),
        stop=Decimal("57000"),
        side=Side.BUY,
        profile=RiskProfile(),
        fee_rate=Decimal("0.01"),
    )
    without = size_position(
        equity=Decimal("1000"),
        entry=Decimal("60000"),
        stop=Decimal("57000"),
        side=Side.BUY,
        profile=RiskProfile(),
        fee_rate=Decimal("0"),
    )
    assert with_fees.quantity < without.quantity


def test_a_tight_stop_cannot_justify_an_unlimited_position():
    """A gap through a very tight stop would otherwise be catastrophic."""
    size = size_position(
        equity=Decimal("10000"),
        entry=Decimal("100"),
        stop=Decimal("99.99"),
        side=Side.BUY,
        profile=RiskProfile(),
        fee_rate=Decimal("0"),
    )
    assert size.binding_constraint == "max_position_fraction"
    assert size.notional <= Decimal("2500")


def test_a_small_account_is_told_it_cannot_trade_rather_than_squeezed_in():
    size = size_position(
        equity=Decimal("50"),
        entry=Decimal("60000"),
        stop=Decimal("57000"),
        side=Side.BUY,
        profile=RiskProfile(),
    )
    assert not size.viable
    assert "below the" in size.binding_constraint


def test_risk_above_two_percent_is_refused_outright():
    with pytest.raises(ValueError, match="account-ending"):
        RiskProfile(risk_per_trade=Decimal("0.05"))


def test_a_trade_may_not_risk_more_than_the_daily_limit():
    with pytest.raises(ValueError):
        RiskProfile(risk_per_trade=Decimal("0.02"), daily_loss_limit=Decimal("0.01"))


def test_fee_share_grows_as_the_stop_tightens():
    wide = fee_share_of_risk(
        entry=Decimal("60000"), stop=Decimal("54000"), fee_rate=Decimal("0.0026")
    )
    tight = fee_share_of_risk(
        entry=Decimal("60000"), stop=Decimal("59900"), fee_rate=Decimal("0.0026")
    )
    assert tight > wide
    assert tight > Decimal("0.5")  # more than half the loss is fees


# --- journal -------------------------------------------------------------


def a_commitment(ref: str = "T-1", **overrides) -> Commitment:
    kwargs = dict(
        ref=ref,
        symbol="BTCUSD",
        side=Side.BUY,
        entry=Decimal("60000"),
        stop=Decimal("57000"),
        quantity=Decimal("0.003"),
        risk_amount=Decimal("10"),
        thesis=THESIS,
        invalidation=INVALIDATION,
        signal_source="donchian_55_20",
    )
    kwargs.update(overrides)
    return Commitment(**kwargs)


@pytest.fixture
def journal(tmp_path):
    j = Journal(tmp_path / "j.sqlite")
    yield j
    j.close()


def test_a_vague_thesis_is_refused():
    """'looks good' tells you nothing in three months."""
    with pytest.raises(PlanIncomplete, match="thesis"):
        a_commitment(thesis="sieht gut aus")


def test_a_missing_invalidation_is_refused():
    with pytest.raises(PlanIncomplete, match="prove this wrong"):
        a_commitment(invalidation="mal sehen")


def test_a_plan_cannot_be_rewritten(journal):
    """The mechanism that stops a plan being edited to match the outcome."""
    journal.commit_plan(a_commitment(), now=NOW)
    with pytest.raises(AlreadyClosed, match="not rewritten"):
        journal.commit_plan(a_commitment(thesis="etwas ganz anderes als vorher"), now=NOW)


def test_outcome_is_written_once(journal):
    journal.commit_plan(a_commitment(), now=NOW)
    journal.record_outcome(
        "T-1", exit_price=Decimal("62000"), exit_reason=ExitReason.TARGET_HIT, now=NOW
    )
    with pytest.raises(AlreadyClosed, match="written once"):
        journal.record_outcome(
            "T-1", exit_price=Decimal("1"), exit_reason=ExitReason.STOP_HIT, now=NOW
        )


def test_pnl_is_net_of_fees(journal):
    journal.commit_plan(a_commitment(), now=NOW)
    outcome = journal.record_outcome(
        "T-1",
        exit_price=Decimal("62000"),
        exit_reason=ExitReason.TARGET_HIT,
        now=NOW,
        fee_rate=Decimal("0.0026"),
    )
    gross = (Decimal("62000") - Decimal("60000")) * Decimal("0.003")
    assert outcome.pnl < gross


def test_a_losing_trade_is_recorded_as_a_loss(journal):
    journal.commit_plan(a_commitment(), now=NOW)
    outcome = journal.record_outcome(
        "T-1", exit_price=Decimal("57000"), exit_reason=ExitReason.STOP_HIT, now=NOW
    )
    assert outcome.pnl < Decimal(0)


def test_discretionary_exits_are_marked_as_deviations(journal):
    journal.commit_plan(a_commitment(), now=NOW)
    outcome = journal.record_outcome(
        "T-1", exit_price=Decimal("60500"), exit_reason=ExitReason.DISCRETIONARY, now=NOW
    )
    assert not outcome.followed_plan
    assert collect(journal).deviations == 1


def test_closing_an_unknown_reference_is_refused(journal):
    with pytest.raises(KeyError):
        journal.record_outcome(
            "ghost", exit_price=Decimal("1"), exit_reason=ExitReason.STOP_HIT, now=NOW
        )


def test_reward_to_risk_is_computed_from_the_stated_target():
    assert a_commitment(target=Decimal("69000")).risk_reward == Decimal("3")


# --- review --------------------------------------------------------------


def test_a_small_sample_gets_no_verdict(journal):
    for i in range(5):
        journal.commit_plan(a_commitment(ref=f"T-{i}"), now=NOW)
        journal.record_outcome(
            f"T-{i}", exit_price=Decimal("62000"), exit_reason=ExitReason.TARGET_HIT, now=NOW
        )
    text = review_journal(journal)
    assert "NOT ENOUGH DATA" in text
    assert "within the range of chance" in text


def test_a_usable_sample_of_losses_says_stop(journal):
    for i in range(MIN_TRADES_FOR_A_VERDICT):
        journal.commit_plan(a_commitment(ref=f"L-{i}"), now=NOW)
        journal.record_outcome(
            f"L-{i}", exit_price=Decimal("57000"), exit_reason=ExitReason.STOP_HIT, now=NOW
        )
    text = review_journal(journal)
    assert "Expectancy is negative" in text
    assert "not to adjust the parameters" in text


def test_an_empty_journal_says_so(journal):
    assert "No closed trades yet" in review_journal(journal)


# --- gate ----------------------------------------------------------------


def a_signal(**overrides) -> Signal:
    kwargs = dict(
        ref="S-1",
        symbol="BTCUSD",
        side=Side.BUY,
        entry=Decimal("60000"),
        stop=Decimal("57000"),
        source="donchian_55_20",
        emitted_at=NOW,
    )
    kwargs.update(overrides)
    return Signal(**kwargs)


def an_account(**overrides) -> AccountState:
    kwargs = dict(
        equity=Decimal("1000"),
        peak_equity=Decimal("1000"),
        pnl_today=Decimal("0"),
        open_positions=0,
    )
    kwargs.update(overrides)
    return AccountState(**kwargs)


def gate() -> SignalGate:
    return SignalGate(GateConfig(risk=RiskProfile()))


def test_a_sound_signal_goes_green():
    verdict = gate().evaluate(a_signal(), an_account(), now=NOW)
    assert verdict.green, verdict.explain()
    assert verdict.size is not None and verdict.size.viable
    assert "GREEN" in verdict.explain()


def test_the_gate_never_produces_a_view_of_its_own():
    """It only ever evaluates a signal it was handed."""
    verdict = gate().evaluate(a_signal(), an_account(), now=NOW)
    assert verdict.signal.source == "donchian_55_20"


def test_a_stale_signal_is_refused():
    late = NOW + timedelta(hours=3)
    verdict = gate().evaluate(a_signal(), an_account(), now=late)
    assert not verdict.green
    assert "signal_age" in verdict.explain()


def test_a_signal_from_the_future_is_refused():
    verdict = gate().evaluate(
        a_signal(emitted_at=NOW + timedelta(hours=1)), an_account(), now=NOW
    )
    assert not verdict.green


def test_the_daily_loss_limit_stops_the_day():
    verdict = gate().evaluate(
        a_signal(), an_account(pnl_today=Decimal("-40")), now=NOW
    )
    assert not verdict.green
    assert "daily_loss_limit" in verdict.explain()


def test_a_drawdown_breach_stops_everything():
    verdict = gate().evaluate(
        a_signal(),
        an_account(equity=Decimal("800"), peak_equity=Decimal("1000")),
        now=NOW,
    )
    assert not verdict.green
    assert "max_drawdown" in verdict.explain()


def test_too_many_open_positions_blocks():
    verdict = gate().evaluate(a_signal(), an_account(open_positions=3), now=NOW)
    assert not verdict.green


def test_a_stop_where_fees_dominate_is_refused():
    verdict = gate().evaluate(
        a_signal(stop=Decimal("59900")), an_account(equity=Decimal("5000")), now=NOW
    )
    assert not verdict.green
    assert "fee_share_of_risk" in verdict.explain()


def test_a_target_worth_less_than_the_risk_is_refused():
    verdict = gate().evaluate(
        a_signal(target=Decimal("61000")), an_account(), now=NOW
    )
    assert not verdict.green
    assert "reward_to_risk" in verdict.explain()


def test_a_tiny_account_is_refused_with_a_reason():
    verdict = gate().evaluate(a_signal(), an_account(equity=Decimal("50")), now=NOW)
    assert not verdict.green
    assert "below the" in verdict.explain()


def test_every_rejection_names_the_check_that_caused_it():
    verdict = gate().evaluate(
        a_signal(stop=Decimal("59900")),
        an_account(equity=Decimal("50"), pnl_today=Decimal("-40")),
        now=NOW,
    )
    assert not verdict.green
    assert len(verdict.decision.failures) >= 2
    for check in verdict.decision.failures:
        assert check.reason is not None


def test_a_commitment_needs_a_thesis_even_from_a_green_verdict():
    verdict = gate().evaluate(a_signal(), an_account(), now=NOW)
    with pytest.raises(PlanIncomplete):
        verdict.to_commitment("kurz", "auch kurz")


def test_a_rejected_verdict_cannot_become_a_commitment():
    verdict = gate().evaluate(a_signal(), an_account(equity=Decimal("50")), now=NOW)
    with pytest.raises(ValueError, match="green"):
        verdict.to_commitment(THESIS, INVALIDATION)


def test_green_verdict_carries_the_stop_into_the_commitment():
    verdict = gate().evaluate(a_signal(), an_account(), now=NOW)
    commitment = verdict.to_commitment(THESIS, INVALIDATION)
    assert commitment.stop == Decimal("57000")
    assert commitment.signal_source == "donchian_55_20"


def test_decisions_are_reproducible():
    first = gate().evaluate(a_signal(), an_account(), now=NOW)
    second = gate().evaluate(a_signal(), an_account(), now=NOW)
    assert first.decision.as_dict()["checks"] == second.decision.as_dict()["checks"]
