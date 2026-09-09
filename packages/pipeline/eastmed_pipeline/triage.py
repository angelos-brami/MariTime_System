from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from eastmed_schema.enums import (
    Corridor,
    EventStatus,
    EventType,
    TriageAction,
    TriageStatus,
)
from eastmed_schema.models import (
    AuditLog,
    Event,
    SourceRecord,
    TriageDecision,
    TriageItem,
)
from eastmed_shared.gazetteer import detect_gazetteer_signals
from sqlalchemy import select
from sqlalchemy.orm import Session


class TriageWorkflowError(ValueError):
    pass


@dataclass(frozen=True)
class TriageActionInput:
    action: TriageAction
    reviewer: str
    reason: str | None = None
    event_id: UUID | None = None
    title: str | None = None
    event_type: EventType | None = None
    corridor: Corridor | None = None
    severity: int | None = None
    occurred_start: datetime | None = None


@dataclass(frozen=True)
class TriageActionResult:
    item: TriageItem
    decision: TriageDecision
    event: Event | None


def ensure_triage_item(session: Session, record: SourceRecord) -> TriageItem:
    if record.id is None:
        session.flush()
    existing = session.scalar(select(TriageItem).where(TriageItem.source_record_id == record.id))
    if existing is not None:
        return existing

    signals = detect_gazetteer_signals(record.title, record.extracted_text)
    item = TriageItem(
        source_record_id=record.id,
        status=TriageStatus.PENDING,
        detected_corridors=[corridor.value for corridor in signals.corridors],
        detected_ports=list(signals.ports),
        suggested_event_types=[event_type.value for event_type in signals.event_types],
    )
    session.add(item)
    return item


def backfill_triage_items(session: Session, *, limit: int = 500) -> int:
    records = session.scalars(
        select(SourceRecord)
        .outerjoin(TriageItem, TriageItem.source_record_id == SourceRecord.id)
        .where(TriageItem.id.is_(None))
        .order_by(SourceRecord.fetched_at, SourceRecord.id)
        .limit(limit)
    ).all()
    for record in records:
        ensure_triage_item(session, record)
    session.commit()
    return len(records)


def _event_slug(title: str, item_id: UUID) -> str:
    normalized = unicodedata.normalize("NFKD", title)
    ascii_title = normalized.encode("ascii", "ignore").decode("ascii").casefold()
    stem = re.sub(r"[^a-z0-9]+", "-", ascii_title).strip("-")[:220]
    return f"{stem or 'event'}-{str(item_id)[:8]}"


def apply_triage_action(
    session: Session,
    *,
    item_id: UUID,
    request: TriageActionInput,
    now: datetime | None = None,
) -> TriageActionResult:
    if request.reviewer.casefold().startswith("model:"):
        raise TriageWorkflowError("A model cannot make a triage decision")

    item = session.get(TriageItem, item_id, with_for_update=True)
    if item is None:
        raise LookupError("Triage item not found")
    if item.status != TriageStatus.PENDING:
        raise TriageWorkflowError(f"Triage item is already {item.status.value}")
    record = session.get(SourceRecord, item.source_record_id)
    if record is None:
        raise LookupError("Source record not found")
    if (
        record.security_scan.get("quarantined") or record.security_scan.get("injection_suspected")
    ) and request.action != TriageAction.DISMISS:
        raise TriageWorkflowError(
            "Quarantined source records must be dismissed or cleared through the security runbook"
        )

    reviewed_at = now or datetime.now(UTC)
    event: Event | None = None
    event_id: UUID | None = None
    payload: dict[str, str | int | None] = {}

    if request.action == TriageAction.ATTACH:
        if request.event_id is None:
            raise TriageWorkflowError("Attach requires an event_id")
        event = session.get(Event, request.event_id)
        if event is None:
            raise LookupError("Event not found")
        event_id = event.id
        item.status = TriageStatus.ATTACHED
    elif request.action == TriageAction.NEW_EVENT:
        if not all(
            (
                request.title,
                request.event_type,
                request.corridor,
                request.severity is not None,
            )
        ):
            raise TriageWorkflowError(
                "New event requires title, event_type, corridor, and severity"
            )
        assert request.title is not None
        assert request.event_type is not None
        assert request.corridor is not None
        assert request.severity is not None
        if not 1 <= request.severity <= 4:
            raise TriageWorkflowError("Severity must be between 1 and 4")
        event = Event(
            slug=_event_slug(request.title, item.id),
            event_type=request.event_type,
            corridor=request.corridor,
            status=EventStatus.MONITORING,
            severity=request.severity,
            occurred_start=request.occurred_start,
        )
        session.add(event)
        session.flush()
        event_id = event.id
        item.status = TriageStatus.NEW_EVENT
        payload = {
            "title": request.title,
            "event_type": request.event_type.value,
            "corridor": request.corridor.value,
            "severity": request.severity,
            "occurred_start": (
                request.occurred_start.isoformat() if request.occurred_start else None
            ),
        }
    elif request.action == TriageAction.DISMISS:
        if not request.reason or not request.reason.strip():
            raise TriageWorkflowError("Dismiss requires a reason")
        item.status = TriageStatus.DISMISSED
        item.dismissal_reason = request.reason.strip()
    else:
        raise TriageWorkflowError("Unsupported triage action")

    item.assigned_event_id = event_id
    item.reviewed_at = reviewed_at
    item.reviewed_by = request.reviewer
    decision = TriageDecision(
        triage_item_id=item.id,
        action=request.action,
        reviewer=request.reviewer,
        reason=request.reason,
        event_id=event_id,
        payload_json=payload,
        at=reviewed_at,
    )
    session.add(decision)
    session.add(
        AuditLog(
            actor=request.reviewer,
            action=f"triage.{request.action.value}",
            entity="triage_item",
            entity_id=item.id,
            payload_json={
                "source_record_id": str(item.source_record_id),
                "event_id": str(event_id) if event_id else None,
                "reason": request.reason,
                **payload,
            },
        )
    )
    session.commit()
    session.refresh(item)
    session.refresh(decision)
    if event is not None:
        session.refresh(event)
    return TriageActionResult(item=item, decision=decision, event=event)
