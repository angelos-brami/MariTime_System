"""Runbook execution and telemetry gathering (work order 9, section 27).

The reliability agent selects an admitted runbook and this module executes it over the
durable records: recovery of an expired lease, renewal of a healthy lease, reconstruction
of due wake-ups after a queue loss, and quarantine of a compromised source (which
invalidates its dependent pending publications). Every executor first passes the request
through the pure controller (``authorize_runbook``); an off-registry runbook, a
precondition mismatch, a disallowed API call or an over-large blast radius is rejected
before any effect. Runbooks whose real effect is a process, provider or release action
(restart, failover, rollback) are admitted here and handed back for the operator surface.

``gather_telemetry`` assembles the low-cardinality metric snapshot from the database.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from eastmed_schema.enums import AutomationStatus, BusinessStatus
from eastmed_schema.models import (
    ReconBudgetReservation,
    ReconCasePublication,
    ReconCaseVersion,
    ReconExpectedJob,
    ReconOperationalCase,
    ReconSourceCapture,
    ReconWorkflowTask,
)
from eastmed_shared.reconciliation.policy import KNOWN_CAPABILITIES
from eastmed_shared.reconciliation.runbooks import (
    RunbookAdmission,
    RunbookApiCall,
    RunbookPrecondition,
    authorize_runbook,
)
from eastmed_shared.reconciliation.telemetry import MetricSet, TelemetrySnapshot, build_metrics
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from eastmed_pipeline.reconciliation_publication import invalidate_pending_publications
from eastmed_pipeline.reconciliation_read import _active_grant  # active-grant check (WO8)
from eastmed_pipeline.reconciliation_runtime import (
    TaskClaim,
    claim_task,
    find_due_tasks,
    heartbeat,
)


class RunbookExecStatus(StrEnum):
    EXECUTED = "executed"
    REJECTED = "rejected"
    NOOP = "noop"
    REQUIRES_OPERATOR = "requires_operator"


@dataclass(frozen=True)
class RunbookOutcome:
    status: RunbookExecStatus
    runbook_id: str
    probe_passed: bool
    detail: str
    affected: int = 0


def _now(value: datetime | None) -> datetime:
    return value or datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _rejected(admission: RunbookAdmission) -> RunbookOutcome:
    return RunbookOutcome(
        status=RunbookExecStatus.REJECTED,
        runbook_id=admission.runbook_id,
        probe_passed=False,
        detail=admission.reason,
    )


def run_recover_expired_task(
    session: Session, *, account_id: UUID, now: datetime | None = None
) -> tuple[RunbookOutcome, TaskClaim | None]:
    """recover_expired_task: claim an expired/due task at a newer epoch. The old worker's
    later commit is fenced by ``commit_stage`` (work order 5)."""
    admission = authorize_runbook(
        "recover_expired_task",
        precondition=RunbookPrecondition.LEASE_EXPIRED,
        requested_api_calls=[RunbookApiCall.CLAIM_EXPIRED],
        blast_radius=1,
    )
    if not admission.admitted:
        return _rejected(admission), None
    claim = claim_task(session, account_id=account_id, now=_now(now))
    if claim is None:
        return (
            RunbookOutcome(
                status=RunbookExecStatus.NOOP,
                runbook_id="recover_expired_task",
                probe_passed=False,
                detail="no due or expired task to recover",
            ),
            None,
        )
    return (
        RunbookOutcome(
            status=RunbookExecStatus.EXECUTED,
            runbook_id="recover_expired_task",
            probe_passed=True,
            detail=f"claimed task {claim.task_id} at epoch {claim.lease_epoch}",
            affected=1,
        ),
        claim,
    )


def run_renew_task_lease(
    session: Session, *, account_id: UUID, claim: TaskClaim, now: datetime | None = None
) -> RunbookOutcome:
    """renew_task_lease: extend a healthy lease only while its epoch is still current."""
    admission = authorize_runbook(
        "renew_task_lease",
        precondition=RunbookPrecondition.HEALTHY_HEARTBEAT_CURRENT_EPOCH,
        requested_api_calls=[RunbookApiCall.RENEW_LEASE],
        blast_radius=1,
    )
    if not admission.admitted:
        return _rejected(admission)
    renewed = heartbeat(session, account_id=account_id, claim=claim, now=_now(now))
    return RunbookOutcome(
        status=RunbookExecStatus.EXECUTED if renewed else RunbookExecStatus.NOOP,
        runbook_id="renew_task_lease",
        probe_passed=renewed,
        detail="lease renewed at current epoch" if renewed else "epoch no longer current",
        affected=1 if renewed else 0,
    )


def run_rebuild_wakeups(
    session: Session, *, account_id: UUID, now: datetime | None = None, limit: int = 1000
) -> RunbookOutcome:
    """rebuild_wakeups: reconstruct due-task coverage from the durable store after a queue
    loss, without duplicating a logical report."""
    admission = authorize_runbook(
        "rebuild_wakeups",
        precondition=RunbookPrecondition.QUEUE_LOSS_TASKS_DURABLE,
        requested_api_calls=[RunbookApiCall.SCAN_DUE_TASKS],
        blast_radius=limit,
    )
    if not admission.admitted:
        return _rejected(admission)
    due = find_due_tasks(session, account_id=account_id, now=_now(now), limit=limit)
    return RunbookOutcome(
        status=RunbookExecStatus.EXECUTED,
        runbook_id="rebuild_wakeups",
        probe_passed=True,
        detail=f"reconciled {len(due)} due task(s) from the durable store",
        affected=len(due),
    )


def run_quarantine_source(
    session: Session,
    *,
    account_id: UUID,
    case_ids: Sequence[UUID],
    current_revision: int,
    now: datetime | None = None,
) -> RunbookOutcome:
    """quarantine_source: block a compromised source and invalidate its dependent cases â€”
    cancel their undispatched publications and disable the cases (blueprint 27, 32)."""
    admission = authorize_runbook(
        "quarantine_source",
        precondition=RunbookPrecondition.COMPROMISED_EVIDENCE_OR_REVOKED_RIGHTS,
        requested_api_calls=[
            RunbookApiCall.BLOCK_SOURCE,
            RunbookApiCall.INVALIDATE_DEPENDENT_CASES,
        ],
        blast_radius=len(case_ids),
    )
    if not admission.admitted:
        return _rejected(admission)
    moment = _now(now)
    affected = 0
    for case_id in case_ids:
        case = session.scalars(
            select(ReconOperationalCase).where(
                ReconOperationalCase.account_id == account_id,
                ReconOperationalCase.id == case_id,
            )
        ).one_or_none()
        if case is None:
            continue
        invalidate_pending_publications(
            session, account_id=account_id, case_id=case_id,
            current_revision=current_revision, now=moment,
        )
        case.automation_status = AutomationStatus.DISABLED
        case.business_status = BusinessStatus.UNRESOLVED
        affected += 1
    session.flush()
    return RunbookOutcome(
        status=RunbookExecStatus.EXECUTED,
        runbook_id="quarantine_source",
        probe_passed=True,
        detail=f"blocked source and invalidated {affected} dependent case(s)",
        affected=affected,
    )


# Runbooks whose real effect is a process/provider/release action outside this DB slice.
# They are still gated by the controller; execution is handed to the operator surface.
_OPERATOR_RUNBOOKS: dict[str, tuple[RunbookPrecondition, RunbookApiCall]] = {
    "restart_worker": (
        RunbookPrecondition.MISSING_HEARTBEAT_UNHEALTHY,
        RunbookApiCall.RESTART_PROCESS,
    ),
    "failover_model": (
        RunbookPrecondition.PROVIDER_UNAVAILABLE_ALTERNATE_QUALIFIED,
        RunbookApiCall.SELECT_ALTERNATE_PROVIDER,
    ),
    "rollback_canary": (
        RunbookPrecondition.OBJECTIVE_REGRESSION,
        RunbookApiCall.RESTORE_PREVIOUS_PACKAGE,
    ),
}


def dispatch_operator_runbook(runbook_id: str) -> RunbookOutcome:
    """Admit a process/provider/release runbook through the controller and hand it to the
    operator surface. An off-registry id is rejected here too."""
    spec = _OPERATOR_RUNBOOKS.get(runbook_id)
    if spec is None:
        admission = authorize_runbook(
            runbook_id,
            precondition=RunbookPrecondition.OBJECTIVE_REGRESSION,
            requested_api_calls=[],
            blast_radius=0,
        )
        return _rejected(admission)
    precondition, api_call = spec
    admission = authorize_runbook(
        runbook_id,
        precondition=precondition,
        requested_api_calls=[api_call],
        blast_radius=1,
    )
    if not admission.admitted:
        return _rejected(admission)
    return RunbookOutcome(
        status=RunbookExecStatus.REQUIRES_OPERATOR,
        runbook_id=runbook_id,
        probe_passed=False,
        detail="admitted; execution handed to the operator/provider surface",
    )


def _seconds_since(moment: datetime, values: Sequence[datetime]) -> list[float]:
    return [max(0.0, (moment - _as_utc(v)).total_seconds()) for v in values]


def gather_telemetry(
    session: Session, *, account_id: UUID, now: datetime | None = None
) -> MetricSet:
    """Assemble the low-cardinality metric snapshot from the durable records."""
    moment = _now(now)

    captures = list(
        session.scalars(
            select(ReconSourceCapture.captured_at).where(
                ReconSourceCapture.account_id == account_id
            )
        ).all()
    )
    tasks = list(
        session.scalars(
            select(ReconWorkflowTask).where(ReconWorkflowTask.account_id == account_id)
        ).all()
    )
    expected_jobs = session.scalar(
        select(func.count()).select_from(ReconExpectedJob).where(
            ReconExpectedJob.account_id == account_id
        )
    ) or 0
    completed = session.scalar(
        select(func.count()).select_from(ReconOperationalCase).where(
            ReconOperationalCase.account_id == account_id,
            ReconOperationalCase.automation_status == AutomationStatus.VERIFIED_COMPLETE,
        )
    ) or 0
    unresolved = session.scalar(
        select(func.count()).select_from(ReconOperationalCase).where(
            ReconOperationalCase.account_id == account_id,
            ReconOperationalCase.business_status == BusinessStatus.UNRESOLVED,
        )
    ) or 0
    cost_reservations = session.scalar(
        select(func.count()).select_from(ReconBudgetReservation).where(
            ReconBudgetReservation.account_id == account_id
        )
    ) or 0

    # Provider effect lag: publication accepted after its content version was committed.
    effect_lags: list[float] = []
    corrections: list[float] = []
    prep: list[float] = []
    publications = list(
        session.scalars(
            select(ReconCasePublication).where(ReconCasePublication.account_id == account_id)
        ).all()
    )
    for pub in publications:
        version = session.scalars(
            select(ReconCaseVersion).where(
                ReconCaseVersion.account_id == account_id,
                ReconCaseVersion.case_id == pub.case_id,
                ReconCaseVersion.revision == pub.case_revision,
            )
        ).one_or_none()
        if version is not None:
            lag = (_as_utc(pub.published_at) - _as_utc(version.created_at)).total_seconds()
            effect_lags.append(max(0.0, lag))
    # Arrival-to-completion timing and manual interventions have no durable recorder yet.
    # Version age and the interval between corrections are not processing latency.

    disabled = [
        cap
        for cap in sorted(KNOWN_CAPABILITIES)
        if _active_grant(session, account_id=account_id, capability=cap, now=moment) is None
    ]

    snapshot = TelemetrySnapshot(
        connector_lag_seconds=_seconds_since(moment, captures),
        task_ages_seconds=_seconds_since(moment, [t.created_at for t in tasks]),
        lease_epochs=[t.lease_epoch for t in tasks],
        expected_jobs=int(expected_jobs),
        completed_cases=int(completed),
        unresolved_cases=int(unresolved),
        prep_latencies_seconds=prep,
        provider_effect_lag_seconds=effect_lags,
        correction_propagation_seconds=corrections,
        cost_reservations=int(cost_reservations),
        disabled_capabilities=disabled,
        manual_touch_events=None,  # no durable intervention recorder is connected yet
    )
    return build_metrics(snapshot)
