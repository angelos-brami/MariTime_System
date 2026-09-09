"""Read model assembly for the private fleet report (work order 8, sections 09, 25).

Builds the customer-facing views from the immutable records — case + version, provenance
from source captures and observations, the daily report with honest counts and empty
states, a public-safe timeline, and per-capability service health. Everything is scoped to
the caller's ``account_id``; a case that belongs to another tenant is reported as simply
not found, never confirmed to exist (blueprint 25, non-disclosing behaviour).

The customer views expose no approve/reject task. ``request_recompute`` is the one write,
and it is a service-to-service interface: restricted to an authorised service identity,
gated on the expected revision, and it never overwrites an immutable case version — it asks
the durable runtime for a fresh run (blueprint 25).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from eastmed_schema.enums import AutomationStatus, BusinessStatus, WorkflowStage
from eastmed_schema.models import (
    ReconCasePublication,
    ReconCaseVersion,
    ReconExpectedJob,
    ReconObservation,
    ReconOperationalCase,
    ReconOutbox,
    ReconSourceCapture,
    ReconStandingGrant,
)
from eastmed_shared.reconciliation.policy import KNOWN_CAPABILITIES
from eastmed_shared.reconciliation.read_model import (
    CUSTOMER_ACTIONS,
    READ_MODEL_VERSION,
    EmptyState,
    EvidenceStatus,
    FleetTotal,
    PresentationStatus,
    VesselResultView,
    build_fleet_total,
    daily_report_empty_state,
    label_variances,
    next_machine_step,
    presentation_status,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from eastmed_pipeline.reconciliation_policy import resolve_principal
from eastmed_pipeline.reconciliation_runtime import create_run, enqueue_task


def _now(value: datetime | None) -> datetime:
    return value or datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _iso(value: datetime | None) -> str | None:
    return None if value is None else _as_utc(value).isoformat()


# --- Composite views ---------------------------------------------------------------


@dataclass(frozen=True)
class CaseView:
    case_id: UUID
    expected_job_id: UUID
    result_contract_version: str
    revision: int
    business_status: str
    automation_status: str
    presentation_status: PresentationStatus
    evidence_status: EvidenceStatus
    data_as_of: str | None
    deadline_at: str | None
    next_machine_step: str | None
    results: tuple[VesselResultView, ...]
    unresolved_fields: tuple[str, ...]
    assumptions: tuple[str, ...]
    receipts: dict[str, object]
    supersedes_version: str | None

    @property
    def is_completed(self) -> bool:
        return self.presentation_status is PresentationStatus.COMPLETE

    def render(self) -> dict[str, object]:
        return {
            "read_model_version": READ_MODEL_VERSION,
            "case_id": str(self.case_id),
            "expected_job_id": str(self.expected_job_id),
            "result_contract_version": self.result_contract_version,
            "revision": self.revision,
            "business_status": self.business_status,
            "automation_status": self.automation_status,
            "presentation_status": self.presentation_status.value,
            "evidence_status": self.evidence_status.value,
            "data_as_of": self.data_as_of,
            "deadline": self.deadline_at,
            "next_machine_step": self.next_machine_step,
            "result": {"vessels": [view.render() for view in self.results]},
            "unresolved_fields": list(self.unresolved_fields),
            "assumptions": list(self.assumptions),
            "receipts": self.receipts,
            "supersedes_version": self.supersedes_version,
            "customer_actions": list(CUSTOMER_ACTIONS),
        }


@dataclass(frozen=True)
class CaseSummary:
    case_id: UUID
    expected_job_id: UUID
    revision: int
    business_status: str
    automation_status: str
    presentation_status: PresentationStatus
    deadline_at: str | None

    def render(self) -> dict[str, object]:
        return {
            "case_id": str(self.case_id),
            "expected_job_id": str(self.expected_job_id),
            "revision": self.revision,
            "business_status": self.business_status,
            "automation_status": self.automation_status,
            "presentation_status": self.presentation_status.value,
            "deadline": self.deadline_at,
        }


@dataclass(frozen=True)
class CasePage:
    items: tuple[CaseSummary, ...]
    next_cursor: str | None

    def render(self) -> dict[str, object]:
        return {
            "items": [item.render() for item in self.items],
            "next_cursor": self.next_cursor,
        }


@dataclass(frozen=True)
class TimelineEntry:
    kind: str
    at: str | None
    revision: int | None
    reason_code: str | None
    content_hash: str | None

    def render(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "at": self.at,
            "revision": self.revision,
            "reason_code": self.reason_code,
            "content_hash": self.content_hash,
        }


@dataclass(frozen=True)
class TimelineView:
    case_id: UUID
    entries: tuple[TimelineEntry, ...]

    def render(self) -> dict[str, object]:
        return {"case_id": str(self.case_id), "entries": [e.render() for e in self.entries]}


@dataclass(frozen=True)
class DailyReportView:
    period_start: str
    period_end: str
    expected_jobs: int
    complete_jobs: int
    unresolved_jobs: int
    awaiting_jobs: int
    in_progress_jobs: int
    latest_source_at: str | None
    empty_state: EmptyState
    fleet_total: FleetTotal
    cases: tuple[CaseSummary, ...]

    def render(self) -> dict[str, object]:
        return {
            "read_model_version": READ_MODEL_VERSION,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "counts": {
                "expected": self.expected_jobs,
                "complete": self.complete_jobs,
                "unresolved": self.unresolved_jobs,
                "awaiting": self.awaiting_jobs,
                "in_progress": self.in_progress_jobs,
            },
            "latest_source_at": self.latest_source_at,
            "empty_state": self.empty_state.value,
            "fleet_total": self.fleet_total.render(),
            "cases": [case.render() for case in self.cases],
        }


@dataclass(frozen=True)
class CapabilityHealth:
    capability: str
    available: bool
    last_success_at: str | None
    lag_seconds: int | None
    disabled_reason: str | None

    def render(self) -> dict[str, object]:
        return {
            "capability": self.capability,
            "available": self.available,
            "last_success_at": self.last_success_at,
            "lag_seconds": self.lag_seconds,
            "disabled_reason": self.disabled_reason,
        }


@dataclass(frozen=True)
class ServiceHealthView:
    capabilities: tuple[CapabilityHealth, ...]

    def render(self) -> dict[str, object]:
        return {"capabilities": [c.render() for c in self.capabilities]}


class RecomputeStatus(StrEnum):
    ACCEPTED = "accepted"
    CONFLICT = "conflict"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"


@dataclass(frozen=True)
class RecomputeResult:
    status: RecomputeStatus
    detail: str
    current_revision: int | None = None
    run_id: UUID | None = None


# --- Loaders -----------------------------------------------------------------------


def _load_case(session: Session, *, account_id: UUID, case_id: UUID) -> ReconOperationalCase | None:
    return session.scalars(
        select(ReconOperationalCase).where(
            ReconOperationalCase.account_id == account_id,
            ReconOperationalCase.id == case_id,
        )
    ).one_or_none()


def _load_version(
    session: Session, *, account_id: UUID, case_id: UUID, revision: int | None
) -> ReconCaseVersion | None:
    stmt = select(ReconCaseVersion).where(
        ReconCaseVersion.account_id == account_id,
        ReconCaseVersion.case_id == case_id,
    )
    if revision is not None:
        stmt = stmt.where(ReconCaseVersion.revision == revision)
    else:
        stmt = stmt.order_by(ReconCaseVersion.revision.desc())
    return session.scalars(stmt).first()


def _current_revision(session: Session, *, account_id: UUID, case_id: UUID) -> int | None:
    return session.scalars(
        select(ReconCaseVersion.revision)
        .where(
            ReconCaseVersion.account_id == account_id,
            ReconCaseVersion.case_id == case_id,
        )
        .order_by(ReconCaseVersion.revision.desc())
    ).first()


def _data_as_of(session: Session, *, account_id: UUID, expected_job_id: UUID) -> datetime | None:
    return session.scalars(
        select(func.max(ReconSourceCapture.captured_at))
        .select_from(ReconObservation)
        .join(
            ReconSourceCapture,
            (ReconSourceCapture.account_id == ReconObservation.account_id)
            & (ReconSourceCapture.id == ReconObservation.capture_id),
        )
        .where(
            ReconObservation.account_id == account_id,
            ReconObservation.expected_job_id == expected_job_id,
        )
    ).one_or_none()


def _evidence_status(status: PresentationStatus) -> EvidenceStatus:
    if status is PresentationStatus.COMPLETE:
        return EvidenceStatus.CONFIRMED
    if status is PresentationStatus.UNRESOLVED:
        return EvidenceStatus.INCOMPLETE
    if status is PresentationStatus.DISABLED:
        return EvidenceStatus.UNAVAILABLE
    return EvidenceStatus.PENDING


def _publication(
    session: Session, *, account_id: UUID, case_id: UUID, revision: int
) -> ReconCasePublication | None:
    return session.scalars(
        select(ReconCasePublication).where(
            ReconCasePublication.account_id == account_id,
            ReconCasePublication.case_id == case_id,
            ReconCasePublication.case_revision == revision,
        )
    ).one_or_none()


def get_case_view(
    session: Session, *, account_id: UUID, case_id: UUID, revision: int | None = None
) -> CaseView | None:
    """Assemble the case view, or None when the case is not the caller's (non-disclosing)."""
    case = _load_case(session, account_id=account_id, case_id=case_id)
    if case is None:
        return None
    version = _load_version(session, account_id=account_id, case_id=case_id, revision=revision)
    job = session.scalars(
        select(ReconExpectedJob).where(
            ReconExpectedJob.account_id == account_id,
            ReconExpectedJob.id == case.expected_job_id,
        )
    ).one_or_none()

    status = presentation_status(
        automation_status=case.automation_status.value,
        business_status=case.business_status.value,
    )
    if version is None:
        results: tuple[VesselResultView, ...] = ()
        unresolved: tuple[str, ...] = ()
        receipts: dict[str, object] = {"read_back_confirmed": False}
        result_contract = "" if job is None else job.result_contract_version
        supersedes = None
        shown_revision = 0
    else:
        variances = version.result_json.get("variances", [])
        results, unresolved = label_variances(variances if isinstance(variances, list) else [])
        pub = _publication(
            session, account_id=account_id, case_id=case_id, revision=version.revision
        )
        confirmed = (
            pub is not None and status is PresentationStatus.COMPLETE
        )
        receipts = {
            "content_hash": version.content_hash,
            "evidence_hash": version.evidence_hash,
            "publication_reference": None if pub is None else str(pub.id),
            "read_back_confirmed": confirmed,
        }
        result_contract = str(version.result_json.get("result_contract_version", ""))
        supersedes = None if version.supersedes_version_id is None else str(
            version.supersedes_version_id
        )
        shown_revision = version.revision

    as_of = _data_as_of(session, account_id=account_id, expected_job_id=case.expected_job_id)
    return CaseView(
        case_id=case.id,
        expected_job_id=case.expected_job_id,
        result_contract_version=result_contract,
        revision=shown_revision,
        business_status=case.business_status.value,
        automation_status=case.automation_status.value,
        presentation_status=status,
        evidence_status=_evidence_status(status),
        data_as_of=_iso(as_of),
        deadline_at=_iso(case.deadline_at),
        next_machine_step=next_machine_step(case.automation_status.value),
        results=results,
        unresolved_fields=unresolved,
        assumptions=(),
        receipts=receipts,
        supersedes_version=supersedes,
    )


_AWAITING_STATES = (AutomationStatus.AWAITING_ARRIVAL, AutomationStatus.WAITING_FOR_MACHINE_DATA)
_IN_PROGRESS_STATES = (
    AutomationStatus.RECEIVED,
    AutomationStatus.VALIDATED,
    AutomationStatus.RECONCILED,
    AutomationStatus.CALCULATED,
    AutomationStatus.PUBLICATION_READY,
    AutomationStatus.PUBLISHED,
)


def _status_filter(query: Any, status: PresentationStatus) -> Any:
    if status is PresentationStatus.COMPLETE:
        return query.where(
            ReconOperationalCase.automation_status == AutomationStatus.VERIFIED_COMPLETE
        )
    if status is PresentationStatus.UNRESOLVED:
        return query.where(
            (ReconOperationalCase.business_status == BusinessStatus.UNRESOLVED)
            | (ReconOperationalCase.automation_status == AutomationStatus.UNRESOLVED)
        )
    if status is PresentationStatus.AWAITING_DATA:
        return query.where(ReconOperationalCase.automation_status.in_(_AWAITING_STATES))
    if status is PresentationStatus.DEGRADED:
        return query.where(ReconOperationalCase.automation_status == AutomationStatus.RECOVERING)
    if status is PresentationStatus.DISABLED:
        return query.where(ReconOperationalCase.automation_status == AutomationStatus.DISABLED)
    return query.where(ReconOperationalCase.automation_status.in_(_IN_PROGRESS_STATES))


def _summary(case: ReconOperationalCase) -> CaseSummary:
    status = presentation_status(
        automation_status=case.automation_status.value,
        business_status=case.business_status.value,
    )
    return CaseSummary(
        case_id=case.id,
        expected_job_id=case.expected_job_id,
        revision=case.revision,
        business_status=case.business_status.value,
        automation_status=case.automation_status.value,
        presentation_status=status,
        deadline_at=_iso(case.deadline_at),
    )


def list_cases(
    session: Session,
    *,
    account_id: UUID,
    status: PresentationStatus | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> CasePage:
    """Tenant case list with a stable id-keyed cursor, so background updates never make a
    page silently skip or duplicate a case (blueprint 25)."""
    limit = max(1, min(limit, 200))
    query = select(ReconOperationalCase).where(ReconOperationalCase.account_id == account_id)
    if status is not None:
        query = _status_filter(query, status)
    if since is not None:
        query = query.where(ReconOperationalCase.created_at >= since)
    if until is not None:
        query = query.where(ReconOperationalCase.created_at <= until)
    if cursor is not None:
        query = query.where(ReconOperationalCase.id > UUID(cursor))
    query = query.order_by(ReconOperationalCase.id).limit(limit + 1)

    rows = list(session.scalars(query).all())
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = str(page[-1].id) if has_more and page else None
    return CasePage(items=tuple(_summary(c) for c in page), next_cursor=next_cursor)


def get_case_timeline(
    session: Session, *, account_id: UUID, case_id: UUID
) -> TimelineView | None:
    """Workflow and correction history with public-safe reason codes and hashes only —
    no payloads, keys or other secrets (blueprint 25)."""
    case = _load_case(session, account_id=account_id, case_id=case_id)
    if case is None:
        return None
    versions = list(
        session.scalars(
            select(ReconCaseVersion)
            .where(
                ReconCaseVersion.account_id == account_id,
                ReconCaseVersion.case_id == case_id,
            )
            .order_by(ReconCaseVersion.revision)
        ).all()
    )
    entries: list[TimelineEntry] = []
    for index, version in enumerate(versions):
        superseded = index < len(versions) - 1
        entries.append(
            TimelineEntry(
                kind="version_committed",
                at=_iso(version.created_at),
                revision=version.revision,
                reason_code="superseded" if superseded else "current",
                content_hash=version.content_hash,
            )
        )
    publications = list(
        session.scalars(
            select(ReconCasePublication)
            .where(
                ReconCasePublication.account_id == account_id,
                ReconCasePublication.case_id == case_id,
            )
            .order_by(ReconCasePublication.case_revision)
        ).all()
    )
    for pub in publications:
        outbox = session.scalars(
            select(ReconOutbox).where(
                ReconOutbox.account_id == account_id,
                ReconOutbox.intent_id == pub.intent_id,
            )
        ).one_or_none()
        entries.append(
            TimelineEntry(
                kind="published",
                at=_iso(pub.published_at),
                revision=pub.case_revision,
                reason_code=None if outbox is None else outbox.status.value,
                content_hash=pub.content_hash,
            )
        )
    entries.sort(key=lambda e: (e.at or "", e.revision or 0))
    return TimelineView(case_id=case_id, entries=tuple(entries))


def _reconciled_deltas(
    version: ReconCaseVersion | None,
) -> tuple[list[tuple[str, str, Decimal]], int]:
    """Extract (grade, unit, delta) for reconciled variances and count unresolved ones."""
    if version is None:
        return [], 0
    variances = version.result_json.get("variances", [])
    if not isinstance(variances, list):
        return [], 0
    deltas: list[tuple[str, str, Decimal]] = []
    excluded = 0
    for entry in variances:
        if not isinstance(entry, dict):
            continue
        if entry.get("status") != "reconciled":
            excluded += 1
            continue
        grade = str(entry.get("fuel_grade", ""))
        unit = str(entry.get("unit", ""))
        try:
            delta = Decimal(str(entry.get("delta", "0")))
        except (ArithmeticError, ValueError):
            excluded += 1
            continue
        deltas.append((grade, unit, delta))
    return deltas, excluded


def get_daily_report(
    session: Session,
    *,
    account_id: UUID,
    period_start: datetime,
    period_end: datetime,
    now: datetime | None = None,
    feed_authorized: bool = True,
    feed_delayed: bool = False,
    service_disabled: bool = False,
    invalid_inputs: bool = False,
    calculations_unavailable: bool = False,
) -> DailyReportView:
    """Fleet report for a scheduled period: expected/complete/unresolved counts, a
    compatible-only fleet total, and an honest empty state (blueprint 09, 25)."""
    _ = now  # counts and empty state are point-in-time snapshots of the durable records
    jobs = list(
        session.scalars(
            select(ReconExpectedJob).where(
                ReconExpectedJob.account_id == account_id,
                ReconExpectedJob.period_start >= period_start,
                ReconExpectedJob.period_end <= period_end,
            )
        ).all()
    )
    complete = unresolved = awaiting = in_progress = arrived = 0
    reconciled: list[tuple[str, str, Decimal]] = []
    excluded_unresolved = 0
    variance_count = 0
    summaries: list[CaseSummary] = []
    latest_source: datetime | None = None

    for job in jobs:
        case = session.scalars(
            select(ReconOperationalCase).where(
                ReconOperationalCase.account_id == account_id,
                ReconOperationalCase.expected_job_id == job.id,
            )
        ).one_or_none()
        if case is None:
            awaiting += 1
            continue
        summary = _summary(case)
        summaries.append(summary)
        status = summary.presentation_status
        if status is PresentationStatus.COMPLETE:
            complete += 1
        elif status is PresentationStatus.UNRESOLVED:
            unresolved += 1
        elif status is PresentationStatus.AWAITING_DATA:
            awaiting += 1
        else:
            in_progress += 1

        job_as_of = _data_as_of(session, account_id=account_id, expected_job_id=job.id)
        if job_as_of is not None:
            arrived += 1
            if latest_source is None or _as_utc(job_as_of) > _as_utc(latest_source):
                latest_source = job_as_of
        version = _load_version(session, account_id=account_id, case_id=case.id, revision=None)
        deltas, excluded = _reconciled_deltas(version)
        reconciled.extend(deltas)
        excluded_unresolved += excluded
        variance_count += sum(1 for _g, _u, d in deltas if d != Decimal("0"))

    missing_reports = max(0, len(jobs) - arrived)
    fleet_total = build_fleet_total(
        reconciled, excluded_unresolved=excluded_unresolved, missing_reports=missing_reports
    )
    empty_state = daily_report_empty_state(
        expected_jobs=len(jobs),
        feed_authorized=feed_authorized,
        feed_delayed=feed_delayed,
        service_disabled=service_disabled,
        invalid_inputs=invalid_inputs,
        calculations_unavailable=calculations_unavailable,
        arrived_jobs=arrived,
        complete_jobs=complete,
        variance_count=variance_count,
    )
    return DailyReportView(
        period_start=_as_utc(period_start).isoformat(),
        period_end=_as_utc(period_end).isoformat(),
        expected_jobs=len(jobs),
        complete_jobs=complete,
        unresolved_jobs=unresolved,
        awaiting_jobs=awaiting,
        in_progress_jobs=in_progress,
        latest_source_at=_iso(latest_source),
        empty_state=empty_state,
        fleet_total=fleet_total,
        cases=tuple(summaries),
    )


def _active_grant(
    session: Session, *, account_id: UUID, capability: str, now: datetime
) -> ReconStandingGrant | None:
    grant = session.scalars(
        select(ReconStandingGrant)
        .where(
            ReconStandingGrant.account_id == account_id,
            ReconStandingGrant.capability == capability,
            ReconStandingGrant.active.is_(True),
        )
        .order_by(ReconStandingGrant.policy_revision.desc())
    ).first()
    if grant is None:
        return None
    if not _as_utc(grant.valid_from) <= now < _as_utc(grant.valid_to):
        return None
    return grant


def get_service_health(
    session: Session,
    *,
    account_id: UUID,
    now: datetime | None = None,
    capabilities: tuple[str, ...] | None = None,
) -> ServiceHealthView:
    """Per-capability availability: last success, lag and a disabled reason (blueprint 25)."""
    moment = _now(now)
    caps = capabilities or tuple(sorted(KNOWN_CAPABILITIES))
    last_success = session.scalars(
        select(func.max(ReconCasePublication.published_at)).where(
            ReconCasePublication.account_id == account_id
        )
    ).one_or_none()
    last_iso = _iso(last_success)
    lag = None if last_success is None else int((moment - _as_utc(last_success)).total_seconds())

    rows: list[CapabilityHealth] = []
    for capability in caps:
        grant = _active_grant(session, account_id=account_id, capability=capability, now=moment)
        available = grant is not None
        rows.append(
            CapabilityHealth(
                capability=capability,
                available=available,
                last_success_at=last_iso if available else None,
                lag_seconds=lag if available else None,
                disabled_reason=None if available else "no_active_grant",
            )
        )
    return ServiceHealthView(capabilities=tuple(rows))


def request_recompute(
    session: Session,
    *,
    account_id: UUID,
    case_id: UUID,
    expected_revision: int,
    principal_subject: str,
    workflow_version: str,
    system_fingerprint: str,
    budget_units: int,
    deadline_at: datetime,
    now: datetime | None = None,
) -> RecomputeResult:
    """Service-only request for a fresh run. Restricted to an authorised service identity,
    gated on the expected revision (a stale write gets a safe conflict, not an overwrite),
    and it never mutates the immutable case version (blueprint 25)."""
    moment = _now(now)
    principal = resolve_principal(session, account_id=account_id, subject=principal_subject)
    if principal is None or not principal.active:
        return RecomputeResult(
            status=RecomputeStatus.FORBIDDEN,
            detail="recompute is restricted to an authorized service identity",
        )
    case = _load_case(session, account_id=account_id, case_id=case_id)
    if case is None:
        return RecomputeResult(status=RecomputeStatus.NOT_FOUND, detail="case not found")

    current = _current_revision(session, account_id=account_id, case_id=case_id)
    current = case.revision if current is None else current
    if expected_revision != current:
        return RecomputeResult(
            status=RecomputeStatus.CONFLICT,
            detail=f"expected revision {expected_revision}, current is {current}",
            current_revision=current,
        )

    run = create_run(
        session,
        account_id=account_id,
        case=case,
        workflow_version=workflow_version,
        system_fingerprint=system_fingerprint,
        budget_units=budget_units,
        deadline_at=deadline_at,
    )
    enqueue_task(session, account_id=account_id, run=run, stage=WorkflowStage.RECONCILE, now=moment)
    return RecomputeResult(
        status=RecomputeStatus.ACCEPTED,
        detail="fresh run enqueued",
        current_revision=current,
        run_id=run.id,
    )
