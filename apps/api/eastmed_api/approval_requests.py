from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal, cast
from uuid import UUID

from eastmed_schema.enums import CorrectionImpact
from eastmed_schema.models import AuditLog, DeskApproval, DeskApprovalRequest, DeskUser, Event
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from eastmed_api.approval_bindings import binding_hash
from eastmed_api.approvals import (
    APPROVER_ROLES,
    EVENT_PUBLICATION_APPROVAL,
    OPERATIONAL_CORRECTION_APPROVAL,
    PUBLISHER_ROLES,
    STRONG_ASSURANCE,
    ApprovalAuthorizationError,
    ApprovalWorkflowError,
    approve_event_publication,
    approve_operational_correction,
)
from eastmed_api.contracts import (
    ApprovalRequestDecisionCreate,
    ApprovalRequestRead,
    CorrectionApprovalCreate,
    CorrectionApprovalRequestCreate,
    CorrectionContentDraft,
    CorrectionDraft,
    EventVersionContentDraft,
    EventVersionDraft,
    PublicationApprovalCreate,
    PublicationApprovalRequestCreate,
)
from eastmed_api.corrections import build_correction_plan
from eastmed_api.publication import preview_event_version
from eastmed_api.security import DeskPrincipal

REQUEST_VALIDITY = timedelta(hours=24)
PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"
CANCELLED = "cancelled"
EXPIRED = "expired"
REQUEST_STATUSES = frozenset({PENDING, APPROVED, REJECTED, CANCELLED, EXPIRED})
REQUEST_TYPES = frozenset({EVENT_PUBLICATION_APPROVAL, OPERATIONAL_CORRECTION_APPROVAL})


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _effective_status(request: DeskApprovalRequest, *, now: datetime | None = None) -> str:
    current = now or datetime.now(UTC)
    if request.status == PENDING and _aware_utc(request.expires_at) <= current:
        return EXPIRED
    return request.status


def _request_hash(
    *, request_type: str, target_id: UUID, primary_user_id: UUID, draft: dict[str, object]
) -> str:
    return binding_hash(
        {
            "request_type": request_type,
            "target_id": str(target_id),
            "primary_user_id": str(primary_user_id),
            "draft": draft,
        }
    )


def _supersede_pending_requests(
    session: Session,
    *,
    request_type: str,
    target_id: UUID,
    primary_user_id: UUID,
    now: datetime,
) -> None:
    session.execute(
        update(DeskApprovalRequest)
        .where(
            DeskApprovalRequest.request_type == request_type,
            DeskApprovalRequest.target_id == target_id,
            DeskApprovalRequest.primary_user_id == primary_user_id,
            DeskApprovalRequest.status == PENDING,
        )
        .values(status=CANCELLED, decided_at=now, decision_reason="Superseded by a newer request")
    )


def create_publication_request(
    session: Session,
    *,
    event_id: UUID,
    payload: PublicationApprovalRequestCreate,
    principal: DeskPrincipal,
) -> DeskApprovalRequest:
    if principal.role not in PUBLISHER_ROLES:
        raise ApprovalAuthorizationError("Desk role cannot request publication approval")
    event = session.get(Event, event_id)
    if event is None:
        raise LookupError("Event not found")
    if event.severity < 3:
        raise ApprovalWorkflowError("Severity 1-2 publications do not require approval requests")

    pending_draft = EventVersionDraft(
        **payload.draft.model_dump(),
        published_by=principal.actor,
        signed_off_by="approval:pending",
    )
    preview = preview_event_version(session, event_id=event_id, payload=pending_draft)
    if not preview.ready:
        failure = next((check.detail for check in preview.checks if not check.passed), None)
        raise ApprovalWorkflowError(failure or "Publication policy checks did not pass")

    draft_json = payload.draft.model_dump(mode="json")
    request_hash = _request_hash(
        request_type=EVENT_PUBLICATION_APPROVAL,
        target_id=event_id,
        primary_user_id=principal.user_id,
        draft=draft_json,
    )
    existing = session.scalar(
        select(DeskApprovalRequest).where(
            DeskApprovalRequest.request_hash == request_hash,
            DeskApprovalRequest.status == PENDING,
        )
    )
    if existing is not None and _effective_status(existing) == PENDING:
        return existing

    now = datetime.now(UTC)
    _supersede_pending_requests(
        session,
        request_type=EVENT_PUBLICATION_APPROVAL,
        target_id=event_id,
        primary_user_id=principal.user_id,
        now=now,
    )
    request = DeskApprovalRequest(
        request_type=EVENT_PUBLICATION_APPROVAL,
        target_id=event_id,
        request_hash=request_hash,
        request_payload_json={
            "draft": draft_json,
            "preapproval_preview": preview.model_dump(mode="json"),
        },
        primary_user_id=principal.user_id,
        status=PENDING,
        request_reason=payload.reason,
        requested_at=now,
        expires_at=now + REQUEST_VALIDITY,
    )
    session.add(request)
    session.flush()
    session.add(
        AuditLog(
            actor=principal.actor,
            action="event_publication.approval_requested",
            entity="desk_approval_request",
            entity_id=request.id,
            payload_json={
                "event_id": str(event_id),
                "request_hash": request_hash,
                "expires_at": request.expires_at.isoformat(),
                "reason": payload.reason,
            },
        )
    )
    session.commit()
    session.refresh(request)
    return request


def create_correction_request(
    session: Session,
    *,
    payload: CorrectionApprovalRequestCreate,
    principal: DeskPrincipal,
) -> DeskApprovalRequest:
    if principal.role not in PUBLISHER_ROLES:
        raise ApprovalAuthorizationError("Desk role cannot request correction approval")
    if payload.draft.impact != CorrectionImpact.OPERATIONALLY_RELEVANT:
        raise ApprovalWorkflowError(
            "Only operationally relevant corrections use the approval inbox"
        )
    pending_draft = CorrectionDraft(
        **payload.draft.model_dump(),
        drafted_by=principal.actor,
        signed_off_by="approval:pending",
    )
    plan = build_correction_plan(session, **pending_draft.model_dump())
    draft_json = payload.draft.model_dump(mode="json")
    request_hash = _request_hash(
        request_type=OPERATIONAL_CORRECTION_APPROVAL,
        target_id=payload.draft.event_id,
        primary_user_id=principal.user_id,
        draft=draft_json,
    )
    existing = session.scalar(
        select(DeskApprovalRequest).where(
            DeskApprovalRequest.request_hash == request_hash,
            DeskApprovalRequest.status == PENDING,
        )
    )
    if existing is not None and _effective_status(existing) == PENDING:
        return existing

    now = datetime.now(UTC)
    _supersede_pending_requests(
        session,
        request_type=OPERATIONAL_CORRECTION_APPROVAL,
        target_id=payload.draft.event_id,
        primary_user_id=principal.user_id,
        now=now,
    )
    request = DeskApprovalRequest(
        request_type=OPERATIONAL_CORRECTION_APPROVAL,
        target_id=payload.draft.event_id,
        request_hash=request_hash,
        request_payload_json={
            "draft": draft_json,
            "preapproval_preview": {
                "preview_hash": plan.preview_hash,
                "channels": [channel.value for channel in plan.channels],
                "recipient_count": len(plan.recipients),
                "message": plan.message,
            },
        },
        primary_user_id=principal.user_id,
        status=PENDING,
        request_reason=payload.reason,
        requested_at=now,
        expires_at=now + REQUEST_VALIDITY,
    )
    session.add(request)
    session.flush()
    session.add(
        AuditLog(
            actor=principal.actor,
            action="operational_correction.approval_requested",
            entity="desk_approval_request",
            entity_id=request.id,
            payload_json={
                "event_id": str(payload.draft.event_id),
                "request_hash": request_hash,
                "expires_at": request.expires_at.isoformat(),
                "reason": payload.reason,
            },
        )
    )
    session.commit()
    session.refresh(request)
    return request


def decide_approval_request(
    session: Session,
    *,
    request_id: UUID,
    payload: ApprovalRequestDecisionCreate,
    principal: DeskPrincipal,
) -> DeskApprovalRequest:
    if principal.role not in APPROVER_ROLES:
        raise ApprovalAuthorizationError(
            "Approval requests require a senior analyst or administrator"
        )
    if principal.assurance not in STRONG_ASSURANCE:
        raise ApprovalAuthorizationError("Approval decisions require MFA")
    request = session.scalar(
        select(DeskApprovalRequest)
        .where(DeskApprovalRequest.id == request_id)
        .with_for_update()
    )
    if request is None:
        raise LookupError("Approval request not found")
    now = datetime.now(UTC)
    if _effective_status(request, now=now) == EXPIRED:
        request.status = EXPIRED
        request.decided_at = now
        request.decision_reason = "Request expired before a decision"
        session.commit()
        raise ApprovalWorkflowError("Approval request has expired")
    if request.status != PENDING:
        raise ApprovalWorkflowError("Approval request has already been decided")
    if request.primary_user_id == principal.user_id:
        raise ApprovalAuthorizationError("Approver must be distinct from the requester")

    if payload.decision == "reject":
        request.status = REJECTED
        request.decided_at = now
        request.decided_by_user_id = principal.user_id
        request.decision_reason = payload.reason
        session.add(
            AuditLog(
                actor=principal.actor,
                action="desk_approval_request.rejected",
                entity="desk_approval_request",
                entity_id=request.id,
                payload_json={"request_hash": request.request_hash, "reason": payload.reason},
            )
        )
        session.commit()
        session.refresh(request)
        return request

    draft = request.request_payload_json.get("draft")
    if not isinstance(draft, dict):
        raise ApprovalWorkflowError("Approval request payload is malformed")
    if request.request_type == EVENT_PUBLICATION_APPROVAL:
        publication_result = approve_event_publication(
            session,
            event_id=request.target_id,
            payload=PublicationApprovalCreate(
                publisher_user_id=request.primary_user_id,
                draft=EventVersionContentDraft.model_validate(draft),
                reason=payload.reason,
            ),
            principal=principal,
            commit=False,
        )
        approval = publication_result.approval
    elif request.request_type == OPERATIONAL_CORRECTION_APPROVAL:
        correction_result = approve_operational_correction(
            session,
            payload=CorrectionApprovalCreate(
                drafter_user_id=request.primary_user_id,
                draft=CorrectionContentDraft.model_validate(draft),
                reason=payload.reason,
            ),
            principal=principal,
            commit=False,
        )
        approval = correction_result.approval
    else:
        raise ApprovalWorkflowError("Approval request type is not supported")

    request.status = APPROVED
    request.decided_at = now
    request.decided_by_user_id = principal.user_id
    request.decision_reason = payload.reason
    request.approval_id = approval.id
    session.add(
        AuditLog(
            actor=principal.actor,
            action="desk_approval_request.approved",
            entity="desk_approval_request",
            entity_id=request.id,
            payload_json={
                "approval_id": str(approval.id),
                "release_hash": approval.binding_hash,
                "release_expires_at": approval.expires_at.isoformat(),
                "reason": payload.reason,
            },
        )
    )
    session.commit()
    session.refresh(request)
    return request


def list_approval_requests(
    session: Session,
    *,
    principal: DeskPrincipal,
    status: str | None = None,
    limit: int = 100,
) -> list[DeskApprovalRequest]:
    if status is not None and status not in REQUEST_STATUSES:
        raise ApprovalWorkflowError("Approval request status is invalid")
    query = select(DeskApprovalRequest)
    if principal.role not in APPROVER_ROLES:
        query = query.where(DeskApprovalRequest.primary_user_id == principal.user_id)
    if status is not None:
        if status == EXPIRED:
            query = query.where(
                (DeskApprovalRequest.status == EXPIRED)
                | (
                    (DeskApprovalRequest.status == PENDING)
                    & (DeskApprovalRequest.expires_at <= datetime.now(UTC))
                )
            )
        else:
            query = query.where(DeskApprovalRequest.status == status)
    return list(
        session.scalars(
            query.order_by(
                DeskApprovalRequest.requested_at.desc(), DeskApprovalRequest.id.desc()
            ).limit(limit)
        ).all()
    )


def approval_request_read(session: Session, request: DeskApprovalRequest) -> ApprovalRequestRead:
    primary = session.get(DeskUser, request.primary_user_id)
    decider = (
        session.get(DeskUser, request.decided_by_user_id)
        if request.decided_by_user_id is not None
        else None
    )
    approval = session.get(DeskApproval, request.approval_id) if request.approval_id else None
    approved_payload: dict[str, object] | None = None
    if approval is not None:
        raw_payload = approval.binding_payload_json.get("approved_draft")
        if isinstance(raw_payload, dict):
            approved_payload = raw_payload
    return ApprovalRequestRead(
        id=request.id,
        request_type=cast(
            Literal["event_publication", "operational_correction"], request.request_type
        ),
        target_id=request.target_id,
        request_hash=request.request_hash,
        request_payload=request.request_payload_json,
        primary_user_id=request.primary_user_id,
        primary_display_name=primary.display_name if primary is not None else "Unknown user",
        status=cast(
            Literal["pending", "approved", "rejected", "cancelled", "expired"],
            _effective_status(request),
        ),
        request_reason=request.request_reason,
        requested_at=request.requested_at,
        expires_at=request.expires_at,
        decided_at=request.decided_at,
        decided_by_user_id=request.decided_by_user_id,
        decided_by_display_name=decider.display_name if decider is not None else None,
        decision_reason=request.decision_reason,
        approval_id=request.approval_id,
        release_hash=approval.binding_hash if approval is not None else None,
        release_expires_at=approval.expires_at if approval is not None else None,
        approved_payload=approved_payload,
    )
