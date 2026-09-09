from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from eastmed_schema.enums import DeliveryStatus
from eastmed_schema.models import (
    AuditLog,
    CorrectionDelivery,
    Delivery,
    WhatsAppWebhookReceipt,
)
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from eastmed_api.contracts import WhatsAppWebhookRead


class WhatsAppWebhookError(ValueError):
    pass


def _event_time(value: object) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(str(value)), tz=UTC)
    except (ValueError, TypeError, OSError):
        return None


def _status_events(payload: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    entries = payload.get("entry")
    if not isinstance(entries, list):
        return events
    for entry in entries[:100]:
        if not isinstance(entry, dict):
            continue
        changes = entry.get("changes")
        if not isinstance(changes, list):
            continue
        for change in changes[:100]:
            if not isinstance(change, dict):
                continue
            value = change.get("value")
            statuses = value.get("statuses") if isinstance(value, dict) else None
            if not isinstance(statuses, list):
                continue
            for status in statuses[:1000]:
                if not isinstance(status, dict):
                    continue
                provider_ref = str(status.get("id") or "").strip()
                provider_status = str(status.get("status") or "").strip().casefold()
                if not provider_ref or provider_status not in {
                    "sent",
                    "delivered",
                    "read",
                    "failed",
                }:
                    continue
                errors: list[dict[str, object]] = []
                raw_errors = status.get("errors")
                if isinstance(raw_errors, list):
                    for error in raw_errors[:10]:
                        if isinstance(error, dict):
                            errors.append(
                                {
                                    "code": error.get("code"),
                                    "title": str(error.get("title") or "")[:500],
                                }
                            )
                events.append(
                    {
                        "provider_ref": provider_ref[:255],
                        "status": provider_status,
                        "timestamp": str(status.get("timestamp") or "")[:32],
                        "errors": errors,
                    }
                )
                if len(events) >= 1000:
                    return events
    return events


def _event_hash(event: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(event, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _apply_status(
    delivery: Delivery | CorrectionDelivery,
    *,
    provider_status: str,
    event_at: datetime | None,
    received_at: datetime,
    errors: list[dict[str, object]],
) -> bool:
    if delivery.status in {DeliveryStatus.DELIVERED, DeliveryStatus.FAILED}:
        return False
    timestamp = event_at or received_at
    if provider_status == "sent":
        if delivery.status != DeliveryStatus.QUEUED:
            return False
        delivery.status = DeliveryStatus.SENT
        delivery.sent_at = timestamp
    elif provider_status in {"delivered", "read"}:
        delivery.status = DeliveryStatus.DELIVERED
        delivery.sent_at = delivery.sent_at or timestamp
        delivery.delivered_at = (
            timestamp if delivery.sent_at is None or timestamp >= delivery.sent_at else received_at
        )
        delivery.last_error = None
        delivery.next_attempt_at = None
    elif provider_status == "failed":
        delivery.status = DeliveryStatus.FAILED
        delivery.last_error = f"WhatsApp delivery failed: {json.dumps(errors)[:1000]}"
        delivery.next_attempt_at = None
    else:
        return False
    delivery.updated_at = received_at
    return True


def process_whatsapp_webhook(
    session: Session,
    *,
    payload: dict[str, Any],
    now: datetime | None = None,
) -> WhatsAppWebhookRead:
    received_at = now or datetime.now(UTC)
    events = _status_events(payload)
    accepted = 0
    unmatched = 0
    unique_events: dict[str, dict[str, Any]] = {}
    for event in events:
        event_hash = _event_hash(event)
        unique_events.setdefault(event_hash, event)
    duplicates = len(events) - len(unique_events)
    rows = []
    for event_hash, event in unique_events.items():
        provider_ref = str(event["provider_ref"])
        provider_status = str(event["status"])
        event_at = _event_time(event.get("timestamp"))
        rows.append(
            {
                "event_hash": event_hash,
                "provider_ref": provider_ref,
                "provider_status": provider_status,
                "event_at": event_at,
                "received_at": received_at,
                "payload_json": event,
            }
        )
    inserted_hashes: set[str] = set()
    if rows:
        inserted_hashes = set(
            session.scalars(
                insert(WhatsAppWebhookReceipt)
                .values(rows)
                .on_conflict_do_nothing(constraint="uq_whatsapp_webhook_receipts_event_hash")
                .returning(WhatsAppWebhookReceipt.event_hash)
            ).all()
        )
    duplicates += len(unique_events) - len(inserted_hashes)
    new_events = [
        event for event_hash, event in unique_events.items() if event_hash in inserted_hashes
    ]
    provider_refs = {str(event["provider_ref"]) for event in new_events}
    delivery_by_ref = {
        row.provider_ref: row
        for row in session.scalars(
            select(Delivery).where(Delivery.provider_ref.in_(provider_refs))
        ).all()
        if row.provider_ref is not None
    }
    missing_refs = provider_refs - delivery_by_ref.keys()
    correction_by_ref = {
        row.provider_ref: row
        for row in session.scalars(
            select(CorrectionDelivery).where(CorrectionDelivery.provider_ref.in_(missing_refs))
        ).all()
        if row.provider_ref is not None
    }
    for event in new_events:
        provider_ref = str(event["provider_ref"])
        provider_status = str(event["status"])
        event_at = _event_time(event.get("timestamp"))
        errors = event["errors"] if isinstance(event["errors"], list) else []
        target = delivery_by_ref.get(provider_ref) or correction_by_ref.get(provider_ref)
        if target is None:
            unmatched += 1
            continue
        if _apply_status(
            target,
            provider_status=provider_status,
            event_at=event_at,
            received_at=received_at,
            errors=errors,
        ):
            accepted += 1
            session.add(
                AuditLog(
                    actor="webhook:360dialog",
                    action="delivery.whatsapp_status",
                    entity=("delivery" if isinstance(target, Delivery) else "correction_delivery"),
                    entity_id=target.id,
                    payload_json={
                        "provider_ref": provider_ref,
                        "provider_status": provider_status,
                        "event_at": event_at.isoformat() if event_at else None,
                    },
                )
            )
    session.commit()
    return WhatsAppWebhookRead(
        accepted=accepted,
        duplicates=duplicates,
        unmatched=unmatched,
    )
