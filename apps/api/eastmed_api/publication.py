from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from eastmed_schema.enums import AuthAssurance, ClaimState, DeskRole
from eastmed_schema.models import (
    AuditLog,
    Claim,
    DeskApproval,
    DeskUser,
    Event,
    EventVersion,
    Evidence,
    SourceRecord,
    TriageItem,
    event_version_claims,
    event_version_evidence,
)
from sqlalchemy import func, insert, select
from sqlalchemy.orm import Session

from eastmed_api.approval_bindings import claim_second_review_is_valid
from eastmed_api.contracts import (
    EventVersionCreate,
    EventVersionDraft,
    EventVersionPreviewRead,
    EventVersionRead,
    PublicationFieldDiffRead,
    PublicationGateCheckRead,
)


class PublicationPolicyError(ValueError):
    pass


@dataclass(frozen=True)
class ClaimPolicyView:
    id: UUID
    event_id: UUID
    state: ClaimState
    proposed_by: str
    reviewed_by: str | None
    second_reviewed_by: str | None
    sensitivity_flags: tuple[str, ...]
    evidence_count: int
    second_review_valid: bool = False


@dataclass(frozen=True)
class PublicationContext:
    event: Event
    event_snapshot: dict[str, Any]
    claim_rows: list[Claim]
    evidence_rows: list[Evidence]
    policy_views: list[ClaimPolicyView]
    latest_version: EventVersion | None
    next_version_no: int


def _publication_errors(
    *, event: Event, payload: EventVersionDraft, claims: list[ClaimPolicyView]
) -> list[str]:
    errors: list[str] = []
    claim_by_id = {claim.id: claim for claim in claims}
    referenced = {claim_id for sentence in payload.sentences for claim_id in sentence.claim_ids}
    missing = referenced - claim_by_id.keys()
    if missing:
        formatted_missing = sorted(str(item) for item in missing)
        errors.append(f"Unknown claims referenced: {formatted_missing}")

    if payload.published_by.casefold().startswith("model:"):
        errors.append("A model cannot publish an event version")
    if event.severity >= 3 and not payload.signed_off_by:
        errors.append("Severity 3-4 publications require human sign-off")
    if payload.signed_off_by and payload.signed_off_by.casefold().startswith("model:"):
        errors.append("A model cannot sign off a publication")
    if payload.signed_off_by and (
        payload.signed_off_by.casefold() == payload.published_by.casefold()
    ):
        errors.append("Publication sign-off must be performed by a distinct human")

    allowed_by_section = {
        "confirmed": {ClaimState.CONFIRMED, ClaimState.CORROBORATED_2X, ClaimState.SINGLE_OFFICIAL},
        "reported": {ClaimState.REPORTED, ClaimState.DISPUTED},
        "unknown": {ClaimState.UNVERIFIED, ClaimState.DISPUTED},
        "changed": set(ClaimState),
    }
    for sentence in payload.sentences:
        for claim_id in sentence.claim_ids:
            claim = claim_by_id.get(claim_id)
            if claim is None:
                continue
            if claim.event_id != event.id:
                errors.append("Publication cannot include a claim from another event")
            if claim.state not in allowed_by_section[sentence.section]:
                errors.append(
                    f"Claim {claim.id} state {claim.state.value} conflicts with "
                    f"section {sentence.section}"
                )
            if (
                claim.state
                in {
                    ClaimState.CONFIRMED,
                    ClaimState.CORROBORATED_2X,
                    ClaimState.SINGLE_OFFICIAL,
                }
                and claim.evidence_count == 0
            ):
                errors.append(f"Confirmed claim {claim.id} has no safe selected evidence")
            if claim.proposed_by.casefold().startswith("model:") and not claim.reviewed_by:
                errors.append(f"Model-proposed claim {claim.id} lacks human review")
            if claim.reviewed_by and claim.reviewed_by.casefold().startswith("model:"):
                errors.append(f"Claim {claim.id} has a non-human primary reviewer")
            if claim.sensitivity_flags and not claim.second_reviewed_by:
                errors.append(f"Sensitive claim {claim.id} requires a second analyst review")
            if claim.sensitivity_flags and claim.second_reviewed_by:
                if claim.second_reviewed_by.casefold().startswith("model:"):
                    errors.append(f"Sensitive claim {claim.id} has a non-human second reviewer")
                if claim.reviewed_by and (
                    claim.second_reviewed_by.casefold() == claim.reviewed_by.casefold()
                ):
                    errors.append(f"Sensitive claim {claim.id} requires a distinct second analyst")
                if not claim.second_review_valid:
                    errors.append(
                        f"Sensitive claim {claim.id} lacks an authenticated content-bound "
                        "second review"
                    )
    return list(dict.fromkeys(errors))


def validate_publication(
    *, event: Event, payload: EventVersionDraft, claims: list[ClaimPolicyView]
) -> None:
    errors = _publication_errors(event=event, payload=payload, claims=claims)
    if errors:
        raise PublicationPolicyError(errors[0])


def _load_context(
    session: Session,
    *,
    event_id: UUID,
    payload: EventVersionDraft,
    lock_event: bool = False,
) -> PublicationContext:
    event_query = select(Event).where(Event.id == event_id)
    if lock_event:
        event_query = event_query.with_for_update()
    event = session.scalar(event_query)
    if event is None:
        raise LookupError("Event not found")

    claim_ids = {claim_id for sentence in payload.sentences for claim_id in sentence.claim_ids}
    claim_rows = list(session.scalars(select(Claim).where(Claim.id.in_(claim_ids))).all())
    requested_evidence = list(
        session.scalars(select(Evidence).where(Evidence.id.in_(payload.evidence_ids))).all()
    )
    if {row.id for row in requested_evidence} != set(payload.evidence_ids):
        raise PublicationPolicyError("Publication references unknown evidence")
    if any(row.claim_id not in claim_ids for row in requested_evidence):
        raise PublicationPolicyError("Publication evidence must belong to its exact claim set")

    evidence_security = {
        evidence_id: security_scan
        for evidence_id, security_scan in session.execute(
            select(Evidence.id, SourceRecord.security_scan)
            .join(SourceRecord, SourceRecord.id == Evidence.source_record_id)
            .where(Evidence.id.in_(payload.evidence_ids))
        ).tuples()
    }
    selected_evidence_counts: dict[UUID, int] = {}
    for evidence in requested_evidence:
        scan = evidence_security.get(evidence.id, {})
        if scan.get("quarantined") or scan.get("injection_suspected"):
            continue
        selected_evidence_counts[evidence.claim_id] = (
            selected_evidence_counts.get(evidence.claim_id, 0) + 1
        )
    database_evidence_counts: dict[UUID, int] = {}
    if claim_ids:
        database_evidence_counts = {
            claim_id: count
            for claim_id, count in session.execute(
                select(Evidence.claim_id, func.count(Evidence.id))
                .where(Evidence.claim_id.in_(claim_ids))
                .group_by(Evidence.claim_id)
            ).tuples()
        }
    policy_views = [
        ClaimPolicyView(
            id=claim.id,
            event_id=claim.event_id,
            state=claim.claim_state,
            proposed_by=claim.proposed_by,
            reviewed_by=claim.reviewed_by,
            second_reviewed_by=claim.second_reviewed_by,
            sensitivity_flags=tuple(claim.sensitivity_flags),
            evidence_count=min(
                database_evidence_counts.get(claim.id, 0),
                selected_evidence_counts.get(claim.id, 0),
            ),
            second_review_valid=(
                claim_second_review_is_valid(session, claim=claim)
                if claim.sensitivity_flags
                else False
            ),
        )
        for claim in claim_rows
    ]
    latest_version = session.scalar(
        select(EventVersion)
        .where(EventVersion.event_id == event_id)
        .order_by(EventVersion.version_no.desc())
        .limit(1)
    )
    raw_geojson = session.scalar(select(func.ST_AsGeoJSON(Event.geo)).where(Event.id == event_id))
    detected_ports: dict[tuple[str, str], dict[str, str]] = {}
    for port_rows in session.scalars(
        select(TriageItem.detected_ports).where(TriageItem.assigned_event_id == event_id)
    ).all():
        for port in port_rows:
            name = str(port.get("name") or "").strip()
            unlocode = str(port.get("unlocode") or "").strip().upper()
            if name or unlocode:
                detected_ports[(unlocode, name.casefold())] = {
                    "name": name,
                    "unlocode": unlocode,
                }
    event_snapshot: dict[str, Any] = {
        "id": str(event.id),
        "slug": event.slug,
        "event_type": event.event_type.value,
        "corridor": event.corridor.value,
        "status": event.status.value,
        "severity": event.severity,
        "geojson": json.loads(raw_geojson) if raw_geojson else None,
        "geo_precision": event.geo_precision,
        "occurred_start": event.occurred_start.isoformat() if event.occurred_start else None,
        "occurred_end": event.occurred_end.isoformat() if event.occurred_end else None,
        "ports": [detected_ports[key] for key in sorted(detected_ports)],
    }
    return PublicationContext(
        event=event,
        event_snapshot=event_snapshot,
        claim_rows=claim_rows,
        evidence_rows=requested_evidence,
        policy_views=policy_views,
        latest_version=latest_version,
        next_version_no=(latest_version.version_no if latest_version else 0) + 1,
    )


def _claim_snapshots(claim_rows: list[Claim]) -> list[dict[str, Any]]:
    return sorted(
        [
            {
                "id": str(claim.id),
                "text": claim.text,
                "claimant": claim.claimant,
                "state": claim.claim_state.value,
                "occurred_at": claim.occurred_at.isoformat() if claim.occurred_at else None,
                "quantity": claim.quantity_json,
                "proposed_by": claim.proposed_by,
                "reviewed_by": claim.reviewed_by,
                "reviewed_at": claim.reviewed_at.isoformat() if claim.reviewed_at else None,
                "second_reviewed_by": claim.second_reviewed_by,
                "second_review_approval_id": (
                    str(claim.second_review_approval_id)
                    if claim.second_review_approval_id
                    else None
                ),
                "sensitivity_flags": claim.sensitivity_flags,
                "first_seen_at": claim.first_seen_at.isoformat(),
            }
            for claim in claim_rows
        ],
        key=lambda item: str(item["id"]),
    )


def _evidence_snapshots(evidence_rows: list[Evidence]) -> list[dict[str, Any]]:
    return sorted(
        [
            {
                "id": str(evidence.id),
                "claim_id": str(evidence.claim_id),
                "source_record_id": str(evidence.source_record_id),
                "directness": evidence.directness.value,
                "lineage_root_id": str(evidence.lineage_root_id),
                "excerpt": evidence.excerpt,
                "capture_snapshot_r2": evidence.capture_snapshot_r2,
                "rights_decision": evidence.rights_decision,
            }
            for evidence in evidence_rows
        ],
        key=lambda item: str(item["id"]),
    )


def _draft_artifacts(
    payload: EventVersionDraft,
) -> tuple[dict[str, list[str]], dict[str, list[str]], list[dict[str, Any]]]:
    sections: dict[str, list[str]] = {
        key: [] for key in ("confirmed", "reported", "unknown", "changed")
    }
    sentence_claim_map: dict[str, list[str]] = {}
    sentence_snapshots: list[dict[str, Any]] = []
    section_indexes: dict[str, int] = defaultdict(int)
    for sentence in payload.sentences:
        index = section_indexes[sentence.section]
        section_indexes[sentence.section] += 1
        key = f"{sentence.section}:{index}"
        claim_ids = [str(claim_id) for claim_id in sentence.claim_ids]
        sections[sentence.section].append(sentence.text)
        sentence_claim_map[key] = claim_ids
        sentence_snapshots.append(
            {
                "key": key,
                "section": sentence.section,
                "text": sentence.text,
                "claim_ids": claim_ids,
            }
        )
    return sections, sentence_claim_map, sentence_snapshots


def _canonical_draft(
    *,
    event_id: UUID,
    context: PublicationContext,
    payload: EventVersionDraft,
    sections: dict[str, list[str]],
    sentence_snapshots: list[dict[str, Any]],
    claim_snapshots: list[dict[str, Any]],
    evidence_snapshots: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "event_id": str(event_id),
        "version_no": context.next_version_no,
        "previous_content_hash": (
            context.latest_version.content_hash if context.latest_version else None
        ),
        "event_snapshot": context.event_snapshot,
        "title": payload.title,
        "sections": sections,
        "sentences": sentence_snapshots,
        "claim_ids": sorted(str(claim.id) for claim in context.claim_rows),
        "evidence_ids": sorted(str(evidence.id) for evidence in context.evidence_rows),
        "claim_snapshots": claim_snapshots,
        "evidence_snapshots": evidence_snapshots,
        "published_by": payload.published_by,
        "signed_off_by": payload.signed_off_by,
        "policy_version": payload.policy_version,
        "model_versions": payload.model_versions,
    }


def _hash_canonical(canonical: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _gate_checks(
    errors: list[str], *, sentence_count: int, event: Event
) -> list[PublicationGateCheckRead]:
    groups = [
        (
            "sentence-mapping",
            "Every material sentence maps to a claim",
            ["Unknown claims", "another event"],
            f"{sentence_count} material sentence(s) carry explicit claim IDs.",
        ),
        (
            "public-labels",
            "Claim states match public labels",
            ["conflicts with section"],
            "Confirmed, Reported, and Unknown sections match editorial claim states.",
        ),
        (
            "evidence",
            "Confirmed claims have selected evidence",
            ["has no safe selected evidence", "unknown evidence", "exact claim set"],
            "Every confirmed claim has evidence in this exact version snapshot.",
        ),
        (
            "human-review",
            "Model and sensitive claims have human review",
            ["Model-proposed", "second analyst"],
            "All model proposals and sensitivity flags satisfy human review policy.",
        ),
        (
            "severity-signoff",
            "Severity release has human sign-off",
            ["human sign-off", "model cannot", "model cannot publish"],
            (
                "Named human sign-off is present."
                if event.severity >= 3
                else "Severity 1-2 does not require an additional sign-off."
            ),
        ),
    ]
    checks: list[PublicationGateCheckRead] = []
    for key, label, needles, success in groups:
        failures = [
            error
            for error in errors
            if any(needle.casefold() in error.casefold() for needle in needles)
        ]
        checks.append(
            PublicationGateCheckRead(
                key=key,
                label=label,
                passed=not failures,
                detail="; ".join(failures) if failures else success,
            )
        )
    return checks


def preview_event_version(
    session: Session, *, event_id: UUID, payload: EventVersionDraft
) -> EventVersionPreviewRead:
    context = _load_context(session, event_id=event_id, payload=payload)
    sections, _, sentence_snapshots = _draft_artifacts(payload)
    claim_snapshots = _claim_snapshots(context.claim_rows)
    evidence_snapshots = _evidence_snapshots(context.evidence_rows)
    canonical = _canonical_draft(
        event_id=event_id,
        context=context,
        payload=payload,
        sections=sections,
        sentence_snapshots=sentence_snapshots,
        claim_snapshots=claim_snapshots,
        evidence_snapshots=evidence_snapshots,
    )
    errors = _publication_errors(event=context.event, payload=payload, claims=context.policy_views)
    previous = context.latest_version
    before_values = {
        "title": previous.title if previous else "",
        "confirmed": previous.summary_confirmed if previous else "",
        "reported": previous.summary_reported if previous else "",
        "unknown": previous.summary_unknown if previous else "",
        "changed": previous.whats_changed if previous else "",
    }
    after_values = {
        "title": payload.title,
        **{key: "\n".join(value) for key, value in sections.items()},
    }
    diff = {
        key: PublicationFieldDiffRead(
            before=before_values[key],
            after=after_values[key],
            changed=before_values[key] != after_values[key],
        )
        for key in before_values
    }
    return EventVersionPreviewRead(
        previous_version_no=previous.version_no if previous else None,
        next_version_no=context.next_version_no,
        preview_hash=_hash_canonical(canonical),
        ready=not errors,
        checks=_gate_checks(errors, sentence_count=len(payload.sentences), event=context.event),
        diff=diff,
    )


def publish_event_version(
    session: Session,
    *,
    event_id: UUID,
    payload: EventVersionCreate,
    publication_approval_id: UUID | None = None,
) -> EventVersionRead:
    context = _load_context(session, event_id=event_id, payload=payload, lock_event=True)
    validate_publication(event=context.event, payload=payload, claims=context.policy_views)
    sections, sentence_claim_map, sentence_snapshots = _draft_artifacts(payload)
    claim_snapshots = _claim_snapshots(context.claim_rows)
    evidence_snapshots = _evidence_snapshots(context.evidence_rows)
    canonical = _canonical_draft(
        event_id=event_id,
        context=context,
        payload=payload,
        sections=sections,
        sentence_snapshots=sentence_snapshots,
        claim_snapshots=claim_snapshots,
        evidence_snapshots=evidence_snapshots,
    )
    content_hash = _hash_canonical(canonical)
    if payload.preview_hash != content_hash:
        raise PublicationPolicyError(
            "Preview is stale; regenerate the version diff before publishing"
        )
    if context.event.severity >= 3:
        if publication_approval_id is None:
            raise PublicationPolicyError(
                "Severity 3-4 publication requires a content-bound approval record"
            )
        approval = session.get(DeskApproval, publication_approval_id)
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
            or approval.approval_type != "event_publication"
            or approval.target_id != event_id
            or approval.binding_hash != content_hash
            or payload.published_by != f"desk:{approval.primary_user_id}"
            or payload.signed_off_by != f"desk:{approval.approved_by_user_id}"
            or approval.primary_user_id == approval.approved_by_user_id
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
            raise PublicationPolicyError(
                "Publication approval is missing, expired, or not bound to this exact release"
            )

    version = EventVersion(
        event_id=event_id,
        version_no=context.next_version_no,
        title=payload.title,
        summary_confirmed="\n".join(sections["confirmed"]),
        summary_reported="\n".join(sections["reported"]),
        summary_unknown="\n".join(sections["unknown"]),
        whats_changed="\n".join(sections["changed"]),
        sentence_claim_map=sentence_claim_map,
        event_snapshot_json=context.event_snapshot,
        claim_snapshot_json=claim_snapshots,
        evidence_snapshot_json=evidence_snapshots,
        published_by=payload.published_by,
        signed_off_by=payload.signed_off_by,
        publication_approval_id=publication_approval_id,
        policy_version=payload.policy_version,
        model_versions=payload.model_versions,
        content_hash=content_hash,
    )
    session.add(version)
    session.flush()
    session.execute(
        insert(event_version_claims),
        [{"event_version_id": version.id, "claim_id": claim.id} for claim in context.claim_rows],
    )
    if context.evidence_rows:
        session.execute(
            insert(event_version_evidence),
            [
                {"event_version_id": version.id, "evidence_id": evidence.id}
                for evidence in context.evidence_rows
            ],
        )
    session.add(
        AuditLog(
            actor=payload.published_by,
            action="event_version.published",
            entity="event_version",
            entity_id=version.id,
            payload_json={
                **canonical,
                "content_hash": content_hash,
                "publication_approval_id": (
                    str(publication_approval_id) if publication_approval_id else None
                ),
            },
        )
    )
    session.commit()
    session.refresh(version)
    return EventVersionRead.model_validate(version, from_attributes=True)
