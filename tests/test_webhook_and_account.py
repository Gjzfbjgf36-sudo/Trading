"""Settings, derived account state, and the webhook receiver."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from arbcore.decide.account import AccountLedger
from arbcore.decide.gate import GateConfig, Signal, SignalGate
from arbcore.decide.journal import Commitment, ExitReason, Journal
from arbcore.decide.settings import SettingsError, settings_from_mapping
from arbcore.decide.sizing import RiskProfile
from arbcore.decide.webhook import (
    InvalidPayload,
    SignalQueue,
    make_server,
    parse_alert,
)
from arbcore.domain.types import Side

NOW = datetime(2026, 1, 1, 12, tzinfo=UTC)
TODAY = NOW.date()
THESIS = "Ausbruch ueber das 55-Tage-Hoch, Trend seit Oktober intakt"
INVALIDATION = "Schlusskurs unter dem 20-Tage-Tief beendet die These"


def base_settings(**overrides):
    data = {"starting_capital": "1000", "fee_rate": "0.0026"}
    data.update(overrides)
    return settings_from_mapping(data)


# --- settings ------------------------------------------------------------


def test_a_mistyped_setting_is_an_error_not_a_silent_default():
    with pytest.raises(SettingsError, match="unknown settings"):
        settings_from_mapping(
            {"starting_capital": "1000", "fee_rate": "0.0026", "risk_pertrade": "0.05"}
        )


def test_missing_required_settings_are_named():
    with pytest.raises(SettingsError, match="fee_rate"):
        settings_from_mapping({"starting_capital": "1000"})


def test_a_fee_given_as_a_percent_is_caught():
    """0.26% is 0.0026. Writing 0.26 would be a 26% fee."""
    with pytest.raises(SettingsError, match="fraction"):
        settings_from_mapping({"starting_capital": "1000", "fee_rate": "0.26"})


def test_an_unsafe_risk_setting_is_refused_at_load():
    with pytest.raises(ValueError, match="account-ending"):
        settings_from_mapping(
            {"starting_capital": "1000", "fee_rate": "0.0026", "risk_per_trade": "0.10"}
        )


def test_paper_is_the_default():
    assert base_settings().real_money is False


def test_missing_config_file_says_what_to_do(tmp_path):
    from arbcore.decide.settings import load_settings

    with pytest.raises(SettingsError, match="decide.example.yaml"):
        load_settings(tmp_path / "nope.yaml")


# --- derived account state ----------------------------------------------


@pytest.fixture
def journal(tmp_path):
    j = Journal(tmp_path / "j.sqlite")
    yield j
    j.close()


def a_commitment(ref: str) -> Commitment:
    return Commitment(
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


def close_at(journal: Journal, ref: str, price: str, *, when: datetime) -> None:
    journal.commit_plan(a_commitment(ref), now=when)
    journal.record_outcome(
        ref,
        exit_price=Decimal(price),
        exit_reason=ExitReason.STOP_HIT,
        now=when,
        fee_rate=Decimal("0"),
    )


def test_a_fresh_account_starts_at_its_starting_capital(journal):
    ledger = AccountLedger(journal, base_settings())
    state = ledger.state(today=TODAY)
    assert state.equity == Decimal("1000")
    assert state.peak_equity == Decimal("1000")
    assert state.drawdown == Decimal(0)


def test_equity_follows_realised_outcomes(journal):
    ledger = AccountLedger(journal, base_settings())
    close_at(journal, "A", "57000", when=NOW)  # loss of 9
    state = ledger.state(today=TODAY)
    assert state.equity == Decimal("991")


def test_peak_starts_at_the_starting_capital_so_a_bad_start_is_a_drawdown(journal):
    """Otherwise a losing account looks flat until its first profit."""
    ledger = AccountLedger(journal, base_settings())
    close_at(journal, "A", "57000", when=NOW)
    assert ledger.state(today=TODAY).drawdown > Decimal(0)


def test_pnl_today_ignores_earlier_days(journal):
    ledger = AccountLedger(journal, base_settings())
    close_at(journal, "OLD", "57000", when=NOW - timedelta(days=3))
    close_at(journal, "NEW", "57000", when=NOW)
    state = ledger.state(today=TODAY)
    assert state.pnl_today == Decimal("-9")
    assert state.equity == Decimal("982")


def test_open_positions_are_counted(journal):
    journal.commit_plan(a_commitment("OPEN"), now=NOW)
    assert AccountLedger(journal, base_settings()).state(today=TODAY).open_positions == 1


def test_the_ledger_drives_the_gate_so_limits_cannot_be_mistyped(journal):
    """A losing streak must close the gate without anyone typing a number."""
    settings = base_settings()
    ledger = AccountLedger(journal, settings)
    gate = SignalGate(GateConfig(risk=RiskProfile(), fee_rate=settings.fee_rate))
    signal = Signal(
        ref="S",
        symbol="BTCUSD",
        side=Side.BUY,
        entry=Decimal("60000"),
        stop=Decimal("57000"),
        source="donchian_55_20",
        emitted_at=NOW,
    )
    assert gate.evaluate(signal, ledger.state(today=TODAY), now=NOW).green

    for i in range(6):  # six stop-outs on one day
        close_at(journal, f"L{i}", "57000", when=NOW)
    verdict = gate.evaluate(signal, ledger.state(today=TODAY), now=NOW)
    assert not verdict.green
    assert "daily_loss_limit" in verdict.explain()


def test_summary_names_the_mode(journal):
    text = AccountLedger(journal, base_settings()).summary(today=TODAY)
    assert "Papier" in text


# --- alert parsing -------------------------------------------------------


def good_body(**overrides) -> bytes:
    import json

    payload = {
        "ref": "BTCUSD-1",
        "symbol": "BTCUSD",
        "side": "BUY",
        "entry": "60000",
        "stop": "57000",
        "source": "donchian_55_20",
    }
    payload.update(overrides)
    return json.dumps(payload).encode()


def test_a_well_formed_alert_parses():
    signal = parse_alert(good_body(), now=NOW)
    assert signal.side is Side.BUY
    assert signal.entry == Decimal("60000")


@pytest.mark.parametrize("field", ["ref", "symbol", "side", "entry", "stop", "source"])
def test_every_field_is_required(field):
    import json

    payload = json.loads(good_body())
    del payload[field]
    with pytest.raises(InvalidPayload, match="missing fields"):
        parse_alert(json.dumps(payload).encode(), now=NOW)


def test_non_json_is_refused():
    with pytest.raises(InvalidPayload, match="not JSON"):
        parse_alert(b"buy btc now", now=NOW)


def test_an_oversized_body_is_refused_before_parsing():
    with pytest.raises(InvalidPayload, match="too large"):
        parse_alert(b"{" + b"x" * 20_000, now=NOW)


@pytest.mark.parametrize("value", ["abc", "-1", "0", "NaN", "Infinity"])
def test_implausible_numbers_are_refused(value):
    """Nothing reaches position sizing without passing this."""
    with pytest.raises(InvalidPayload):
        parse_alert(good_body(entry=value), now=NOW)


def test_an_unknown_side_is_refused():
    with pytest.raises(InvalidPayload, match="BUY or SELL"):
        parse_alert(good_body(side="MAYBE"), now=NOW)


def test_overlong_strings_are_truncated_not_stored_whole():
    signal = parse_alert(good_body(symbol="X" * 500), now=NOW)
    assert len(signal.symbol) <= 32


def test_an_empty_ref_is_refused():
    with pytest.raises(InvalidPayload, match="ref"):
        parse_alert(good_body(ref="  "), now=NOW)


# --- queue ---------------------------------------------------------------


@pytest.fixture
def queue(tmp_path):
    q = SignalQueue(tmp_path / "q.sqlite")
    yield q
    q.close()


def a_verdict(ref: str = "S-1", equity: str = "1000"):
    from arbcore.decide.gate import AccountState

    gate = SignalGate(GateConfig(risk=RiskProfile()))
    signal = Signal(
        ref=ref,
        symbol="BTCUSD",
        side=Side.BUY,
        entry=Decimal("60000"),
        stop=Decimal("57000"),
        source="donchian_55_20",
        emitted_at=NOW,
    )
    account = AccountState(
        equity=Decimal(equity),
        peak_equity=Decimal(equity),
        pnl_today=Decimal("0"),
        open_positions=0,
    )
    return gate.evaluate(signal, account, now=NOW)


def test_a_verdict_is_queued_for_the_human(queue):
    assert queue.record(a_verdict(), now=NOW)
    pending = queue.pending()
    assert len(pending) == 1
    assert pending[0]["verdict"] == "GREEN"


def test_a_redelivered_alert_is_recognised_as_a_duplicate(queue):
    assert queue.record(a_verdict("S-1"), now=NOW)
    assert not queue.record(a_verdict("S-1"), now=NOW)
    assert len(queue.pending()) == 1


def test_rejected_signals_are_queued_too_with_their_reason(queue):
    queue.record(a_verdict("S-2", equity="50"), now=NOW)
    pending = queue.pending()
    assert pending[0]["verdict"] == "NO"
    assert "Mindestauftrag" in pending[0]["explanation"]


def test_green_only_filters(queue):
    queue.record(a_verdict("G"), now=NOW)
    queue.record(a_verdict("N", equity="50"), now=NOW)
    assert len(queue.pending(green_only=True)) == 1


def test_acting_on_a_signal_removes_it_from_pending(queue):
    queue.record(a_verdict("S-1"), now=NOW)
    queue.mark_acted("S-1")
    assert queue.pending() == ()


# --- server construction -------------------------------------------------


def test_a_weak_token_is_refused(queue):
    """The endpoint is reachable by anyone who finds the URL."""
    with pytest.raises(ValueError, match="24 random characters"):
        make_server(
            gate=SignalGate(GateConfig(risk=RiskProfile())),
            queue=queue,
            account_provider=lambda: None,
            token="secret",
        )


def test_the_receiver_never_creates_a_commitment(queue, journal):
    """The thesis is yours to write; the system may not file a plan for you."""
    queue.record(a_verdict(), now=NOW)
    assert journal.open_positions() == ()


# --- token in the body (TradingView cannot send headers) ------------------


def test_the_token_is_read_from_the_body():
    from arbcore.decide.webhook import extract_token

    assert extract_token(good_body(token="abc")) == "abc"


def test_a_missing_or_broken_body_yields_no_token_rather_than_raising():
    """Authentication must fail before anything reveals the body was malformed."""
    from arbcore.decide.webhook import extract_token

    assert extract_token(b"not json") == ""
    assert extract_token(b"[1,2,3]") == ""
    assert extract_token(good_body()) == ""


def test_a_non_string_token_is_ignored():
    from arbcore.decide.webhook import extract_token

    assert extract_token(good_body(token=12345)) == ""


def test_the_token_never_becomes_part_of_the_signal():
    """It is authentication, not signal data, and must not reach the queue."""
    signal = parse_alert(good_body(token="super-secret-value"), now=NOW)
    rendered = f"{signal.ref}{signal.symbol}{signal.source}"
    assert "super-secret-value" not in rendered
