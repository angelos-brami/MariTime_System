from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from eastmed_schema.enums import AuthAssurance, CorrectionImpact, DeskRole
from eastmed_schema.models import AuditLog, Claim, DeskApproval, DeskUser, Event
from sqlalchemy import select
from sqlalchemy.orm import Session

from eastmed_api.approval_bindings import (
    CLAIM_SECOND_REVIEW_APPROVAL,
    binding_hash,
    claim_review_snapshot,
    desk_actor_user_id,
)
from eastmed_api.contracts import (
    ClaimSecondReviewCreate,
    CorrectionApprovalCreate,
    CorrectionDraft,
    EventVersionDraft,
    EventVersionPreviewRead,
    PublicationApprovalCreate,
)
from eastmed_api.corrections import CorrectionPlan, build_correction_plan
from eastmed_api.publication import preview_event_version
from eastmed_api.security import DeskPrincipal

EVENT_PUBLICATION_APPROVAL = "event_publication"
OPERATIONAL_CORRECTION_APPROVAL = "operational_correction"
PUBLICATION_APPROVAL_VALIDITY = timedelta(minutes=15)
APPROVER_ROLES = frozenset({DeskRole.SENIOR_ANALYST, DeskRole.ADMINISTRATOR})
PUBLISHER_ROLES = frozenset(
    {DeskRole.ANALYST, DeskRole.SENIOR_ANALYST, DeskRole.ADMINISTRATOR}
)
STRONG_ASSURANCE = frozenset({AuthAssurance.MFA, AuthAssurance.PHISHING_RESISTANT})


class ApprovalWorkflowError(ValueError):
    pass


class ApprovalAuthorizationError(ApprovalWorkflowError):
    pass


@dataclass(frozen=True)
class PublicationApprovalResult:
    approval: DeskApproval
    approved_draft: EventVersionDraft
    preview: EventVersionPreviewRead


@dataclass(frozen=True)
class PublicationAuthorization:
    approval_id: UUID | None
    signed_off_by: str | None


@dataclass(frozen=True)
class CorrectionApprovalResult:
    approval: DeskApproval
    approved_draft: CorrectionDraft
    plan: CorrectionPlan


@dataclass(frozen=True)
class CorrectionAuthorization:
    approval_id: UUID | None
    drafted_by: str
    signed_off_by: str


def approve_operational_correction(
    session: Session,
    *,
    payload: CorrectionApprovalCreate,
    principal: DeskPrincipal,
    commit: bool = True,
) -> CorrectionApprovalResult:
    if principal.role not in APPROVER_ROLES:
        raise ApprovalAuthorizationError(
            "Correction approval requires a senior analyst or administrator"
        )
    if principal.assurance not in STRONG_ASSURANCE:
        raise ApprovalAuthorizationError("Correction approval requires MFA")
    if payload.draft.impact != CorrectionImpact.OPERATIONALLY_RELEVANT:
        raise ApprovalWorkflowError(
            "Only operationally relevant corrections require second-person approval"
        )

    drafter = session.get(DeskUser, payload.drafter_user_id)
    if drafter is None or not drafter.active or drafter.role not in PUBLISHER_ROLES:
        raise ApprovalWorkflowError("The correction drafter is not an active desk analyst")
    if drafter.id == principal.user_id:
        raise ApprovalAuthorizationError(
            "Operational correction approval must be distinct from the drafter"
        )

    approved_draft = CorrectionDraft(
        **payload.draft.model_dump(),
        drafted_by=_actor(drafter.id),
        signed_off_by=principal.actor,
    )
    plan = build_correction_plan(session, **approved_draft.model_dump())
    approved_at = datetime.now(UTC)
    approval = DeskApproval(
        approval_type=OPERATIONAL_CORRECTION_APPROVAL,
        target_id=payload.draft.event_id,
        binding_hash=plan.preview_hash,
        binding_payload_json={
            "approved_draft": approved_draft.model_dump(mode="json"),
            "preview_hash": plan.preview_hash,
        },
        primary_user_id=drafter.id,
        approved_by_user_id=principal.user_id,
        auth_assurance=principal.assurance,
        reason=payload.reason,
        approved_at=approved_at,
        expires_at=approved_at + PUBLICATION_APPROVAL_VALIDITY,
    )
    session.add(approval)
    session.flush()
    session.add(
        AuditLog(
            actor=principal.actor,
            action="operational_correction.approved",
            entity="desk_approval",
            entity_id=approval.id,
            payload_json={
                "event_id": str(payload.draft.event_id),
                "preview_hash": plan.preview_hash,
                "drafter_user_id": str(drafter.id),
                "approved_by_user_id": str(principal.user_id),
                "auth_assurance": principal.assurance.value,
                "expires_at": approval.expires_at.isoformat(),
                "reason": payload.reason,
            },
        )
    )
    if commit:
        session.commit()
        session.refresh(approval)
    return CorrectionApprovalResult(
        approval=approval,
        approved_draft=approved_draft,
        plan=plan,
    )


def authorize_correction_issue(
    session: Session,
    *,
    event_id: UUID,
    impact: CorrectionImpact,
    preview_hash: str,
    principal: DeskPrincipal,
) -> CorrectionAuthorization:
    if impact != CorrectionImpact.OPERATIONALLY_RELEVANT:
        if principal.role not in APPROVER_ROLES:
            raise ApprovalAuthorizationError(
                "Non-operational correction issuance requires a senior analyst"
            )
        return CorrectionAuthorization(
            approval_id=None,
            drafted_by=principal.actor,
            signed_off_by=principal.actor,
        )

    approval = session.scalar(
        select(DeskApproval)
        .where(
            DeskApproval.approval_type == OPERATIONAL_CORRECTION_APPROVAL,
            DeskApproval.target_id == event_id,
            DeskApproval.binding_hash == preview_hash,
            DeskApproval.primary_user_id == principal.user_id,
        )
        .order_by(DeskApproval.approved_at.desc(), DeskApproval.id.desc())
        .limit(1)
    )
    if approval is None:
        raise ApprovalAuthorizationError(
            "No second-person approval exists for this exact operational correction"
        )
    if _as_aware_utc(approval.expires_at) <= datetime.now(UTC):
        raise ApprovalAuthorizationError(
            "The operational correction approval has expired; request a new approval"
        )
    if approval.auth_assurance not in STRONG_ASSURANCE:
        raise ApprovalAuthorizationError("The correction approval was not authenticated with MFA")
    approver = session.get(DeskUser, approval.approved_by_user_id)
    if approver is None or not approver.active or approver.role not in APPROVER_ROLES:
        raise ApprovalAuthorizationError("The correction approver is no longer authorized")
    if approver.id == principal.user_id:
        raise ApprovalAuthorizationError(
            "Operational correction approval must be distinct from the drafter"
        )
    stored_draft = approval.binding_payload_json.get("approved_draft")
    if not isinstance(stored_draft, dict):
        raise ApprovalAuthorizationError("The correction approval record is malformed")
    if stored_draft.get("drafted_by") != principal.actor:
        raise ApprovalAuthorizationError("The correction approval is bound to another drafter")
    signer = _actor(approver.id)
    if stored_draft.get("signed_off_by") != signer:
        raise ApprovalAuthorizationError("The correction approval signer binding is invalid")
    return CorrectionAuthorization(
        approval_id=approval.id,
        drafted_by=principal.actor,
        signed_off_by=signer,
    )


def approve_sensitive_claim(
    session: Session,
    *,
    event_id: UUID,
    claim_id: UUID,
    payload: ClaimSecondReviewCreate,
    principal: DeskPrincipal,
) -> DeskApproval:
    if principal.role not in APPROVER_ROLES:
        raise ApprovalAuthorizationError(
            "Claim second review requires a senior analyst or administrator"
        )
    if principal.assurance not in STRONG_ASSURANCE:
        raise ApprovalAuthorizationError("Claim second review requires MFA")

    claim = session.scalar(select(Claim).where(Claim.id == claim_id).with_for_update())
    if claim is None or claim.event_id != event_id:
        raise LookupError("Claim not found")
    if not claim.sensitivity_flags:
        raise ApprovalWorkflowError("Only sensitivity-flagged claims require second review")
    if claim.second_review_approval_id is not None:
        raise ApprovalWorkflowError("This exact claim already has a second review")

    primary_user_id = desk_actor_user_id(claim.reviewed_by)
    if primary_user_id is None:
        raise ApprovalWorkflowError(
            "Claim primary review is not bound to an authenticated desk identity"
        )
    primary = session.get(DeskUser, primary_user_id)
    if primary is None or not primary.active or primary.role not in PUBLISHER_ROLES:
        raise ApprovalWorkflowError("Claim primary reviewer is not an active desk analyst")
    if primary.id == principal.user_id:
        raise ApprovalAuthorizationError(
            "Claim second review must be performed by a distinct analyst"
        )

    snapshot = claim_review_snapshot(session, claim=claim)
    approved_at = datetime.now(UTC)
    approval = DeskApproval(
        approval_type=CLAIM_SECOND_REVIEW_APPROVAL,
        target_id=claim.id,
        binding_hash=binding_hash(snapshot),
        binding_payload_json={"claim_snapshot": snapshot},
        primary_user_id=primary.id,
        approved_by_user_id=principal.user_id,
        auth_assurance=principal.assurance,
        reason=payload.reason,
        approved_at=approved_at,
        expires_at=approved_at + PUBLICATION_APPROVAL_VALIDITY,
    )
    session.add(approval)
    session.flush()
    claim.second_reviewed_by = principal.actor
    claim.second_review_approval_id = approval.id
    session.add(
        AuditLog(
            actor=principal.actor,
            action="claim.second_reviewed",
            entity="claim",
            entity_id=claim.id,
            payload_json={
                "event_id": str(event_id),
                "approval_id": str(approval.id),
                "binding_hash": approval.binding_hash,
                "primary_reviewer": claim.reviewed_by,
                "second_reviewer": principal.actor,
                "auth_assurance": principal.assurance.value,
                "reason": payload.reason,
            },
        )
    )
    session.commit()
    session.refresh(approval)
    return approval


def _actor(user_id: UUID) -> str:
    return f"desk:{user_id}"


def _as_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def approve_event_publication(
    session: Session,
    *,
    event_id: UUID,
    payload: PublicationApprovalCreate,
    principal: DeskPrincipal,
    commit: bool = True,
) -> PublicationApprovalResult:
    if principal.role not in APPROVER_ROLES:
        raise ApprovalAuthorizationError(
            "Publication approval requires a senior analyst or administrator"
        )
    if principal.assurance not in STRONG_ASSURANCE:
        raise ApprovalAuthorizationError("Publication approval requires MFA")

    event = session.get(Event, event_id)
    if event is None:
        raise LookupError("Event not found")
    if event.severity < 3:
        raise ApprovalWorkflowError(
            "Severity 1-2 publications do not require a second-person approval"
        )

    publisher = session.get(DeskUser, payload.publisher_user_id)
    if publisher is None or not publisher.active or publisher.role not in PUBLISHER_ROLES:
        raise ApprovalWorkflowError("The named publisher is not an active desk publisher")
    if publisher.id == principal.user_id:
        raise ApprovalAuthorizationError(
            "The publication approver must be distinct from the publisher"
        )

    approved_draft = EventVersionDraft(
        **payload.draft.model_dump(),
        published_by=_actor(publisher.id),
        signed_off_by=principal.actor,
    )
    preview = preview_event_version(session, event_id=event_id, payload=approved_draft)
    if not preview.ready:
        failure = next((check.detail for check in preview.checks if not check.passed), None)
        raise ApprovalWorkflowError(failure or "Publication policy checks did not pass")

    approved_at = datetime.now(UTC)
    expires_at = approved_at + PUBLICATION_APPROVAL_VALIDITY
    approval = DeskApproval(
        approval_type=EVENT_PUBLICATION_APPROVAL,
        target_id=event_id,
        binding_hash=preview.preview_hash,
        binding_payload_json={
            "event_id": str(event_id),
            "approved_draft": approved_draft.model_dump(mode="json"),
            "preview": preview.model_dump(mode="json"),
        },
        primary_user_id=publisher.id,
        approved_by_user_id=principal.user_id,
        auth_assurance=principal.assurance,
        reason=payload.reason,
        approved_at=approved_at,
        expires_at=expires_at,
    )
    session.add(approval)
    session.flush()
    session.add(
        AuditLog(
            actor=principal.actor,
            action="event_publication.approved",
            entity="desk_approval",
            entity_id=approval.id,
            payload_json={
                "event_id": str(event_id),
                "preview_hash": preview.preview_hash,
                "publisher_user_id": str(publisher.id),
                "approved_by_user_id": str(principal.user_id),
                "auth_assurance": principal.assurance.value,
                "approved_at": approved_at.isoformat(),
                "expires_at": expires_at.isoformat(),
                "reason": payload.reason,
            },
        )
    )
    if commit:
        session.commit()
        session.refresh(approval)
    return PublicationApprovalResult(
        approval=approval,
        approved_draft=approved_draft,
        preview=preview,
    )


def authorize_event_publication(
    session: Session,
    *,
    event_id: UUID,
    preview_hash: str,
    principal: DeskPrincipal,
) -> PublicationAuthorization:
    event = session.get(Event, event_id)
    if event is None:
        raise LookupError("Event not found")
    if event.severity < 3:
        return PublicationAuthorization(approval_id=None, signed_off_by=None)

    approval = session.scalar(
        select(DeskApproval)
        .where(
            DeskApproval.approval_type == EVENT_PUBLICATION_APPROVAL,
            DeskApproval.target_id == event_id,
            DeskApproval.binding_hash == preview_hash,
            DeskApproval.primary_user_id == principal.user_id,
        )
        .order_by(DeskApproval.approved_at.desc(), DeskApproval.id.desc())
        .limit(1)
    )
    if approval is None:
        raise ApprovalAuthorizationError(
            "No second-person approval exists for this exact publication preview"
        )
    if _as_aware_utc(approval.expires_at) <= datetime.now(UTC):
        raise ApprovalAuthorizationError(
            "The second-person publication approval has expired; request a new approval"
        )
    if approval.auth_assurance not in STRONG_ASSURANCE:
        raise ApprovalAuthorizationError("The publication approval was not authenticated with MFA")

    approver = session.get(DeskUser, approval.approved_by_user_id)
    if approver is None or not approver.active or approver.role not in APPROVER_ROLES:
        raise ApprovalAuthorizationError("The publication approver is no longer authorized")
    if approver.id == principal.user_id:
        raise ApprovalAuthorizationError(
            "The publication approver must be distinct from the publisher"
        )

    stored_draft = approval.binding_payload_json.get("approved_draft")
    if not isinstance(stored_draft, dict):
        raise ApprovalAuthorizationError("The publication approval record is malformed")
    if stored_draft.get("published_by") != principal.actor:
        raise ApprovalAuthorizationError("The publication approval is bound to another publisher")
    signer = _actor(approver.id)
    if stored_draft.get("signed_off_by") != signer:
        raise ApprovalAuthorizationError("The publication approval signer binding is invalid")
    return PublicationAuthorization(approval_id=approval.id, signed_off_by=signer)
