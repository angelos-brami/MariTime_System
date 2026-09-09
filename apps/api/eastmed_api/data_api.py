from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from eastmed_schema.enums import ClaimState, Corridor, EventStatus, EventType
from eastmed_schema.models import EventVersion, event_version_claims
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.selectable import Subquery

from eastmed_api.contracts import (
    DataClaimRead,
    DataEventListRead,
    DataEventRead,
    DataVersionRead,
)


def _snapshot_datetime(value: object, *, fallback: datetime | None = None) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return fallback
    return fallback


def _event_read(version: EventVersion) -> DataEventRead:
    snapshot = version.event_snapshot_json
    return DataEventRead(
        id=version.event_id,
        slug=str(snapshot["slug"]),
        event_type=EventType(str(snapshot["event_type"])),
        corridor=Corridor(str(snapshot["corridor"])),
        status=str(snapshot["status"]),
        severity=int(snapshot["severity"]),
        occurred_start=_snapshot_datetime(snapshot.get("occurred_start")),
        occurred_end=_snapshot_datetime(snapshot.get("occurred_end")),
        latest_version_id=version.id,
        latest_version_no=version.version_no,
        title=version.title,
        published_at=version.published_at,
        content_hash=version.content_hash,
    )


def _latest_join() -> Subquery:
    return (
        select(EventVersion.event_id, func.max(EventVersion.version_no).label("version_no"))
        .group_by(EventVersion.event_id)
        .subquery()
    )


def list_data_events(
    session: Session,
    *,
    corridor: Corridor | None = None,
    event_type: EventType | None = None,
    event_status: EventStatus | None = None,
    limit: int = 100,
    offset: int = 0,
) -> DataEventListRead:
    conditions = []
    if corridor is not None:
        conditions.append(
            EventVersion.event_snapshot_json["corridor"].as_string() == corridor.value
        )
    if event_type is not None:
        conditions.append(
            EventVersion.event_snapshot_json["event_type"].as_string() == event_type.value
        )
    if event_status is not None:
        conditions.append(
            EventVersion.event_snapshot_json["status"].as_string() == event_status.value
        )
    latest = _latest_join()
    latest_versions = (
        select(EventVersion)
        .join(
            latest,
            (EventVersion.event_id == latest.c.event_id)
            & (EventVersion.version_no == latest.c.version_no),
        )
        .where(*conditions)
    )
    total = int(session.scalar(select(func.count()).select_from(latest_versions.subquery())) or 0)
    versions = session.scalars(
        latest_versions.order_by(EventVersion.published_at.desc()).limit(limit).offset(offset)
    ).all()
    return DataEventListRead(
        total=total,
        limit=limit,
        offset=offset,
        results=[_event_read(version) for version in versions],
    )


def get_data_event(session: Session, *, event_id: UUID) -> DataEventRead:
    version = session.scalar(
        select(EventVersion)
        .where(EventVersion.event_id == event_id)
        .order_by(EventVersion.version_no.desc())
        .limit(1)
    )
    if version is None:
        raise LookupError("Published event not found")
    return _event_read(version)


def _version_read(version: EventVersion) -> DataVersionRead:
    return DataVersionRead(
        id=version.id,
        event_id=version.event_id,
        version_no=version.version_no,
        title=version.title,
        summary_confirmed=version.summary_confirmed,
        summary_reported=version.summary_reported,
        summary_unknown=version.summary_unknown,
        whats_changed=version.whats_changed,
        sentence_claim_map=version.sentence_claim_map,
        published_at=version.published_at,
        policy_version=version.policy_version,
        content_hash=version.content_hash,
    )


def list_data_versions(session: Session, *, event_id: UUID) -> list[DataVersionRead]:
    versions = [
        _version_read(version)
        for version in session.scalars(
            select(EventVersion)
            .where(EventVersion.event_id == event_id)
            .order_by(EventVersion.version_no)
        ).all()
    ]
    if not versions:
        raise LookupError("Published event not found")
    return versions


def get_data_version(session: Session, *, version_id: UUID) -> DataVersionRead:
    version = session.get(EventVersion, version_id)
    if version is None:
        raise LookupError("Event version not found")
    return _version_read(version)


def _claim_read(
    snapshot: dict[str, Any], *, event_id: UUID, published_at: datetime
) -> DataClaimRead:
    return DataClaimRead(
        id=UUID(str(snapshot["id"])),
        event_id=event_id,
        text=str(snapshot["text"]),
        claimant=str(snapshot["claimant"]) if snapshot.get("claimant") is not None else None,
        claim_state=ClaimState(str(snapshot["state"])),
        occurred_at=_snapshot_datetime(snapshot.get("occurred_at")),
        quantity_json=list(snapshot.get("quantity") or []),
        reviewed_at=_snapshot_datetime(snapshot.get("reviewed_at")),
        # Publications created before first_seen_at entered the snapshot use the
        # immutable publication time as a conservative compatibility fallback.
        first_seen_at=_snapshot_datetime(snapshot.get("first_seen_at"), fallback=published_at)
        or published_at,
    )


def list_data_claims(session: Session, *, event_id: UUID) -> list[DataClaimRead]:
    version = session.scalar(
        select(EventVersion)
        .where(EventVersion.event_id == event_id)
        .order_by(EventVersion.version_no.desc())
        .limit(1)
    )
    if version is None:
        raise LookupError("Published event not found")
    return [
        _claim_read(snapshot, event_id=event_id, published_at=version.published_at)
        for snapshot in sorted(
            version.claim_snapshot_json,
            key=lambda item: str(item.get("first_seen_at") or item.get("id")),
        )
    ]


def get_data_claim(session: Session, *, claim_id: UUID) -> DataClaimRead:
    version = session.scalar(
        select(EventVersion)
        .join(
            event_version_claims,
            event_version_claims.c.event_version_id == EventVersion.id,
        )
        .where(event_version_claims.c.claim_id == claim_id)
        .order_by(EventVersion.published_at.desc(), EventVersion.version_no.desc())
        .limit(1)
    )
    if version is None:
        raise LookupError("Published claim not found")
    snapshot = next(
        (item for item in version.claim_snapshot_json if str(item.get("id")) == str(claim_id)),
        None,
    )
    if snapshot is None:
        raise LookupError("Published claim snapshot not found")
    return _claim_read(snapshot, event_id=version.event_id, published_at=version.published_at)
