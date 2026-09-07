import pytest

from arbcore.domain.state import (
    IllegalTransition,
    OpportunityLifecycle,
)
from arbcore.domain.state import (
    OpportunityState as S,
)


def test_happy_path_visits_every_required_state():
    lc = OpportunityLifecycle("OPP-1")
    path = [
        S.VALIDATING,
        S.VALIDATED,
        S.RISK_CHECK,
        S.EXECUTION_READY,
        S.EXECUTING,
        S.FILLED,
        S.SETTLEMENT,
        S.RECONCILIATION,
        S.CLOSED,
    ]
    for target in path:
        lc.transition(target, reason="test")
    assert lc.state is S.CLOSED
    assert lc.is_terminal
    assert [r.to_state for r in lc.history] == path


def test_risk_check_cannot_be_skipped():
    lc = OpportunityLifecycle("OPP-2")
    lc.transition(S.VALIDATING, reason="t")
    lc.transition(S.VALIDATED, reason="t")
    with pytest.raises(IllegalTransition):
        lc.transition(S.EXECUTION_READY, reason="skipping risk check")


def test_detection_cannot_jump_straight_to_execution():
    lc = OpportunityLifecycle("OPP-3")
    with pytest.raises(IllegalTransition):
        lc.transition(S.EXECUTING, reason="fast path")


def test_every_transition_requires_a_reason():
    lc = OpportunityLifecycle("OPP-4")
    with pytest.raises(ValueError):
        lc.transition(S.VALIDATING, reason="   ")


def test_unknown_state_only_resolves_through_reconciliation():
    lc = OpportunityLifecycle("OPP-5")
    for target in (S.VALIDATING, S.VALIDATED, S.RISK_CHECK, S.EXECUTION_READY, S.EXECUTING):
        lc.transition(target, reason="t")
    lc.transition(S.UNKNOWN, reason="order state could not be established")
    assert lc.capital_at_risk
    with pytest.raises(IllegalTransition):
        lc.transition(S.CLOSED, reason="assume it worked")
    lc.transition(S.RECONCILIATION, reason="operator reconciled venue state")
    lc.transition(S.CLOSED, reason="reconciled flat")


def test_terminal_states_are_final():
    lc = OpportunityLifecycle("OPP-6")
    lc.transition(S.REJECTED, reason="data stale")
    with pytest.raises(IllegalTransition):
        lc.transition(S.VALIDATING, reason="retry")


def test_execution_ready_can_still_be_rejected_on_decay():
    lc = OpportunityLifecycle("OPP-7")
    for target in (S.VALIDATING, S.VALIDATED, S.RISK_CHECK, S.EXECUTION_READY):
        lc.transition(target, reason="t")
    lc.transition(S.REJECTED, reason="quote decayed before dispatch")
    assert lc.is_terminal
