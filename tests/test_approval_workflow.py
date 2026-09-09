from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from eastmed_api.approval_bindings import (
    CLAIM_SECOND_REVIEW_APPROVAL,
    binding_hash,
    claim_review_snapshot,
    claim_second_review_is_valid,
)
from eastmed_api.approval_requests import (
    create_correction_request,
    decide_approval_request,
    list_approval_requests,
)
from eastmed_api.approvals import (
    OPERATIONAL_CORRECTION_APPROVAL,
    ApprovalAuthorizationError,
    approve_event_publication,
    authorize_correction_issue,
    authorize_event_publication,
)
from eastmed_api.contracts import (
    ApprovalRequestDecisionCreate,
    CorrectionApprovalRequestCreate,
    CorrectionContentDraft,
    EventVersionContentDraft,
    EventVersionPreviewRead,
    PublicationApprovalCreate,
    PublicationGateCheckRead,
    PublicationSentence,
)
from eastmed_api.security import DeskPrincipal
from eastmed_schema import Base
from eastmed_schema.enums import (
    AuthAssurance,
    ClaimState,
    CorrectionImpact,
    CorrectionType,
    Corridor,
    DeskRole,
    EventStatus,
    EventType,
)
from eastmed_schema.models import Claim, DeskApproval, DeskApprovalRequest, DeskUser, Event
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

TABLES = ("desk_users", "desk_approvals", "desk_approval_requests", "audit_log")


@pytest.fixture
def session() -> Generator[Session, None, None]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[Base.metadata.tables[name] for name in TABLES])
    with Session(engine, expire_on_commit=False) as db:
        yield db


def add_user(session: Session, *, role: DeskRole, label: str) -> DeskUser:
    user = DeskUser(
        auth_issuer="eastmed-console",
        auth_subject=f"{label}-{uuid4()}",
        email=f"{label}-{uuid4()}@example.test",
        display_name=label.title(),
        role=role,
        active=True,
        created_by="test",
    )
    session.add(user)
    session.commit()
    return user


def principal(user: DeskUser) -> DeskPrincipal:
    return DeskPrincipal(
        user_id=user.id,
        issuer=user.auth_issuer,
        subject=user.auth_subject,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        assurance=AuthAssurance.MFA,
    )


def event(event_id: object) -> Event:
    return Event(
        id=event_id,
        slug="high-severity-test-event",
        event_type=EventType.SECURITY_INCIDENT,
        corridor=Corridor.EAST_MED,
        status=EventStatus.DEVELOPING,
        severity=4,
    )


def approval_payload(publisher: DeskUser) -> PublicationApprovalCreate:
    return PublicationApprovalCreate(
        publisher_user_id=publisher.id,
        reason="Independent review of the exact severity-four publication preview.",
        draft=EventVersionContentDraft(
            title="High-severity maritime incident",
            sentences=[
                PublicationSentence(
                    section="reported",
                    text="An authority reported a maritime incident.",
                    claim_ids=[uuid4()],
                )
            ],
            evidence_ids=[],
        ),
    )


def approved_preview() -> EventVersionPreviewRead:
    return EventVersionPreviewRead(
        previous_version_no=None,
        next_version_no=1,
        preview_hash="a" * 64,
        ready=True,
        checks=[
            PublicationGateCheckRead(
                key="severity-signoff",
                label="Severity release has human sign-off",
                passed=True,
                detail="Authenticated second-person approval is present.",
            )
        ],
        diff={},
    )


def patch_event_lookup(
    session: Session, monkeypatch: pytest.MonkeyPatch, row: Event
) -> None:
    original_get = session.get

    def get(model: type[object], identifier: object) -> object | None:
        if model is Event:
            return row if identifier == row.id else None
        return original_get(model, identifier)

    monkeypatch.setattr(session, "get", get)


def test_approval_is_authenticated_content_bound_short_lived_and_retrievable(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    publisher = add_user(session, role=DeskRole.ANALYST, label="publisher")
    approver = add_user(session, role=DeskRole.SENIOR_ANALYST, label="approver")
    event_id = uuid4()
    patch_event_lookup(session, monkeypatch, event(event_id))
    monkeypatch.setattr(
        "eastmed_api.approvals.preview_event_version",
        lambda *_args, **_kwargs: approved_preview(),
    )

    result = approve_event_publication(
        session,
        event_id=event_id,
        payload=approval_payload(publisher),
        principal=principal(approver),
    )

    assert result.approved_draft.published_by == f"desk:{publisher.id}"
    assert result.approved_draft.signed_off_by == f"desk:{approver.id}"
    assert result.approval.binding_hash == "a" * 64
    assert result.approval.binding_payload_json["approved_draft"]["title"] == (
        "High-severity maritime incident"
    )
    validity = result.approval.expires_at - result.approval.approved_at
    assert validity == timedelta(minutes=15)
    assert session.scalar(select(func.count()).select_from(DeskApproval)) == 1

    authorization = authorize_event_publication(
        session,
        event_id=event_id,
        preview_hash="a" * 64,
        principal=principal(publisher),
    )
    assert authorization.approval_id == result.approval.id
    assert authorization.signed_off_by == f"desk:{approver.id}"


def test_approval_rejects_self_signoff_changed_content_and_expired_authorization(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    publisher = add_user(session, role=DeskRole.SENIOR_ANALYST, label="publisher")
    approver = add_user(session, role=DeskRole.ADMINISTRATOR, label="approver")
    event_id = uuid4()
    patch_event_lookup(session, monkeypatch, event(event_id))
    monkeypatch.setattr(
        "eastmed_api.approvals.preview_event_version",
        lambda *_args, **_kwargs: approved_preview(),
    )

    with pytest.raises(ApprovalAuthorizationError, match="distinct"):
        approve_event_publication(
            session,
            event_id=event_id,
            payload=approval_payload(publisher),
            principal=principal(publisher),
        )

    result = approve_event_publication(
        session,
        event_id=event_id,
        payload=approval_payload(publisher),
        principal=principal(approver),
    )
    with pytest.raises(ApprovalAuthorizationError, match="exact publication preview"):
        authorize_event_publication(
            session,
            event_id=event_id,
            preview_hash="b" * 64,
            principal=principal(publisher),
        )

    result.approval.approved_at = datetime.now(UTC) - timedelta(minutes=30)
    result.approval.expires_at = datetime.now(UTC) - timedelta(minutes=15)
    session.commit()
    with pytest.raises(ApprovalAuthorizationError, match="expired"):
        authorize_event_publication(
            session,
            event_id=event_id,
            preview_hash="a" * 64,
            principal=principal(publisher),
        )


def test_sensitive_claim_approval_is_invalidated_by_material_change() -> None:
    primary_id = uuid4()
    approver_id = uuid4()
    approval_id = uuid4()
    claim = Claim(
        id=uuid4(),
        event_id=uuid4(),
        text="An authority reported casualties.",
        claimant="Authority",
        claim_state=ClaimState.REPORTED,
        occurred_at=None,
        quantity_json=[],
        proposed_by=f"desk:{primary_id}",
        reviewed_by=f"desk:{primary_id}",
        second_reviewed_by=f"desk:{approver_id}",
        second_review_approval_id=approval_id,
        reviewed_at=datetime.now(UTC),
        sensitivity_flags=["casualties"],
        first_seen_at=datetime.now(UTC),
    )
    db = MagicMock(spec=Session)
    db.scalars.return_value.all.return_value = []
    snapshot = claim_review_snapshot(db, claim=claim)
    approval = DeskApproval(
        id=approval_id,
        approval_type=CLAIM_SECOND_REVIEW_APPROVAL,
        target_id=claim.id,
        binding_hash=binding_hash(snapshot),
        binding_payload_json={"claim_snapshot": snapshot},
        primary_user_id=primary_id,
        approved_by_user_id=approver_id,
        auth_assurance=AuthAssurance.MFA,
        reason="Independent review of a sensitivity-flagged factual claim.",
        approved_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
    )
    primary = DeskUser(
        id=primary_id,
        auth_issuer="eastmed-console",
        auth_subject="sensitive-primary",
        email="sensitive-primary@example.test",
        display_name="Sensitive Primary",
        role=DeskRole.ANALYST,
        active=True,
        created_by="test",
    )
    approver = DeskUser(
        id=approver_id,
        auth_issuer="eastmed-console",
        auth_subject="sensitive-approver",
        email="sensitive-approver@example.test",
        display_name="Sensitive Approver",
        role=DeskRole.SENIOR_ANALYST,
        active=True,
        created_by="test",
    )
    rows = {
        (DeskApproval, approval_id): approval,
        (DeskUser, primary_id): primary,
        (DeskUser, approver_id): approver,
    }
    db.get.side_effect = lambda model, identifier: rows.get((model, identifier))

    assert claim_second_review_is_valid(db, claim=claim)
    claim.text = "An authority revised the casualty report."
    assert not claim_second_review_is_valid(db, claim=claim)

    claim.text = "An authority reported casualties."
    approval.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert not claim_second_review_is_valid(db, claim=claim)

    approval.expires_at = datetime.now(UTC) + timedelta(minutes=15)
    approver.active = False
    assert not claim_second_review_is_valid(db, claim=claim)


def test_operational_correction_requires_exact_distinct_approval(
    session: Session,
) -> None:
    drafter = add_user(session, role=DeskRole.ANALYST, label="correction-drafter")
    approver = add_user(
        session,
        role=DeskRole.SENIOR_ANALYST,
        label="correction-approver",
    )
    event_id = uuid4()
    approval = DeskApproval(
        approval_type=OPERATIONAL_CORRECTION_APPROVAL,
        target_id=event_id,
        binding_hash="c" * 64,
        binding_payload_json={
            "approved_draft": {
                "drafted_by": f"desk:{drafter.id}",
                "signed_off_by": f"desk:{approver.id}",
            }
        },
        primary_user_id=drafter.id,
        approved_by_user_id=approver.id,
        auth_assurance=AuthAssurance.MFA,
        reason="Independent approval of the exact operational correction.",
        approved_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
    )
    session.add(approval)
    session.commit()

    authorization = authorize_correction_issue(
        session,
        event_id=event_id,
        impact=CorrectionImpact.OPERATIONALLY_RELEVANT,
        preview_hash="c" * 64,
        principal=principal(drafter),
    )
    assert authorization.approval_id == approval.id
    assert authorization.signed_off_by == f"desk:{approver.id}"

    with pytest.raises(ApprovalAuthorizationError, match="exact operational correction"):
        authorize_correction_issue(
            session,
            event_id=event_id,
            impact=CorrectionImpact.OPERATIONALLY_RELEVANT,
            preview_hash="d" * 64,
            principal=principal(drafter),
        )
    with pytest.raises(ApprovalAuthorizationError, match="senior analyst"):
        authorize_correction_issue(
            session,
            event_id=event_id,
            impact=CorrectionImpact.NON_OPERATIONAL,
            preview_hash="e" * 64,
            principal=principal(drafter),
        )


def test_persistent_correction_request_is_visible_and_approved_atomically(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    drafter = add_user(session, role=DeskRole.ANALYST, label="request-drafter")
    approver = add_user(session, role=DeskRole.SENIOR_ANALYST, label="request-approver")
    event_id = uuid4()
    preview_hash = "f" * 64
    plan = MagicMock()
    plan.preview_hash = preview_hash
    plan.channels = ()
    plan.recipients = ()
    plan.message = {"note": "Exact operational correction"}
    monkeypatch.setattr(
        "eastmed_api.approval_requests.build_correction_plan", lambda *_a, **_k: plan
    )
    monkeypatch.setattr("eastmed_api.approvals.build_correction_plan", lambda *_a, **_k: plan)

    request = create_correction_request(
        session,
        payload=CorrectionApprovalRequestCreate(
            draft=CorrectionContentDraft(
                event_id=event_id,
                version_from_id=uuid4(),
                version_to_id=uuid4(),
                correction_type=CorrectionType.CORRECTION,
                impact=CorrectionImpact.OPERATIONALLY_RELEVANT,
                note="The original convoy time was incorrect.",
                root_cause="The source timezone was transposed.",
                corrective_action="A second analyst checks every timezone conversion.",
                detected_at=datetime.now(UTC) - timedelta(minutes=10),
            ),
            reason="Operational impact requires independent review.",
        ),
        principal=principal(drafter),
    )

    assert request.status == "pending"
    assert list_approval_requests(session, principal=principal(drafter)) == [request]
    assert list_approval_requests(session, principal=principal(approver)) == [request]

    approved = decide_approval_request(
        session,
        request_id=request.id,
        payload=ApprovalRequestDecisionCreate(
            decision="approve",
            reason="Reviewed exact versions, impact, cause, and corrective action.",
        ),
        principal=principal(approver),
    )

    assert approved.status == "approved"
    assert approved.approval_id is not None
    approval = session.get(DeskApproval, approved.approval_id)
    assert approval is not None
    assert approval.binding_hash == preview_hash
    assert approval.primary_user_id == drafter.id
    assert approval.approved_by_user_id == approver.id
    assert session.scalar(select(func.count()).select_from(DeskApprovalRequest)) == 1


def test_approval_request_rejects_same_person_and_weak_authentication(
    session: Session,
) -> None:
    requester = add_user(session, role=DeskRole.SENIOR_ANALYST, label="same-requester")
    request = DeskApprovalRequest(
        request_type=OPERATIONAL_CORRECTION_APPROVAL,
        target_id=uuid4(),
        request_hash="e" * 64,
        request_payload_json={"draft": {}},
        primary_user_id=requester.id,
        status="pending",
        request_reason="Independent approval is required for this correction.",
        requested_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    session.add(request)
    session.commit()

    with pytest.raises(ApprovalAuthorizationError, match="distinct"):
        decide_approval_request(
            session,
            request_id=request.id,
            payload=ApprovalRequestDecisionCreate(
                decision="reject",
                reason="This request must be handled by another reviewer.",
            ),
            principal=principal(requester),
        )

    other = add_user(session, role=DeskRole.SENIOR_ANALYST, label="weak-approver")
    weak = DeskPrincipal(
        user_id=other.id,
        issuer=other.auth_issuer,
        subject=other.auth_subject,
        email=other.email,
        display_name=other.display_name,
        role=other.role,
        assurance=AuthAssurance.PASSWORD,
    )
    with pytest.raises(ApprovalAuthorizationError, match="MFA"):
        decide_approval_request(
            session,
            request_id=request.id,
            payload=ApprovalRequestDecisionCreate(
                decision="reject",
                reason="The exact request does not meet publication requirements.",
            ),
            principal=weak,
        )
