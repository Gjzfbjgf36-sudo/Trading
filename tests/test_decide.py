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
    assert "Mindestauftrag" in size.binding_constraint


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
    assert "ZU WENIG DATEN" in text
    assert "Bereich des Zufalls" in text


def test_a_usable_sample_of_losses_says_stop(journal):
    for i in range(MIN_TRADES_FOR_A_VERDICT):
        journal.commit_plan(a_commitment(ref=f"L-{i}"), now=NOW)
        journal.record_outcome(
            f"L-{i}", exit_price=Decimal("57000"), exit_reason=ExitReason.STOP_HIT, now=NOW
        )
    text = review_journal(journal)
    assert "Erwartungswert negativ" in text
    assert "nicht die Parameter drehen" in text


def test_an_empty_journal_says_so(journal):
    assert "Noch keine abgeschlossenen Trades" in review_journal(journal)


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
    assert "GRÜN" in verdict.explain()


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
    assert "Mindestauftrag" in verdict.explain()


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


# --- reward to risk ------------------------------------------------------


def test_the_reward_to_risk_floor_defaults_above_one():
    """A 1.0 target needs a >50% win rate, which trend rules do not have."""
    from arbcore.decide.gate import GateConfig

    assert GateConfig(risk=RiskProfile()).min_reward_to_risk >= Decimal("1.5")


def test_a_one_to_one_target_is_now_refused():
    verdict = gate().evaluate(a_signal(target=Decimal("63000")), an_account(), now=NOW)
    assert not verdict.green
    assert "Trefferquote" in verdict.explain()


def test_the_rejection_states_the_break_even_win_rate():
    verdict = gate().evaluate(a_signal(target=Decimal("63000")), an_account(), now=NOW)
    detail = " ".join(c.detail for c in verdict.decision.failures)
    assert "50 % Trefferquote" in detail


def test_a_target_meeting_the_floor_passes():
    verdict = gate().evaluate(a_signal(target=Decimal("64500")), an_account(), now=NOW)
    assert verdict.green, verdict.explain()


# --- affordability -------------------------------------------------------


def test_scalping_is_unaffordable_at_retail_fees():
    """The number behind 'high transaction costs'."""
    from arbcore.decide.costcheck import COMMON_PROFILES, assess

    scalping = next(p for p in COMMON_PROFILES if p.name == "Scalping")
    result = assess(scalping, equity=Decimal("1000"), fee_rate=Decimal("0.0026"))
    assert result.monthly_fee_share_of_equity > Decimal("1")  # over 100% per month
    assert result.verdict.startswith("NEIN")


def test_slow_trend_following_is_affordable():
    from arbcore.decide.costcheck import COMMON_PROFILES, assess

    trend = next(p for p in COMMON_PROFILES if p.name.startswith("Trendfolge"))
    result = assess(trend, equity=Decimal("1000"), fee_rate=Decimal("0.0026"))
    assert result.monthly_fee_share_of_equity < Decimal("0.01")
    assert result.verdict == "tragbar"


def test_a_zero_fee_venue_changes_the_answer():
    """The constraint is the fee, not the strategy."""
    from arbcore.decide.costcheck import COMMON_PROFILES, assess

    scalping = next(p for p in COMMON_PROFILES if p.name == "Scalping")
    free = assess(scalping, equity=Decimal("1000"), fee_rate=Decimal("0"))
    assert not free.stop_is_mostly_fees


def test_a_tight_stop_is_mostly_fees():
    from arbcore.decide.costcheck import StrategyProfile, assess

    tight = StrategyProfile("tight", Decimal("10"), Decimal("0.0005"))
    result = assess(tight, equity=Decimal("1000"), fee_rate=Decimal("0.0026"))
    assert result.stop_is_mostly_fees


def test_the_report_names_every_profile():
    from arbcore.decide.costcheck import COMMON_PROFILES, report

    text = report(equity=Decimal("1000"), fee_rate=Decimal("0.0026"))
    for profile in COMMON_PROFILES:
        assert profile.name in text


# --- setup check ---------------------------------------------------------


def test_setup_names_the_missing_config(tmp_path):
    from arbcore.decide.setup_check import run_checks

    lines, next_step = run_checks(str(tmp_path / "nope.yaml"))
    assert not lines[0].ok
    assert "decide.example.yaml" in lines[0].detail
    assert "Lege" in next_step


def _write_config(tmp_path, **overrides) -> str:
    import yaml

    data = {
        "starting_capital": "1000",
        "fee_rate": "0.0026",
        "journal_path": str(tmp_path / "j.sqlite"),
    }
    data.update(overrides)
    path = tmp_path / "decide.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return str(path)


def test_a_complete_setup_reports_the_next_step(tmp_path):
    from arbcore.decide.setup_check import run_checks

    lines, next_step = run_checks(_write_config(tmp_path))
    assert all(line.ok for line in lines), [x.label for x in lines if not x.ok]
    # A fresh journal is told to go and wait for the first signal, and that
    # waiting weeks is normal rather than broken.
    assert "TradingView" in next_step
    assert "normal" in next_step


def test_an_implausible_fee_is_flagged(tmp_path):
    """The number most often wrong, and it decides what is affordable."""
    from arbcore.decide.setup_check import run_checks

    lines, next_step = run_checks(_write_config(tmp_path, fee_rate="0.00001"))
    fee_line = next(line for line in lines if line.label == "Gebührensatz")
    assert not fee_line.ok
    assert "Gebührensatz" in next_step


def test_real_money_is_flagged_as_not_yet_appropriate(tmp_path):
    from arbcore.decide.setup_check import run_checks

    lines, next_step = run_checks(_write_config(tmp_path, real_money=True))
    mode = next(line for line in lines if line.label == "Modus")
    assert not mode.ok
    assert "ANLEITUNG" in mode.detail
    assert "real_money" in next_step


def test_a_broken_config_is_reported_rather_than_crashing(tmp_path):
    from arbcore.decide.setup_check import run_checks

    path = tmp_path / "bad.yaml"
    path.write_text("starting_capital: '1000'\nfee_rate: '0.26'\n", encoding="utf-8")
    lines, next_step = run_checks(str(path))
    assert not lines[-1].ok
    assert "Korrigiere" in next_step


def test_the_rule_files_are_found_regardless_of_working_directory(tmp_path, monkeypatch):
    """Someone setting this up will run it from wherever they happen to be."""
    from arbcore.decide.setup_check import run_checks

    config = _write_config(tmp_path)
    monkeypatch.chdir(tmp_path)
    lines, _ = run_checks(config)
    rules = next(line for line in lines if line.label.startswith("Regeln"))
    assert rules.ok
    assert "donchian" in rules.detail


def test_the_report_always_ends_with_one_next_step(tmp_path):
    from arbcore.decide.setup_check import report

    assert "NÄCHSTER SCHRITT" in report(_write_config(tmp_path))
