from __future__ import annotations

import pytest
from eastmed_shared.reconciliation.runbooks import (
    RUNBOOK_REGISTRY,
    RunbookApiCall,
    RunbookPrecondition,
    RunbookRejection,
    authorize_runbook,
)


def test_registry_lists_every_section_27_runbook() -> None:
    assert set(RUNBOOK_REGISTRY) == {
        "renew_task_lease",
        "recover_expired_task",
        "restart_worker",
        "failover_model",
        "rebuild_wakeups",
        "rollback_canary",
        "quarantine_source",
    }


def test_controller_admits_a_valid_runbook() -> None:
    admission = authorize_runbook(
        "recover_expired_task",
        precondition=RunbookPrecondition.LEASE_EXPIRED,
        requested_api_calls=[RunbookApiCall.CLAIM_EXPIRED],
        blast_radius=1,
    )
    assert admission.admitted is True
    assert admission.spec is not None and admission.spec.runbook_id == "recover_expired_task"


def test_controller_rejects_off_registry_runbook() -> None:
    admission = authorize_runbook(
        "delete_everything",
        precondition=RunbookPrecondition.LEASE_EXPIRED,
        requested_api_calls=[],
        blast_radius=0,
    )
    assert admission.admitted is False
    assert admission.rejection is RunbookRejection.UNKNOWN_RUNBOOK


def test_controller_rejects_precondition_mismatch() -> None:
    admission = authorize_runbook(
        "recover_expired_task",
        precondition=RunbookPrecondition.HEALTHY_HEARTBEAT_CURRENT_EPOCH,
        requested_api_calls=[RunbookApiCall.CLAIM_EXPIRED],
        blast_radius=1,
    )
    assert admission.rejection is RunbookRejection.PRECONDITION_NOT_MET


def test_controller_rejects_disallowed_api_call() -> None:
    admission = authorize_runbook(
        "renew_task_lease",
        precondition=RunbookPrecondition.HEALTHY_HEARTBEAT_CURRENT_EPOCH,
        requested_api_calls=[RunbookApiCall.RESTART_PROCESS],
        blast_radius=1,
    )
    assert admission.rejection is RunbookRejection.API_CALL_NOT_ALLOWED


def test_controller_rejects_blast_radius_over_max() -> None:
    admission = authorize_runbook(
        "renew_task_lease",
        precondition=RunbookPrecondition.HEALTHY_HEARTBEAT_CURRENT_EPOCH,
        requested_api_calls=[RunbookApiCall.RENEW_LEASE],
        blast_radius=5,
    )
    assert admission.rejection is RunbookRejection.BLAST_RADIUS_EXCEEDED


@pytest.mark.parametrize("runbook_id", sorted(RUNBOOK_REGISTRY))
def test_every_spec_is_self_consistent(runbook_id: str) -> None:
    spec = RUNBOOK_REGISTRY[runbook_id]
    assert spec.allowed_api_calls  # at least one allowed call
    assert spec.max_blast_radius >= 1
    assert spec.success_probe and spec.terminal_failure
