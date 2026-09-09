from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

from eastmed_schema.models import Event, EventVersion
from sqlalchemy import select
from sqlalchemy.orm import Session

from eastmed_api.contracts import ReliabilityReceiptRead


def _canonical_json(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def reliability_receipt_for_version(version: EventVersion) -> ReliabilityReceiptRead:
    payload: dict[str, Any] = {
        "receipt_schema_version": "1.0",
        "event_version_id": str(version.id),
        "event_id": str(version.event_id),
        "version_no": version.version_no,
        "published_at": version.published_at.isoformat(),
        "published_by": version.published_by,
        "signed_off_by": version.signed_off_by,
        "publication_approval_id": (
            str(version.publication_approval_id)
            if version.publication_approval_id is not None
            else None
        ),
        "policy_version": version.policy_version,
        "model_versions": dict(version.model_versions),
        "content_hash": version.content_hash,
        "sentence_claim_map": dict(version.sentence_claim_map),
        "event_snapshot": dict(version.event_snapshot_json),
        "claim_snapshots": list(version.claim_snapshot_json),
        "evidence_snapshots": list(version.evidence_snapshot_json),
    }
    receipt_hash = hashlib.sha256(_canonical_json(payload)).hexdigest()
    return ReliabilityReceiptRead(
        **payload,
        receipt_hash=receipt_hash,
    )


def get_reliability_receipt(
    session: Session, *, event_version_id: UUID
) -> ReliabilityReceiptRead:
    version = session.get(EventVersion, event_version_id)
    if version is None:
        raise LookupError("Published event version not found")
    return reliability_receipt_for_version(version)


def latest_reliability_receipt(
    session: Session, *, event_slug: str
) -> ReliabilityReceiptRead:
    version = session.scalar(
        select(EventVersion)
        .join(Event, Event.id == EventVersion.event_id)
        .where(Event.slug == event_slug)
        .order_by(EventVersion.version_no.desc())
        .limit(1)
    )
    if version is None:
        raise LookupError("Published event not found")
    return reliability_receipt_for_version(version)
