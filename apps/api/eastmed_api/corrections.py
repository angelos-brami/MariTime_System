from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from eastmed_schema.enums import (
    AuthAssurance,
    CorrectionImpact,
    CorrectionType,
    DeliveryChannel,
    DeliveryStatus,
    DeskRole,
)
from eastmed_schema.models import (
    Alert,
    AuditLog,
    Correction,
    CorrectionDelivery,
    Delivery,
    DeskApproval,
    DeskUser,
    Event,
    EventVersion,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


class CorrectionWorkflowError(ValueError):
    pass


@dataclass(frozen=True)
class CorrectionRecipient:
    account_id: UUID
    user_id: UUID
    channel: DeliveryChannel
    recipient: str


@dataclass(frozen=True)
class CorrectionPlan:
    event: Event
    version_from: EventVersion
    version_to: EventVersion
    recipients: tuple[CorrectionRecipient, ...]
    channels: tuple[DeliveryChannel, ...]
    preview_hash: str
    message: dict[str, Any]


def _human(value: str, action: str) -> str:
    actor = value.strip()
    if not 2 <= len(actor) <= 255 or actor.casefold().startswith("model:"):
        raise CorrectionWorkflowError(f"a named human must {action} the correction")
    return actor


def _versions(
    session: Session, *, event_id: UUID, version_from_id: UUID, version_to_id: UUID
) -> tuple[Event, EventVersion, EventVersion]:
    event = session.get(Event, event_id)
    if event is None:
        raise LookupError("Event not found")
    version_from = session.get(EventVersion, version_from_id)
    version_to = session.get(EventVersion, version_to_id)
    if version_from is None or version_to is None:
        raise LookupError("Correction version not found")
    if version_from.event_id != event_id or version_to.event_id != event_id:
        raise CorrectionWorkflowError("correction versions must belong to the selected event")
    if version_to.version_no <= version_from.version_no:
        raise CorrectionWorkflowError("corrected version must be newer than the affected version")
    return event, version_from, version_to


def _original_recipients(
    session: Session, *, version_from_id: UUID
) -> tuple[CorrectionRecipient, ...]:
    rows = session.execute(
        select(Delivery)
        .join(Alert, Alert.id == Delivery.alert_id)
        .where(
            Alert.event_version_id == version_from_id,
            Delivery.status == DeliveryStatus.DELIVERED,
        )
    ).scalars()
    unique: dict[tuple[UUID, DeliveryChannel], CorrectionRecipient] = {}
    for delivery in rows:
        key = (delivery.user_id, delivery.channel)
        unique[key] = CorrectionRecipient(
            account_id=delivery.account_id,
            user_id=delivery.user_id,
            channel=delivery.channel,
            recipient=delivery.recipient,
        )
    return tuple(
        sorted(
            unique.values(),
            key=lambda item: (
                str(item.account_id),
                str(item.user_id),
                item.channel.value,
            ),
        )
    )


def build_correction_plan(
    session: Session,
    *,
    event_id: UUID,
    version_from_id: UUID,
    version_to_id: UUID,
    correction_type: CorrectionType,
    impact: CorrectionImpact,
    note: str,
    root_cause: str,
    corrective_action: str,
    detected_at: datetime,
    drafted_by: str,
    signed_off_by: str,
) -> CorrectionPlan:
    drafter = _human(drafted_by, "draft")
    signer = _human(signed_off_by, "sign off")
    if (
        impact == CorrectionImpact.OPERATIONALLY_RELEVANT
        and drafter.casefold() == signer.casefold()
    ):
        raise CorrectionWorkflowError(
            "operationally relevant corrections require a distinct human sign-off"
        )
    if detected_at.tzinfo is None or detected_at.utcoffset() is None:
        raise CorrectionWorkflowError("correction detection time must include a timezone")
    clean_note = note.strip()
    clean_root = root_cause.strip()
    clean_action = corrective_action.strip()
    if not clean_note or not clean_root or not clean_action:
        raise CorrectionWorkflowError("note, root cause, and corrective action are required")
    event, version_from, version_to = _versions(
        session,
        event_id=event_id,
        version_from_id=version_from_id,
        version_to_id=version_to_id,
    )
    recipients = _original_recipients(session, version_from_id=version_from_id)
    channels = tuple(sorted({item.channel for item in recipients}, key=lambda item: item.value))
    message = {
        "label": correction_type.value.upper(),
        "correction_type": correction_type.value,
        "event_id": str(event.id),
        "event_slug": event.slug,
        "title": version_to.title,
        "note": clean_note,
        "affected_version": {
            "id": str(version_from.id),
            "number": version_from.version_no,
            "content_hash": version_from.content_hash,
        },
        "corrected_version": {
            "id": str(version_to.id),
            "number": version_to.version_no,
            "content_hash": version_to.content_hash,
        },
    }
    canonical = {
        "message": message,
        "impact": impact.value,
        "detected_at": detected_at.isoformat(),
        "drafted_by": drafter,
        "signed_off_by": signer,
        "root_cause": clean_root,
        "corrective_action": clean_action,
        "recipients": [
            {
                "account_id": str(item.account_id),
                "user_id": str(item.user_id),
                "channel": item.channel.value,
                "recipient": item.recipient,
            }
            for item in recipients
        ],
        "portal": True,
    }
    preview_hash = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return CorrectionPlan(
        event=event,
        version_from=version_from,
        version_to=version_to,
        recipients=recipients,
        channels=channels,
        preview_hash=preview_hash,
        message=message,
    )


def issue_correction(
    session: Session,
    *,
    preview_hash: str,
    event_id: UUID,
    version_from_id: UUID,
    version_to_id: UUID,
    correction_type: CorrectionType,
    impact: CorrectionImpact,
    note: str,
    root_cause: str,
    corrective_action: str,
    detected_at: datetime,
    drafted_by: str,
    signed_off_by: str,
    correction_approval_id: UUID | None = None,
    released_by: str | None = None,
    now: datetime | None = None,
) -> Correction:
    plan = build_correction_plan(
        session,
        event_id=event_id,
        version_from_id=version_from_id,
        version_to_id=version_to_id,
        correction_type=correction_type,
        impact=impact,
        note=note,
        root_cause=root_cause,
        corrective_action=corrective_action,
        detected_at=detected_at,
        drafted_by=drafted_by,
        signed_off_by=signed_off_by,
    )
    if preview_hash != plan.preview_hash:
        raise CorrectionWorkflowError("correction preview is stale or does not match the release")
    if impact == CorrectionImpact.OPERATIONALLY_RELEVANT:
        if correction_approval_id is None:
            raise CorrectionWorkflowError(
                "Operational correction requires a content-bound approval record"
            )
        approval = session.get(DeskApproval, correction_approval_id)
        primary = (
            session.get(DeskUser, approval.primary_user_id) if approval is not None else None
        )
        approver = (
            session.get(DeskUser, approval.approved_by_user_id) if approval is not None else None
        )
        expires_at = approval.expires_at if approval is not None else None
        if expires_at is not None and (
            expires_at.tzinfo is None or expires_at.utcoffset() is None
        ):
            expires_at = expires_at.replace(tzinfo=UTC)
        if (
            approval is None
            or approval.approval_type != "operational_correction"
            or approval.target_id != event_id
            or approval.binding_hash != preview_hash
            or drafted_by != f"desk:{approval.primary_user_id}"
            or signed_off_by != f"desk:{approval.approved_by_user_id}"
            or approval.primary_user_id == approval.approved_by_user_id
            or (released_by or drafted_by) != drafted_by
            or primary is None
            or not primary.active
            or primary.role
            not in {DeskRole.ANALYST, DeskRole.SENIOR_ANALYST, DeskRole.ADMINISTRATOR}
            or approver is None
            or not approver.active
            or approver.role not in {DeskRole.SENIOR_ANALYST, DeskRole.ADMINISTRATOR}
            or approval.auth_assurance
            not in {AuthAssurance.MFA, AuthAssurance.PHISHING_RESISTANT}
            or expires_at is None
            or expires_at <= datetime.now(UTC)
        ):
            raise CorrectionWorkflowError(
                "Correction approval is missing, expired, or not bound to this exact release"
            )
    elif correction_approval_id is not None:
        raise CorrectionWorkflowError(
            "Non-operational corrections must not attach an unrelated approval record"
        )
    existing = session.scalar(select(Correction).where(Correction.preview_hash == preview_hash))
    if existing is not None:
        return existing
    issued_at = now or datetime.now(UTC)
    if detected_at > issued_at:
        raise CorrectionWorkflowError("correction detection time cannot be in the future")
    channel_snapshot = {
        "portal": True,
        "channels": [channel.value for channel in plan.channels],
        "recipient_count": len(plan.recipients),
        "recipients": [
            {
                "account_id": str(item.account_id),
                "user_id": str(item.user_id),
                "channel": item.channel.value,
                "recipient": item.recipient,
            }
            for item in plan.recipients
        ],
    }
    correction = Correction(
        event_id=event_id,
        version_from_id=plan.version_from.id,
        version_to_id=plan.version_to.id,
        version_from=plan.version_from.version_no,
        version_to=plan.version_to.version_no,
        correction_type=correction_type,
        note=note.strip(),
        propagated_channels_json=channel_snapshot,
        impact=impact,
        root_cause=root_cause.strip(),
        corrective_action=corrective_action.strip(),
        drafted_by=drafted_by.strip(),
        signed_off_by=signed_off_by.strip(),
        correction_approval_id=correction_approval_id,
        preview_hash=preview_hash,
        propagation_deadline_at=detected_at + timedelta(minutes=60),
        detected_at=detected_at,
        issued_at=issued_at,
        issued_by=(released_by or signed_off_by).strip(),
    )
    session.add(correction)
    session.flush()
    for recipient in plan.recipients:
        session.add(
            CorrectionDelivery(
                correction_id=correction.id,
                account_id=recipient.account_id,
                user_id=recipient.user_id,
                channel=recipient.channel,
                recipient=recipient.recipient,
                status=DeliveryStatus.QUEUED,
                queued_at=issued_at,
                attempt_count=0,
                message_snapshot_json=plan.message,
            )
        )
    session.add(
        AuditLog(
            actor=correction.issued_by,
            action="correction.issued",
            entity="correction",
            entity_id=correction.id,
            payload_json={
                "event_id": str(event_id),
                "version_from_id": str(version_from_id),
                "version_to_id": str(version_to_id),
                "type": correction_type.value,
                "impact": impact.value,
                "preview_hash": preview_hash,
                "correction_approval_id": (
                    str(correction_approval_id) if correction_approval_id else None
                ),
                "drafted_by": correction.drafted_by,
                "signed_off_by": correction.signed_off_by,
                "released_by": correction.issued_by,
                "recipient_count": len(plan.recipients),
                "channels": [channel.value for channel in plan.channels],
                "issued_within_60_minutes": issued_at <= detected_at + timedelta(minutes=60),
            },
        )
    )
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raced = session.scalar(select(Correction).where(Correction.preview_hash == preview_hash))
        if raced is not None:
            return raced
        raise
    session.refresh(correction)
    return correction
