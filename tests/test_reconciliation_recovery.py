from __future__ import annotations

import pytest
from eastmed_shared.reconciliation.recovery import (
    RUNBOOK_ALLOWLIST,
    RecoveryAction,
    RecoveryDecision,
    TaskFailureKind,
    select_recovery,
)


def test_transient_retries_within_attempt_limit() -> None:
    decision = select_recovery(TaskFailureKind.TRANSIENT_PROVIDER, attempts=1, max_attempts=3)
    assert decision.action is RecoveryAction.RETRY
    assert decision.runbook_id == "retry_same_stage"


def test_transient_fails_over_when_attempts_exhausted() -> None:
    decision = select_recovery(
        TaskFailureKind.MALFORMED_OUTPUT, attempts=3, max_attempts=3, alternate_available=True
    )
    assert decision.action is RecoveryAction.FAILOVER_ALTERNATE


def test_transient_stops_when_no_alternate() -> None:
    decision = select_recovery(
        TaskFailureKind.TRANSIENT_PROVIDER, attempts=3, max_attempts=3, alternate_available=False
    )
    assert decision.action is RecoveryAction.STOP_UNRESOLVED
    assert decision.runbook_id is None


def test_refusal_never_retries_same_payload() -> None:
    decision = select_recovery(TaskFailureKind.REFUSAL, attempts=0, max_attempts=3)
    # No alternate available -> stop, never RETRY.
    assert decision.action is RecoveryAction.STOP_UNRESOLVED


def test_refusal_fails_over_when_alternate_exists() -> None:
    decision = select_recovery(
        TaskFailureKind.REFUSAL, attempts=0, max_attempts=3, alternate_available=True
    )
    assert decision.action is RecoveryAction.FAILOVER_ALTERNATE


def test_provider_outage_fails_over() -> None:
    decision = select_recovery(
        TaskFailureKind.PROVIDER_OUTAGE, attempts=1, max_attempts=3, alternate_available=True
    )
    assert decision.action is RecoveryAction.FAILOVER_ALTERNATE


def test_missing_data_waits_then_stops() -> None:
    waiting = select_recovery(
        TaskFailureKind.MISSING_MACHINE_DATA, attempts=0, max_attempts=3, within_data_deadline=True
    )
    assert waiting.action is RecoveryAction.WAIT_FOR_MACHINE_DATA
    stopped = select_recovery(
        TaskFailureKind.MISSING_MACHINE_DATA, attempts=0, max_attempts=3, within_data_deadline=False
    )
    assert stopped.action is RecoveryAction.STOP_UNRESOLVED


def test_integrity_failure_disables_capability() -> None:
    decision = select_recovery(TaskFailureKind.INTEGRITY_FAILURE, attempts=0, max_attempts=3)
    assert decision.action is RecoveryAction.DISABLE_CAPABILITY
    assert decision.runbook_id == "disable_capability"


def test_budget_exhausted_stops() -> None:
    decision = select_recovery(TaskFailureKind.BUDGET_EXHAUSTED, attempts=0, max_attempts=3)
    assert decision.action is RecoveryAction.STOP_UNRESOLVED


def test_every_decision_names_an_allowlisted_runbook_or_none() -> None:
    for failure in TaskFailureKind:
        decision = select_recovery(
            failure, attempts=0, max_attempts=3, alternate_available=True
        )
        assert decision.runbook_id is None or decision.runbook_id in RUNBOOK_ALLOWLIST


def test_off_allowlist_runbook_is_rejected() -> None:
    with pytest.raises(ValueError, match="not on the allowlist"):
        RecoveryDecision(action=RecoveryAction.RETRY, runbook_id="rm_rf_everything", reason="no")
