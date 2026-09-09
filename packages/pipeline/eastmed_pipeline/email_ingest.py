from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import quote
from uuid import UUID

from eastmed_api.contracts import PostmarkInboundMessage
from eastmed_schema.enums import AccessMethod, DeskAlertKind, DeskAlertStatus
from eastmed_schema.models import (
    AuditLog,
    DeskAlert,
    ExternalRecordRef,
    Source,
    SourceRecord,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from eastmed_pipeline.rights import authorize_automation
from eastmed_pipeline.security_scan import scan_untrusted_text
from eastmed_pipeline.snapshots import FileSnapshotStore, SnapshotStore
from eastmed_pipeline.triage import ensure_triage_item
from eastmed_pipeline.watcher import DocumentExtractionError, extract_html, extract_pdf

PARSER_VERSION = "postmark-inbound-v1"
ATTACHMENT_PARSER_VERSION = "postmark-pdf-v1"


@dataclass(frozen=True)
class InboundImportResult:
    source_record: SourceRecord
    created: bool
    attachment_record_ids: tuple[UUID, ...]


def _message_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return parsed.replace(tzinfo=parsed.tzinfo or UTC).astimezone(UTC)


def _message_text(payload: PostmarkInboundMessage) -> str:
    for candidate in (payload.stripped_text_reply, payload.text_body):
        normalized = candidate.strip()
        if normalized:
            return normalized
    if payload.html_body.strip():
        try:
            return extract_html(payload.html_body.encode("utf-8"))[1]
        except DocumentExtractionError:
            return payload.html_body.strip()
    return (payload.raw_email or "").strip()


def _is_pdf_attachment(name: str, content_type: str) -> bool:
    normalized_type = content_type.lower().split(";", 1)[0].strip()
    return normalized_type == "application/pdf" or name.lower().endswith(".pdf")


def _existing_attachment_ids(
    session: Session, *, mailbox_hash: str, message_id: str
) -> tuple[UUID, ...]:
    return tuple(
        session.scalars(
            select(ExternalRecordRef.entity_id)
            .where(
                ExternalRecordRef.provider == "postmark",
                ExternalRecordRef.container_id == mailbox_hash,
                ExternalRecordRef.table_name == "attachment",
                ExternalRecordRef.external_id.startswith(f"{message_id}:"),
            )
            .order_by(ExternalRecordRef.external_id)
        ).all()
    )


def _ingest_pdf_attachments(
    session: Session,
    *,
    source: Source,
    parent: SourceRecord,
    payload: PostmarkInboundMessage,
    rights_payload: dict[str, str | bool],
    snapshot_store: SnapshotStore,
) -> tuple[UUID, ...]:
    attachment_ids: list[UUID] = []
    for index, attachment in enumerate(payload.attachments):
        if not _is_pdf_attachment(attachment.name, attachment.content_type):
            continue
        external_id = f"{payload.message_id}:{index}"
        existing_ref = session.scalar(
            select(ExternalRecordRef).where(
                ExternalRecordRef.provider == "postmark",
                ExternalRecordRef.container_id == payload.mailbox_hash,
                ExternalRecordRef.table_name == "attachment",
                ExternalRecordRef.external_id == external_id,
            )
        )
        if existing_ref:
            attachment_ids.append(existing_ref.entity_id)
            continue

        raw = base64.b64decode(attachment.content, validate=True)
        content_hash = hashlib.sha256(raw).hexdigest()
        record = session.scalar(
            select(SourceRecord).where(
                SourceRecord.source_id == source.id,
                SourceRecord.content_hash == content_hash,
            )
        )
        created = record is None
        extraction_error: str | None = None
        if record is None:
            snapshot_ref = snapshot_store.put(source_id=str(source.id), payload=raw, suffix="pdf")
            try:
                extracted_text = extract_pdf(raw)
            except Exception as exc:
                extracted_text = ""
                extraction_error = f"{type(exc).__name__}: {str(exc)[:1000]}"
            scan = scan_untrusted_text(extracted_text)
            attachment_url = (
                f"postmark://inbound/{quote(payload.message_id, safe='')}/attachment/"
                f"{index}/{quote(attachment.name, safe='')}"
            )
            record = SourceRecord(
                source_id=source.id,
                url=attachment_url,
                canonical_url=attachment_url,
                content_hash=content_hash,
                raw_ref_r2=snapshot_ref,
                extracted_text=extracted_text,
                title=attachment.name,
                author=payload.sender[:255],
                lang=source.language,
                published_at=_message_datetime(payload.date),
                parser_version=ATTACHMENT_PARSER_VERSION,
                security_scan={
                    **scan.as_dict(),
                    "rights": rights_payload,
                    "postmark_message_id": payload.message_id,
                    "parent_source_record_id": str(parent.id),
                    "attachment_index": index,
                    "attachment_content_type": attachment.content_type,
                    "extraction_error": extraction_error,
                    "quarantined": extraction_error is not None,
                },
            )
            try:
                with session.begin_nested():
                    session.add(record)
                    session.flush()
            except IntegrityError:
                created = False
                record = session.scalar(
                    select(SourceRecord).where(
                        SourceRecord.source_id == source.id,
                        SourceRecord.content_hash == content_hash,
                    )
                )
                if record is None:
                    raise

        ensure_triage_item(session, record)

        now = datetime.now(UTC)
        session.add(
            ExternalRecordRef(
                provider="postmark",
                container_id=payload.mailbox_hash,
                table_name="attachment",
                external_id=external_id,
                entity="source_record",
                entity_id=record.id,
                imported_at=now,
                payload_hash=content_hash,
            )
        )
        if extraction_error:
            session.add(
                DeskAlert(
                    source_id=source.id,
                    kind=DeskAlertKind.SOURCE_QUARANTINE,
                    status=DeskAlertStatus.OPEN,
                    detected_at=now,
                    detail={
                        "source_record_id": str(record.id),
                        "filename": attachment.name,
                        "reason": extraction_error,
                    },
                )
            )
        session.add(
            AuditLog(
                actor="postmark-inbound",
                action="email.pdf_attachment_received",
                entity="source_record",
                entity_id=record.id,
                payload_json={
                    "parent_source_record_id": str(parent.id),
                    "message_id": payload.message_id,
                    "filename": attachment.name,
                    "created": created,
                    "quarantined": extraction_error is not None,
                },
            )
        )
        attachment_ids.append(record.id)
    return tuple(attachment_ids)


def ingest_postmark_message(
    session: Session,
    *,
    payload: PostmarkInboundMessage,
    snapshot_store: SnapshotStore | None = None,
) -> InboundImportResult:
    source = session.scalar(
        select(Source).where(
            Source.access_method == AccessMethod.EMAIL,
            Source.inbound_mailbox_hash == payload.mailbox_hash,
        )
    )
    if source is None:
        raise LookupError("No email source is mapped to this mailbox hash")
    rights = authorize_automation(source)
    active_store = snapshot_store or FileSnapshotStore()

    existing_ref = session.scalar(
        select(ExternalRecordRef).where(
            ExternalRecordRef.provider == "postmark",
            ExternalRecordRef.container_id == payload.mailbox_hash,
            ExternalRecordRef.table_name == "inbound",
            ExternalRecordRef.external_id == payload.message_id,
        )
    )
    if existing_ref:
        existing_record = session.get(SourceRecord, existing_ref.entity_id)
        if existing_record is None:
            raise RuntimeError("Postmark idempotency reference points to a missing record")
        return InboundImportResult(
            source_record=existing_record,
            created=False,
            attachment_record_ids=_existing_attachment_ids(
                session,
                mailbox_hash=payload.mailbox_hash,
                message_id=payload.message_id,
            ),
        )

    canonical_payload = payload.model_dump(mode="json", by_alias=True, exclude_none=True)
    raw_payload = json.dumps(
        canonical_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    content_hash = hashlib.sha256(raw_payload).hexdigest()
    existing_record = session.scalar(
        select(SourceRecord).where(
            SourceRecord.source_id == source.id,
            SourceRecord.content_hash == content_hash,
        )
    )
    created = existing_record is None
    if existing_record is None:
        text = _message_text(payload)
        scan = scan_untrusted_text(f"{payload.subject}\n{text}")
        snapshot_ref = active_store.put(
            source_id=str(source.id), payload=raw_payload, suffix="json"
        )
        message_url = f"postmark://inbound/{quote(payload.message_id, safe='')}"
        existing_record = SourceRecord(
            source_id=source.id,
            url=message_url,
            canonical_url=message_url,
            content_hash=content_hash,
            raw_ref_r2=snapshot_ref,
            extracted_text=text,
            title=payload.subject or None,
            author=payload.sender[:255],
            lang=source.language,
            published_at=_message_datetime(payload.date),
            parser_version=PARSER_VERSION,
            security_scan={
                **scan.as_dict(),
                "rights": rights.as_dict(),
                "postmark_message_id": payload.message_id,
                "attachments": [
                    {
                        "name": item.name,
                        "content_type": item.content_type,
                        "content_length": item.content_length,
                    }
                    for item in payload.attachments
                ],
            },
        )
        try:
            with session.begin_nested():
                session.add(existing_record)
                session.flush()
        except IntegrityError:
            created = False
            existing_record = session.scalar(
                select(SourceRecord).where(
                    SourceRecord.source_id == source.id,
                    SourceRecord.content_hash == content_hash,
                )
            )
            if existing_record is None:
                raise

    ensure_triage_item(session, existing_record)

    now = datetime.now(UTC)
    session.add(
        ExternalRecordRef(
            provider="postmark",
            container_id=payload.mailbox_hash,
            table_name="inbound",
            external_id=payload.message_id,
            entity="source_record",
            entity_id=existing_record.id,
            imported_at=now,
            payload_hash=content_hash,
        )
    )
    attachment_record_ids = _ingest_pdf_attachments(
        session,
        source=source,
        parent=existing_record,
        payload=payload,
        rights_payload=rights.as_dict(),
        snapshot_store=active_store,
    )
    source.last_successful_poll_at = now
    session.add(
        AuditLog(
            actor="postmark-inbound",
            action="email.source_record_received",
            entity="source_record",
            entity_id=existing_record.id,
            payload_json={
                "source_id": str(source.id),
                "message_id": payload.message_id,
                "created": created,
                "attachment_count": len(payload.attachments),
            },
        )
    )
    session.commit()
    session.refresh(existing_record)
    return InboundImportResult(
        source_record=existing_record,
        created=created,
        attachment_record_ids=attachment_record_ids,
    )
