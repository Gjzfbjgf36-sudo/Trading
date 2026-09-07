import pytest

from arbcore.domain.decision import CheckResult, RejectReason, Verdict, decide


def test_empty_check_list_rejects():
    """Evaluating nothing is not the same as everything passing."""
    decision = decide("X", [])
    assert decision.verdict is Verdict.REJECT
    assert RejectReason.CHECK_UNEVALUABLE in decision.reasons


def test_any_failure_rejects_and_all_reasons_are_kept():
    decision = decide(
        "X",
        [
            CheckResult.ok("a"),
            CheckResult.fail("b", RejectReason.DATA_STALE),
            CheckResult.fail("c", RejectReason.SLIPPAGE_EXCEEDED),
        ],
    )
    assert decision.verdict is Verdict.REJECT
    assert set(decision.reasons) == {RejectReason.DATA_STALE, RejectReason.SLIPPAGE_EXCEEDED}


def test_failed_check_must_carry_a_reason():
    with pytest.raises(ValueError):
        CheckResult(name="b", passed=False)


def test_passed_check_must_not_carry_a_reason():
    with pytest.raises(ValueError):
        CheckResult(name="b", passed=True, reason=RejectReason.DATA_STALE)


def test_accept_record_keeps_every_check_for_audit():
    decision = decide("X", [CheckResult.ok("a"), CheckResult.ok("b")])
    assert decision.accepted
    assert len(decision.as_dict()["checks"]) == 2


def test_audit_dict_is_json_serialisable():
    import json

    decision = decide("X", [CheckResult.fail("b", RejectReason.DATA_STALE)])
    json.dumps(decision.as_dict())
