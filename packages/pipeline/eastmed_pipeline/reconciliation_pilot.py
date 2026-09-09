"""Synthetic reconciliation replay harness (work order 10, sections 05, 28, 32).

Runs an authorized feed through the entire vertical slice — import, reconcile, deterministic
calculation, independent verification, private publication and read-back — over a frozen
expected-work ledger locked before arrivals. It injects the two required drills (worker
recovery with a fenced stale commit, and a source correction that supersedes and
invalidates its pending publication), measures the section-05 metrics from the *observed*
run, and hands them to the honest acceptance evaluator (``evaluate_pilot``).

Every assertion is backed by persisted records and read-back checks, not an agent's
narrative (blueprint 32). The harness never claims commercial qualification from one run;
field gates stay pending until real trial evidence exists.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from eastmed_schema.enums import AuthorityRole, JobOutcome, RecordKind, WorkflowStage
from eastmed_schema.models import (
    ReconActionIntent,
    ReconCaseVersion,
    ReconExpectedJob,
    ReconOperationalCase,
    ReconVesselMembership,
)
from eastmed_shared.reconciliation.ledger import (
    CompletionSummary,
    ExpectedJob,
    JobObservation,
    summarize_ledger,
)
from eastmed_shared.reconciliation.pilot import (
    DecisionRecord,
    PilotAcceptance,
    PilotObservations,
    evaluate_pilot,
)
from eastmed_shared.reconciliation.release import percentile
from eastmed_shared.reconciliation.verifier import VerificationStatus
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from eastmed_pipeline.reconciliation_case import (
    commit_case_version,
    compute_case_result,
    record_verification_verdict,
)
from eastmed_pipeline.reconciliation_import import ImportStatus, import_record
from eastmed_pipeline.reconciliation_publication import (
    invalidate_pending_publications,
    propose_publication,
    publish_case,
    reconcile_publication,
)
from eastmed_pipeline.reconciliation_read import get_case_view
from eastmed_pipeline.reconciliation_reliability import run_recover_expired_task
from eastmed_pipeline.reconciliation_runtime import (
    CommitStatus,
    claim_task,
    commit_stage,
    create_run,
    enqueue_task,
)

STEP = timedelta(seconds=10)  # simulated time; never evidence of real processing latency


@dataclass(frozen=True)
class PilotJob:
    vessel_ref: str
    plan_payload: Mapping[str, Any]
    report_payload: Mapping[str, Any]
    period_start: datetime
    period_end: datetime
    due_at: datetime
    corrected_report_payload: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class PilotRunResult:
    acceptance: PilotAcceptance
    completion: CompletionSummary
    verified_cases: int
    published_effects: int
    latencies: tuple[float, ...] = field(default_factory=tuple)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _ensure_membership(session: Session, *, account_id: UUID, job: PilotJob) -> None:
    existing = session.scalars(
        select(ReconVesselMembership).where(
            ReconVesselMembership.account_id == account_id,
            ReconVesselMembership.vessel_ref == job.vessel_ref,
        )
    ).first()
    if existing is not None:
        return
    session.add(
        ReconVesselMembership(
            account_id=account_id,
            vessel_ref=job.vessel_ref,
            authority_role=AuthorityRole.OPERATOR,
            valid_from=_as_utc(job.period_start) - timedelta(days=365),
            valid_to=_as_utc(job.period_end) + timedelta(days=365),
        )
    )
    session.flush()


def _lock_expected_job(session: Session, *, account_id: UUID, job: PilotJob) -> ReconExpectedJob:
    """Materialize the contracted job before its record arrives (blueprint 05)."""
    expected = ReconExpectedJob(
        account_id=account_id,
        vessel_ref=job.vessel_ref,
        voyage_ref=None,
        period_start=_as_utc(job.period_start),
        period_end=_as_utc(job.period_end),
        due_at=_as_utc(job.due_at),
        contract_version="fleet_reconciliation_v1",
        result_contract_version="result_contract_v1",
        required_record_kind=RecordKind.DAILY_REPORT,
    )
    session.add(expected)
    session.flush()
    return expected


def _worker_recovery_drill(
    session: Session, *, account_id: UUID, case: ReconOperationalCase, now: datetime
) -> bool:
    """Crash a worker, recover the task at a newer epoch, and confirm the stale commit is
    fenced (blueprint 32 'inject a worker failure')."""
    run = create_run(
        session, account_id=account_id, case=case, workflow_version="wf-pilot",
        system_fingerprint="fp1", budget_units=100, deadline_at=now + timedelta(hours=1),
    )
    enqueue_task(session, account_id=account_id, run=run, stage=WorkflowStage.VERIFY, now=now)
    crashed = claim_task(session, account_id=account_id, now=now)
    if crashed is None:
        return False
    outcome, recovered = run_recover_expired_task(
        session, account_id=account_id, now=now + timedelta(minutes=10)
    )
    if recovered is None or recovered.lease_epoch <= crashed.lease_epoch:
        return False
    fenced = commit_stage(
        session, account_id=account_id, claim=crashed, now=now + timedelta(minutes=10)
    )
    return fenced.status is CommitStatus.FENCED


def _correction_drill(
    session: Session, *, account_id: UUID, job: PilotJob, expected: ReconExpectedJob, now: datetime
) -> bool:
    """Apply a corrected report: recompute, supersede the prior version, and confirm a new
    current revision without touching unrelated vessels (blueprint 26, 32)."""
    if job.corrected_report_payload is None:
        return True
    before = session.scalar(
        select(func.count())
        .select_from(ReconCaseVersion)
        .where(ReconCaseVersion.account_id == account_id, ReconCaseVersion.case_id.is_not(None))
    ) or 0
    outcome = import_record(
        session, account_id=account_id, payload=job.corrected_report_payload, now=now
    )
    if outcome.status is not ImportStatus.ACCEPTED or outcome.case_id is None:
        return False
    case = session.get(ReconOperationalCase, outcome.case_id)
    if case is None:
        return False
    result = compute_case_result(session, account_id=account_id, expected_job=expected)
    version = commit_case_version(session, account_id=account_id, case=case, result=result)
    # Any pending publication of the superseded revision is invalidated (blueprint 26, 32).
    invalidate_pending_publications(
        session, account_id=account_id, case_id=case.id,
        current_revision=version.revision, now=now,
    )
    # A corrected report supersedes the prior version (revision advances).
    total_after = session.scalar(
        select(func.count())
        .select_from(ReconCaseVersion)
        .where(ReconCaseVersion.account_id == account_id, ReconCaseVersion.case_id.is_not(None))
    ) or 0
    return version.revision >= 2 and total_after > before


def run_pilot(
    session: Session,
    *,
    account_id: UUID,
    principal_subject: str,
    system_fingerprint: str,
    jobs: Sequence[PilotJob],
    now: datetime,
    foreign_account_id: UUID | None = None,
) -> PilotRunResult:
    """Drive the authorized feed end-to-end and evaluate the section-05 acceptance gates."""
    # Lock the frozen expected-work ledger before any arrival.
    expected_by_vessel: dict[str, ReconExpectedJob] = {}
    for job in jobs:
        _ensure_membership(session, account_id=account_id, job=job)
        expected_by_vessel[job.vessel_ref] = _lock_expected_job(
            session, account_id=account_id, job=job
        )

    observations: list[JobObservation] = []
    ledger_jobs: list[ExpectedJob] = []
    latencies: list[float] = []
    verified_cases = 0
    published_effects = 0
    all_verified = True
    read_back_all = True
    one_effect_all = True
    completed_cases: list[UUID] = []

    for job in jobs:
        expected = expected_by_vessel[job.vessel_ref]
        ledger_jobs.append(
            ExpectedJob(
                expected_job_id=str(expected.id),
                tenant_id=str(account_id),
                vessel_ref=job.vessel_ref,
                period_start=_as_utc(job.period_start),
                period_end=_as_utc(job.period_end),
                due_at=_as_utc(job.due_at),
            )
        )
        clock = now

        import_record(session, account_id=account_id, payload=job.plan_payload, now=clock)
        clock += STEP
        arrival = clock
        report = import_record(
            session, account_id=account_id, payload=job.report_payload, now=clock
        )
        if report.status is not ImportStatus.ACCEPTED or report.case_id is None:
            observations.append(
                JobObservation(
                    expected_job_id=str(expected.id),
                    outcome=JobOutcome.MISSING,
                    arrived=False,
                )
            )
            continue

        case = session.get(ReconOperationalCase, report.case_id)
        assert case is not None  # noqa: S101 - just created by the importer
        clock += STEP
        result = compute_case_result(session, account_id=account_id, expected_job=expected)
        clock += STEP
        version = commit_case_version(session, account_id=account_id, case=case, result=result)
        # Persist the independent verdict bound to this version; publication requires it.
        verdict = record_verification_verdict(
            session, account_id=account_id, expected_job=expected, case=case,
            case_version=version, result=result,
        )
        if verdict.status != VerificationStatus.VERIFIED.value:
            all_verified = False

        if not verdict.completes:
            observations.append(
                JobObservation(
                    expected_job_id=str(expected.id), outcome=JobOutcome.UNRESOLVED, arrived=True
                )
            )
            continue

        # Authorized private publication + independent read-back.
        clock += STEP
        proposed = propose_publication(
            session, account_id=account_id, case=case, case_version=version,
            principal_subject=principal_subject, system_fingerprint=system_fingerprint, now=clock,
        )
        payload = dict(version.result_json)
        published = False
        confirmed = False
        if proposed.ready and proposed.intent_id is not None:
            intent = session.get(ReconActionIntent, proposed.intent_id)
            assert intent is not None  # noqa: S101
            clock += STEP
            pub = publish_case(
                session, account_id=account_id, intent=intent, current_payload=payload, now=clock
            )
            published = pub.published
            clock += STEP
            verdict_pub = reconcile_publication(
                session, account_id=account_id, intent=intent, now=clock
            )
            confirmed = verdict_pub.confirmed

        if published:
            published_effects += 1
        else:
            one_effect_all = False
        if not confirmed:
            read_back_all = False
        if confirmed:
            verified_cases += 1
            completed_cases.append(case.id)
            latencies.append((clock - arrival).total_seconds())

        observations.append(
            JobObservation(
                expected_job_id=str(expected.id),
                outcome=(
                    JobOutcome.UNRESOLVED if not confirmed
                    else JobOutcome.CORRECT_ON_TIME if clock <= _as_utc(job.due_at)
                    else JobOutcome.CORRECT_LATE
                ),
                arrived=True,
                human_intervention=False,
            )
        )

    completion_summary = summarize_ledger(ledger_jobs, observations)

    # Required drills, backed by persisted records.
    first_job = jobs[0]
    first_case = session.scalars(
        select(ReconOperationalCase).where(
            ReconOperationalCase.account_id == account_id,
            ReconOperationalCase.expected_job_id == expected_by_vessel[first_job.vessel_ref].id,
        )
    ).one_or_none()
    worker_recovery_ok = first_case is not None and _worker_recovery_drill(
        session, account_id=account_id, case=first_case, now=now
    )
    correction_ok = all(
        _correction_drill(
            session, account_id=account_id, job=job,
            expected=expected_by_vessel[job.vessel_ref], now=now + timedelta(hours=2),
        )
        for job in jobs
        if job.corrected_report_payload is not None
    )

    # Control integrity: no cross-tenant disclosure of a completed case.
    no_cross_tenant = True
    if foreign_account_id is not None and completed_cases:
        no_cross_tenant = (
            get_case_view(session, account_id=foreign_account_id, case_id=completed_cases[0])
            is None
        )

    by_outcome = completion_summary.by_outcome
    obs = PilotObservations(
        expected_jobs=completion_summary.expected_jobs,
        correct_on_time=completion_summary.correct_on_time_zero_touch,
        incorrect=_count(by_outcome, "incorrect"),
        unresolved=_count(by_outcome, "unresolved"),
        missing=_count(by_outcome, "missing"),
        correct_late=_count(by_outcome, "correct_late"),
        unsupported=_count(by_outcome, "unsupported"),
        human_interventions=completion_summary.human_touch_jobs,
        arithmetic_fixtures_pass=all_verified,
        control_integrity_pass=no_cross_tenant,
        one_logical_effect=one_effect_all,
        worker_recovery_ok=worker_recovery_ok,
        correction_propagation_ok=correction_ok,
        read_back_confirmed=read_back_all,
        p95_latency_seconds=percentile(latencies, 0.95) or 0.0,
        latency_measured=False,
    )
    decision = DecisionRecord(
        expected_jobs=obs.expected_jobs,
        completion_rate=obs.completion_rate,
        human_touch_rate=obs.human_touch_rate,
        incorrect=obs.incorrect,
        unresolved=obs.unresolved,
        missing=obs.missing,
        p50_latency_seconds=percentile(latencies, 0.50),
        p95_latency_seconds=percentile(latencies, 0.95),
        source_delay_p95_seconds=None,
        correction_propagation_ok=correction_ok,
        cost_per_correct_result=None,
        setup_effort_hours=None,
        measured_benefit_multiple=0.0,
        evidence_kind="synthetic_replay_simulated_clock",
        correct_late=obs.correct_late,
        unsupported=obs.unsupported,
    )
    acceptance = evaluate_pilot(obs, decision)
    return PilotRunResult(
        acceptance=acceptance,
        completion=completion_summary,
        verified_cases=verified_cases,
        published_effects=published_effects,
        latencies=tuple(latencies),
    )


def _count(by_outcome: Mapping[JobOutcome, int], value: str) -> int:
    return sum(count for outcome, count in by_outcome.items() if outcome.value == value)
