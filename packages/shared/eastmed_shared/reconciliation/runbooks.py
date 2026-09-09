"""Reliability runbook registry and controller (work order 9, section 27).

The reliability agent is a *selector of tested operations*, not a general administrator.
Every operation it may take is a registered runbook that declares its trigger evidence,
precondition, maximum blast radius, the exact API calls it is allowed to make, a success
probe, a rollback and a terminal-failure path. ``authorize_runbook`` is the controller
gate: it rejects anything not in the registry, any precondition mismatch, any API call
outside the runbook's allowlist, and any request whose blast radius exceeds the declared
maximum. There is no path to an arbitrary administrative action.

This module is pure policy; the durable execution of the admitted runbooks lives in the
pipeline (``eastmed_pipeline.reconciliation_reliability``).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum


class RunbookPrecondition(StrEnum):
    HEALTHY_HEARTBEAT_CURRENT_EPOCH = "healthy_heartbeat_current_epoch"
    LEASE_EXPIRED = "lease_expired"
    MISSING_HEARTBEAT_UNHEALTHY = "missing_heartbeat_unhealthy"
    PROVIDER_UNAVAILABLE_ALTERNATE_QUALIFIED = "provider_unavailable_alternate_qualified"
    QUEUE_LOSS_TASKS_DURABLE = "queue_loss_tasks_durable"
    OBJECTIVE_REGRESSION = "objective_regression"
    COMPROMISED_EVIDENCE_OR_REVOKED_RIGHTS = "compromised_evidence_or_revoked_rights"


class RunbookApiCall(StrEnum):
    RENEW_LEASE = "renew_lease"
    CLAIM_EXPIRED = "claim_expired"
    RESTART_PROCESS = "restart_process"
    SELECT_ALTERNATE_PROVIDER = "select_alternate_provider"
    SCAN_DUE_TASKS = "scan_due_tasks"
    RESTORE_PREVIOUS_PACKAGE = "restore_previous_package"
    BLOCK_SOURCE = "block_source"
    INVALIDATE_DEPENDENT_CASES = "invalidate_dependent_cases"


class RunbookRejection(StrEnum):
    UNKNOWN_RUNBOOK = "unknown_runbook"
    PRECONDITION_NOT_MET = "precondition_not_met"
    API_CALL_NOT_ALLOWED = "api_call_not_allowed"
    BLAST_RADIUS_EXCEEDED = "blast_radius_exceeded"


@dataclass(frozen=True)
class RunbookSpec:
    runbook_id: str
    trigger: str
    precondition: RunbookPrecondition
    max_blast_radius: int
    allowed_api_calls: frozenset[RunbookApiCall]
    success_probe: str
    rollback_runbook_id: str | None
    terminal_failure: str


# The complete set of operations the reliability agent may select (blueprint 27). The
# controller rejects anything not on this registry.
RUNBOOK_REGISTRY: dict[str, RunbookSpec] = {
    "renew_task_lease": RunbookSpec(
        runbook_id="renew_task_lease",
        trigger="healthy worker heartbeat with the current lease epoch",
        precondition=RunbookPrecondition.HEALTHY_HEARTBEAT_CURRENT_EPOCH,
        max_blast_radius=1,
        allowed_api_calls=frozenset({RunbookApiCall.RENEW_LEASE}),
        success_probe="lease renewed and no competing owner holds it",
        rollback_runbook_id=None,
        terminal_failure="epoch no longer current: yield to the newer lease holder",
    ),
    "recover_expired_task": RunbookSpec(
        runbook_id="recover_expired_task",
        trigger="lease expired; prior stage/effect classified",
        precondition=RunbookPrecondition.LEASE_EXPIRED,
        max_blast_radius=1,
        allowed_api_calls=frozenset({RunbookApiCall.CLAIM_EXPIRED}),
        success_probe="a newer epoch is claimed and the stale commit is denied",
        rollback_runbook_id=None,
        terminal_failure="attempt budget exhausted: mark the task dead",
    ),
    "restart_worker": RunbookSpec(
        runbook_id="restart_worker",
        trigger="missing heartbeat plus a confirmed unhealthy process",
        precondition=RunbookPrecondition.MISSING_HEARTBEAT_UNHEALTHY,
        max_blast_radius=1,
        allowed_api_calls=frozenset({RunbookApiCall.RESTART_PROCESS}),
        success_probe="worker healthy and pending tasks advance",
        rollback_runbook_id=None,
        terminal_failure="process will not become healthy: disable and page out",
    ),
    "failover_model": RunbookSpec(
        runbook_id="failover_model",
        trigger="provider unavailable and a qualified alternate exists",
        precondition=RunbookPrecondition.PROVIDER_UNAVAILABLE_ALTERNATE_QUALIFIED,
        max_blast_radius=1,
        allowed_api_calls=frozenset({RunbookApiCall.SELECT_ALTERNATE_PROVIDER}),
        success_probe="alternate shares schema/policy/rights and its probe passes",
        rollback_runbook_id=None,
        terminal_failure="no qualified alternate: disable the capability",
    ),
    "rebuild_wakeups": RunbookSpec(
        runbook_id="rebuild_wakeups",
        trigger="queue loss with the durable tasks intact",
        precondition=RunbookPrecondition.QUEUE_LOSS_TASKS_DURABLE,
        max_blast_radius=100_000,
        allowed_api_calls=frozenset({RunbookApiCall.SCAN_DUE_TASKS}),
        success_probe="due-task coverage reconciled against the database",
        rollback_runbook_id=None,
        terminal_failure="durable store unreadable: disable scheduling",
    ),
    "rollback_canary": RunbookSpec(
        runbook_id="rollback_canary",
        trigger="objective regression against the release thresholds",
        precondition=RunbookPrecondition.OBJECTIVE_REGRESSION,
        max_blast_radius=100_000,
        allowed_api_calls=frozenset({RunbookApiCall.RESTORE_PREVIOUS_PACKAGE}),
        success_probe="qualified previous package restored and effects reconciled",
        rollback_runbook_id=None,
        terminal_failure="previous package unavailable: disable the workflow",
    ),
    "quarantine_source": RunbookSpec(
        runbook_id="quarantine_source",
        trigger="compromised evidence or revoked source rights",
        precondition=RunbookPrecondition.COMPROMISED_EVIDENCE_OR_REVOKED_RIGHTS,
        max_blast_radius=100_000,
        allowed_api_calls=frozenset(
            {RunbookApiCall.BLOCK_SOURCE, RunbookApiCall.INVALIDATE_DEPENDENT_CASES}
        ),
        success_probe="source blocked and dependent cases invalidated",
        rollback_runbook_id=None,
        terminal_failure="dependent set cannot be bounded: disable the capability",
    ),
}


@dataclass(frozen=True)
class RunbookAdmission:
    admitted: bool
    runbook_id: str
    reason: str
    rejection: RunbookRejection | None = None
    spec: RunbookSpec | None = None


def authorize_runbook(
    runbook_id: str,
    *,
    precondition: RunbookPrecondition,
    requested_api_calls: Iterable[RunbookApiCall],
    blast_radius: int,
) -> RunbookAdmission:
    """Admit a runbook only if it is registered, its precondition holds, every requested
    API call is on its allowlist, and its blast radius is within the declared maximum."""
    spec = RUNBOOK_REGISTRY.get(runbook_id)
    if spec is None:
        return RunbookAdmission(
            admitted=False,
            runbook_id=runbook_id,
            reason="runbook is not on the registry",
            rejection=RunbookRejection.UNKNOWN_RUNBOOK,
        )
    if precondition is not spec.precondition:
        return RunbookAdmission(
            admitted=False,
            runbook_id=runbook_id,
            reason=f"precondition {precondition.value} does not match {spec.precondition.value}",
            rejection=RunbookRejection.PRECONDITION_NOT_MET,
            spec=spec,
        )
    requested = frozenset(requested_api_calls)
    if not requested <= spec.allowed_api_calls:
        extra = sorted(call.value for call in requested - spec.allowed_api_calls)
        return RunbookAdmission(
            admitted=False,
            runbook_id=runbook_id,
            reason=f"api calls not allowed: {', '.join(extra)}",
            rejection=RunbookRejection.API_CALL_NOT_ALLOWED,
            spec=spec,
        )
    if blast_radius < 0 or blast_radius > spec.max_blast_radius:
        return RunbookAdmission(
            admitted=False,
            runbook_id=runbook_id,
            reason=f"blast radius {blast_radius} exceeds maximum {spec.max_blast_radius}",
            rejection=RunbookRejection.BLAST_RADIUS_EXCEEDED,
            spec=spec,
        )
    return RunbookAdmission(
        admitted=True, runbook_id=runbook_id, reason="admitted", spec=spec
    )


__all__ = [
    "RUNBOOK_REGISTRY",
    "RunbookAdmission",
    "RunbookApiCall",
    "RunbookPrecondition",
    "RunbookRejection",
    "RunbookSpec",
    "authorize_runbook",
]
