"""Connected reconciliation runtime: scheduled input to private report (blueprint 21, 05).

This is the missing execution path between the durable primitives. A scheduled pass pulls
every arrived-but-unscheduled case into a durable workflow, then drives due tasks through
the prescribed stages until each reaches a customer-visible outcome — with no button press.

Authoritative state lives entirely in ``recon_workflow_tasks``/``recon_workflow_runs`` and
the case tables, so a pass is fully reconstructable after a process restart: a fresh pass
claims whatever the durable store says is due and resumes from exactly there.

Two ordered stages run under the work-order-5 lease/fencing primitives:

* ``RECONCILE`` — deterministically compute the case result, commit its immutable version
  (idempotently, so a crash-and-retry never appends a duplicate), and persist the
  independent verification verdict. A completing verdict advances to ``PUBLISH``; anything
  unresolved or unverified stops here honestly, with no publication.
* ``PUBLISH`` — propose the private publication (gated on that persisted verdict), publish
  it atomically, and confirm it by independent read-back, reaching ``verified_complete``.

Every stage is idempotent: re-running after a crash reuses the existing version, the
insert-once verdict, and the content-bound publication action key, so recovery produces at
most one logical effect. Each ``execute_next`` is one durable boundary; the caller owns the
surrounding transaction (commit on success, roll back a fenced stale worker).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from eastmed_schema.enums import AutomationStatus, BusinessStatus, TaskStatus, WorkflowStage
from eastmed_schema.models import (
    ReconActionIntent,
    ReconCaseVersion,
    ReconExpectedJob,
    ReconOperationalCase,
    ReconWorkflowRun,
    ReconWorkflowTask,
)
from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from eastmed_pipeline.reconciliation_case import (
    CaseResult,
    commit_case_version,
    compute_case_result,
    record_verification_verdict,
)
from eastmed_pipeline.reconciliation_publication import (
    propose_publication,
    publish_case,
    reconcile_publication,
)
from eastmed_pipeline.reconciliation_runtime import (
    CommitStatus,
    TaskClaim,
    claim_task,
    commit_stage,
    create_run,
    enqueue_task,
    fail_attempt,
)

WORKFLOW_VERSION = "recon_wf_v1"
DEFAULT_BUDGET_UNITS = 1000
# The ordered stages this connected path drives. The deterministic reconcile+calculate+
# verify collapse into one stage (they derive from the same immutable inputs); publication
# is its own stage so a restart resumes cleanly between analysis and the external-facing act.
STAGE_ORDER: tuple[WorkflowStage, ...] = (WorkflowStage.RECONCILE, WorkflowStage.PUBLISH)


class DispatchResult(StrEnum):
    IDLE = "idle"  # no due task
    ADVANCED = "advanced"  # a stage ran and enqueued the next
    COMPLETED = "completed"  # case reached verified_complete
    UNRESOLVED = "unresolved"  # terminal: nothing to publish, honestly withheld
    DEFERRED = "deferred"  # publication not yet authorized; retried within the attempt budget
    FENCED = "fenced"  # a stale worker lost its lease; the caller should roll back


@dataclass(frozen=True)
class DispatchStep:
    result: DispatchResult
    case_id: UUID | None = None
    stage: WorkflowStage | None = None
    detail: str | None = None


@dataclass(frozen=True)
class PassSummary:
    scheduled: int  # newly enqueued case workflows
    steps: tuple[DispatchStep, ...]

    @property
    def completed(self) -> int:
        return sum(1 for s in self.steps if s.result is DispatchResult.COMPLETED)

    @property
    def unresolved(self) -> int:
        return sum(1 for s in self.steps if s.result is DispatchResult.UNRESOLVED)


def _has_run(session: Session, *, account_id: UUID, case_id: UUID) -> bool:
    return bool(
        session.scalar(
            select(
                exists().where(
                    ReconWorkflowRun.account_id == account_id,
                    ReconWorkflowRun.case_id == case_id,
                )
            )
        )
    )


def start_case_workflow(
    session: Session,
    *,
    account_id: UUID,
    case: ReconOperationalCase,
    system_fingerprint: str,
    budget_units: int = DEFAULT_BUDGET_UNITS,
    workflow_version: str = WORKFLOW_VERSION,
    now: datetime | None = None,
) -> ReconWorkflowRun | None:
    """Create the durable run and enqueue the first stage for one case.

    Idempotent per case: if a run already exists (a prior pass scheduled it), returns None
    rather than opening a second workflow."""
    if _has_run(session, account_id=account_id, case_id=case.id):
        return None
    run = create_run(
        session,
        account_id=account_id,
        case=case,
        workflow_version=workflow_version,
        system_fingerprint=system_fingerprint,
        budget_units=budget_units,
        deadline_at=case.deadline_at,
    )
    enqueue_task(
        session, account_id=account_id, run=run, stage=WorkflowStage.RECONCILE, now=now
    )
    return run


def enqueue_ready_cases(
    session: Session,
    *,
    account_id: UUID,
    system_fingerprint: str,
    budget_units: int = DEFAULT_BUDGET_UNITS,
    workflow_version: str = WORKFLOW_VERSION,
    now: datetime | None = None,
) -> int:
    """Pull every arrived-but-unscheduled case into a durable workflow.

    A case reaches ``VALIDATED`` when its report is imported and reconciled to an expected
    job. This is the connector between scheduled import and the stage dispatcher: it needs
    no manual trigger and is safe to run every pass (already-scheduled cases are skipped)."""
    ready = session.scalars(
        select(ReconOperationalCase).where(
            ReconOperationalCase.account_id == account_id,
            ReconOperationalCase.automation_status == AutomationStatus.VALIDATED,
        )
    ).all()
    scheduled = 0
    for case in ready:
        run = start_case_workflow(
            session,
            account_id=account_id,
            case=case,
            system_fingerprint=system_fingerprint,
            budget_units=budget_units,
            workflow_version=workflow_version,
            now=now,
        )
        if run is not None:
            case.automation_status = AutomationStatus.RECEIVED
            scheduled += 1
    session.flush()
    return scheduled


def _expected_job(
    session: Session, *, account_id: UUID, case: ReconOperationalCase
) -> ReconExpectedJob | None:
    return session.scalars(
        select(ReconExpectedJob).where(
            ReconExpectedJob.account_id == account_id,
            ReconExpectedJob.id == case.expected_job_id,
        )
    ).one_or_none()


def _version_for(
    session: Session, *, account_id: UUID, case: ReconOperationalCase, result: CaseResult
) -> ReconCaseVersion:
    """Return the committed version for this exact result, committing one only if absent.

    Idempotency for the RECONCILE stage: a crash after commit but before the stage boundary
    must not append a second version. The content hash is deterministic, so a replay finds
    and reuses the version it already wrote."""
    existing = session.scalars(
        select(ReconCaseVersion).where(
            ReconCaseVersion.account_id == account_id,
            ReconCaseVersion.case_id == case.id,
            ReconCaseVersion.content_hash == result.content_hash,
        )
    ).first()
    if existing is not None:
        return existing
    return commit_case_version(session, account_id=account_id, case=case, result=result)


def _latest_version(
    session: Session, *, account_id: UUID, case_id: UUID
) -> ReconCaseVersion | None:
    return session.scalars(
        select(ReconCaseVersion)
        .where(
            ReconCaseVersion.account_id == account_id,
            ReconCaseVersion.case_id == case_id,
        )
        .order_by(ReconCaseVersion.revision.desc())
    ).first()


def _run_reconcile_stage(
    session: Session,
    *,
    account_id: UUID,
    claim: TaskClaim,
    case: ReconOperationalCase,
    expected_job: ReconExpectedJob,
    now: datetime,
) -> DispatchStep:
    result = compute_case_result(session, account_id=account_id, expected_job=expected_job)
    version = _version_for(session, account_id=account_id, case=case, result=result)
    verdict = record_verification_verdict(
        session,
        account_id=account_id,
        expected_job=expected_job,
        case=case,
        case_version=version,
        result=result,
    )
    case.automation_status = AutomationStatus.CALCULATED
    if not verdict.completes:
        # Honest terminal: the result is unresolved or unverified, so nothing is published.
        case.business_status = BusinessStatus.UNRESOLVED
        case.automation_status = AutomationStatus.WAITING_FOR_MACHINE_DATA
        commit_stage(session, account_id=account_id, claim=claim, now=now)
        return DispatchStep(
            result=DispatchResult.UNRESOLVED, case_id=case.id, stage=WorkflowStage.RECONCILE
        )
    case.business_status = BusinessStatus.VARIANCE_PRESENT
    outcome = commit_stage(
        session, account_id=account_id, claim=claim, now=now,
        next_stage=WorkflowStage.PUBLISH,
    )
    if outcome.status is CommitStatus.FENCED:
        return DispatchStep(
            result=DispatchResult.FENCED, case_id=case.id, stage=WorkflowStage.RECONCILE
        )
    return DispatchStep(
        result=DispatchResult.ADVANCED, case_id=case.id, stage=WorkflowStage.RECONCILE
    )


def _run_publish_stage(
    session: Session,
    *,
    account_id: UUID,
    claim: TaskClaim,
    case: ReconOperationalCase,
    case_version: ReconCaseVersion,
    principal_subject: str,
    system_fingerprint: str,
    now: datetime,
) -> DispatchStep:
    proposed = propose_publication(
        session,
        account_id=account_id,
        case=case,
        case_version=case_version,
        principal_subject=principal_subject,
        system_fingerprint=system_fingerprint,
        now=now,
    )
    if not proposed.ready or proposed.intent_id is None:
        # Authority not (yet) available. Retry within the attempt budget, then stop.
        fail_attempt(
            session, account_id=account_id, claim=claim, reason=proposed.reason, now=now
        )
        return DispatchStep(
            result=DispatchResult.DEFERRED, case_id=case.id, stage=WorkflowStage.PUBLISH,
            detail=proposed.reason,
        )
    intent = session.get(ReconActionIntent, proposed.intent_id)
    assert intent is not None  # noqa: S101 - just committed by propose_publication
    published = publish_case(
        session,
        account_id=account_id,
        intent=intent,
        current_payload=dict(case_version.result_json),
        now=now,
    )
    confirmed = False
    if published.published:
        verdict = reconcile_publication(session, account_id=account_id, intent=intent, now=now)
        confirmed = verdict.confirmed
    outcome = commit_stage(session, account_id=account_id, claim=claim, now=now)
    if outcome.status is CommitStatus.FENCED:
        return DispatchStep(
            result=DispatchResult.FENCED, case_id=case.id, stage=WorkflowStage.PUBLISH
        )
    if confirmed:
        return DispatchStep(
            result=DispatchResult.COMPLETED, case_id=case.id, stage=WorkflowStage.PUBLISH
        )
    return DispatchStep(
        result=DispatchResult.DEFERRED, case_id=case.id, stage=WorkflowStage.PUBLISH,
        detail="published but not yet confirmed",
    )


def execute_next(
    session: Session,
    *,
    account_id: UUID,
    principal_subject: str,
    system_fingerprint: str,
    now: datetime,
) -> DispatchStep:
    """Claim one due task and run its stage. Returns IDLE when there is no due work.

    This is one durable boundary. On a COMMITTED/terminal result the caller commits; on a
    FENCED result (a stale worker whose lease a recovery worker superseded) the caller rolls
    back — the recovery worker's claim will redo the stage."""
    claim = claim_task(session, account_id=account_id, now=now)
    if claim is None:
        return DispatchStep(result=DispatchResult.IDLE)

    run = session.get(ReconWorkflowRun, claim.run_id)
    if run is None or run.account_id != account_id:
        fail_attempt(session, account_id=account_id, claim=claim, reason="missing_run", now=now)
        return DispatchStep(result=DispatchResult.DEFERRED, detail="missing_run")
    case = session.get(ReconOperationalCase, run.case_id)
    expected_job = (
        None if case is None else _expected_job(session, account_id=account_id, case=case)
    )
    if case is None or expected_job is None:
        fail_attempt(session, account_id=account_id, claim=claim, reason="missing_case", now=now)
        return DispatchStep(result=DispatchResult.DEFERRED, detail="missing_case")

    if claim.stage is WorkflowStage.RECONCILE:
        return _run_reconcile_stage(
            session, account_id=account_id, claim=claim, case=case,
            expected_job=expected_job, now=now,
        )
    if claim.stage is WorkflowStage.PUBLISH:
        version = _latest_version(session, account_id=account_id, case_id=case.id)
        if version is None:
            fail_attempt(
                session, account_id=account_id, claim=claim, reason="missing_version", now=now
            )
            return DispatchStep(result=DispatchResult.DEFERRED, detail="missing_version")
        return _run_publish_stage(
            session, account_id=account_id, claim=claim, case=case, case_version=version,
            principal_subject=principal_subject, system_fingerprint=system_fingerprint, now=now,
        )
    # An unknown stage is not silently succeeded; it fails within the bounded attempt budget.
    fail_attempt(session, account_id=account_id, claim=claim, reason="unknown_stage", now=now)
    return DispatchStep(result=DispatchResult.DEFERRED, stage=claim.stage, detail="unknown_stage")


def run_reconciliation_pass(
    session: Session,
    *,
    account_id: UUID,
    principal_subject: str,
    system_fingerprint: str,
    now: datetime,
    budget_units: int = DEFAULT_BUDGET_UNITS,
    workflow_version: str = WORKFLOW_VERSION,
    max_steps: int = 1000,
) -> PassSummary:
    """One scheduler pass: schedule arrived cases, then drain due work to a stable state.

    Reconstructable across restarts — it only ever acts on what the durable store reports as
    due, so a crashed pass loses no work and a fresh pass resumes exactly where the tasks
    stand. A single worker calls this on an interval; multiple workers are safe because the
    claim/commit primitives fence concurrent claims (work order 5)."""
    scheduled = enqueue_ready_cases(
        session,
        account_id=account_id,
        system_fingerprint=system_fingerprint,
        budget_units=budget_units,
        workflow_version=workflow_version,
        now=now,
    )
    steps: list[DispatchStep] = []
    for _ in range(max_steps):
        step = execute_next(
            session,
            account_id=account_id,
            principal_subject=principal_subject,
            system_fingerprint=system_fingerprint,
            now=now,
        )
        if step.result is DispatchResult.IDLE:
            break
        steps.append(step)
        if step.result in (DispatchResult.DEFERRED, DispatchResult.FENCED):
            # Nothing more to make progress on in this instant; the next pass retries when due.
            break
    return PassSummary(scheduled=scheduled, steps=tuple(steps))


def next_pass_due_at(now: datetime, *, interval: timedelta = timedelta(minutes=1)) -> datetime:
    """When a driver with no external signal should wake for the next pass."""
    return now + interval


def has_pending_work(session: Session, *, account_id: UUID) -> bool:
    """Whether any workflow task is still open (READY or LEASED) for this tenant.

    A driver uses this to decide whether another pass is worth scheduling."""
    return bool(
        session.scalar(
            select(
                exists().where(
                    ReconWorkflowTask.account_id == account_id,
                    ReconWorkflowTask.status.in_((TaskStatus.READY, TaskStatus.LEASED)),
                )
            )
        )
    )
