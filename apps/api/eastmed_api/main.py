import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from typing import Annotated, Any
from uuid import UUID

from eastmed_pipeline.claim_extraction import (
    ClaimExtractionError,
    ExtractedClaim,
    evaluate_component,
    record_qa,
    review_proposal,
    weekly_reason_report,
)
from eastmed_pipeline.email_ingest import ingest_postmark_message
from eastmed_pipeline.lineage import (
    LineageReviewError,
    evaluate_lineage_component,
    review_lineage_proposal,
)
from eastmed_pipeline.operations import run_source_now
from eastmed_pipeline.reconciliation_read import (
    get_case_timeline,
    get_case_view,
    get_daily_report,
    get_service_health,
    list_cases,
)
from eastmed_pipeline.rights import RightsPolicyError
from eastmed_pipeline.triage import (
    TriageActionInput,
    TriageWorkflowError,
    apply_triage_action,
)
from eastmed_schema.enums import (
    AccountTier,
    ClaimExtractionProposalStatus,
    CorrectionImpact,
    Corridor,
    DeskRole,
    EventStatus,
    EventType,
    LineageProposalStatus,
    SourceTier,
    TriageStatus,
)
from eastmed_schema.models import (
    Account,
    AICapabilityControl,
    AISystemVersion,
    AuditLog,
    ClaimExtractionProposal,
    ClaimExtractionRun,
    Correction,
    DeskAlert,
    DeskUser,
    Event,
    EventVersion,
    LineageProposal,
    Source,
    SourceRecord,
    StateKnowledgeReport,
    TriageItem,
)
from eastmed_shared import configure_error_monitoring, get_settings
from eastmed_shared.logging import configure_logging
from eastmed_shared.reconciliation.read_model import PresentationStatus
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from eastmed_api.ai_governance import (
    AIGovernanceError,
    create_desk_user,
    list_ai_system_versions,
    list_latest_capability_controls,
    register_ai_system_version,
    set_capability_control,
)
from eastmed_api.ai_incidents import (
    append_ai_incident_event,
    create_ai_incident,
    list_ai_incidents,
)
from eastmed_api.ais import list_ais_positions
from eastmed_api.alerts import (
    AlertWorkflowError,
    build_alert_plan,
    delivery_dashboard,
    release_alert,
)
from eastmed_api.api_keys import (
    ApiKeyWorkflowError,
    issue_account_api_key,
    list_account_api_keys,
    revoke_account_api_key,
)
from eastmed_api.approval_requests import (
    approval_request_read,
    create_correction_request,
    create_publication_request,
    decide_approval_request,
    list_approval_requests,
)
from eastmed_api.approvals import (
    ApprovalAuthorizationError,
    ApprovalWorkflowError,
    approve_event_publication,
    approve_operational_correction,
    approve_sensitive_claim,
    authorize_correction_issue,
    authorize_event_publication,
)
from eastmed_api.briefs import (
    BriefWorkflowError,
    compile_daily_brief,
    daily_brief_by_date,
    finalize_daily_brief,
    latest_finalized_brief,
)
from eastmed_api.calendar import (
    CalendarWorkflowError,
    list_calendar_events,
    publish_calendar_event,
)
from eastmed_api.contracts import (
    AccountRead,
    AccountUserRead,
    AICapabilityControlCreate,
    AICapabilityControlRead,
    AIIncidentCreate,
    AIIncidentEventCreate,
    AIIncidentRead,
    AISPositionListRead,
    AISystemManifest,
    AISystemVersionCreate,
    AISystemVersionRead,
    AlertPreviewRead,
    AlertReleaseCreate,
    AlertReleaseDraft,
    AlertReleaseRead,
    ApiKeyCreate,
    ApiKeyIssuedRead,
    ApiKeyRead,
    ApiKeyRevokeCreate,
    ApprovalRequestDecisionCreate,
    ApprovalRequestRead,
    BriefCompileCreate,
    BriefFinalizeCreate,
    CalendarEventListRead,
    CalendarEventPublishCreate,
    CalendarEventRead,
    ClaimCreate,
    ClaimExtractionEvaluationRead,
    ClaimExtractionProposalRead,
    ClaimExtractionQACreate,
    ClaimExtractionQARead,
    ClaimExtractionReviewCreate,
    ClaimExtractionReviewRead,
    ClaimExtractionWeeklyReportRead,
    ClaimSecondReviewCreate,
    ClaimSecondReviewRead,
    ClaimUpdate,
    CorrectionApprovalCreate,
    CorrectionApprovalRead,
    CorrectionApprovalRequestCreate,
    CorrectionDraft,
    CorrectionIssueCreate,
    CorrectionPreviewRead,
    CorrectionRead,
    CustomerChannelsUpdate,
    DailyBriefRead,
    DataClaimRead,
    DataEventListRead,
    DataEventRead,
    DataVersionRead,
    DeliveryDashboardRead,
    DeskAlertRead,
    DeskPrincipalRead,
    DeskUserCreate,
    DeskUserRead,
    EventVersionCreate,
    EventVersionDraft,
    EventVersionPreviewRead,
    EventVersionRead,
    EventWorkspaceRead,
    EvidenceLinkCreate,
    EvidenceLinkRead,
    HealthResponse,
    InboundReceipt,
    LineageProposalRead,
    LineageReviewCreate,
    LineageReviewRead,
    OpenEventRead,
    OperationsDashboardRead,
    PipelineEvaluationRead,
    PollerHealthRead,
    PortalAccessUpdate,
    PortalArchiveRead,
    PortalBoardRead,
    PortalEventRead,
    PortalMeRead,
    PostmarkInboundMessage,
    PublicationApprovalCreate,
    PublicationApprovalRead,
    PublicationApprovalRequestCreate,
    QualityScoreboardRead,
    ReliabilityReceiptRead,
    SourceCreate,
    SourceOperationalUpdate,
    SourceRead,
    StateKnowledgeReportCreate,
    StateKnowledgeReportRead,
    TriageActionCreate,
    TriageActionRead,
    TriageItemRead,
    TTVCreate,
    TTVRead,
    TTVUpdate,
    WatchProfileCreate,
    WatchProfileRead,
    WatchProfileUpdate,
    WhatsAppWebhookRead,
    WorkspaceClaimRead,
)
from eastmed_api.corrections import (
    CorrectionWorkflowError,
    build_correction_plan,
    issue_correction,
)
from eastmed_api.data_api import (
    get_data_claim,
    get_data_event,
    get_data_version,
    list_data_claims,
    list_data_events,
    list_data_versions,
)
from eastmed_api.database import get_db
from eastmed_api.operations_dashboard import operations_dashboard
from eastmed_api.poller_health import poller_health
from eastmed_api.portal import (
    portal_archive,
    portal_board,
    portal_event,
    record_portal_usage,
)
from eastmed_api.publication import (
    PublicationPolicyError,
    preview_event_version,
    publish_event_version,
)
from eastmed_api.quality import (
    QualityWorkflowError,
    create_ttv_log,
    quality_scoreboard,
    update_ttv_log,
)
from eastmed_api.reconciliation_service import ingest_reconciliation_record
from eastmed_api.reliability import get_reliability_receipt, latest_reliability_receipt
from eastmed_api.reports import (
    ReportWorkflowError,
    create_state_knowledge_report,
    render_state_knowledge_pdf,
)
from eastmed_api.security import (
    DataApiPrincipal,
    DeskPrincipal,
    PortalPrincipal,
    require_data_api_key,
    require_data_scope,
    require_desk_principal,
    require_desk_role,
    require_outbound_enabled,
    require_portal_user,
    require_postmark_basic,
    require_whatsapp_basic,
)
from eastmed_api.watch_profiles import (
    WatchProfileError,
    create_watch_profile,
    disable_watch_profile,
    list_accounts,
    list_watch_profiles,
    update_customer_channels,
    update_portal_access,
    update_watch_profile,
)
from eastmed_api.whatsapp import process_whatsapp_webhook
from eastmed_api.workspace import (
    WorkspaceError,
    create_claim,
    get_event_workspace,
    link_evidence,
    update_claim,
)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging(get_settings().log_level)
    yield


settings = get_settings()
configure_error_monitoring(settings)

app = FastAPI(
    title="East Med Intelligence API",
    version="0.1.0",
    docs_url="/api/v1/docs",
    openapi_url="/api/v1/openapi.json",
    redoc_url=None,
    lifespan=lifespan,
)

DB = Annotated[Session, Depends(get_db)]
DeskAuth = Annotated[DeskPrincipal, Depends(require_desk_principal)]
PostmarkAuth = Annotated[None, Depends(require_postmark_basic)]
PortalAuth = Annotated[PortalPrincipal, Depends(require_portal_user)]
WhatsAppAuth = Annotated[None, Depends(require_whatsapp_basic)]
DataAuth = Annotated[DataApiPrincipal, Depends(require_data_api_key)]
OutboundGate = Annotated[None, Depends(require_outbound_enabled)]

ANALYST_ROLES = frozenset({DeskRole.ANALYST, DeskRole.SENIOR_ANALYST, DeskRole.ADMINISTRATOR})
SENIOR_ROLES = frozenset({DeskRole.SENIOR_ANALYST, DeskRole.ADMINISTRATOR})
ADMIN_ROLES = frozenset({DeskRole.ADMINISTRATOR})
RIGHTS_ROLES = frozenset({DeskRole.COMPLIANCE, DeskRole.ADMINISTRATOR})


@app.get("/health/live", response_model=HealthResponse, include_in_schema=False)
def liveness() -> HealthResponse:
    return HealthResponse()


@app.get("/health/ready", response_model=HealthResponse, include_in_schema=False)
def readiness(db: DB) -> HealthResponse:
    db.execute(text("SELECT 1"))
    return HealthResponse()


@app.get("/health", response_model=HealthResponse, include_in_schema=False)
def health(db: DB) -> HealthResponse:
    return readiness(db)


def _desk_user_read(user: DeskUser) -> DeskUserRead:
    return DeskUserRead.model_validate(user)


def _ai_system_version_read(row: AISystemVersion) -> AISystemVersionRead:
    return AISystemVersionRead(
        id=row.id,
        name=row.name,
        purpose=row.purpose,
        manifest_schema_version=row.manifest_schema_version,
        manifest=AISystemManifest.model_validate(row.manifest_json),
        fingerprint=row.fingerprint,
        created_by_user_id=row.created_by_user_id,
        created_at=row.created_at,
    )


def _ai_capability_control_read(row: AICapabilityControl) -> AICapabilityControlRead:
    return AICapabilityControlRead(
        id=row.id,
        scope=row.scope,
        revision=row.revision,
        mode=row.mode,
        risk_tier=row.risk_tier,
        system_version_id=row.system_version_id,
        reason=row.reason,
        approval_refs=row.approval_refs_json,
        changed_by_user_id=row.changed_by_user_id,
        auth_assurance=row.auth_assurance,
        previous_control_id=row.previous_control_id,
        expires_at=row.expires_at,
        changed_at=row.changed_at,
    )


@app.get("/api/v1/desk/me", response_model=DeskPrincipalRead, tags=["AI Governance"])
def desk_me(principal: DeskAuth) -> DeskPrincipalRead:
    return DeskPrincipalRead(
        id=principal.user_id,
        auth_issuer=principal.issuer,
        auth_subject=principal.subject,
        email=principal.email,
        display_name=principal.display_name,
        role=principal.role,
        auth_assurance=principal.assurance,
    )


@app.post(
    "/api/v1/ai/governance/desk-users",
    response_model=DeskUserRead,
    status_code=status.HTTP_201_CREATED,
    tags=["AI Governance"],
)
def ai_governance_create_desk_user(
    payload: DeskUserCreate, db: DB, principal: DeskAuth
) -> DeskUserRead:
    try:
        user = create_desk_user(
            db,
            auth_issuer=payload.auth_issuer,
            auth_subject=payload.auth_subject,
            email=payload.email.casefold(),
            display_name=payload.display_name,
            role=payload.role,
            principal=principal,
        )
    except AIGovernanceError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return _desk_user_read(user)


@app.get(
    "/api/v1/ai/governance/system-versions",
    response_model=list[AISystemVersionRead],
    tags=["AI Governance"],
)
def ai_governance_system_versions(db: DB, _: DeskAuth) -> list[AISystemVersionRead]:
    return [_ai_system_version_read(row) for row in list_ai_system_versions(db)]


@app.post(
    "/api/v1/ai/governance/system-versions",
    response_model=AISystemVersionRead,
    status_code=status.HTTP_201_CREATED,
    tags=["AI Governance"],
)
def ai_governance_register_system(
    payload: AISystemVersionCreate, db: DB, principal: DeskAuth
) -> AISystemVersionRead:
    try:
        row = register_ai_system_version(
            db,
            name=payload.name,
            purpose=payload.purpose,
            manifest=payload.manifest,
            principal=principal,
        )
    except AIGovernanceError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return _ai_system_version_read(row)


@app.get(
    "/api/v1/ai/governance/capabilities",
    response_model=list[AICapabilityControlRead],
    tags=["AI Governance"],
)
def ai_governance_capabilities(db: DB, _: DeskAuth) -> list[AICapabilityControlRead]:
    return [_ai_capability_control_read(row) for row in list_latest_capability_controls(db)]


@app.post(
    "/api/v1/ai/governance/capabilities/{scope}",
    response_model=AICapabilityControlRead,
    status_code=status.HTTP_201_CREATED,
    tags=["AI Governance"],
)
def ai_governance_set_capability(
    scope: str,
    payload: AICapabilityControlCreate,
    db: DB,
    principal: DeskAuth,
) -> AICapabilityControlRead:
    try:
        row = set_capability_control(db, scope=scope, payload=payload, principal=principal)
    except AIGovernanceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _ai_capability_control_read(row)


@app.get(
    "/api/v1/ai/governance/incidents",
    response_model=list[AIIncidentRead],
    tags=["AI Governance"],
)
def ai_governance_incidents(
    db: DB,
    _: DeskAuth,
    include_resolved: bool = Query(default=True),
) -> list[AIIncidentRead]:
    return list_ai_incidents(db, include_resolved=include_resolved)


@app.post(
    "/api/v1/ai/governance/incidents",
    response_model=AIIncidentRead,
    status_code=status.HTTP_201_CREATED,
    tags=["AI Governance"],
)
def ai_governance_create_incident(
    payload: AIIncidentCreate, db: DB, principal: DeskAuth
) -> AIIncidentRead:
    try:
        return create_ai_incident(db, payload=payload, principal=principal)
    except AIGovernanceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post(
    "/api/v1/ai/governance/incidents/{incident_id}/events",
    response_model=AIIncidentRead,
    status_code=status.HTTP_201_CREATED,
    tags=["AI Governance"],
)
def ai_governance_append_incident_event(
    incident_id: UUID,
    payload: AIIncidentEventCreate,
    db: DB,
    principal: DeskAuth,
) -> AIIncidentRead:
    try:
        return append_ai_incident_event(
            db,
            incident_id=incident_id,
            payload=payload,
            principal=principal,
        )
    except AIGovernanceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/v1/portal/me", response_model=PortalMeRead)
def portal_me(principal: PortalAuth) -> PortalMeRead:
    return PortalMeRead(
        user_id=principal.user_id,
        account_id=principal.account_id,
        email=principal.email,
        company=principal.company,
        role=principal.role,
    )


@app.get("/api/v1/public/board", response_model=PortalBoardRead)
def public_board(db: DB) -> PortalBoardRead:
    return portal_board(db)


@app.get("/api/v1/public/scoreboard", response_model=QualityScoreboardRead)
def public_quality_scoreboard(db: DB) -> QualityScoreboardRead:
    return quality_scoreboard(db)


@app.get("/api/v1/data/events", response_model=DataEventListRead, tags=["Data API"])
def data_events(
    db: DB,
    principal: DataAuth,
    corridor: Corridor | None = None,
    event_type: EventType | None = None,
    event_status: Annotated[EventStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> DataEventListRead:
    require_data_scope(principal, "events:read")
    return list_data_events(
        db,
        corridor=corridor,
        event_type=event_type,
        event_status=event_status,
        limit=limit,
        offset=offset,
    )


@app.get("/api/v1/data/events/{event_id}", response_model=DataEventRead, tags=["Data API"])
def data_event(event_id: UUID, db: DB, principal: DataAuth) -> DataEventRead:
    require_data_scope(principal, "events:read")
    try:
        return get_data_event(db, event_id=event_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get(
    "/api/v1/data/events/{event_id}/versions",
    response_model=list[DataVersionRead],
    tags=["Data API"],
)
def data_event_versions(event_id: UUID, db: DB, principal: DataAuth) -> list[DataVersionRead]:
    require_data_scope(principal, "versions:read")
    try:
        return list_data_versions(db, event_id=event_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get(
    "/api/v1/data/versions/{version_id}",
    response_model=DataVersionRead,
    tags=["Data API"],
)
def data_version(version_id: UUID, db: DB, principal: DataAuth) -> DataVersionRead:
    require_data_scope(principal, "versions:read")
    try:
        return get_data_version(db, version_id=version_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get(
    "/api/v1/data/versions/{version_id}/reliability-receipt",
    response_model=ReliabilityReceiptRead,
    tags=["Data API"],
)
def data_version_reliability_receipt(
    version_id: UUID, db: DB, principal: DataAuth
) -> ReliabilityReceiptRead:
    require_data_scope(principal, "versions:read")
    try:
        return get_reliability_receipt(db, event_version_id=version_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get(
    "/api/v1/data/events/{event_id}/claims",
    response_model=list[DataClaimRead],
    tags=["Data API"],
)
def data_event_claims(event_id: UUID, db: DB, principal: DataAuth) -> list[DataClaimRead]:
    require_data_scope(principal, "claims:read")
    try:
        return list_data_claims(db, event_id=event_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/v1/data/claims/{claim_id}", response_model=DataClaimRead, tags=["Data API"])
def data_claim(claim_id: UUID, db: DB, principal: DataAuth) -> DataClaimRead:
    require_data_scope(principal, "claims:read")
    try:
        return get_data_claim(db, claim_id=claim_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/v1/data/ais", response_model=AISPositionListRead, tags=["Data API"])
def data_ais_positions(
    db: DB,
    principal: DataAuth,
    corridor: Corridor | None = None,
    max_age_minutes: Annotated[int, Query(ge=1, le=1_440)] = 180,
    limit: Annotated[int, Query(ge=1, le=2_000)] = 500,
) -> AISPositionListRead:
    require_data_scope(principal, "ais:read")
    return list_ais_positions(
        db,
        corridor=corridor,
        max_age_minutes=max_age_minutes,
        limit=limit,
    )


# --- Reconciliation customer API (blueprint 25) --------------------------------------
# Authenticated ingestion + account-scoped read routes wiring the work-order-8 read model.
# Every route is scoped to the API key's account; a case that is not the caller's is reported
# as not found, never confirmed to exist. Ingestion takes the tenant from the authenticated
# key, never from the payload (blueprint 17).

RECON_READ_SCOPE = "recon:read"
RECON_INGEST_SCOPE = "recon:ingest"


@app.post("/api/v1/recon/ingest", tags=["Reconciliation"])
def recon_ingest(payload: dict[str, Any], db: DB, principal: DataAuth) -> dict[str, object]:
    require_data_scope(principal, RECON_INGEST_SCOPE)
    receipt = ingest_reconciliation_record(db, account_id=principal.account_id, payload=payload)
    if receipt.is_client_error:
        db.rollback()
        raise HTTPException(
            status_code=receipt.http_status,
            detail=receipt.detail or receipt.status.value,
        )
    db.commit()
    return receipt.render()


@app.get("/api/v1/recon/cases", tags=["Reconciliation"])
def recon_cases(
    db: DB,
    principal: DataAuth,
    presentation_status: Annotated[PresentationStatus | None, Query(alias="status")] = None,
    cursor: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, object]:
    require_data_scope(principal, RECON_READ_SCOPE)
    return list_cases(
        db,
        account_id=principal.account_id,
        status=presentation_status,
        cursor=cursor,
        limit=limit,
    ).render()


@app.get("/api/v1/recon/cases/{case_id}", tags=["Reconciliation"])
def recon_case(case_id: UUID, db: DB, principal: DataAuth) -> dict[str, object]:
    require_data_scope(principal, RECON_READ_SCOPE)
    view = get_case_view(db, account_id=principal.account_id, case_id=case_id)
    if view is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return view.render()


@app.get("/api/v1/recon/cases/{case_id}/timeline", tags=["Reconciliation"])
def recon_case_timeline(case_id: UUID, db: DB, principal: DataAuth) -> dict[str, object]:
    require_data_scope(principal, RECON_READ_SCOPE)
    timeline = get_case_timeline(db, account_id=principal.account_id, case_id=case_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail="Case not found")
    return timeline.render()


@app.get("/api/v1/recon/daily-report", tags=["Reconciliation"])
def recon_daily_report(
    db: DB,
    principal: DataAuth,
    period_start: datetime,
    period_end: datetime,
) -> dict[str, object]:
    require_data_scope(principal, RECON_READ_SCOPE)
    if period_end <= period_start:
        raise HTTPException(status_code=422, detail="period_end must be after period_start")
    return get_daily_report(
        db,
        account_id=principal.account_id,
        period_start=period_start,
        period_end=period_end,
    ).render()


@app.get("/api/v1/recon/health", tags=["Reconciliation"])
def recon_health(db: DB, principal: DataAuth) -> dict[str, object]:
    require_data_scope(principal, RECON_READ_SCOPE)
    return get_service_health(db, account_id=principal.account_id).render()


@app.get("/api/v1/portal/board", response_model=PortalBoardRead)
def subscriber_board(db: DB, principal: PortalAuth) -> PortalBoardRead:
    return portal_board(db, principal=principal)


@app.get("/api/v1/portal/calendar", response_model=CalendarEventListRead)
def subscriber_calendar(
    db: DB,
    principal: PortalAuth,
    starts_before: datetime | None = None,
    ends_after: datetime | None = None,
) -> CalendarEventListRead:
    result = list_calendar_events(
        db,
        starts_before=starts_before,
        ends_after=ends_after,
    )
    record_portal_usage(
        db,
        principal=principal,
        event_name="portal.calendar_view",
        properties={"result_count": len(result.results)},
    )
    return result


@app.get("/api/v1/portal/ais", response_model=AISPositionListRead)
def subscriber_ais_positions(
    db: DB,
    principal: PortalAuth,
    corridor: Corridor | None = None,
    max_age_minutes: Annotated[int, Query(ge=1, le=1_440)] = 180,
    limit: Annotated[int, Query(ge=1, le=1_000)] = 500,
) -> AISPositionListRead:
    result = list_ais_positions(
        db,
        corridor=corridor,
        max_age_minutes=max_age_minutes,
        limit=limit,
    )
    record_portal_usage(
        db,
        principal=principal,
        event_name="portal.ais_view",
        properties={
            "result_count": len(result.results),
            "cache_status": result.cache_status,
        },
    )
    return result


@app.get(
    "/api/v1/data/calendar",
    response_model=CalendarEventListRead,
    tags=["Data API"],
)
def data_calendar(
    db: DB,
    principal: DataAuth,
    starts_before: datetime | None = None,
    ends_after: datetime | None = None,
) -> CalendarEventListRead:
    require_data_scope(principal, "calendar:read")
    return list_calendar_events(
        db,
        starts_before=starts_before,
        ends_after=ends_after,
    )


@app.post(
    "/api/v1/calendar",
    response_model=CalendarEventRead,
    status_code=status.HTTP_201_CREATED,
)
def desk_publish_calendar_event(
    payload: CalendarEventPublishCreate,
    db: DB,
    principal: DeskAuth,
    _outbound: OutboundGate,
) -> CalendarEventRead:
    require_desk_role(principal, ANALYST_ROLES)
    payload = payload.model_copy(update={"published_by": principal.actor})
    try:
        return publish_calendar_event(db, payload)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CalendarWorkflowError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/v1/portal/archive", response_model=PortalArchiveRead)
def subscriber_archive(
    db: DB,
    principal: PortalAuth,
    q: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    corridor: Corridor | None = None,
    event_type: EventType | None = None,
    min_severity: Annotated[int | None, Query(ge=1, le=4)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> PortalArchiveRead:
    return portal_archive(
        db,
        principal=principal,
        query=q,
        corridor=corridor,
        event_type=event_type,
        min_severity=min_severity,
        limit=limit,
        offset=offset,
    )


@app.get("/api/v1/portal/events/{slug}", response_model=PortalEventRead)
def subscriber_event(slug: str, db: DB, principal: PortalAuth) -> PortalEventRead:
    if not 1 <= len(slug) <= 255:
        raise HTTPException(status_code=422, detail="Invalid event slug")
    try:
        return portal_event(db, principal=principal, slug=slug)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get(
    "/api/v1/portal/events/{slug}/reliability-receipt",
    response_model=ReliabilityReceiptRead,
)
def subscriber_event_reliability_receipt(
    slug: str, db: DB, _: PortalAuth
) -> ReliabilityReceiptRead:
    if not 1 <= len(slug) <= 255:
        raise HTTPException(status_code=422, detail="Invalid event slug")
    try:
        return latest_reliability_receipt(db, event_slug=slug)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/v1/portal/briefs/latest", response_model=DailyBriefRead)
def subscriber_latest_brief(db: DB, principal: PortalAuth) -> DailyBriefRead:
    try:
        brief = latest_finalized_brief(db)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    record_portal_usage(
        db,
        principal=principal,
        event_name="portal.brief_view",
        properties={"brief_id": str(brief.id), "brief_date": brief.brief_date.isoformat()},
    )
    return brief


def _report_pdf_response(report: StateKnowledgeReport) -> Response:
    return Response(
        content=render_state_knowledge_pdf(report),
        media_type="application/pdf",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f'attachment; filename="state-of-knowledge-{report.id}.pdf"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.post(
    "/api/v1/reports/state-of-knowledge",
    response_model=StateKnowledgeReportRead,
    status_code=status.HTTP_201_CREATED,
)
def desk_create_state_report(
    payload: StateKnowledgeReportCreate, db: DB, principal: DeskAuth
) -> StateKnowledgeReport:
    require_desk_role(principal, ANALYST_ROLES)
    try:
        return create_state_knowledge_report(
            db,
            event_id=payload.event_id,
            requested_timestamp=payload.requested_timestamp,
            requested_by=principal.actor,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ReportWorkflowError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/v1/reports/state-of-knowledge/{report_id}.pdf")
def desk_download_state_report(report_id: UUID, db: DB, _: DeskAuth) -> Response:
    report = db.get(StateKnowledgeReport, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="State-of-knowledge report not found")
    return _report_pdf_response(report)


@app.post(
    "/api/v1/portal/reports/state-of-knowledge",
    response_model=StateKnowledgeReportRead,
    status_code=status.HTTP_201_CREATED,
)
def portal_create_state_report(
    payload: StateKnowledgeReportCreate, db: DB, principal: PortalAuth
) -> StateKnowledgeReport:
    account = db.get(Account, principal.account_id)
    if account is None or account.tier not in {AccountTier.DESK_PRO, AccountTier.DATA}:
        raise HTTPException(status_code=403, detail="Desk Pro or Data access is required")
    try:
        report = create_state_knowledge_report(
            db,
            event_id=payload.event_id,
            requested_timestamp=payload.requested_timestamp,
            requested_by=principal.email,
            account_id=principal.account_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ReportWorkflowError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    record_portal_usage(
        db,
        principal=principal,
        event_name="portal.state_report_generated",
        properties={"report_id": str(report.id), "event_id": str(report.event_id)},
    )
    return report


@app.get("/api/v1/portal/reports/state-of-knowledge/{report_id}.pdf")
def portal_download_state_report(report_id: UUID, db: DB, principal: PortalAuth) -> Response:
    report = db.get(StateKnowledgeReport, report_id)
    if report is None or report.account_id != principal.account_id:
        raise HTTPException(status_code=404, detail="State-of-knowledge report not found")
    record_portal_usage(
        db,
        principal=principal,
        event_name="portal.state_report_downloaded",
        properties={"report_id": str(report.id), "event_id": str(report.event_id)},
    )
    return _report_pdf_response(report)


@app.get("/api/v1/briefs/{brief_date}", response_model=DailyBriefRead)
def desk_daily_brief(brief_date: date, db: DB, _: DeskAuth) -> DailyBriefRead:
    try:
        return daily_brief_by_date(db, brief_date=brief_date)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/v1/briefs/{brief_date}/compile", response_model=DailyBriefRead)
def desk_compile_daily_brief(
    brief_date: date,
    payload: BriefCompileCreate,
    db: DB,
    principal: DeskAuth,
) -> DailyBriefRead:
    require_desk_role(principal, ANALYST_ROLES)
    try:
        return compile_daily_brief(db, brief_date=brief_date, compiled_by=principal.actor)
    except BriefWorkflowError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/v1/briefs/{brief_id}/finalize", response_model=DailyBriefRead)
def desk_finalize_daily_brief(
    brief_id: UUID,
    payload: BriefFinalizeCreate,
    db: DB,
    principal: DeskAuth,
    _outbound: OutboundGate,
) -> DailyBriefRead:
    require_desk_role(principal, SENIOR_ROLES)
    payload = payload.model_copy(update={"finalized_by": principal.actor})
    try:
        return finalize_daily_brief(db, brief_id=brief_id, payload=payload)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except BriefWorkflowError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/v1/sources", response_model=list[SourceRead])
def list_sources(db: DB, _: DeskAuth) -> list[Source]:
    return list(db.scalars(select(Source).order_by(Source.tier, Source.name)).all())


@app.post("/api/v1/sources", response_model=SourceRead, status_code=status.HTTP_201_CREATED)
def create_source(payload: SourceCreate, db: DB, principal: DeskAuth) -> Source:
    require_desk_role(principal, ANALYST_ROLES | RIGHTS_ROLES)
    approval_requested = bool(
        payload.rights_reviewed_by
        or payload.automation_approved_at
        or payload.model_processing_approved_by
        or payload.model_processing_approved_at
    )
    if approval_requested:
        require_desk_role(principal, RIGHTS_ROLES)
        payload = payload.model_copy(
            update={
                "rights_reviewed_by": principal.actor,
                "model_processing_approved_by": (
                    principal.actor if payload.model_processing_approved_at else None
                ),
            }
        )
    if payload.syndicates_from_id and db.get(Source, payload.syndicates_from_id) is None:
        raise HTTPException(status_code=422, detail="Syndication origin source not found")
    source = Source(
        **payload.model_dump(mode="python", exclude={"feed_url"}),
        feed_url=str(payload.feed_url) if payload.feed_url else None,
        active=False,
    )
    db.add(source)
    db.flush()
    db.add(
        AuditLog(
            actor=principal.actor,
            action="source.created",
            entity="source",
            entity_id=source.id,
            payload_json={
                "name": source.name,
                "rights_basis": source.rights_basis.value,
                "automation_approved_at": (
                    source.automation_approved_at.isoformat()
                    if source.automation_approved_at
                    else None
                ),
                "model_processing_approved_at": (
                    source.model_processing_approved_at.isoformat()
                    if source.model_processing_approved_at
                    else None
                ),
                "model_processing_approved_by": source.model_processing_approved_by,
                "active": False,
            },
        )
    )
    db.commit()
    db.refresh(source)
    return source


@app.patch("/api/v1/sources/{source_id}/operations", response_model=SourceRead)
def update_source_operations(
    source_id: UUID, payload: SourceOperationalUpdate, db: DB, principal: DeskAuth
) -> Source:
    require_desk_role(principal, RIGHTS_ROLES)
    source = db.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")

    reviewer = principal.actor
    approved_at = payload.automation_approved_at or source.automation_approved_at
    if payload.active and (not reviewer or approved_at is None):
        raise HTTPException(
            status_code=422,
            detail="Activation requires a named rights reviewer and approval timestamp",
        )

    source.active = payload.active
    source.rights_reviewed_by = reviewer
    source.automation_approved_at = approved_at
    model_approval_changed = {
        "model_processing_approved_at",
        "model_processing_approved_by",
    } <= payload.model_fields_set
    if model_approval_changed:
        source.model_processing_approved_at = payload.model_processing_approved_at
        source.model_processing_approved_by = (
            principal.actor if payload.model_processing_approved_at is not None else None
        )
    if payload.rights_basis is not None:
        source.rights_basis = payload.rights_basis
    if payload.rights_notes is not None:
        source.rights_notes = payload.rights_notes
    if payload.poll_interval_seconds is not None:
        source.poll_interval_seconds = payload.poll_interval_seconds
    if "inbound_mailbox_hash" in payload.model_fields_set:
        source.inbound_mailbox_hash = payload.inbound_mailbox_hash
    if "syndicates_from_id" in payload.model_fields_set:
        syndication_origin = payload.syndicates_from_id
        if syndication_origin == source.id:
            raise HTTPException(status_code=422, detail="Source cannot syndicate from itself")
        if syndication_origin is not None and db.get(Source, syndication_origin) is None:
            raise HTTPException(status_code=422, detail="Syndication origin source not found")
        source.syndicates_from_id = syndication_origin
    db.add(
        AuditLog(
            actor=principal.actor,
            action="source.activated" if source.active else "source.deactivated",
            entity="source",
            entity_id=source.id,
            payload_json={
                "active": source.active,
                "rights_basis": source.rights_basis.value,
                "automation_approved_at": (
                    source.automation_approved_at.isoformat()
                    if source.automation_approved_at
                    else None
                ),
                "model_processing_approved_at": (
                    source.model_processing_approved_at.isoformat()
                    if source.model_processing_approved_at
                    else None
                ),
                "model_processing_approved_by": source.model_processing_approved_by,
                "poll_interval_seconds": source.poll_interval_seconds,
                "syndicates_from_id": (
                    str(source.syndicates_from_id) if source.syndicates_from_id else None
                ),
            },
        )
    )
    if model_approval_changed:
        db.add(
            AuditLog(
                actor=principal.actor,
                action=(
                    "source.model_processing_approved"
                    if source.model_processing_approved_at
                    else "source.model_processing_revoked"
                ),
                entity="source",
                entity_id=source.id,
                payload_json={
                    "approved_at": (
                        source.model_processing_approved_at.isoformat()
                        if source.model_processing_approved_at
                        else None
                    ),
                    "approved_by": source.model_processing_approved_by,
                },
            )
        )
    db.commit()
    db.refresh(source)
    return source


@app.post("/api/v1/ingest/postmark", response_model=InboundReceipt)
def ingest_postmark(payload: PostmarkInboundMessage, db: DB, _: PostmarkAuth) -> InboundReceipt:
    try:
        result = ingest_postmark_message(db, payload=payload)
    except LookupError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except RightsPolicyError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return InboundReceipt(
        source_record_id=result.source_record.id,
        created=result.created,
        attachment_record_ids=list(result.attachment_record_ids),
    )


@app.post("/api/v1/webhooks/360dialog", response_model=WhatsAppWebhookRead)
async def whatsapp_status_webhook(
    request: Request,
    db: DB,
    _: WhatsAppAuth,
) -> WhatsAppWebhookRead:
    body = await request.body()
    if len(body) > 1_048_576:
        raise HTTPException(status_code=413, detail="WhatsApp webhook payload is too large")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid WhatsApp webhook JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="WhatsApp webhook body must be an object")
    return process_whatsapp_webhook(db, payload=payload)


@app.get("/api/v1/triage", response_model=list[TriageItemRead])
def list_triage_items(
    db: DB,
    _: DeskAuth,
    triage_status: Annotated[TriageStatus, Query(alias="status")] = TriageStatus.PENDING,
    tier: SourceTier | None = None,
    corridor: Corridor | None = None,
    event_type: EventType | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[TriageItemRead]:
    query = (
        select(TriageItem, SourceRecord, Source)
        .join(SourceRecord, SourceRecord.id == TriageItem.source_record_id)
        .join(Source, Source.id == SourceRecord.source_id)
        .where(TriageItem.status == triage_status)
        .order_by(Source.tier, SourceRecord.published_at.desc().nulls_last(), TriageItem.created_at)
        .limit(1000)
    )
    if tier is not None:
        query = query.where(Source.tier == tier)

    results: list[TriageItemRead] = []
    for item, record, source in db.execute(query).all():
        if corridor is not None and corridor.value not in item.detected_corridors:
            continue
        if event_type is not None and event_type.value not in item.suggested_event_types:
            continue
        results.append(
            TriageItemRead(
                id=item.id,
                status=item.status,
                source_record_id=record.id,
                source_id=source.id,
                source_name=source.name,
                source_tier=source.tier,
                source_language=source.language,
                rights_basis=source.rights_basis,
                url=record.url,
                title=record.title,
                text=record.extracted_text[:50_000],
                published_at=record.published_at,
                fetched_at=record.fetched_at,
                security_scan=record.security_scan,
                detected_corridors=item.detected_corridors,
                detected_ports=item.detected_ports,
                suggested_event_types=item.suggested_event_types,
                assigned_event_id=item.assigned_event_id,
                created_at=item.created_at,
            )
        )
        if len(results) == limit:
            break
    return results


@app.get("/api/v1/events/open", response_model=list[OpenEventRead])
def list_open_events(db: DB, _: DeskAuth) -> list[OpenEventRead]:
    events = db.scalars(
        select(Event)
        .where(Event.status != EventStatus.CLOSED)
        .order_by(Event.severity.desc(), Event.created_at.desc())
        .limit(200)
    ).all()
    results: list[OpenEventRead] = []
    for event in events:
        latest_title = db.scalar(
            select(EventVersion.title)
            .where(EventVersion.event_id == event.id)
            .order_by(EventVersion.version_no.desc())
            .limit(1)
        )
        results.append(
            OpenEventRead(
                id=event.id,
                slug=event.slug,
                title=latest_title or event.slug.replace("-", " "),
                event_type=event.event_type,
                corridor=event.corridor,
                severity=event.severity,
                status=event.status.value,
            )
        )
    return results


@app.get("/api/v1/accounts", response_model=list[AccountRead])
def account_list(
    db: DB,
    _: DeskAuth,
    active_only: bool = True,
) -> list[AccountRead]:
    return list_accounts(
        db,
        active_on=datetime.now(UTC).date() if active_only else None,
    )


@app.get("/api/v1/api-keys", response_model=list[ApiKeyRead])
def account_api_key_list(
    db: DB,
    _: DeskAuth,
    account_id: UUID | None = None,
) -> list[ApiKeyRead]:
    return list_account_api_keys(db, account_id=account_id)


@app.post(
    "/api/v1/api-keys",
    response_model=ApiKeyIssuedRead,
    status_code=status.HTTP_201_CREATED,
)
def account_api_key_issue(payload: ApiKeyCreate, db: DB, principal: DeskAuth) -> ApiKeyIssuedRead:
    require_desk_role(principal, ADMIN_ROLES)
    payload = payload.model_copy(update={"created_by": principal.actor})
    try:
        return issue_account_api_key(db, payload)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ApiKeyWorkflowError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/v1/api-keys/{api_key_id}/revoke", response_model=ApiKeyRead)
def account_api_key_revoke(
    api_key_id: UUID,
    payload: ApiKeyRevokeCreate,
    db: DB,
    principal: DeskAuth,
) -> ApiKeyRead:
    require_desk_role(principal, ADMIN_ROLES)
    try:
        return revoke_account_api_key(
            db,
            api_key_id=api_key_id,
            revoked_by=principal.actor,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ApiKeyWorkflowError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.patch("/api/v1/users/{user_id}/portal-access", response_model=AccountUserRead)
def user_portal_access_update(
    user_id: UUID,
    payload: PortalAccessUpdate,
    db: DB,
    principal: DeskAuth,
) -> AccountUserRead:
    require_desk_role(principal, ADMIN_ROLES)
    payload = payload.model_copy(update={"updated_by": principal.actor})
    try:
        return update_portal_access(db, user_id=user_id, payload=payload)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WatchProfileError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.patch("/api/v1/users/{user_id}/channels", response_model=AccountUserRead)
def user_customer_channels_update(
    user_id: UUID,
    payload: CustomerChannelsUpdate,
    db: DB,
    principal: DeskAuth,
) -> AccountUserRead:
    require_desk_role(principal, ADMIN_ROLES)
    payload = payload.model_copy(update={"updated_by": principal.actor})
    try:
        return update_customer_channels(db, user_id=user_id, payload=payload)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/v1/watch-profiles", response_model=list[WatchProfileRead])
def watch_profile_list(
    db: DB,
    _: DeskAuth,
    account_id: UUID | None = None,
) -> list[WatchProfileRead]:
    return list_watch_profiles(db, account_id=account_id)


@app.post(
    "/api/v1/watch-profiles",
    response_model=WatchProfileRead,
    status_code=status.HTTP_201_CREATED,
)
def watch_profile_create(
    payload: WatchProfileCreate, db: DB, principal: DeskAuth
) -> WatchProfileRead:
    require_desk_role(principal, ANALYST_ROLES)
    if payload.configured_by is not None:
        payload = payload.model_copy(update={"configured_by": principal.actor})
    try:
        return create_watch_profile(db, payload=payload)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WatchProfileError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.patch("/api/v1/watch-profiles/{profile_id}", response_model=WatchProfileRead)
def watch_profile_update(
    profile_id: UUID,
    payload: WatchProfileUpdate,
    db: DB,
    principal: DeskAuth,
) -> WatchProfileRead:
    require_desk_role(principal, ANALYST_ROLES)
    updates: dict[str, object] = {"updated_by": principal.actor}
    if payload.configured_by is not None:
        updates["configured_by"] = principal.actor
    payload = payload.model_copy(update=updates)
    try:
        return update_watch_profile(db, profile_id=profile_id, payload=payload)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WatchProfileError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.delete("/api/v1/watch-profiles/{profile_id}", response_model=WatchProfileRead)
def watch_profile_disable(
    profile_id: UUID,
    db: DB,
    principal: DeskAuth,
    reviewer: Annotated[str, Query(min_length=2, max_length=255)],
) -> WatchProfileRead:
    require_desk_role(principal, ANALYST_ROLES)
    try:
        return disable_watch_profile(db, profile_id=profile_id, reviewer=principal.actor)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WatchProfileError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/v1/events/{event_id}/workspace", response_model=EventWorkspaceRead)
def event_workspace(event_id: UUID, db: DB, _: DeskAuth) -> EventWorkspaceRead:
    try:
        return get_event_workspace(db, event_id=event_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _workspace_claim(db: Session, *, event_id: UUID, claim_id: UUID) -> WorkspaceClaimRead:
    workspace = get_event_workspace(db, event_id=event_id)
    for claim in workspace.claims:
        if claim.id == claim_id:
            return claim
    raise LookupError("Claim not found")


@app.post(
    "/api/v1/events/{event_id}/claims",
    response_model=WorkspaceClaimRead,
    status_code=status.HTTP_201_CREATED,
)
def event_claim_create(
    event_id: UUID, payload: ClaimCreate, db: DB, principal: DeskAuth
) -> WorkspaceClaimRead:
    require_desk_role(principal, ANALYST_ROLES)
    payload = payload.model_copy(update={"reviewer": principal.actor})
    try:
        claim = create_claim(db, event_id=event_id, payload=payload)
        return _workspace_claim(db, event_id=event_id, claim_id=claim.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.patch(
    "/api/v1/events/{event_id}/claims/{claim_id}",
    response_model=WorkspaceClaimRead,
)
def event_claim_update(
    event_id: UUID,
    claim_id: UUID,
    payload: ClaimUpdate,
    db: DB,
    principal: DeskAuth,
) -> WorkspaceClaimRead:
    require_desk_role(principal, ANALYST_ROLES)
    if payload.second_reviewed_by is not None:
        raise HTTPException(
            status_code=422,
            detail="Second review requires the dedicated authenticated review workflow",
        )
    payload = payload.model_copy(update={"reviewer": principal.actor})
    try:
        claim = update_claim(db, event_id=event_id, claim_id=claim_id, payload=payload)
        return _workspace_claim(db, event_id=event_id, claim_id=claim.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WorkspaceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post(
    "/api/v1/events/{event_id}/claims/{claim_id}/second-review",
    response_model=ClaimSecondReviewRead,
    status_code=status.HTTP_201_CREATED,
)
def second_review_claim(
    event_id: UUID,
    claim_id: UUID,
    payload: ClaimSecondReviewCreate,
    db: DB,
    principal: DeskAuth,
) -> ClaimSecondReviewRead:
    require_desk_role(principal, SENIOR_ROLES)
    try:
        approval = approve_sensitive_claim(
            db,
            event_id=event_id,
            claim_id=claim_id,
            payload=payload,
            principal=principal,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ApprovalAuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ApprovalWorkflowError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ClaimSecondReviewRead(
        claim_id=claim_id,
        event_id=event_id,
        approval_id=approval.id,
        binding_hash=approval.binding_hash,
        primary_reviewer=f"desk:{approval.primary_user_id}",
        second_reviewer=f"desk:{approval.approved_by_user_id}",
        auth_assurance=approval.auth_assurance,
        reason=approval.reason,
        approved_at=approval.approved_at,
        expires_at=approval.expires_at,
    )


@app.post(
    "/api/v1/events/{event_id}/claims/{claim_id}/evidence",
    response_model=EvidenceLinkRead,
    status_code=status.HTTP_201_CREATED,
)
def event_evidence_link(
    event_id: UUID,
    claim_id: UUID,
    payload: EvidenceLinkCreate,
    db: DB,
    principal: DeskAuth,
) -> EvidenceLinkRead:
    require_desk_role(principal, ANALYST_ROLES)
    payload = payload.model_copy(update={"reviewer": principal.actor})
    try:
        return link_evidence(
            db,
            event_id=event_id,
            claim_id=claim_id,
            payload=payload,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (RightsPolicyError, WorkspaceError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post(
    "/api/v1/triage/{item_id}/actions",
    response_model=TriageActionRead,
)
def triage_action(
    item_id: UUID, payload: TriageActionCreate, db: DB, principal: DeskAuth
) -> TriageActionRead:
    require_desk_role(principal, ANALYST_ROLES)
    try:
        result = apply_triage_action(
            db,
            item_id=item_id,
            request=TriageActionInput(
                **payload.model_copy(update={"reviewer": principal.actor}).model_dump(mode="python")
            ),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TriageWorkflowError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return TriageActionRead(
        triage_item_id=result.item.id,
        decision_id=result.decision.id,
        status=result.item.status,
        event_id=result.item.assigned_event_id,
        reviewed_at=result.decision.at,
    )


@app.get("/api/v1/lineage/proposals", response_model=list[LineageProposalRead])
def list_lineage_proposals(
    db: DB,
    _: DeskAuth,
    status_filter: Annotated[LineageProposalStatus, Query(alias="status")] = (
        LineageProposalStatus.PENDING
    ),
) -> list[LineageProposal]:
    return list(
        db.scalars(
            select(LineageProposal)
            .where(LineageProposal.status == status_filter)
            .order_by(LineageProposal.score.desc(), LineageProposal.created_at)
            .limit(500)
        ).all()
    )


@app.post(
    "/api/v1/lineage/proposals/{proposal_id}/review",
    response_model=LineageReviewRead,
)
def review_lineage(
    proposal_id: UUID, payload: LineageReviewCreate, db: DB, principal: DeskAuth
) -> LineageReviewRead:
    require_desk_role(principal, ANALYST_ROLES)
    try:
        review = review_lineage_proposal(
            db,
            proposal_id=proposal_id,
            decision=payload.decision,
            reviewer=principal.actor,
            reason=payload.reason,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except LineageReviewError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return LineageReviewRead.model_validate(review)


@app.post(
    "/api/v1/lineage/evaluations/{component_version}",
    response_model=PipelineEvaluationRead,
)
def evaluate_lineage(component_version: str, db: DB, _: DeskAuth) -> PipelineEvaluationRead:
    evaluation = evaluate_lineage_component(db, component_version=component_version)
    if evaluation is None:
        raise HTTPException(status_code=404, detail="No reviews for this component version")
    return PipelineEvaluationRead.model_validate(evaluation)


@app.get(
    "/api/v1/claim-extraction/proposals",
    response_model=list[ClaimExtractionProposalRead],
)
def list_claim_extraction_proposals(
    db: DB,
    _: DeskAuth,
    proposal_status: Annotated[
        ClaimExtractionProposalStatus, Query(alias="status")
    ] = ClaimExtractionProposalStatus.PENDING,
    language: str | None = Query(default=None, min_length=2, max_length=16),
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[ClaimExtractionProposalRead]:
    query = (
        select(ClaimExtractionProposal, ClaimExtractionRun, SourceRecord, Source)
        .join(ClaimExtractionRun, ClaimExtractionRun.id == ClaimExtractionProposal.run_id)
        .join(SourceRecord, SourceRecord.id == ClaimExtractionProposal.source_record_id)
        .join(Source, Source.id == SourceRecord.source_id)
        .where(ClaimExtractionProposal.status == proposal_status)
        .order_by(ClaimExtractionRun.started_at, ClaimExtractionProposal.proposal_index)
        .limit(limit)
    )
    if language:
        query = query.where(ClaimExtractionRun.language == language.casefold())
    return [
        ClaimExtractionProposalRead(
            id=proposal.id,
            run_id=run.id,
            source_record_id=record.id,
            source_name=source.name,
            source_language=run.language,
            source_title=record.title,
            source_text=record.extracted_text[:50_000],
            source_url=record.url,
            model_version=run.model_version,
            prompt_version=run.prompt_version,
            mode=run.mode,
            proposal_index=proposal.proposal_index,
            text=proposal.text,
            claimant=proposal.claimant,
            occurred_time=proposal.occurred_time,
            location=proposal.location_json,
            quantities=proposal.quantities_json,
            hedging_language=proposal.hedging_language,
            source_sentence_quote=proposal.source_sentence_quote,
            source_start=proposal.source_start,
            source_end=proposal.source_end,
            segment_index=proposal.segment_index,
            status=proposal.status,
        )
        for proposal, run, record, source in db.execute(query).all()
    ]


@app.post(
    "/api/v1/claim-extraction/proposals/{proposal_id}/review",
    response_model=ClaimExtractionReviewRead,
)
def review_claim_extraction_proposal(
    proposal_id: UUID,
    payload: ClaimExtractionReviewCreate,
    db: DB,
    principal: DeskAuth,
) -> ClaimExtractionReviewRead:
    require_desk_role(principal, ANALYST_ROLES)
    edited_claim = (
        ExtractedClaim.model_validate(payload.edited_claim.model_dump(mode="python"))
        if payload.edited_claim
        else None
    )
    try:
        review = review_proposal(
            db,
            proposal_id=proposal_id,
            decision=payload.decision,
            reviewer=principal.actor,
            event_id=payload.event_id,
            claim_state=payload.claim_state,
            edited_claim=edited_claim,
            reason_codes=payload.reason_codes,
            note=payload.note,
            baseline_seconds=payload.baseline_seconds,
            review_seconds=payload.review_seconds,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ClaimExtractionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ClaimExtractionReviewRead.model_validate(review)


@app.post(
    "/api/v1/claim-extraction/reviews/{review_id}/qa",
    response_model=ClaimExtractionQARead,
)
def qa_claim_extraction_review(
    review_id: UUID,
    payload: ClaimExtractionQACreate,
    db: DB,
    principal: DeskAuth,
) -> ClaimExtractionQARead:
    require_desk_role(principal, SENIOR_ROLES)
    try:
        qa = record_qa(
            db,
            review_id=review_id,
            evaluator=principal.actor,
            error_found=payload.error_found,
            error_codes=payload.error_codes,
            note=payload.note,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ClaimExtractionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ClaimExtractionQARead.model_validate(qa)


@app.post(
    "/api/v1/claim-extraction/evaluations/{language}",
    response_model=ClaimExtractionEvaluationRead,
)
def evaluate_claim_extraction_language(
    language: str,
    db: DB,
    _: DeskAuth,
) -> ClaimExtractionEvaluationRead:
    try:
        evaluation, metrics = evaluate_component(db, language=language.casefold())
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ClaimExtractionEvaluationRead(
        evaluation=PipelineEvaluationRead.model_validate(evaluation),
        **metrics.__dict__,
    )


@app.get(
    "/api/v1/claim-extraction/weekly-report",
    response_model=ClaimExtractionWeeklyReportRead,
)
def claim_extraction_weekly_report(
    db: DB,
    _: DeskAuth,
    language: str | None = Query(default=None, min_length=2, max_length=16),
) -> ClaimExtractionWeeklyReportRead:
    return ClaimExtractionWeeklyReportRead.model_validate(
        weekly_reason_report(db, language=language)
    )


@app.post("/api/v1/sources/{source_id}/poll-now")
def ingest_source(source_id: UUID, db: DB, _: DeskAuth) -> dict[str, int | str]:
    try:
        run_id, created = run_source_now(db, source_id=source_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RightsPolicyError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {"run_id": str(run_id), "created": created}


@app.get("/api/v1/operations/pollers", response_model=list[PollerHealthRead])
def poller_health_list(db: DB, _: DeskAuth) -> list[PollerHealthRead]:
    sources = db.scalars(select(Source).order_by(Source.tier, Source.name)).all()
    return [poller_health(source) for source in sources]


@app.get("/api/v1/operations/desk-alerts", response_model=list[DeskAlertRead])
def desk_alerts(db: DB, _: DeskAuth) -> list[DeskAlert]:
    return list(
        db.scalars(select(DeskAlert).order_by(DeskAlert.detected_at.desc()).limit(200)).all()
    )


@app.get("/api/v1/operations/dashboard", response_model=OperationsDashboardRead)
def launch_operations_dashboard(db: DB, _: DeskAuth) -> OperationsDashboardRead:
    return operations_dashboard(db)


@app.post(
    "/api/v1/events/{event_id}/publication-approvals",
    response_model=PublicationApprovalRead,
    status_code=status.HTTP_201_CREATED,
)
def create_publication_approval(
    event_id: UUID,
    payload: PublicationApprovalCreate,
    db: DB,
    principal: DeskAuth,
) -> PublicationApprovalRead:
    require_desk_role(principal, SENIOR_ROLES)
    try:
        result = approve_event_publication(
            db,
            event_id=event_id,
            payload=payload,
            principal=principal,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ApprovalAuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (ApprovalWorkflowError, PublicationPolicyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    approval = result.approval
    signer = result.approved_draft.signed_off_by
    if signer is None:  # Defensive invariant: approval service always supplies the signer.
        raise HTTPException(status_code=500, detail="Publication approval signer is missing")
    return PublicationApprovalRead(
        id=approval.id,
        event_id=event_id,
        preview_hash=approval.binding_hash,
        publisher_user_id=approval.primary_user_id,
        approver_user_id=approval.approved_by_user_id,
        published_by=result.approved_draft.published_by,
        signed_off_by=signer,
        auth_assurance=approval.auth_assurance,
        reason=approval.reason,
        approved_at=approval.approved_at,
        expires_at=approval.expires_at,
        approved_draft=result.approved_draft,
        preview=result.preview,
    )


@app.post(
    "/api/v1/events/{event_id}/publication-requests",
    response_model=ApprovalRequestRead,
    status_code=status.HTTP_201_CREATED,
)
def request_publication_approval(
    event_id: UUID,
    payload: PublicationApprovalRequestCreate,
    db: DB,
    principal: DeskAuth,
) -> ApprovalRequestRead:
    require_desk_role(principal, ANALYST_ROLES)
    try:
        request = create_publication_request(
            db,
            event_id=event_id,
            payload=payload,
            principal=principal,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ApprovalAuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (ApprovalWorkflowError, PublicationPolicyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return approval_request_read(db, request)


@app.get("/api/v1/approval-requests", response_model=list[ApprovalRequestRead])
def approval_request_queue(
    db: DB,
    principal: DeskAuth,
    request_status: str | None = Query(default=None, alias="status", max_length=16),
    limit: Annotated[int, Query(ge=1, le=250)] = 100,
) -> list[ApprovalRequestRead]:
    try:
        requests = list_approval_requests(
            db,
            principal=principal,
            status=request_status,
            limit=limit,
        )
    except ApprovalWorkflowError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return [approval_request_read(db, request) for request in requests]


@app.post(
    "/api/v1/approval-requests/{request_id}/decision",
    response_model=ApprovalRequestRead,
)
def decide_approval_request_route(
    request_id: UUID,
    payload: ApprovalRequestDecisionCreate,
    db: DB,
    principal: DeskAuth,
) -> ApprovalRequestRead:
    require_desk_role(principal, SENIOR_ROLES)
    try:
        request = decide_approval_request(
            db,
            request_id=request_id,
            payload=payload,
            principal=principal,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ApprovalAuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (ApprovalWorkflowError, PublicationPolicyError, CorrectionWorkflowError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return approval_request_read(db, request)


@app.post(
    "/api/v1/events/{event_id}/versions/preview",
    response_model=EventVersionPreviewRead,
)
def preview_event_version_route(
    event_id: UUID, payload: EventVersionDraft, db: DB, principal: DeskAuth
) -> EventVersionPreviewRead:
    require_desk_role(principal, ANALYST_ROLES)
    payload = payload.model_copy(
        update={"published_by": principal.actor, "signed_off_by": None}
    )
    try:
        return preview_event_version(db, event_id=event_id, payload=payload)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PublicationPolicyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post(
    "/api/v1/event-versions/{event_version_id}/alerts/preview",
    response_model=AlertPreviewRead,
)
def alert_preview(
    event_version_id: UUID,
    payload: AlertReleaseDraft,
    db: DB,
    principal: DeskAuth,
) -> AlertPreviewRead:
    require_desk_role(principal, ANALYST_ROLES)
    payload = payload.model_copy(update={"released_by": principal.actor})
    try:
        return build_alert_plan(
            db,
            event_version_id=event_version_id,
            payload=payload,
        ).preview
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post(
    "/api/v1/event-versions/{event_version_id}/alerts",
    response_model=AlertReleaseRead,
    status_code=status.HTTP_201_CREATED,
)
def alert_release(
    event_version_id: UUID,
    payload: AlertReleaseCreate,
    db: DB,
    principal: DeskAuth,
    _outbound: OutboundGate,
) -> AlertReleaseRead:
    require_desk_role(principal, ANALYST_ROLES)
    payload = payload.model_copy(update={"released_by": principal.actor})
    try:
        return release_alert(
            db,
            event_version_id=event_version_id,
            payload=payload,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AlertWorkflowError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/v1/alerts/delivery-dashboard", response_model=DeliveryDashboardRead)
def alert_delivery_dashboard(
    db: DB,
    principal: DeskAuth,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> DeliveryDashboardRead:
    return delivery_dashboard(db, limit=limit)


@app.get(
    "/api/v1/event-versions/{event_version_id}/reliability-receipt",
    response_model=ReliabilityReceiptRead,
)
def desk_event_version_reliability_receipt(
    event_version_id: UUID, db: DB, _: DeskAuth
) -> ReliabilityReceiptRead:
    try:
        return get_reliability_receipt(db, event_version_id=event_version_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post(
    "/api/v1/events/{event_id}/versions",
    response_model=EventVersionRead,
    status_code=status.HTTP_201_CREATED,
)
def create_event_version(
    event_id: UUID,
    payload: EventVersionCreate,
    db: DB,
    principal: DeskAuth,
    _outbound: OutboundGate,
) -> EventVersionRead:
    require_desk_role(principal, ANALYST_ROLES)
    try:
        authorization = authorize_event_publication(
            db,
            event_id=event_id,
            preview_hash=payload.preview_hash,
            principal=principal,
        )
        payload = payload.model_copy(
            update={
                "published_by": principal.actor,
                "signed_off_by": authorization.signed_off_by,
            }
        )
        return publish_event_version(
            db,
            event_id=event_id,
            payload=payload,
            publication_approval_id=authorization.approval_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ApprovalAuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except PublicationPolicyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/v1/corrections/preview", response_model=CorrectionPreviewRead)
def preview_correction(
    payload: CorrectionDraft, db: DB, principal: DeskAuth
) -> CorrectionPreviewRead:
    require_desk_role(principal, ANALYST_ROLES)
    if payload.impact == CorrectionImpact.OPERATIONALLY_RELEVANT:
        raise HTTPException(
            status_code=422,
            detail=(
                "Operational corrections require the dedicated authenticated "
                "second-person approval workflow"
            ),
        )
    payload = payload.model_copy(
        update={"drafted_by": principal.actor, "signed_off_by": principal.actor}
    )
    try:
        plan = build_correction_plan(db, **payload.model_dump())
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CorrectionWorkflowError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return CorrectionPreviewRead(
        preview_hash=plan.preview_hash,
        event_id=plan.event.id,
        event_slug=plan.event.slug,
        version_from_id=plan.version_from.id,
        version_to_id=plan.version_to.id,
        version_from=plan.version_from.version_no,
        version_to=plan.version_to.version_no,
        affected_version_hash=plan.version_from.content_hash,
        corrected_version_hash=plan.version_to.content_hash,
        channels=list(plan.channels),
        recipient_count=len(plan.recipients),
        message=plan.message,
    )


@app.post(
    "/api/v1/corrections/approvals",
    response_model=CorrectionApprovalRead,
    status_code=status.HTTP_201_CREATED,
)
def create_correction_approval(
    payload: CorrectionApprovalCreate,
    db: DB,
    principal: DeskAuth,
) -> CorrectionApprovalRead:
    require_desk_role(principal, SENIOR_ROLES)
    try:
        result = approve_operational_correction(db, payload=payload, principal=principal)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ApprovalAuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (ApprovalWorkflowError, CorrectionWorkflowError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    approval = result.approval
    return CorrectionApprovalRead(
        id=approval.id,
        preview_hash=approval.binding_hash,
        drafter_user_id=approval.primary_user_id,
        approver_user_id=approval.approved_by_user_id,
        drafted_by=result.approved_draft.drafted_by,
        signed_off_by=result.approved_draft.signed_off_by,
        auth_assurance=approval.auth_assurance,
        reason=approval.reason,
        approved_at=approval.approved_at,
        expires_at=approval.expires_at,
        approved_draft=result.approved_draft,
    )


@app.post(
    "/api/v1/corrections/approval-requests",
    response_model=ApprovalRequestRead,
    status_code=status.HTTP_201_CREATED,
)
def request_correction_approval(
    payload: CorrectionApprovalRequestCreate,
    db: DB,
    principal: DeskAuth,
) -> ApprovalRequestRead:
    require_desk_role(principal, ANALYST_ROLES)
    try:
        request = create_correction_request(db, payload=payload, principal=principal)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ApprovalAuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (ApprovalWorkflowError, CorrectionWorkflowError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return approval_request_read(db, request)


@app.post(
    "/api/v1/corrections",
    response_model=CorrectionRead,
    status_code=status.HTTP_201_CREATED,
)
def create_correction(
    payload: CorrectionIssueCreate,
    db: DB,
    principal: DeskAuth,
    _outbound: OutboundGate,
) -> Correction:
    require_desk_role(principal, ANALYST_ROLES)
    try:
        authorization = authorize_correction_issue(
            db,
            event_id=payload.event_id,
            impact=payload.impact,
            preview_hash=payload.preview_hash,
            principal=principal,
        )
        payload = payload.model_copy(
            update={
                "drafted_by": authorization.drafted_by,
                "signed_off_by": authorization.signed_off_by,
            }
        )
        return issue_correction(
            db,
            **payload.model_dump(),
            correction_approval_id=authorization.approval_id,
            released_by=principal.actor,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ApprovalAuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except CorrectionWorkflowError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/v1/corrections", response_model=list[CorrectionRead])
def list_corrections(db: DB, _: DeskAuth) -> list[Correction]:
    return list(
        db.scalars(select(Correction).order_by(Correction.detected_at.desc()).limit(500)).all()
    )


@app.post(
    "/api/v1/ttv",
    response_model=TTVRead,
    status_code=status.HTTP_201_CREATED,
)
def create_ttv(payload: TTVCreate, db: DB, _: DeskAuth) -> TTVRead:
    try:
        return create_ttv_log(db, payload)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except QualityWorkflowError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.patch("/api/v1/ttv/{event_id}", response_model=TTVRead)
def update_ttv(event_id: UUID, payload: TTVUpdate, db: DB, _: DeskAuth) -> TTVRead:
    try:
        return update_ttv_log(db, event_id=event_id, payload=payload)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except QualityWorkflowError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
