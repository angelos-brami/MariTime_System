"""Deterministic fixture importer for fleet_reconciliation_v1 (blueprint work order 2).

Takes one already-decoded contract record (CSV or JSON both decode to a mapping) and
persists it through the durable sequence of blueprint 18:

  1. Preserve the raw capture and its content hash before anything else.
  2. Bind each source revision to that hash: an identical repeat is idempotent; a
     different payload under the same revision is a REVISION_CONFLICT.
  3. Validate the contract and the tenant-scoped vessel membership.
  4. Store immutable observations and reconcile them to the already-materialized
     expected job.

The tenant is the authenticated connector's ``account_id`` argument, never a value
read from the payload (blueprint 17). No language model is involved. This is the
"deterministic replay using fixtures before connecting a language model" step.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from eastmed_schema.enums import AutomationStatus, BusinessStatus, RecordKind
from eastmed_schema.models import (
    ReconExpectedJob,
    ReconObservation,
    ReconOperationalCase,
    ReconSourceCapture,
    ReconVesselMembership,
    ReconVoyage,
    ReconVoyageVersion,
)
from eastmed_shared.reconciliation.contract import (
    NormalizedRecord,
    Rejection,
    validate_record,
)
from sqlalchemy import or_, select
from sqlalchemy.orm import Session


class ImportStatus(StrEnum):
    ACCEPTED = "accepted"
    IDEMPOTENT = "idempotent"
    REVISION_CONFLICT = "revision_conflict"
    REJECTED = "rejected"
    MEMBERSHIP_DENIED = "membership_denied"
    OUT_OF_CONTRACT = "out_of_contract"


@dataclass(frozen=True)
class ImportOutcome:
    status: ImportStatus
    capture_id: UUID | None = None
    expected_job_id: UUID | None = None
    case_id: UUID | None = None
    observation_ids: tuple[UUID, ...] = field(default_factory=tuple)
    rejection: Rejection | None = None
    detail: str | None = None


def canonical_hash(payload: Mapping[str, Any]) -> str:
    """A stable content hash of the raw payload, independent of key order."""
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _as_utc(value: datetime) -> datetime:
    """Attach UTC to a naive value. We only ever store UTC, so a backend that drops
    tzinfo (SQLite in tests) still compares correctly against timezone-aware inputs."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _membership_covers(
    session: Session,
    *,
    account_id: UUID,
    vessel_ref: str,
    period_start: datetime,
    period_end: datetime,
) -> bool:
    memberships = session.scalars(
        select(ReconVesselMembership).where(
            ReconVesselMembership.account_id == account_id,
            ReconVesselMembership.vessel_ref == vessel_ref,
        )
    ).all()
    start = _as_utc(period_start)
    end = _as_utc(period_end)
    return any(_as_utc(m.valid_from) <= start and _as_utc(m.valid_to) >= end for m in memberships)


def _existing_capture(
    session: Session, *, account_id: UUID, record: NormalizedRecord
) -> ReconSourceCapture | None:
    return session.scalars(
        select(ReconSourceCapture).where(
            ReconSourceCapture.account_id == account_id,
            ReconSourceCapture.source_system == record.source_system,
            ReconSourceCapture.external_ref == record.external_ref,
            ReconSourceCapture.source_revision == record.source_revision,
        )
    ).one_or_none()


def _find_expected_job(
    session: Session, *, account_id: UUID, record: NormalizedRecord
) -> ReconExpectedJob | None:
    # Daily reports always carry a reporting period (contract guarantee).
    assert record.interval_start is not None and record.interval_end is not None  # noqa: S101
    return session.scalars(
        select(ReconExpectedJob).where(
            ReconExpectedJob.account_id == account_id,
            ReconExpectedJob.vessel_ref == record.vessel_ref,
            or_(
                ReconExpectedJob.voyage_ref.is_(None),
                ReconExpectedJob.voyage_ref == record.voyage_ref,
            ),
            ReconExpectedJob.required_record_kind == RecordKind.DAILY_REPORT,
            ReconExpectedJob.period_start == record.interval_start.utc,
            ReconExpectedJob.period_end == record.interval_end.utc,
        )
    ).one_or_none()


def import_record(
    session: Session,
    *,
    account_id: UUID,
    payload: Mapping[str, Any],
    now: datetime | None = None,
) -> ImportOutcome:
    now = now or datetime.now(UTC)

    validated = validate_record(payload)
    if isinstance(validated, Rejection):
        return ImportOutcome(status=ImportStatus.REJECTED, rejection=validated)
    record = validated

    # Step 1-2: capture and revision binding, before any downstream processing.
    raw_hash = canonical_hash(payload)
    existing = _existing_capture(session, account_id=account_id, record=record)
    if existing is not None:
        if existing.raw_hash == raw_hash:
            return ImportOutcome(status=ImportStatus.IDEMPOTENT, capture_id=existing.id)
        return ImportOutcome(
            status=ImportStatus.REVISION_CONFLICT,
            capture_id=existing.id,
            detail=(
                f"source_revision {record.source_revision!r} already stored with a "
                "different payload hash"
            ),
        )

    # Step 3: tenant-scoped membership. Fleet membership records define membership and
    # so are exempt; every operational record must reference a currently-owned vessel.
    if record.record_kind is not RecordKind.FLEET_MEMBERSHIP:
        start = record.interval_start.utc if record.interval_start else record.recorded_at.utc
        end = record.interval_end.utc if record.interval_end else record.recorded_at.utc
        if not _membership_covers(
            session,
            account_id=account_id,
            vessel_ref=record.vessel_ref,
            period_start=start,
            period_end=end,
        ):
            return ImportOutcome(
                status=ImportStatus.MEMBERSHIP_DENIED,
                detail=f"vessel {record.vessel_ref!r} is not a current member for this period",
            )

    capture = ReconSourceCapture(
        account_id=account_id,
        record_kind=record.record_kind,
        source_system=record.source_system,
        external_ref=record.external_ref,
        source_revision=record.source_revision,
        raw_hash=raw_hash,
        payload_json=dict(payload),
    )
    session.add(capture)
    session.flush()

    if record.record_kind is RecordKind.FLEET_MEMBERSHIP:
        return _import_membership(session, account_id=account_id, record=record, capture=capture)
    if record.record_kind is RecordKind.VOYAGE_PLAN:
        return _import_voyage_plan(session, account_id=account_id, record=record, capture=capture)
    return _import_daily_report(
        session, account_id=account_id, record=record, capture=capture, now=now
    )


def _import_membership(
    session: Session, *, account_id: UUID, record: NormalizedRecord, capture: ReconSourceCapture
) -> ImportOutcome:
    assert record.authority_role is not None  # noqa: S101 - contract guarantees for this kind
    assert record.interval_start is not None and record.interval_end is not None  # noqa: S101
    membership = ReconVesselMembership(
        account_id=account_id,
        vessel_ref=record.vessel_ref,
        authority_role=record.authority_role,
        valid_from=record.interval_start.utc,
        valid_to=record.interval_end.utc,
    )
    session.add(membership)
    session.flush()
    return ImportOutcome(status=ImportStatus.ACCEPTED, capture_id=capture.id)


def _import_voyage_plan(
    session: Session, *, account_id: UUID, record: NormalizedRecord, capture: ReconSourceCapture
) -> ImportOutcome:
    assert record.voyage_ref is not None and record.plan_revision is not None  # noqa: S101
    assert record.interval_start is not None and record.interval_end is not None  # noqa: S101
    voyage = session.scalars(
        select(ReconVoyage).where(
            ReconVoyage.account_id == account_id,
            ReconVoyage.source_system == record.source_system,
            ReconVoyage.voyage_ref == record.voyage_ref,
        )
    ).one_or_none()
    if voyage is None:
        voyage = ReconVoyage(
            account_id=account_id,
            voyage_ref=record.voyage_ref,
            vessel_ref=record.vessel_ref,
            source_system=record.source_system,
        )
        session.add(voyage)
        session.flush()
    version = ReconVoyageVersion(
        account_id=account_id,
        voyage_id=voyage.id,
        plan_revision=record.plan_revision,
        source_revision=record.source_revision,
        effective_from=record.interval_start.utc,
        effective_to=record.interval_end.utc,
        plan_json=dict(capture.payload_json),
    )
    session.add(version)
    observation_ids = _store_observations(
        session, account_id=account_id, record=record, capture=capture, expected_job_id=None
    )
    session.flush()
    return ImportOutcome(
        status=ImportStatus.ACCEPTED, capture_id=capture.id, observation_ids=observation_ids
    )


def _import_daily_report(
    session: Session,
    *,
    account_id: UUID,
    record: NormalizedRecord,
    capture: ReconSourceCapture,
    now: datetime,
) -> ImportOutcome:
    job = _find_expected_job(session, account_id=account_id, record=record)
    if job is None:
        # A scheduled report reconciles to a job materialized before arrival. A report
        # with no matching expected job is an out-of-contract arrival, kept separate.
        observation_ids = _store_observations(
            session, account_id=account_id, record=record, capture=capture, expected_job_id=None
        )
        session.flush()
        return ImportOutcome(
            status=ImportStatus.OUT_OF_CONTRACT,
            capture_id=capture.id,
            observation_ids=observation_ids,
        )

    observation_ids = _store_observations(
        session, account_id=account_id, record=record, capture=capture, expected_job_id=job.id
    )
    case = session.scalars(
        select(ReconOperationalCase).where(
            ReconOperationalCase.account_id == account_id,
            ReconOperationalCase.expected_job_id == job.id,
        )
    ).one_or_none()
    if case is None:
        case = ReconOperationalCase(
            account_id=account_id,
            expected_job_id=job.id,
            automation_status=AutomationStatus.VALIDATED,
            business_status=BusinessStatus.PENDING,
            revision=1,
            deadline_at=job.due_at,
        )
        session.add(case)
    session.flush()
    return ImportOutcome(
        status=ImportStatus.ACCEPTED,
        capture_id=capture.id,
        expected_job_id=job.id,
        case_id=case.id,
        observation_ids=observation_ids,
    )


def _store_observations(
    session: Session,
    *,
    account_id: UUID,
    record: NormalizedRecord,
    capture: ReconSourceCapture,
    expected_job_id: UUID | None,
) -> tuple[UUID, ...]:
    ids: list[UUID] = []
    for observation in record.observations:
        row = ReconObservation(
            account_id=account_id,
            capture_id=capture.id,
            expected_job_id=expected_job_id,
            field=observation.field,
            item_key=observation.item_key,
            fuel_grade=observation.fuel_grade,
            value=observation.value,
            unit=observation.unit,
            origin=observation.origin,
            period_start=observation.period_start.utc,
            period_end=observation.period_end.utc,
            source_pointer=observation.source_pointer,
            normalization_version=observation.normalization_version,
            recorded_at=record.recorded_at.utc,
        )
        session.add(row)
        session.flush()
        ids.append(row.id)
    return tuple(ids)
