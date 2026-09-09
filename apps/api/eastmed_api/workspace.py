from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from eastmed_pipeline.rights import authorize_automation, authorize_public_excerpt
from eastmed_schema.models import (
    AuditLog,
    Claim,
    Event,
    EventVersion,
    Evidence,
    LineageRoot,
    Source,
    SourceRecord,
    SourceRecordLineage,
    TriageItem,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from eastmed_api.contracts import (
    ClaimCreate,
    ClaimUpdate,
    EventWorkspaceRead,
    EvidenceLinkCreate,
    EvidenceLinkRead,
    PublicationSentence,
    WorkspaceClaimRead,
    WorkspaceEvidenceRead,
    WorkspaceSourceRead,
    WorkspaceVersionRead,
)


class WorkspaceError(ValueError):
    pass


def _version_sentences(version: EventVersion) -> list[PublicationSentence]:
    section_values = {
        "confirmed": version.summary_confirmed,
        "reported": version.summary_reported,
        "unknown": version.summary_unknown,
        "changed": version.whats_changed,
    }
    sentences: list[PublicationSentence] = []
    for section, value in section_values.items():
        for index, text in enumerate(filter(None, value.splitlines())):
            claim_ids = version.sentence_claim_map.get(f"{section}:{index}")
            if claim_ids is None:
                claim_ids = version.sentence_claim_map.get(text, [])
            sentences.append(
                PublicationSentence(
                    section=section,  # type: ignore[arg-type]
                    text=text,
                    claim_ids=[UUID(claim_id) for claim_id in claim_ids],
                )
            )
    return sentences


def get_event_workspace(session: Session, *, event_id: UUID) -> EventWorkspaceRead:
    event = session.get(Event, event_id)
    if event is None:
        raise LookupError("Event not found")

    claim_rows = list(
        session.scalars(
            select(Claim)
            .where(Claim.event_id == event_id)
            .order_by(Claim.first_seen_at, Claim.created_at)
        ).all()
    )
    claim_ids = [claim.id for claim in claim_rows]
    evidence_rows: list[tuple[Evidence, SourceRecord, Source]] = []
    if claim_ids:
        evidence_rows = list(
            session.execute(
                select(Evidence, SourceRecord, Source)
                .join(SourceRecord, SourceRecord.id == Evidence.source_record_id)
                .join(Source, Source.id == SourceRecord.source_id)
                .where(Evidence.claim_id.in_(claim_ids))
                .order_by(Source.tier, SourceRecord.fetched_at)
            )
            .tuples()
            .all()
        )

    evidence_by_claim: dict[UUID, list[WorkspaceEvidenceRead]] = defaultdict(list)
    linked_claims_by_record: dict[UUID, set[UUID]] = defaultdict(set)
    source_rows: dict[UUID, tuple[SourceRecord, Source]] = {}
    for evidence, record, source in evidence_rows:
        evidence_by_claim[evidence.claim_id].append(
            WorkspaceEvidenceRead(
                id=evidence.id,
                source_record_id=record.id,
                source_name=source.name,
                source_tier=source.tier,
                url=record.url,
                directness=evidence.directness,
                lineage_root_id=evidence.lineage_root_id,
                excerpt=evidence.excerpt,
                rights_basis=source.rights_basis,
            )
        )
        linked_claims_by_record[record.id].add(evidence.claim_id)
        source_rows[record.id] = (record, source)

    assigned_rows = session.execute(
        select(SourceRecord, Source)
        .join(TriageItem, TriageItem.source_record_id == SourceRecord.id)
        .join(Source, Source.id == SourceRecord.source_id)
        .where(TriageItem.assigned_event_id == event_id)
    ).all()
    for record, source in assigned_rows:
        source_rows[record.id] = (record, source)

    record_ids = list(source_rows)
    lineage_by_record: dict[UUID, UUID] = {}
    if record_ids:
        lineage_by_record = {
            record_id: root_id
            for record_id, root_id in session.execute(
                select(
                    SourceRecordLineage.source_record_id,
                    SourceRecordLineage.lineage_root_id,
                ).where(SourceRecordLineage.source_record_id.in_(record_ids))
            ).tuples()
        }

    claims = [
        WorkspaceClaimRead(
            id=claim.id,
            text=claim.text,
            claimant=claim.claimant,
            claim_state=claim.claim_state,
            occurred_at=claim.occurred_at,
            proposed_by=claim.proposed_by,
            reviewed_by=claim.reviewed_by,
            second_reviewed_by=claim.second_reviewed_by,
            second_review_approval_id=claim.second_review_approval_id,
            reviewed_at=claim.reviewed_at,
            sensitivity_flags=claim.sensitivity_flags,
            first_seen_at=claim.first_seen_at,
            evidence=evidence_by_claim[claim.id],
        )
        for claim in claim_rows
    ]
    sources = [
        WorkspaceSourceRead(
            id=record.id,
            source_name=source.name,
            source_tier=source.tier,
            rights_basis=source.rights_basis,
            url=record.url,
            title=record.title,
            text=record.extracted_text[:50_000],
            published_at=record.published_at,
            fetched_at=record.fetched_at,
            lineage_root_id=lineage_by_record.get(record.id),
            linked_claim_ids=sorted(linked_claims_by_record[record.id], key=str),
        )
        for record, source in sorted(
            source_rows.values(),
            key=lambda item: item[0].published_at or item[0].fetched_at,
            reverse=True,
        )
    ]

    version_rows = list(
        session.scalars(
            select(EventVersion)
            .where(EventVersion.event_id == event_id)
            .order_by(EventVersion.version_no.desc())
        ).all()
    )
    versions = [
        WorkspaceVersionRead(
            id=version.id,
            version_no=version.version_no,
            title=version.title,
            sentences=_version_sentences(version),
            published_at=version.published_at,
            published_by=version.published_by,
            signed_off_by=version.signed_off_by,
            policy_version=version.policy_version,
            model_versions=version.model_versions,
            content_hash=version.content_hash,
        )
        for version in version_rows
    ]
    return EventWorkspaceRead(
        id=event.id,
        slug=event.slug,
        event_type=event.event_type,
        corridor=event.corridor,
        status=event.status.value,
        severity=event.severity,
        occurred_start=event.occurred_start,
        occurred_end=event.occurred_end,
        created_at=event.created_at,
        title=versions[0].title if versions else event.slug.replace("-", " "),
        claims=claims,
        sources=sources,
        versions=versions,
    )


def create_claim(session: Session, *, event_id: UUID, payload: ClaimCreate) -> Claim:
    if session.get(Event, event_id) is None:
        raise LookupError("Event not found")
    now = datetime.now(UTC)
    claim = Claim(
        event_id=event_id,
        text=payload.text,
        claimant=payload.claimant,
        claim_state=payload.claim_state,
        occurred_at=payload.occurred_at,
        quantity_json=[],
        proposed_by=payload.reviewer,
        reviewed_by=payload.reviewer,
        reviewed_at=now,
        sensitivity_flags=payload.sensitivity_flags,
        first_seen_at=now,
    )
    session.add(claim)
    session.flush()
    session.add(
        AuditLog(
            actor=payload.reviewer,
            action="claim.created",
            entity="claim",
            entity_id=claim.id,
            payload_json={
                "event_id": str(event_id),
                "text": claim.text,
                "claimant": claim.claimant,
                "claim_state": claim.claim_state.value,
                "sensitivity_flags": claim.sensitivity_flags,
            },
        )
    )
    session.commit()
    session.refresh(claim)
    return claim


def update_claim(
    session: Session, *, event_id: UUID, claim_id: UUID, payload: ClaimUpdate
) -> Claim:
    claim = session.get(Claim, claim_id)
    if claim is None or claim.event_id != event_id:
        raise LookupError("Claim not found")
    if payload.second_reviewed_by and payload.second_reviewed_by == payload.reviewer:
        raise WorkspaceError("Second review must be performed by another analyst")

    before: dict[str, Any] = {
        "text": claim.text,
        "claimant": claim.claimant,
        "claim_state": claim.claim_state.value,
        "occurred_at": claim.occurred_at.isoformat() if claim.occurred_at else None,
        "sensitivity_flags": claim.sensitivity_flags,
        "second_reviewed_by": claim.second_reviewed_by,
        "second_review_approval_id": (
            str(claim.second_review_approval_id) if claim.second_review_approval_id else None
        ),
    }
    changed_fields = payload.model_fields_set - {"reviewer", "second_reviewed_by"}
    for field in changed_fields:
        setattr(claim, field, getattr(payload, field))
    claim.reviewed_by = payload.reviewer
    claim.reviewed_at = datetime.now(UTC)
    if changed_fields:
        claim.second_reviewed_by = None
        claim.second_review_approval_id = None
    if "second_reviewed_by" in payload.model_fields_set:
        claim.second_reviewed_by = payload.second_reviewed_by
        claim.second_review_approval_id = None

    after: dict[str, Any] = {
        "text": claim.text,
        "claimant": claim.claimant,
        "claim_state": claim.claim_state.value,
        "occurred_at": claim.occurred_at.isoformat() if claim.occurred_at else None,
        "sensitivity_flags": claim.sensitivity_flags,
        "second_reviewed_by": claim.second_reviewed_by,
        "second_review_approval_id": (
            str(claim.second_review_approval_id) if claim.second_review_approval_id else None
        ),
    }
    session.add(
        AuditLog(
            actor=payload.reviewer,
            action="claim.updated",
            entity="claim",
            entity_id=claim.id,
            payload_json={"event_id": str(event_id), "before": before, "after": after},
        )
    )
    session.commit()
    session.refresh(claim)
    return claim


def link_evidence(
    session: Session,
    *,
    event_id: UUID,
    claim_id: UUID,
    payload: EvidenceLinkCreate,
) -> EvidenceLinkRead:
    claim = session.get(Claim, claim_id)
    if claim is None or claim.event_id != event_id:
        raise LookupError("Claim not found")
    record = session.get(SourceRecord, payload.source_record_id)
    if record is None:
        raise LookupError("Source record not found")
    source = session.get(Source, record.source_id)
    if source is None:
        raise LookupError("Source not found")
    if record.security_scan.get("quarantined") or record.security_scan.get("injection_suspected"):
        raise WorkspaceError("Quarantined source records cannot be linked as evidence")
    assigned = session.scalar(
        select(TriageItem.id).where(
            TriageItem.source_record_id == record.id,
            TriageItem.assigned_event_id == event_id,
        )
    )
    if assigned is None:
        raise WorkspaceError("Source record is not assigned to this event")
    existing = session.scalar(
        select(Evidence).where(
            Evidence.claim_id == claim_id,
            Evidence.source_record_id == record.id,
        )
    )
    if existing is not None:
        raise WorkspaceError("Source record is already linked to this claim")

    rights = (
        authorize_public_excerpt(source)
        if payload.excerpt and payload.excerpt.strip()
        else authorize_automation(source)
    )
    lineage = session.get(SourceRecordLineage, record.id)
    if lineage is None:
        root = LineageRoot(
            description=f"Single-source lineage for {record.canonical_url}",
            origin_source_id=source.id,
            origin_url=record.canonical_url,
            first_seen_at=record.published_at or record.fetched_at,
        )
        session.add(root)
        session.flush()
        lineage = SourceRecordLineage(
            source_record_id=record.id,
            lineage_root_id=root.id,
            proposal_id=None,
            assigned_by=payload.reviewer,
            assigned_at=datetime.now(UTC),
        )
        session.add(lineage)
        session.flush()

    # A second review is bound to the exact evidence package. Adding evidence
    # invalidates it and requires the independent reviewer to review again.
    claim.second_reviewed_by = None
    claim.second_review_approval_id = None
    evidence = Evidence(
        source_record_id=record.id,
        claim_id=claim.id,
        directness=payload.directness,
        lineage_root_id=lineage.lineage_root_id,
        excerpt=payload.excerpt,
        capture_snapshot_r2=record.raw_ref_r2,
        rights_decision=rights.as_dict(),
    )
    session.add(evidence)
    session.flush()
    session.add(
        AuditLog(
            actor=payload.reviewer,
            action="evidence.linked",
            entity="evidence",
            entity_id=evidence.id,
            payload_json={
                "event_id": str(event_id),
                "claim_id": str(claim.id),
                "source_record_id": str(record.id),
                "directness": evidence.directness.value,
                "lineage_root_id": str(evidence.lineage_root_id),
                "rights_decision": evidence.rights_decision,
            },
        )
    )
    session.commit()
    session.refresh(evidence)
    return EvidenceLinkRead.model_validate(evidence, from_attributes=True)
