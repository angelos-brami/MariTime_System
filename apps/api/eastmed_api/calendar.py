from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID

from eastmed_pipeline.rights import RightsPolicyError, authorize_automation
from eastmed_schema.enums import SourceTier
from eastmed_schema.models import (
    AuditLog,
    MaritimeCalendarEvent,
    MaritimeCalendarEventVersion,
    Source,
    SourceRecord,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from eastmed_api.contracts import (
    CalendarEventListRead,
    CalendarEventPublishCreate,
    CalendarEventRead,
)


class CalendarWorkflowError(ValueError):
    pass


def _aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise CalendarWorkflowError(f"{label} must include a timezone")


def _read(event: MaritimeCalendarEvent, version: MaritimeCalendarEventVersion) -> CalendarEventRead:
    return CalendarEventRead(
        id=version.id,
        calendar_event_id=event.id,
        slug=event.slug,
        version_no=version.version_no,
        event_type=version.event_type,
        title=version.title,
        corridor=version.corridor,
        ports=version.ports_json,
        starts_at=version.starts_at,
        ends_at=version.ends_at,
        status=version.status,
        public_note=version.public_note,
        source_record_ids=[UUID(value) for value in version.source_record_ids],
        published_at=version.published_at,
        published_by=version.published_by,
        content_hash=version.content_hash,
    )


def publish_calendar_event(
    session: Session, payload: CalendarEventPublishCreate
) -> CalendarEventRead:
    _aware(payload.starts_at, "calendar event start")
    if payload.ends_at is not None:
        _aware(payload.ends_at, "calendar event end")
    rows = session.execute(
        select(SourceRecord, Source)
        .join(Source, Source.id == SourceRecord.source_id)
        .where(SourceRecord.id.in_(payload.source_record_ids))
    ).all()
    if len(rows) != len(payload.source_record_ids):
        raise CalendarWorkflowError("every calendar source record must exist")
    if any(source.tier == SourceTier.E for _, source in rows):
        raise CalendarWorkflowError("Tier E records cannot support a published calendar item")
    try:
        for _, source in rows:
            authorize_automation(source)
    except RightsPolicyError as exc:
        raise CalendarWorkflowError(str(exc)) from exc

    if payload.calendar_event_id is None:
        if (
            session.scalar(
                select(MaritimeCalendarEvent.id).where(MaritimeCalendarEvent.slug == payload.slug)
            )
            is not None
        ):
            raise CalendarWorkflowError("calendar slug already exists")
        event = MaritimeCalendarEvent(
            slug=payload.slug,
            created_at=datetime.now(UTC),
            created_by=payload.published_by.strip(),
        )
        session.add(event)
        session.flush()
        version_no = 1
    else:
        existing_event = session.scalar(
            select(MaritimeCalendarEvent)
            .where(MaritimeCalendarEvent.id == payload.calendar_event_id)
            .with_for_update()
        )
        if existing_event is None:
            raise LookupError("Calendar event not found")
        event = existing_event
        if event.slug != payload.slug:
            raise CalendarWorkflowError("calendar event slug is immutable")
        latest_no = session.scalar(
            select(func.max(MaritimeCalendarEventVersion.version_no)).where(
                MaritimeCalendarEventVersion.calendar_event_id == event.id
            )
        )
        version_no = int(latest_no or 0) + 1

    published_at = datetime.now(UTC)
    source_ids = sorted(str(value) for value in payload.source_record_ids)
    canonical = {
        "calendar_event_id": str(event.id),
        "slug": event.slug,
        "version_no": version_no,
        "event_type": payload.event_type.value,
        "title": payload.title.strip(),
        "corridor": payload.corridor.value,
        "ports": payload.ports,
        "starts_at": payload.starts_at.isoformat(),
        "ends_at": payload.ends_at.isoformat() if payload.ends_at else None,
        "status": payload.status,
        "public_note": payload.public_note.strip(),
        "source_record_ids": source_ids,
        "published_at": published_at.isoformat(),
        "published_by": payload.published_by.strip(),
    }
    content_hash = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    version = MaritimeCalendarEventVersion(
        calendar_event_id=event.id,
        version_no=version_no,
        event_type=payload.event_type,
        title=payload.title.strip(),
        corridor=payload.corridor,
        ports_json=payload.ports,
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
        status=payload.status,
        public_note=payload.public_note.strip(),
        source_record_ids=source_ids,
        published_at=published_at,
        published_by=payload.published_by.strip(),
        content_hash=content_hash,
    )
    session.add(version)
    session.flush()
    session.add(
        AuditLog(
            actor=version.published_by,
            action="maritime_calendar_event.published",
            entity="maritime_calendar_event_version",
            entity_id=version.id,
            payload_json={
                "calendar_event_id": str(event.id),
                "version_no": version_no,
                "content_hash": content_hash,
                "status": version.status,
            },
        )
    )
    session.commit()
    session.refresh(version)
    return _read(event, version)


def list_calendar_events(
    session: Session,
    *,
    starts_before: datetime | None = None,
    ends_after: datetime | None = None,
) -> CalendarEventListRead:
    latest = (
        select(
            MaritimeCalendarEventVersion.calendar_event_id,
            func.max(MaritimeCalendarEventVersion.version_no).label("version_no"),
        )
        .group_by(MaritimeCalendarEventVersion.calendar_event_id)
        .subquery()
    )
    query = (
        select(MaritimeCalendarEvent, MaritimeCalendarEventVersion)
        .join(latest, latest.c.calendar_event_id == MaritimeCalendarEvent.id)
        .join(
            MaritimeCalendarEventVersion,
            (MaritimeCalendarEventVersion.calendar_event_id == MaritimeCalendarEvent.id)
            & (MaritimeCalendarEventVersion.version_no == latest.c.version_no),
        )
    )
    if starts_before is not None:
        _aware(starts_before, "calendar upper bound")
        query = query.where(MaritimeCalendarEventVersion.starts_at <= starts_before)
    if ends_after is not None:
        _aware(ends_after, "calendar lower bound")
        query = query.where(
            (MaritimeCalendarEventVersion.ends_at.is_(None))
            | (MaritimeCalendarEventVersion.ends_at >= ends_after)
        )
    rows = session.execute(
        query.order_by(MaritimeCalendarEventVersion.starts_at, MaritimeCalendarEvent.slug)
    ).all()
    return CalendarEventListRead(
        generated_at=datetime.now(UTC),
        results=[_read(event, version) for event, version in rows],
    )
