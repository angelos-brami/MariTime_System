from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, time
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from eastmed_schema.enums import BriefStatus, Corridor, EventType
from eastmed_schema.models import AuditLog, Correction, DailyBrief, Event, EventVersion
from sqlalchemy import select
from sqlalchemy.orm import Session

from eastmed_api.contracts import (
    BriefFinalizeCreate,
    DailyBriefCorrectionRead,
    DailyBriefItemRead,
    DailyBriefRead,
)

ATHENS = ZoneInfo("Europe/Athens")


class BriefWorkflowError(ValueError):
    pass


def _human(actor: str, action: str) -> str:
    normalized = actor.strip()
    if not normalized or normalized.casefold().startswith("model:"):
        raise BriefWorkflowError(f"a human analyst must {action} the daily brief")
    return normalized


def _content_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _brief_item(version: EventVersion) -> dict[str, Any]:
    snapshot = version.event_snapshot_json or {}
    return {
        "event_id": str(snapshot.get("id") or version.event_id),
        "event_slug": str(snapshot.get("slug") or version.event_id),
        "event_version_id": str(version.id),
        "version_no": version.version_no,
        "title": version.title,
        "event_type": str(snapshot.get("event_type") or EventType.NAVIGATION_WARNING.value),
        "corridor": str(snapshot.get("corridor") or Corridor.PORT_SPECIFIC.value),
        "status": str(snapshot.get("status") or "monitoring"),
        "severity": max(1, min(int(snapshot.get("severity") or 1), 4)),
        "published_at": version.published_at.isoformat(),
        "summary_confirmed": version.summary_confirmed,
        "summary_reported": version.summary_reported,
        "summary_unknown": version.summary_unknown,
        "whats_changed": version.whats_changed,
        "content_hash": version.content_hash,
    }


def _hash_payload(brief: DailyBrief) -> dict[str, Any]:
    return {
        "brief_date": brief.brief_date.isoformat(),
        "title": brief.title,
        "introduction": brief.introduction,
        "forward_watch": brief.forward_watch,
        "status": brief.status.value,
        "items": brief.items_json,
        "source_version_ids": brief.source_version_ids,
        "corrections": brief.corrections_json,
    }


def brief_read(brief: DailyBrief) -> DailyBriefRead:
    return DailyBriefRead(
        id=brief.id,
        brief_date=brief.brief_date,
        title=brief.title,
        introduction=brief.introduction,
        forward_watch=brief.forward_watch,
        status=brief.status,
        items=[DailyBriefItemRead.model_validate(item) for item in brief.items_json],
        source_version_ids=[UUID(value) for value in brief.source_version_ids],
        corrections=[
            DailyBriefCorrectionRead.model_validate(item) for item in brief.corrections_json
        ],
        compiled_at=brief.compiled_at,
        compiled_by=brief.compiled_by,
        finalized_at=brief.finalized_at,
        finalized_by=brief.finalized_by,
        content_hash=brief.content_hash,
    )


def compile_daily_brief(
    session: Session,
    *,
    brief_date: date,
    compiled_by: str,
) -> DailyBriefRead:
    actor = _human(compiled_by, "compile")
    local_start = datetime.combine(brief_date, time.min, tzinfo=ATHENS)
    local_end = datetime.combine(brief_date, time.max, tzinfo=ATHENS)
    versions = list(
        session.scalars(
            select(EventVersion)
            .where(
                EventVersion.published_at >= local_start.astimezone(UTC),
                EventVersion.published_at <= local_end.astimezone(UTC),
            )
            .order_by(EventVersion.published_at, EventVersion.event_id, EventVersion.version_no)
        ).all()
    )
    items = [_brief_item(version) for version in versions]
    source_ids = [str(version.id) for version in versions]
    previous_brief = session.scalar(
        select(DailyBrief)
        .where(
            DailyBrief.status == BriefStatus.FINALIZED,
            DailyBrief.brief_date < brief_date,
        )
        .order_by(DailyBrief.brief_date.desc())
        .limit(1)
    )
    correction_start = (
        previous_brief.finalized_at
        if previous_brief and previous_brief.finalized_at
        else local_start.astimezone(UTC)
    )
    correction_rows = session.execute(
        select(Correction, Event)
        .join(Event, Event.id == Correction.event_id)
        .where(
            Correction.issued_at.is_not(None),
            Correction.issued_at > correction_start,
            Correction.issued_at <= local_end.astimezone(UTC),
        )
        .order_by(Correction.issued_at)
    ).all()
    corrections: list[dict[str, Any]] = []
    for correction, event in correction_rows:
        affected_version = session.get(EventVersion, correction.version_from_id)
        corrected_version = session.get(EventVersion, correction.version_to_id)
        if affected_version is None or corrected_version is None or correction.issued_at is None:
            continue
        corrections.append(
            {
                "id": str(correction.id),
                "event_id": str(event.id),
                "event_slug": event.slug,
                "correction_type": correction.correction_type.value,
                "note": correction.note,
                "version_from": correction.version_from,
                "version_to": correction.version_to,
                "affected_version_hash": affected_version.content_hash,
                "corrected_version_hash": corrected_version.content_hash,
                "issued_at": correction.issued_at.isoformat(),
            }
        )
    now = datetime.now(UTC)
    brief = session.scalar(
        select(DailyBrief).where(DailyBrief.brief_date == brief_date).with_for_update()
    )
    if brief is not None and brief.status == BriefStatus.FINALIZED:
        raise BriefWorkflowError("a finalized daily brief cannot be recompiled")
    if brief is None:
        brief = DailyBrief(
            brief_date=brief_date,
            title=f"East Med Corridor Watch — {brief_date:%d %B %Y}",
            introduction="",
            forward_watch="",
            status=BriefStatus.DRAFT,
            items_json=items,
            source_version_ids=source_ids,
            corrections_json=corrections,
            compiled_at=now,
            compiled_by=actor,
            finalized_at=None,
            finalized_by=None,
            content_hash="",
        )
        session.add(brief)
    else:
        brief.items_json = items
        brief.source_version_ids = source_ids
        brief.corrections_json = corrections
        brief.compiled_at = now
        brief.compiled_by = actor
    brief.content_hash = _content_hash(_hash_payload(brief))
    session.flush()
    session.add(
        AuditLog(
            actor=actor,
            action="daily_brief.compiled",
            entity="daily_brief",
            entity_id=brief.id,
            payload_json={
                "brief_date": brief_date.isoformat(),
                "event_version_ids": source_ids,
                "content_hash": brief.content_hash,
            },
        )
    )
    session.commit()
    session.refresh(brief)
    return brief_read(brief)


def finalize_daily_brief(
    session: Session,
    *,
    brief_id: UUID,
    payload: BriefFinalizeCreate,
) -> DailyBriefRead:
    actor = _human(payload.finalized_by, "finalize")
    brief = session.scalar(select(DailyBrief).where(DailyBrief.id == brief_id).with_for_update())
    if brief is None:
        raise LookupError("Daily brief not found")
    if brief.status == BriefStatus.FINALIZED:
        raise BriefWorkflowError("daily brief is already finalized")

    compiled_items = {UUID(str(item["event_version_id"])): item for item in brief.items_json}
    requested_ids = payload.item_version_ids
    if any(version_id not in compiled_items for version_id in requested_ids):
        raise BriefWorkflowError("final brief references a version outside its compiled draft")
    brief.title = payload.title.strip()
    brief.introduction = payload.introduction.strip()
    brief.forward_watch = payload.forward_watch.strip()
    brief.items_json = [compiled_items[version_id] for version_id in requested_ids]
    brief.source_version_ids = [str(version_id) for version_id in requested_ids]
    brief.status = BriefStatus.FINALIZED
    brief.finalized_at = datetime.now(UTC)
    brief.finalized_by = actor
    brief.content_hash = _content_hash(_hash_payload(brief))
    session.add(
        AuditLog(
            actor=actor,
            action="daily_brief.finalized",
            entity="daily_brief",
            entity_id=brief.id,
            payload_json={
                "brief_date": brief.brief_date.isoformat(),
                "event_version_ids": brief.source_version_ids,
                "content_hash": brief.content_hash,
            },
        )
    )
    session.commit()
    session.refresh(brief)
    return brief_read(brief)


def latest_finalized_brief(session: Session) -> DailyBriefRead:
    brief = session.scalar(
        select(DailyBrief)
        .where(DailyBrief.status == BriefStatus.FINALIZED)
        .order_by(DailyBrief.brief_date.desc())
        .limit(1)
    )
    if brief is None:
        raise LookupError("No finalized daily brief is available")
    return brief_read(brief)


def daily_brief_by_date(session: Session, *, brief_date: date) -> DailyBriefRead:
    brief = session.scalar(select(DailyBrief).where(DailyBrief.brief_date == brief_date))
    if brief is None:
        raise LookupError("Daily brief not found")
    return brief_read(brief)
