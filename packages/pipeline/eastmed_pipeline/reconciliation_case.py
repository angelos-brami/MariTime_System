"""Deterministic case build, private commit and read-back (blueprint work order 3).

Turns the immutable observations reconciled to an expected job into a versioned case
result: per-grade fuel variances from the deterministic calculator, a canonical
result payload, a content hash and an evidence hash. Committing writes an immutable
ReconCaseVersion; read-back recomputes the content hash from the stored payload and
confirms it matches (blueprint 23). Recomputing from the same immutable inputs yields
the identical hash, so the normal path and a replay agree (blueprint 15).

No language model is involved and nothing outside this account's own rows is read.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from eastmed_schema.models import (
    ReconCaseVersion,
    ReconExpectedJob,
    ReconObservation,
    ReconOperationalCase,
    ReconSourceCapture,
    ReconVerificationVerdict,
)
from eastmed_shared.reconciliation.calculator import (
    CALCULATOR_VERSION,
    CalcRefusal,
    FuelVarianceResult,
    QuantityInput,
    fuel_variance,
)
from eastmed_shared.reconciliation.contract import Rejection, validate_record
from eastmed_shared.reconciliation.verifier import (
    VERIFIER_VERSION,
    CaseCompletionStatus,
    EvidenceFact,
    VerificationVerdict,
    finalize_case_status,
    verify_case_result,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

ACTUAL_FIELD = "fuel_consumed"
PLAN_FIELD = "fuel_planned"


class CaseBuildError(RuntimeError):
    pass


@dataclass(frozen=True)
class CaseResult:
    expected_job_id: UUID
    result_json: dict[str, Any]
    content_hash: str
    evidence_hash: str
    unresolved: bool


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _sha256(payload: Any) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _to_quantity(observation: ReconObservation, revision: str) -> QuantityInput:
    return QuantityInput(
        input_id=str(observation.id),
        fuel_grade=observation.fuel_grade,
        value=observation.value,
        unit=observation.unit,
        period_start=_as_utc(observation.period_start),
        period_end=_as_utc(observation.period_end),
        revision=revision,
    )


def _revision_by_capture(session: Session, account_id: UUID) -> dict[UUID, str]:
    captures = session.scalars(
        select(ReconSourceCapture).where(ReconSourceCapture.account_id == account_id)
    ).all()
    return {capture.id: capture.source_revision for capture in captures}


def _resolve_current(
    rows: list[ReconObservation], revisions: dict[UUID, str]
) -> tuple[ReconObservation | None, bool]:
    """Pick the current observation for one (grade, interval) by explicit source-revision
    lineage, never by arrival time. Returns (chosen, ambiguous). A revision set that is
    not integer-orderable cannot be resolved here and is reported ambiguous."""
    if not rows:
        return None, False
    if len(rows) == 1:
        return rows[0], False
    try:
        newest = max(int(revisions.get(o.capture_id, "")) for o in rows)
        candidates = [o for o in rows if int(revisions.get(o.capture_id, "")) == newest]
    except ValueError:
        return None, True
    # Equal revisions are not a tie to break by database/arrival order.
    return (candidates[0], False) if len(candidates) == 1 else (None, True)


def _matching_plans(
    session: Session, *, account_id: UUID, job: ReconExpectedJob, actual: ReconObservation
) -> list[ReconObservation]:
    """Bind plans to the actual report's vessel, voyage and applicable effective interval.

    Observations do not have vessel/voyage columns: identity must be reconstructed from
    their tenant-owned source captures, not inferred from grade and time alone.
    """
    capture = session.scalars(select(ReconSourceCapture).where(
        ReconSourceCapture.account_id == account_id,
        ReconSourceCapture.id == actual.capture_id,
    )).one_or_none()
    if capture is None:
        return []
    report = validate_record(capture.payload_json)
    if isinstance(report, Rejection) or report.vessel_ref != job.vessel_ref:
        return []
    if job.voyage_ref is not None and report.voyage_ref != job.voyage_ref:
        return []
    rows = session.execute(
        select(ReconObservation, ReconSourceCapture)
        .join(ReconSourceCapture, (
            (ReconSourceCapture.id == ReconObservation.capture_id)
            & (ReconSourceCapture.account_id == ReconObservation.account_id)
        ))
        .where(
            ReconObservation.account_id == account_id,
            ReconObservation.field == PLAN_FIELD,
            ReconObservation.fuel_grade == actual.fuel_grade,
            ReconObservation.period_start == actual.period_start,
            ReconObservation.period_end == actual.period_end,
        )
    ).all()
    matches: list[ReconObservation] = []
    lineages: set[tuple[str, str]] = set()
    for observation, source in rows:
        plan = validate_record(source.payload_json)
        if isinstance(plan, Rejection):
            continue
        if plan.vessel_ref != job.vessel_ref or plan.voyage_ref != report.voyage_ref:
            continue
        if plan.interval_start is None or plan.interval_end is None:
            continue
        if not (
            plan.interval_start.utc <= _as_utc(actual.period_start)
            and plan.interval_end.utc >= _as_utc(actual.period_end)
        ):
            continue
        matches.append(observation)
        lineages.add((source.source_system, source.external_ref))
    # No declared cross-source precedence exists in v1; competing plans remain unresolved.
    return matches if len(lineages) <= 1 else []


def compute_case_result(
    session: Session, *, account_id: UUID, expected_job: ReconExpectedJob
) -> CaseResult:
    """Reconcile a job's actual observations against matching plan observations.

    Matching is by fuel grade and reporting interval. When a grade has several actual
    or plan observations (a correction), the current one is selected by source-revision
    lineage. A grade with no matching plan is unresolved; an unorderable revision set is
    unresolved rather than silently picked."""
    if expected_job.account_id != account_id:
        raise CaseBuildError("expected job is not owned by the account")
    revisions = _revision_by_capture(session, account_id)

    actuals = session.scalars(
        select(ReconObservation).where(
            ReconObservation.account_id == account_id,
            ReconObservation.expected_job_id == expected_job.id,
            ReconObservation.field == ACTUAL_FIELD,
        )
    ).all()

    groups: dict[tuple[str, datetime, datetime], list[ReconObservation]] = {}
    for actual in actuals:
        key = (actual.fuel_grade.value, actual.period_start, actual.period_end)
        groups.setdefault(key, []).append(actual)

    variances: list[dict[str, Any]] = []
    evidence: list[dict[str, str]] = []
    unresolved = not actuals

    for key in sorted(groups):
        grade_value = key[0]
        current_actual, ambiguous = _resolve_current(groups[key], revisions)
        if current_actual is None:
            unresolved = True
            variances.append(
                {"fuel_grade": grade_value, "status": "unresolved",
                 "reason": "unresolved_revision" if ambiguous else "missing_input"}
            )
            continue

        actual_rev = revisions.get(current_actual.capture_id, "")
        evidence.append(_evidence_ref("actual", current_actual, actual_rev))

        plan_rows = _matching_plans(
            session, account_id=account_id, job=expected_job, actual=current_actual
        )
        plan, plan_ambiguous = _resolve_current(plan_rows, revisions)
        if plan is None:
            unresolved = True
            variances.append(
                {"fuel_grade": grade_value, "status": "unresolved",
                 "reason": "unresolved_plan_revision" if plan_ambiguous else "missing_input"}
            )
            continue

        plan_rev = revisions.get(plan.capture_id, "")
        evidence.append(_evidence_ref("planned", plan, plan_rev))
        outcome = fuel_variance(_to_quantity(actual, actual_rev), _to_quantity(plan, plan_rev))
        if isinstance(outcome, CalcRefusal):
            unresolved = True
            variances.append(
                {"fuel_grade": grade_value, "status": "unresolved", "reason": outcome.reason.value}
            )
        else:
            variances.append(_variance_entry(outcome))

    result_json: dict[str, Any] = {
        "result_contract_version": expected_job.result_contract_version,
        "expected_job_id": str(expected_job.id),
        "vessel_ref": expected_job.vessel_ref,
        "period_start": _as_utc(expected_job.period_start).isoformat(),
        "period_end": _as_utc(expected_job.period_end).isoformat(),
        "calculator_version": CALCULATOR_VERSION,
        "variances": variances,
    }
    return CaseResult(
        expected_job_id=expected_job.id,
        result_json=result_json,
        content_hash=_sha256(result_json),
        evidence_hash=_sha256(sorted(evidence, key=lambda e: (e["role"], e["input_id"]))),
        unresolved=unresolved,
    )


def _variance_entry(outcome: FuelVarianceResult) -> dict[str, Any]:
    entry = outcome.to_dict()
    entry["status"] = "reconciled"
    return entry


def _evidence_ref(role: str, observation: ReconObservation, revision: str) -> dict[str, str]:
    return {
        "role": role,
        "input_id": str(observation.id),
        "fuel_grade": observation.fuel_grade.value,
        "unit": observation.unit.value,
        "value": str(observation.value),
        "revision": revision,
        "period_start": _as_utc(observation.period_start).isoformat(),
        "period_end": _as_utc(observation.period_end).isoformat(),
    }


def commit_case_version(
    session: Session, *, account_id: UUID, case: ReconOperationalCase, result: CaseResult
) -> ReconCaseVersion:
    """Append an immutable case version. A later result supersedes the prior one; it
    never edits it in place (blueprint 16, 32)."""
    prior = session.scalars(
        select(ReconCaseVersion)
        .where(
            ReconCaseVersion.account_id == account_id,
            ReconCaseVersion.case_id == case.id,
        )
        .order_by(ReconCaseVersion.revision.desc())
    ).first()
    revision = 1 if prior is None else prior.revision + 1
    version = ReconCaseVersion(
        account_id=account_id,
        case_id=case.id,
        revision=revision,
        evidence_hash=result.evidence_hash,
        content_hash=result.content_hash,
        result_json=result.result_json,
        supersedes_version_id=None if prior is None else prior.id,
    )
    session.add(version)
    session.flush()
    return version


def build_evidence(
    session: Session, *, account_id: UUID, expected_job: ReconExpectedJob
) -> list[EvidenceFact]:
    """Reconstruct the immutable actual/planned facts for the independent verifier.

    Reading the same immutable observations is legitimate reconstruction; the verifier
    recomputes the arithmetic with its own implementation (blueprint 19)."""
    revisions = _revision_by_capture(session, account_id)
    actuals = session.scalars(
        select(ReconObservation).where(
            ReconObservation.account_id == account_id,
            ReconObservation.expected_job_id == expected_job.id,
            ReconObservation.field == ACTUAL_FIELD,
        )
    ).all()

    groups: dict[tuple[str, datetime, datetime], list[ReconObservation]] = {}
    for actual in actuals:
        key = (actual.fuel_grade.value, actual.period_start, actual.period_end)
        groups.setdefault(key, []).append(actual)

    evidence: list[EvidenceFact] = []
    for key in sorted(groups):
        current_actual, ambiguous = _resolve_current(groups[key], revisions)
        if current_actual is None:
            continue
        plan_rows = _matching_plans(
            session, account_id=account_id, job=expected_job, actual=current_actual
        )
        plan, _ = _resolve_current(plan_rows, revisions)
        evidence.append(
            EvidenceFact(
                fuel_grade=current_actual.fuel_grade.value,
                period_start=_as_utc(current_actual.period_start).isoformat(),
                period_end=_as_utc(current_actual.period_end).isoformat(),
                actual=current_actual.value,
                planned=None if plan is None else plan.value,
                owner_ok=True,
                is_current=not ambiguous,
            )
        )
    return evidence


def verify_case(
    session: Session, *, account_id: UUID, expected_job: ReconExpectedJob, result: CaseResult
) -> VerificationVerdict:
    """Independently verify a computed case result against reconstructed evidence."""
    evidence = build_evidence(session, account_id=account_id, expected_job=expected_job)
    return verify_case_result(result.result_json, evidence)


def record_verification_verdict(
    session: Session,
    *,
    account_id: UUID,
    expected_job: ReconExpectedJob,
    case: ReconOperationalCase,
    case_version: ReconCaseVersion,
    result: CaseResult,
) -> ReconVerificationVerdict:
    """Independently verify the committed case version and persist the verdict.

    The verdict is bound to the exact source (evidence hash), expected job, result
    (content hash) and formula (calculator version) it judged. The publication gate loads
    it and requires a matching completion, so an empty, unverified or unresolved result
    cannot acquire a publication entitlement from a caller-supplied label.

    Insert-once per case version (the row is immutable): a replay returns the existing
    verdict rather than writing a second one."""
    existing = session.scalars(
        select(ReconVerificationVerdict).where(
            ReconVerificationVerdict.account_id == account_id,
            ReconVerificationVerdict.case_version_id == case_version.id,
        )
    ).one_or_none()
    if existing is not None:
        return existing

    verdict = verify_case(session, account_id=account_id, expected_job=expected_job, result=result)
    completion = finalize_case_status(verdict, has_unresolved=result.unresolved)
    row = ReconVerificationVerdict(
        account_id=account_id,
        case_id=case.id,
        case_version_id=case_version.id,
        expected_job_id=expected_job.id,
        case_revision=case_version.revision,
        content_hash=case_version.content_hash,
        evidence_hash=case_version.evidence_hash,
        calculator_version=str(result.result_json.get("calculator_version", "")),
        verifier_version=VERIFIER_VERSION,
        status=verdict.status.value,
        completion_status=completion.value,
        checked=verdict.checked,
        has_unresolved=result.unresolved,
        completes=completion is CaseCompletionStatus.COMPLETED,
        findings_json=[
            {"fuel_grade": finding.fuel_grade, "reason": finding.reason.value}
            for finding in verdict.findings
        ],
    )
    session.add(row)
    session.flush()
    return row


def read_back(session: Session, *, account_id: UUID, case_version_id: UUID) -> bool:
    """Independently read the committed version and recompute its content hash from the
    stored payload. Returns True only when the stored and recomputed hashes match."""
    version = session.scalars(
        select(ReconCaseVersion).where(
            ReconCaseVersion.account_id == account_id,
            ReconCaseVersion.id == case_version_id,
        )
    ).one_or_none()
    if version is None:
        return False
    recomputed = _sha256(version.result_json)
    return recomputed == version.content_hash
