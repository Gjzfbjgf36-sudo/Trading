import pytest

from arbcore.config.environment import (
    Environment,
    LiveGateError,
    ReadinessChecklist,
    RuntimeProfile,
    TradingMode,
    profile_from_env,
)

FULLY_READY = ReadinessChecklist(*[True] * len(ReadinessChecklist.criteria()))


def test_defaults_are_the_safe_ones():
    p = profile_from_env({})
    assert p.environment is Environment.DEVELOPMENT
    assert p.trading_mode is TradingMode.RESEARCH
    assert not p.uses_real_money
    assert not p.may_place_orders


def test_live_mode_is_impossible_outside_production():
    for env in (Environment.DEVELOPMENT, Environment.TESTING, Environment.PAPER):
        with pytest.raises(LiveGateError):
            RuntimeProfile(environment=env, trading_mode=TradingMode.LIVE)


def test_canary_environment_cannot_run_full_live():
    with pytest.raises(LiveGateError):
        RuntimeProfile(environment=Environment.CANARY, trading_mode=TradingMode.LIVE)


def test_live_requires_flag_approval_and_full_checklist():
    with pytest.raises(LiveGateError, match="live_trading_enabled"):
        RuntimeProfile(Environment.PRODUCTION, TradingMode.LIVE)
    with pytest.raises(LiveGateError, match="manual approval"):
        RuntimeProfile(Environment.PRODUCTION, TradingMode.LIVE, live_trading_enabled=True)
    with pytest.raises(LiveGateError, match="readiness"):
        RuntimeProfile(
            Environment.PRODUCTION,
            TradingMode.LIVE,
            live_trading_enabled=True,
            manual_approval_reference="TICKET-1",
        )


def test_one_unmet_criterion_still_blocks():
    almost = ReadinessChecklist(*([True] * 7 + [False]))
    with pytest.raises(LiveGateError):
        RuntimeProfile(
            Environment.PRODUCTION,
            TradingMode.LIVE,
            live_trading_enabled=True,
            manual_approval_reference="TICKET-1",
            readiness=almost,
        )


def test_fully_authorised_live_profile_is_constructible():
    p = RuntimeProfile(
        Environment.PRODUCTION,
        TradingMode.LIVE,
        live_trading_enabled=True,
        manual_approval_reference="TICKET-1",
        readiness=FULLY_READY,
    )
    assert p.uses_real_money


@pytest.mark.parametrize("value", ["", "TRUE ", "1", "yes", "on"])
def test_flag_parsing_only_accepts_explicit_truth(value):
    env = {
        "ARBCORE_ENV": "paper",
        "ARBCORE_TRADING_MODE": "paper",
        "ARBCORE_LIVE_TRADING_ENABLED": value,
    }
    expected = value.strip().lower() in {"1", "true", "yes", "on"}
    assert profile_from_env(env).live_trading_enabled is expected


@pytest.mark.parametrize("value", ["maybe", "0", "false", "no", "y"])
def test_ambiguous_flags_read_as_false(value):
    env = {"ARBCORE_LIVE_TRADING_ENABLED": value}
    assert profile_from_env(env).live_trading_enabled is False
