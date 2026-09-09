from __future__ import annotations

import base64
import binascii
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from uuid import UUID

from eastmed_schema.enums import (
    AccessMethod,
    AccountTier,
    AICapabilityMode,
    AuthAssurance,
    BriefStatus,
    CalendarEventType,
    ClaimExtractionDecision,
    ClaimExtractionProposalStatus,
    ClaimState,
    CorrectionImpact,
    CorrectionType,
    Corridor,
    DeliveryChannel,
    DeliveryStatus,
    DeskAlertKind,
    DeskAlertStatus,
    DeskRole,
    Directness,
    EventType,
    LineageProposalStatus,
    LineageReviewDecision,
    RightsBasis,
    SourceTier,
    SourceType,
    TriageAction,
    TriageStatus,
)
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: str = "eastmed-api"


class DeskUserCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    auth_issuer: str = Field(min_length=3, max_length=255)
    auth_subject: str = Field(min_length=1, max_length=255)
    email: str = Field(min_length=3, max_length=320, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    display_name: str = Field(min_length=2, max_length=255)
    role: DeskRole


class DeskUserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    auth_issuer: str
    auth_subject: str
    email: str
    display_name: str
    role: DeskRole
    active: bool
    created_by: str
    created_at: datetime
    updated_at: datetime


class DeskPrincipalRead(BaseModel):
    id: UUID
    auth_issuer: str
    auth_subject: str
    email: str
    display_name: str
    role: DeskRole
    auth_assurance: AuthAssurance


class AISystemManifest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    schema_version: Literal["1"] = "1"
    provider: str = Field(min_length=1, max_length=128)
    model_family: str = Field(min_length=1, max_length=255)
    model_snapshot: str = Field(min_length=1, max_length=255)
    inference_settings: dict[str, str | int | float | bool | None]
    prompt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_schema_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    tokenizer_version: str = Field(min_length=1, max_length=255)
    context_policy_version: str = Field(min_length=1, max_length=255)
    preprocessing_versions: dict[str, str]
    security_versions: dict[str, str]
    retrieval_versions: dict[str, str]
    registry_versions: dict[str, str]
    verifier_versions: dict[str, str]
    adjudication_versions: dict[str, str]
    composer_versions: dict[str, str]
    calibration_versions: dict[str, str]

    @field_validator(
        "inference_settings",
        "preprocessing_versions",
        "security_versions",
        "retrieval_versions",
        "registry_versions",
        "verifier_versions",
        "adjudication_versions",
        "composer_versions",
        "calibration_versions",
    )
    @classmethod
    def manifest_sections_are_not_empty(cls, value: dict[str, object]) -> dict[str, object]:
        if not value:
            raise ValueError("AI system manifest sections cannot be empty")
        return value


class AISystemVersionCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=3, max_length=255)
    purpose: str = Field(min_length=10, max_length=2_000)
    manifest: AISystemManifest


class AISystemVersionRead(BaseModel):
    id: UUID
    name: str
    purpose: str
    manifest_schema_version: str
    manifest: AISystemManifest
    fingerprint: str
    created_by_user_id: UUID
    created_at: datetime


class AICapabilityControlCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    mode: AICapabilityMode
    risk_tier: int = Field(ge=0, le=4)
    system_version_id: UUID | None = None
    reason: str = Field(min_length=10, max_length=2_000)
    approval_refs: dict[str, str] = Field(default_factory=dict)
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def enabled_controls_are_time_bounded(self) -> AICapabilityControlCreate:
        if self.mode == AICapabilityMode.DISABLED:
            if self.expires_at is not None:
                raise ValueError("disabled controls must not have an expiry")
            return self
        if self.expires_at is None:
            raise ValueError("enabled controls require an expiry")
        if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
            raise ValueError("AI capability expiry must include a timezone")
        return self


class AICapabilityControlRead(BaseModel):
    id: UUID
    scope: str
    revision: int
    mode: AICapabilityMode
    risk_tier: int
    system_version_id: UUID | None
    reason: str
    approval_refs: dict[str, str]
    changed_by_user_id: UUID
    auth_assurance: AuthAssurance
    previous_control_id: UUID | None
    expires_at: datetime | None
    changed_at: datetime


class AIIncidentCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=5, max_length=255)
    capability_scope: str = Field(min_length=3, max_length=128)
    system_version_id: UUID | None = None
    detected_at: datetime
    severity: Literal["low", "medium", "high", "critical"]
    summary: str = Field(min_length=10, max_length=5_000)
    containment_action: str | None = Field(default=None, min_length=10, max_length=5_000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("detected_at")
    @classmethod
    def detected_at_is_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("AI incident detection time must include a timezone")
        return value


class AIIncidentEventCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    status: Literal["open", "contained", "investigating", "resolved"]
    severity: Literal["low", "medium", "high", "critical"]
    summary: str = Field(min_length=10, max_length=5_000)
    containment_action: str | None = Field(default=None, min_length=10, max_length=5_000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=100)


class AIIncidentEventRead(BaseModel):
    id: UUID
    incident_id: UUID
    sequence: int
    status: Literal["open", "contained", "investigating", "resolved"]
    severity: Literal["low", "medium", "high", "critical"]
    summary: str
    containment_action: str | None
    evidence_refs: list[str]
    changed_by_user_id: UUID
    auth_assurance: AuthAssurance
    at: datetime


class AIIncidentRead(BaseModel):
    id: UUID
    title: str
    capability_scope: str
    system_version_id: UUID | None
    detected_at: datetime
    reported_by_user_id: UUID
    created_at: datetime
    status: Literal["open", "contained", "investigating", "resolved"]
    severity: Literal["low", "medium", "high", "critical"]
    events: list[AIIncidentEventRead]


class LaunchReadinessCheckRead(BaseModel):
    key: str
    label: str
    state: Literal["pass", "warning", "fail"]
    detail: str
    href: str | None = None


class OperationsDashboardRead(BaseModel):
    generated_at: datetime
    environment: str
    overall_status: Literal["ready", "degraded", "action_required"]
    outbound_enabled: bool
    desk_auth_mode: Literal["trusted_proxy", "oidc"]
    pending_triage: int
    open_events: int
    high_severity_events: int
    pending_extraction_proposals: int
    open_desk_alerts: int
    pending_approval_requests: int
    approved_releases_expiring: int
    open_ai_incidents: int
    active_sources: int
    total_sources: int
    source_health: dict[str, int]
    delivery_status: dict[str, int]
    ai_controls: list[AICapabilityControlRead]
    readiness: list[LaunchReadinessCheckRead]


def _effective_approval_time(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("approval timestamps must include a timezone")
    if value > datetime.now(UTC) + timedelta(minutes=5):
        raise ValueError("approval timestamps cannot be future-dated")
    return value


class SourceCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=2, max_length=255)
    legal_entity: str | None = None
    source_type: SourceType
    tier: SourceTier
    language: str = Field(min_length=2, max_length=16)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    access_method: AccessMethod
    feed_url: HttpUrl | None = None
    inbound_mailbox_hash: str | None = Field(default=None, min_length=4, max_length=128)
    syndicates_from_id: UUID | None = None
    rights_basis: RightsBasis
    rights_notes: str | None = None
    rights_reviewed_by: str | None = Field(default=None, min_length=2, max_length=255)
    automation_approved_at: datetime | None = None
    model_processing_approved_at: datetime | None = None
    model_processing_approved_by: str | None = Field(default=None, min_length=2, max_length=255)

    _validate_approval_times = field_validator(
        "automation_approved_at", "model_processing_approved_at"
    )(_effective_approval_time)

    @model_validator(mode="after")
    def approval_has_reviewer(self) -> SourceCreate:
        if self.automation_approved_at is not None and not self.rights_reviewed_by:
            raise ValueError("automation approval requires a named rights reviewer")
        if self.rights_reviewed_by and self.rights_reviewed_by.casefold().startswith("model:"):
            raise ValueError("a model cannot review source rights")
        if (self.model_processing_approved_at is None) != (
            self.model_processing_approved_by is None
        ):
            raise ValueError("model-processing approval requires both reviewer and timestamp")
        if (
            self.model_processing_approved_by
            and self.model_processing_approved_by.casefold().startswith("model:")
        ):
            raise ValueError("a model cannot approve source processing")
        return self


class SourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    source_type: SourceType
    tier: SourceTier
    language: str
    access_method: AccessMethod
    feed_url: str | None
    inbound_mailbox_hash: str | None
    syndicates_from_id: UUID | None
    rights_basis: RightsBasis
    rights_reviewed_by: str | None
    automation_approved_at: datetime | None
    model_processing_approved_at: datetime | None
    model_processing_approved_by: str | None
    active: bool


class SourceOperationalUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    active: bool
    rights_basis: RightsBasis | None = None
    rights_notes: str | None = None
    rights_reviewed_by: str | None = Field(default=None, min_length=2, max_length=255)
    automation_approved_at: datetime | None = None
    model_processing_approved_at: datetime | None = None
    model_processing_approved_by: str | None = Field(default=None, min_length=2, max_length=255)
    poll_interval_seconds: int | None = Field(default=None, ge=60, le=86400)
    inbound_mailbox_hash: str | None = Field(default=None, min_length=4, max_length=128)
    syndicates_from_id: UUID | None = None

    _validate_approval_times = field_validator(
        "automation_approved_at", "model_processing_approved_at"
    )(_effective_approval_time)

    @model_validator(mode="after")
    def model_approval_is_complete_and_human(self) -> SourceOperationalUpdate:
        changed = {
            "model_processing_approved_at",
            "model_processing_approved_by",
        } & self.model_fields_set
        if changed and changed != {
            "model_processing_approved_at",
            "model_processing_approved_by",
        }:
            raise ValueError("set or clear both model-processing approval fields together")
        if changed and self.model_processing_approved_by is None and not self.rights_reviewed_by:
            raise ValueError("revoking model-processing approval requires a named human operator")
        if (self.model_processing_approved_at is None) != (
            self.model_processing_approved_by is None
        ):
            raise ValueError("model-processing approval requires both reviewer and timestamp")
        if (
            self.model_processing_approved_by
            and self.model_processing_approved_by.casefold().startswith("model:")
        ):
            raise ValueError("a model cannot approve source processing")
        if self.rights_reviewed_by and self.rights_reviewed_by.casefold().startswith("model:"):
            raise ValueError("a model cannot review source rights or revoke processing approval")
        return self


class PostmarkAttachment(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    name: str = Field(alias="Name", min_length=1, max_length=255)
    content: str = Field(alias="Content", max_length=14_000_000)
    content_type: str = Field(alias="ContentType", max_length=255)
    content_length: int = Field(alias="ContentLength", ge=0, le=10 * 1024 * 1024)

    @model_validator(mode="after")
    def content_is_valid_base64(self) -> PostmarkAttachment:
        try:
            decoded = base64.b64decode(self.content, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("attachment content is not valid base64") from exc
        if len(decoded) != self.content_length:
            raise ValueError("attachment ContentLength does not match decoded content")
        return self


class PostmarkInboundMessage(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    message_id: str = Field(alias="MessageID", min_length=8, max_length=255)
    message_stream: Literal["inbound"] = Field(alias="MessageStream")
    mailbox_hash: str = Field(alias="MailboxHash", min_length=4, max_length=128)
    sender: str = Field(alias="From", min_length=3, max_length=320)
    subject: str = Field(alias="Subject", max_length=998)
    date: str | None = Field(default=None, alias="Date", max_length=255)
    text_body: str = Field(default="", alias="TextBody", max_length=5_000_000)
    html_body: str = Field(default="", alias="HtmlBody", max_length=5_000_000)
    stripped_text_reply: str = Field(default="", alias="StrippedTextReply", max_length=5_000_000)
    raw_email: str | None = Field(default=None, alias="RawEmail", max_length=14_000_000)
    attachments: list[PostmarkAttachment] = Field(
        default_factory=list, alias="Attachments", max_length=20
    )

    @model_validator(mode="after")
    def attachment_total_is_bounded(self) -> PostmarkInboundMessage:
        if sum(item.content_length for item in self.attachments) > 10 * 1024 * 1024:
            raise ValueError("combined attachments exceed 10 MiB")
        serialized_content_size = sum(
            len(value.encode("utf-8"))
            for value in (
                self.stripped_text_reply,
                self.text_body,
                self.html_body,
                self.raw_email or "",
                *(item.content for item in self.attachments),
            )
        )
        if serialized_content_size > 20 * 1024 * 1024:
            raise ValueError("inbound message content exceeds 20 MiB")
        if not any((self.stripped_text_reply, self.text_body, self.html_body, self.raw_email)):
            raise ValueError("inbound message has no usable content")
        return self


class InboundReceipt(BaseModel):
    source_record_id: UUID
    created: bool
    attachment_record_ids: list[UUID] = Field(default_factory=list)


class TriagePortRead(BaseModel):
    name: str
    unlocode: str
    corridor: Corridor
    matched_alias: str


class TriageItemRead(BaseModel):
    id: UUID
    status: TriageStatus
    source_record_id: UUID
    source_id: UUID
    source_name: str
    source_tier: SourceTier
    source_language: str
    rights_basis: RightsBasis
    url: str
    title: str | None
    text: str
    published_at: datetime | None
    fetched_at: datetime
    security_scan: dict[str, object]
    detected_corridors: list[Corridor]
    detected_ports: list[TriagePortRead]
    suggested_event_types: list[EventType]
    assigned_event_id: UUID | None
    created_at: datetime


class TriageActionCreate(BaseModel):
    action: TriageAction
    reviewer: str = Field(min_length=2, max_length=255)
    reason: str | None = Field(default=None, max_length=2000)
    event_id: UUID | None = None
    title: str | None = Field(default=None, min_length=3, max_length=500)
    event_type: EventType | None = None
    corridor: Corridor | None = None
    severity: int | None = Field(default=None, ge=1, le=4)
    occurred_start: datetime | None = None

    @model_validator(mode="after")
    def action_has_required_fields(self) -> TriageActionCreate:
        if self.reviewer.casefold().startswith("model:"):
            raise ValueError("a model cannot make a triage decision")
        if self.action == TriageAction.ATTACH and self.event_id is None:
            raise ValueError("attach requires event_id")
        if self.action == TriageAction.DISMISS and not self.reason:
            raise ValueError("dismiss requires a reason")
        if self.action == TriageAction.NEW_EVENT and not all(
            (
                self.title,
                self.event_type,
                self.corridor,
                self.severity is not None,
            )
        ):
            raise ValueError("new_event requires title, event_type, corridor, and severity")
        return self


class TriageActionRead(BaseModel):
    triage_item_id: UUID
    decision_id: UUID
    status: TriageStatus
    event_id: UUID | None
    reviewed_at: datetime


class OpenEventRead(BaseModel):
    id: UUID
    slug: str
    title: str
    event_type: EventType
    corridor: Corridor
    severity: int
    status: str


class AccountUserRead(BaseModel):
    id: UUID
    email: str
    phone: str | None
    channels: dict[str, object]
    role: str
    auth_subject: str | None
    active: bool
    portal_enabled: bool


class PortalAccessUpdate(BaseModel):
    updated_by: str = Field(min_length=2, max_length=255)
    auth_subject: str | None = Field(default=None, min_length=2, max_length=255)
    portal_enabled: bool

    @model_validator(mode="after")
    def access_is_human_and_bound(self) -> PortalAccessUpdate:
        if self.updated_by.casefold().startswith("model:"):
            raise ValueError("a model cannot provision portal access")
        if self.portal_enabled and not self.auth_subject:
            raise ValueError("enabled portal access requires a Clerk user subject")
        return self


class CustomerChannelsUpdate(BaseModel):
    updated_by: str = Field(min_length=2, max_length=255)
    email_enabled: bool = True
    telegram_chat_id: str | None = Field(default=None, max_length=128)
    whatsapp_enabled: bool = False
    whatsapp_phone: str | None = Field(default=None, max_length=32)
    whatsapp_opt_in_confirmed: bool = False

    @model_validator(mode="after")
    def channels_are_human_configured_and_opted_in(self) -> CustomerChannelsUpdate:
        if self.updated_by.casefold().startswith("model:"):
            raise ValueError("a model cannot configure customer channels")
        if self.whatsapp_enabled and (
            not self.whatsapp_phone or not self.whatsapp_opt_in_confirmed
        ):
            raise ValueError("WhatsApp requires a phone number and confirmed customer opt-in")
        if self.whatsapp_phone:
            digits = "".join(character for character in self.whatsapp_phone if character.isdigit())
            if not 8 <= len(digits) <= 15:
                raise ValueError("WhatsApp phone must contain 8 to 15 digits")
        return self


class AccountRead(BaseModel):
    id: UUID
    company: str
    tier: AccountTier
    contract_start: date
    contract_end: date
    users: list[AccountUserRead]


class WatchProfileCreate(BaseModel):
    account_id: UUID
    name: str = Field(min_length=2, max_length=255)
    corridors: list[Corridor] = Field(default_factory=list, max_length=20)
    event_types: list[EventType] = Field(default_factory=list, max_length=20)
    min_severity: int = Field(ge=1, le=4)
    ports: list[str] = Field(default_factory=list, max_length=100)
    custom_geojson: dict[str, object] | None = None
    active: bool = False
    configured_by: str | None = Field(default=None, min_length=2, max_length=255)
    configured_with_customer_at: datetime | None = None

    @model_validator(mode="after")
    def configured_profile_is_bounded(self) -> WatchProfileCreate:
        if len(self.corridors) != len(set(self.corridors)):
            raise ValueError("corridors must be unique")
        if len(self.event_types) != len(set(self.event_types)):
            raise ValueError("event_types must be unique")
        normalized_ports = [port.strip().upper() for port in self.ports if port.strip()]
        if len(normalized_ports) != len(set(normalized_ports)):
            raise ValueError("ports must be unique")
        self.ports = normalized_ports
        if self.custom_geojson is not None:
            geo_type = self.custom_geojson.get("type")
            if geo_type not in {"Polygon", "MultiPolygon"}:
                raise ValueError("custom_geojson must be a Polygon or MultiPolygon")
            if "coordinates" not in self.custom_geojson:
                raise ValueError("custom_geojson requires coordinates")
        if self.active and (
            not self.configured_by
            or not self.configured_with_customer_at
            or not any((self.corridors, self.event_types, self.ports, self.custom_geojson))
        ):
            raise ValueError(
                "active profile requires customer configuration, reviewer, and watch criteria"
            )
        if self.configured_by and self.configured_by.casefold().startswith("model:"):
            raise ValueError("a model cannot configure a customer watch profile")
        return self


class WatchProfileUpdate(BaseModel):
    updated_by: str = Field(min_length=2, max_length=255)
    name: str | None = Field(default=None, min_length=2, max_length=255)
    corridors: list[Corridor] | None = Field(default=None, max_length=20)
    event_types: list[EventType] | None = Field(default=None, max_length=20)
    min_severity: int | None = Field(default=None, ge=1, le=4)
    ports: list[str] | None = Field(default=None, max_length=100)
    custom_geojson: dict[str, object] | None = None
    active: bool | None = None
    configured_by: str | None = Field(default=None, min_length=2, max_length=255)
    configured_with_customer_at: datetime | None = None

    @model_validator(mode="after")
    def update_is_valid(self) -> WatchProfileUpdate:
        if not (self.model_fields_set - {"updated_by"}):
            raise ValueError("watch profile update has no changes")
        if self.updated_by.casefold().startswith("model:"):
            raise ValueError("a model cannot update a customer watch profile")
        if self.corridors is not None and len(self.corridors) != len(set(self.corridors)):
            raise ValueError("corridors must be unique")
        if self.event_types is not None and len(self.event_types) != len(set(self.event_types)):
            raise ValueError("event_types must be unique")
        if self.ports is not None:
            normalized_ports = [port.strip().upper() for port in self.ports if port.strip()]
            if len(normalized_ports) != len(set(normalized_ports)):
                raise ValueError("ports must be unique")
            self.ports = normalized_ports
        if "custom_geojson" in self.model_fields_set and self.custom_geojson is not None:
            if self.custom_geojson.get("type") not in {"Polygon", "MultiPolygon"}:
                raise ValueError("custom_geojson must be a Polygon or MultiPolygon")
            if "coordinates" not in self.custom_geojson:
                raise ValueError("custom_geojson requires coordinates")
        if self.configured_by and self.configured_by.casefold().startswith("model:"):
            raise ValueError("a model cannot configure a customer watch profile")
        return self


class WatchProfileRead(BaseModel):
    id: UUID
    account_id: UUID
    account_company: str
    account_tier: AccountTier
    name: str
    corridors: list[Corridor]
    event_types: list[EventType]
    min_severity: int
    ports: list[str]
    custom_geojson: dict[str, object] | None
    active: bool
    configured_by: str | None
    configured_with_customer_at: datetime | None
    created_at: datetime
    updated_at: datetime


class WorkspaceEvidenceRead(BaseModel):
    id: UUID
    source_record_id: UUID
    source_name: str
    source_tier: SourceTier
    url: str
    directness: Directness
    lineage_root_id: UUID
    excerpt: str | None
    rights_basis: RightsBasis


class WorkspaceClaimRead(BaseModel):
    id: UUID
    text: str
    claimant: str | None
    claim_state: ClaimState
    occurred_at: datetime | None
    proposed_by: str
    reviewed_by: str | None
    second_reviewed_by: str | None
    second_review_approval_id: UUID | None
    reviewed_at: datetime | None
    sensitivity_flags: list[str]
    first_seen_at: datetime
    evidence: list[WorkspaceEvidenceRead]


class WorkspaceSourceRead(BaseModel):
    id: UUID
    source_name: str
    source_tier: SourceTier
    rights_basis: RightsBasis
    url: str
    title: str | None
    text: str
    published_at: datetime | None
    fetched_at: datetime
    lineage_root_id: UUID | None
    linked_claim_ids: list[UUID]


class WorkspaceVersionRead(BaseModel):
    id: UUID
    version_no: int
    title: str
    sentences: list[PublicationSentence]
    published_at: datetime
    published_by: str
    signed_off_by: str | None
    policy_version: str
    model_versions: dict[str, str]
    content_hash: str


class EventWorkspaceRead(BaseModel):
    id: UUID
    slug: str
    event_type: EventType
    corridor: Corridor
    status: str
    severity: int
    occurred_start: datetime | None
    occurred_end: datetime | None
    created_at: datetime
    title: str
    claims: list[WorkspaceClaimRead]
    sources: list[WorkspaceSourceRead]
    versions: list[WorkspaceVersionRead]


class ClaimCreate(BaseModel):
    text: str = Field(min_length=3, max_length=10_000)
    claimant: str | None = Field(default=None, max_length=255)
    claim_state: ClaimState
    occurred_at: datetime | None = None
    reviewer: str = Field(min_length=2, max_length=255)
    sensitivity_flags: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def reviewer_is_human(self) -> ClaimCreate:
        if self.reviewer.casefold().startswith("model:"):
            raise ValueError("a model cannot approve a claim")
        return self


class ClaimUpdate(BaseModel):
    text: str | None = Field(default=None, min_length=3, max_length=10_000)
    claimant: str | None = Field(default=None, max_length=255)
    claim_state: ClaimState | None = None
    occurred_at: datetime | None = None
    sensitivity_flags: list[str] | None = Field(default=None, max_length=20)
    reviewer: str = Field(min_length=2, max_length=255)
    second_reviewed_by: str | None = Field(default=None, min_length=2, max_length=255)

    @model_validator(mode="after")
    def update_is_human_and_material(self) -> ClaimUpdate:
        if self.reviewer.casefold().startswith("model:"):
            raise ValueError("a model cannot approve a claim")
        if self.second_reviewed_by and self.second_reviewed_by.casefold().startswith("model:"):
            raise ValueError("a model cannot perform a second review")
        if not (self.model_fields_set - {"reviewer"}):
            raise ValueError("claim update has no changes")
        return self


class ClaimSecondReviewCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    reason: str = Field(min_length=10, max_length=2_000)


class ClaimSecondReviewRead(BaseModel):
    claim_id: UUID
    event_id: UUID
    approval_id: UUID
    binding_hash: str
    primary_reviewer: str
    second_reviewer: str
    auth_assurance: AuthAssurance
    reason: str
    approved_at: datetime
    expires_at: datetime


class EvidenceLinkCreate(BaseModel):
    source_record_id: UUID
    directness: Directness
    excerpt: str | None = Field(default=None, max_length=5000)
    reviewer: str = Field(min_length=2, max_length=255)

    @model_validator(mode="after")
    def reviewer_is_human(self) -> EvidenceLinkCreate:
        if self.reviewer.casefold().startswith("model:"):
            raise ValueError("a model cannot link publication evidence")
        return self


class EvidenceLinkRead(BaseModel):
    id: UUID
    claim_id: UUID
    source_record_id: UUID
    lineage_root_id: UUID
    directness: Directness
    excerpt: str | None


class LineageProposalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    subject_record_id: UUID
    candidate_record_id: UUID | None
    proposed_origin_source_id: UUID | None
    proposed_origin_name: str | None
    score: float
    signals_json: dict[str, object]
    status: LineageProposalStatus
    component_version: str
    created_at: datetime


class LineageReviewCreate(BaseModel):
    decision: LineageReviewDecision
    reviewer: str = Field(min_length=2, max_length=255)
    reason: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def rejection_has_reason(self) -> LineageReviewCreate:
        if self.decision == LineageReviewDecision.REJECT and not self.reason:
            raise ValueError("rejected lineage proposals require a reason")
        return self


class LineageReviewRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    proposal_id: UUID
    decision: LineageReviewDecision
    reviewer: str
    reason: str | None
    reviewed_at: datetime


class PipelineEvaluationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    component: str
    component_version: str
    metric_name: str
    metric_value: float
    sample_size: int
    evaluated_at: datetime
    graduated: bool


class ClaimExtractionQuantity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = Field(min_length=1, max_length=100)
    unit: str = Field(min_length=1, max_length=100)
    what: str = Field(min_length=1, max_length=500)


class ClaimExtractionLocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=500)
    unlocode: str | None = Field(default=None, min_length=5, max_length=5)


class ClaimExtractionClaimEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=3, max_length=10_000)
    claimant: str | None = Field(default=None, min_length=1, max_length=255)
    occurred_time: datetime | None = None
    location: ClaimExtractionLocation | None = None
    quantities: list[ClaimExtractionQuantity] = Field(default_factory=list, max_length=50)
    hedging_language: bool
    source_sentence_quote: str = Field(min_length=1, max_length=5_000)

    @field_validator("occurred_time")
    @classmethod
    def occurred_time_has_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("occurred_time must include a timezone")
        return value


class ClaimExtractionProposalRead(BaseModel):
    id: UUID
    run_id: UUID
    source_record_id: UUID
    source_name: str
    source_language: str
    source_title: str | None
    source_text: str
    source_url: str
    model_version: str
    prompt_version: str
    mode: str
    proposal_index: int
    text: str
    claimant: str | None
    occurred_time: datetime | None
    location: dict[str, object] | None
    quantities: list[dict[str, object]]
    hedging_language: bool
    source_sentence_quote: str
    source_start: int
    source_end: int
    segment_index: int
    status: ClaimExtractionProposalStatus


class ClaimExtractionReviewCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    decision: ClaimExtractionDecision
    reviewer: str = Field(min_length=2, max_length=255)
    event_id: UUID | None = None
    claim_state: ClaimState | None = None
    edited_claim: ClaimExtractionClaimEdit | None = None
    reason_codes: list[str] = Field(default_factory=list, max_length=20)
    note: str | None = Field(default=None, max_length=2_000)
    baseline_seconds: int = Field(ge=1, le=86_400)
    review_seconds: int = Field(ge=1, le=86_400)

    @model_validator(mode="after")
    def review_is_human_and_complete(self) -> ClaimExtractionReviewCreate:
        if self.reviewer.casefold().startswith("model:"):
            raise ValueError("a model cannot review a claim proposal")
        if len(self.reason_codes) != len(set(self.reason_codes)):
            raise ValueError("reason codes must be unique")
        if any(not code.strip() or len(code) > 64 for code in self.reason_codes):
            raise ValueError("reason codes must be non-empty and no longer than 64 characters")
        if self.decision in {ClaimExtractionDecision.EDIT, ClaimExtractionDecision.REJECT} and not (
            self.reason_codes
        ):
            raise ValueError("edited and rejected proposals require a reason code")
        if self.decision in {ClaimExtractionDecision.ACCEPT, ClaimExtractionDecision.EDIT} and (
            self.event_id is None or self.claim_state is None
        ):
            raise ValueError("accepted and edited proposals require an event and claim state")
        if (self.decision == ClaimExtractionDecision.EDIT) != (self.edited_claim is not None):
            raise ValueError("edited claim content is allowed and required only for edit decisions")
        return self


class ClaimExtractionReviewRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    proposal_id: UUID
    decision: ClaimExtractionDecision
    event_id: UUID | None
    reviewer: str
    claim_state: ClaimState | None
    final_claim_json: dict[str, object] | None
    reason_codes: list[str]
    note: str | None
    baseline_seconds: int
    review_seconds: int
    mode: str
    resulting_claim_id: UUID | None
    reviewed_at: datetime


class ClaimExtractionQACreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    evaluator: str = Field(min_length=2, max_length=255)
    error_found: bool
    error_codes: list[str] = Field(default_factory=list, max_length=20)
    note: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def qa_is_human_and_consistent(self) -> ClaimExtractionQACreate:
        if self.evaluator.casefold().startswith("model:"):
            raise ValueError("a model cannot perform extraction QA")
        if len(self.error_codes) != len(set(self.error_codes)):
            raise ValueError("error codes must be unique")
        if any(not code.strip() or len(code) > 64 for code in self.error_codes):
            raise ValueError("error codes must be non-empty and no longer than 64 characters")
        if self.error_found != bool(self.error_codes):
            raise ValueError("QA errors require codes, and clean QA cannot have error codes")
        return self


class ClaimExtractionQARead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    review_id: UUID
    evaluator: str
    error_found: bool
    error_codes: list[str]
    note: str | None
    evaluated_at: datetime


class ClaimExtractionEvaluationRead(BaseModel):
    evaluation: PipelineEvaluationRead
    language: str
    prompt_version: str
    model_version: str
    reviewed: int
    qa_reviewed: int
    shadow_days: float
    time_reduction_percent: float
    error_rate: float
    baseline_error_rate: float | None
    graduated: bool


class ClaimExtractionWeeklyReportRead(BaseModel):
    prompt_version: str
    reviewed: int
    reason_codes: dict[str, int]
    decisions: dict[str, int]
    languages: dict[str, int]


class PublicationSentence(BaseModel):
    section: Literal["confirmed", "reported", "unknown", "changed"]
    text: str = Field(min_length=1, max_length=5000)
    claim_ids: list[UUID] = Field(min_length=1, max_length=50)

    @field_validator("text")
    @classmethod
    def text_is_one_material_sentence(cls, value: str) -> str:
        normalized = value.strip()
        if "\n" in normalized or "\r" in normalized:
            raise ValueError("publication sentence text cannot contain line breaks")
        return normalized


class EventVersionContentDraft(BaseModel):
    title: str = Field(min_length=3, max_length=500)
    sentences: list[PublicationSentence] = Field(min_length=1, max_length=200)
    evidence_ids: list[UUID] = Field(max_length=1000)
    policy_version: str = Field(default="publication-policy-v1", max_length=64)
    model_versions: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def publication_references_are_unique(self) -> EventVersionContentDraft:
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("evidence_ids must be unique")
        for sentence in self.sentences:
            if len(sentence.claim_ids) != len(set(sentence.claim_ids)):
                raise ValueError("sentence claim_ids must be unique")
        return self


class EventVersionDraft(EventVersionContentDraft):
    published_by: str = Field(min_length=2, max_length=255)
    signed_off_by: str | None = Field(default=None, min_length=2, max_length=255)


class EventVersionCreate(EventVersionDraft):
    preview_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class PublicationGateCheckRead(BaseModel):
    key: str
    label: str
    passed: bool
    detail: str


class PublicationFieldDiffRead(BaseModel):
    before: str
    after: str
    changed: bool


class EventVersionPreviewRead(BaseModel):
    previous_version_no: int | None
    next_version_no: int
    preview_hash: str
    ready: bool
    checks: list[PublicationGateCheckRead]
    diff: dict[str, PublicationFieldDiffRead]


class PublicationApprovalCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    publisher_user_id: UUID
    draft: EventVersionContentDraft
    reason: str = Field(min_length=10, max_length=2_000)


class PublicationApprovalRead(BaseModel):
    id: UUID
    event_id: UUID
    preview_hash: str
    publisher_user_id: UUID
    approver_user_id: UUID
    published_by: str
    signed_off_by: str
    auth_assurance: AuthAssurance
    reason: str
    approved_at: datetime
    expires_at: datetime
    approved_draft: EventVersionDraft
    preview: EventVersionPreviewRead


class PublicationApprovalRequestCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    draft: EventVersionContentDraft
    reason: str = Field(min_length=10, max_length=2_000)


class ApprovalRequestDecisionCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    decision: Literal["approve", "reject"]
    reason: str = Field(min_length=10, max_length=2_000)


class ApprovalRequestRead(BaseModel):
    id: UUID
    request_type: Literal["event_publication", "operational_correction"]
    target_id: UUID
    request_hash: str
    request_payload: dict[str, object]
    primary_user_id: UUID
    primary_display_name: str
    status: Literal["pending", "approved", "rejected", "cancelled", "expired"]
    request_reason: str
    requested_at: datetime
    expires_at: datetime
    decided_at: datetime | None
    decided_by_user_id: UUID | None
    decided_by_display_name: str | None
    decision_reason: str | None
    approval_id: UUID | None
    release_hash: str | None
    release_expires_at: datetime | None
    approved_payload: dict[str, object] | None


class EventVersionRead(BaseModel):
    id: UUID
    event_id: UUID
    version_no: int
    content_hash: str
    published_at: datetime
    publication_approval_id: UUID | None = None


class ReliabilityReceiptRead(BaseModel):
    receipt_schema_version: Literal["1.0"]
    receipt_hash: str
    event_version_id: UUID
    event_id: UUID
    version_no: int
    published_at: datetime
    published_by: str
    signed_off_by: str | None
    publication_approval_id: UUID | None
    policy_version: str
    model_versions: dict[str, str]
    content_hash: str
    sentence_claim_map: dict[str, list[str]]
    event_snapshot: dict[str, object]
    claim_snapshots: list[dict[str, object]]
    evidence_snapshots: list[dict[str, object]]


class AlertReleaseDraft(BaseModel):
    channels: list[DeliveryChannel] = Field(min_length=1, max_length=3)
    released_by: str = Field(min_length=2, max_length=255)
    note: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def release_is_supported_and_human(self) -> AlertReleaseDraft:
        if self.released_by.casefold().startswith("model:"):
            raise ValueError("a model cannot release a customer alert")
        if len(self.channels) != len(set(self.channels)):
            raise ValueError("alert channels must be unique")
        unsupported = set(self.channels) - {
            DeliveryChannel.EMAIL,
            DeliveryChannel.TELEGRAM,
            DeliveryChannel.WHATSAPP,
        }
        if unsupported:
            raise ValueError("only email, Telegram, and WhatsApp alert channels are supported")
        return self


class AlertAudienceAccountRead(BaseModel):
    account_id: UUID
    company: str
    tier: AccountTier
    matched_profile_ids: list[UUID]
    user_count: int
    delivery_count: int
    alerts_today: int
    daily_cap: int
    throttled: bool
    reason: str | None


class AlertPreviewRead(BaseModel):
    event_version_id: UUID
    event_id: UUID
    title: str
    severity: int
    rules_version: str
    matched_profiles: int
    account_count: int
    user_count: int
    planned_deliveries: dict[str, int]
    accounts: list[AlertAudienceAccountRead]
    blockers: list[str]
    ready: bool
    preview_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class AlertReleaseCreate(AlertReleaseDraft):
    preview_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class AlertReleaseRead(BaseModel):
    alert_id: UUID
    event_version_id: UUID
    delivery_count: int
    queued_at: datetime
    idempotent_replay: bool


class DeliveryRecentRead(BaseModel):
    id: UUID
    alert_id: UUID
    event_version_id: UUID
    event_title: str
    account_company: str
    recipient: str
    channel: DeliveryChannel
    status: DeliveryStatus
    queued_at: datetime
    sent_at: datetime | None
    delivered_at: datetime | None
    attempt_count: int
    last_error: str | None


class DeliveryDashboardRead(BaseModel):
    generated_at: datetime
    total: int
    status_counts: dict[str, int]
    channel_counts: dict[str, int]
    publish_to_delivery_p95_seconds: float | None
    within_60_seconds_percent: float | None
    recent: list[DeliveryRecentRead]


class PortalMeRead(BaseModel):
    user_id: UUID
    account_id: UUID
    email: str
    company: str
    role: str


class PortalBoardEventRead(BaseModel):
    event_id: UUID
    slug: str
    title: str
    event_type: EventType
    corridor: Corridor
    status: str
    severity: int
    ports: list[dict[str, str]]
    version_no: int
    published_at: datetime
    whats_changed: str


class PortalCorridorRead(BaseModel):
    corridor: Corridor
    label: str
    operational_state: str
    event_count: int
    updated_at: datetime | None
    events: list[PortalBoardEventRead]


class PortalBoardRead(BaseModel):
    generated_at: datetime
    corridors: list[PortalCorridorRead]


class PortalArchiveResultRead(PortalBoardEventRead):
    summary_confirmed: str
    rank: float | None = None


class PortalArchiveRead(BaseModel):
    query: str | None
    total: int
    limit: int
    offset: int
    results: list[PortalArchiveResultRead]


class PortalEventTimelineRead(BaseModel):
    version_no: int
    published_at: datetime
    whats_changed: str
    content_hash: str


class PortalEventSourceRead(BaseModel):
    source_record_id: UUID
    source_name: str
    source_tier: SourceTier
    url: str
    directness: Directness
    lineage_root_id: UUID
    excerpt: str | None
    rights_basis: RightsBasis


class PortalEventCorrectionRead(BaseModel):
    id: UUID
    correction_type: CorrectionType
    note: str
    version_from: int
    version_to: int
    affected_version_hash: str
    corrected_version_hash: str
    issued_at: datetime


class PortalEventRead(BaseModel):
    event_id: UUID
    slug: str
    title: str
    event_type: EventType
    corridor: Corridor
    status: str
    severity: int
    ports: list[dict[str, str]]
    occurred_start: datetime | None
    occurred_end: datetime | None
    version_no: int
    latest_version_id: UUID
    published_at: datetime
    content_hash: str
    summary_confirmed: str
    summary_reported: str
    summary_unknown: str
    whats_changed: str
    timeline: list[PortalEventTimelineRead]
    sources: list[PortalEventSourceRead]
    corrections: list[PortalEventCorrectionRead]


class BriefCompileCreate(BaseModel):
    compiled_by: str = Field(min_length=2, max_length=255)

    @model_validator(mode="after")
    def compiler_is_human(self) -> BriefCompileCreate:
        if self.compiled_by.casefold().startswith("model:"):
            raise ValueError("a model cannot compile a customer brief")
        return self


class BriefFinalizeCreate(BaseModel):
    finalized_by: str = Field(min_length=2, max_length=255)
    title: str = Field(min_length=3, max_length=500)
    introduction: str = Field(default="", max_length=10_000)
    forward_watch: str = Field(default="", max_length=10_000)
    item_version_ids: list[UUID] = Field(max_length=500)

    @model_validator(mode="after")
    def finalizer_is_human_and_items_are_unique(self) -> BriefFinalizeCreate:
        if self.finalized_by.casefold().startswith("model:"):
            raise ValueError("a model cannot finalize a customer brief")
        if len(self.item_version_ids) != len(set(self.item_version_ids)):
            raise ValueError("brief item_version_ids must be unique")
        return self


class DailyBriefItemRead(BaseModel):
    event_id: UUID
    event_slug: str
    event_version_id: UUID
    version_no: int
    title: str
    event_type: EventType
    corridor: Corridor
    status: str
    severity: int
    published_at: datetime
    summary_confirmed: str
    summary_reported: str
    summary_unknown: str
    whats_changed: str
    content_hash: str


class DailyBriefCorrectionRead(BaseModel):
    id: UUID
    event_id: UUID
    event_slug: str
    correction_type: CorrectionType
    note: str
    version_from: int
    version_to: int
    affected_version_hash: str
    corrected_version_hash: str
    issued_at: datetime


class DailyBriefRead(BaseModel):
    id: UUID
    brief_date: date
    title: str
    introduction: str
    forward_watch: str
    status: BriefStatus
    items: list[DailyBriefItemRead]
    source_version_ids: list[UUID]
    corrections: list[DailyBriefCorrectionRead]
    compiled_at: datetime
    compiled_by: str
    finalized_at: datetime | None
    finalized_by: str | None
    content_hash: str


class StateKnowledgeReportCreate(BaseModel):
    event_id: UUID
    requested_timestamp: datetime
    requested_by: str = Field(min_length=2, max_length=255)


class StateKnowledgeReportRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    event_id: UUID
    event_version_id: UUID
    account_id: UUID | None
    requested_timestamp: datetime
    requested_by: str
    generated_at: datetime
    version_content_hash: str
    content_hash: str


class CorrectionContentDraft(BaseModel):
    event_id: UUID
    version_from_id: UUID
    version_to_id: UUID
    correction_type: CorrectionType
    impact: CorrectionImpact
    note: str = Field(min_length=1, max_length=20_000)
    root_cause: str = Field(min_length=1, max_length=20_000)
    corrective_action: str = Field(min_length=1, max_length=20_000)
    detected_at: datetime


class CorrectionDraft(CorrectionContentDraft):
    drafted_by: str = Field(min_length=2, max_length=255)
    signed_off_by: str = Field(min_length=2, max_length=255)


class CorrectionIssueCreate(CorrectionDraft):
    preview_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class CorrectionApprovalCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    drafter_user_id: UUID
    draft: CorrectionContentDraft
    reason: str = Field(min_length=10, max_length=2_000)


class CorrectionApprovalRequestCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    draft: CorrectionContentDraft
    reason: str = Field(min_length=10, max_length=2_000)


class CorrectionApprovalRead(BaseModel):
    id: UUID
    preview_hash: str
    drafter_user_id: UUID
    approver_user_id: UUID
    drafted_by: str
    signed_off_by: str
    auth_assurance: AuthAssurance
    reason: str
    approved_at: datetime
    expires_at: datetime
    approved_draft: CorrectionDraft


class CorrectionPreviewRead(BaseModel):
    preview_hash: str
    event_id: UUID
    event_slug: str
    version_from_id: UUID
    version_to_id: UUID
    version_from: int
    version_to: int
    affected_version_hash: str
    corrected_version_hash: str
    channels: list[DeliveryChannel]
    recipient_count: int
    message: dict[str, object]


class CorrectionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    event_id: UUID
    version_from_id: UUID
    version_to_id: UUID
    version_from: int
    version_to: int
    correction_type: CorrectionType
    impact: CorrectionImpact | None
    note: str
    root_cause: str | None
    corrective_action: str | None
    drafted_by: str | None
    signed_off_by: str | None
    correction_approval_id: UUID | None = None
    preview_hash: str | None
    propagation_deadline_at: datetime | None
    detected_at: datetime
    issued_at: datetime | None
    issued_by: str
    propagated_channels_json: dict[str, object]


class TTVCreate(BaseModel):
    event_id: UUID
    first_credible_signal_at: datetime
    signal_source_record_id: UUID
    coverage_window: bool = True
    notes: str | None = Field(default=None, max_length=10_000)
    recorded_by: str = Field(min_length=2, max_length=255)
    corroborated_tier_e: bool = False


class TTVUpdate(BaseModel):
    holding_line_at: datetime | None = None
    verified_update_at: datetime | None = None
    coverage_window: bool | None = None
    notes: str | None = Field(default=None, max_length=10_000)
    updated_by: str = Field(min_length=2, max_length=255)


class TTVRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    event_id: UUID
    first_credible_signal_at: datetime
    signal_source_record_id: UUID
    holding_line_at: datetime | None
    verified_update_at: datetime | None
    coverage_window: bool
    notes: str | None
    created_by: str
    updated_by: str
    created_at: datetime
    updated_at: datetime


class PublicCorrectionRead(BaseModel):
    id: UUID
    event_id: UUID
    event_slug: str
    correction_type: CorrectionType
    note: str
    affected_version_hash: str
    corrected_version_hash: str
    issued_at: datetime
    issued_within_60_minutes: bool


class QualityScoreboardRead(BaseModel):
    generated_at: datetime
    published_version_count: int
    correction_count: int
    correction_rate_percent: float
    corrections_within_60_minutes_percent: float | None
    ttv_coverage_count: int
    holding_line_median_minutes: float | None
    verified_update_median_minutes: float | None
    holding_line_within_15_minutes_percent: float | None
    verified_update_within_45_minutes_percent: float | None
    corrections: list[PublicCorrectionRead]


class WhatsAppWebhookRead(BaseModel):
    accepted: int
    duplicates: int
    unmatched: int


ApiScope = Literal["events:read", "versions:read", "claims:read", "calendar:read", "ais:read"]


class ApiKeyCreate(BaseModel):
    account_id: UUID
    name: str = Field(min_length=2, max_length=255)
    scopes: list[ApiScope] = Field(min_length=1, max_length=5)
    created_by: str = Field(min_length=2, max_length=255)
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def unique_scopes_and_human_creator(self) -> ApiKeyCreate:
        if len(self.scopes) != len(set(self.scopes)):
            raise ValueError("API key scopes must be unique")
        if self.created_by.casefold().startswith("model:"):
            raise ValueError("a model cannot create an account API key")
        return self


class ApiKeyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    account_id: UUID
    name: str
    key_prefix: str
    scopes_json: list[ApiScope]
    created_at: datetime
    created_by: str
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None
    revoked_by: str | None


class ApiKeyIssuedRead(ApiKeyRead):
    api_key: str


class ApiKeyRevokeCreate(BaseModel):
    revoked_by: str = Field(min_length=2, max_length=255)


class DataEventRead(BaseModel):
    id: UUID
    slug: str
    event_type: EventType
    corridor: Corridor
    status: str
    severity: int
    occurred_start: datetime | None
    occurred_end: datetime | None
    latest_version_id: UUID | None
    latest_version_no: int | None
    title: str | None
    published_at: datetime | None
    content_hash: str | None


class DataEventListRead(BaseModel):
    total: int
    limit: int
    offset: int
    results: list[DataEventRead]


class DataVersionRead(BaseModel):
    id: UUID
    event_id: UUID
    version_no: int
    title: str
    summary_confirmed: str
    summary_reported: str
    summary_unknown: str
    whats_changed: str
    sentence_claim_map: dict[str, list[str]]
    published_at: datetime
    policy_version: str
    content_hash: str


class DataClaimRead(BaseModel):
    id: UUID
    event_id: UUID
    text: str
    claimant: str | None
    claim_state: ClaimState
    occurred_at: datetime | None
    quantity_json: list[dict[str, object]]
    reviewed_at: datetime | None
    first_seen_at: datetime


class CalendarEventPublishCreate(BaseModel):
    calendar_event_id: UUID | None = None
    slug: str = Field(min_length=3, max_length=255, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    event_type: CalendarEventType
    title: str = Field(min_length=3, max_length=500)
    corridor: Corridor
    ports: list[dict[str, str]] = Field(default_factory=list, max_length=100)
    starts_at: datetime
    ends_at: datetime | None = None
    status: Literal["announced", "confirmed", "cancelled", "completed"]
    public_note: str = Field(min_length=1, max_length=10_000)
    source_record_ids: list[UUID] = Field(min_length=1, max_length=100)
    published_by: str = Field(min_length=2, max_length=255)

    @model_validator(mode="after")
    def calendar_item_is_ordered_and_human(self) -> CalendarEventPublishCreate:
        if self.ends_at is not None and self.ends_at < self.starts_at:
            raise ValueError("calendar event end cannot precede its start")
        if len(self.source_record_ids) != len(set(self.source_record_ids)):
            raise ValueError("calendar source record IDs must be unique")
        if self.published_by.casefold().startswith("model:"):
            raise ValueError("a model cannot publish a calendar event")
        return self


class CalendarEventRead(BaseModel):
    id: UUID
    calendar_event_id: UUID
    slug: str
    version_no: int
    event_type: CalendarEventType
    title: str
    corridor: Corridor
    ports: list[dict[str, str]]
    starts_at: datetime
    ends_at: datetime | None
    status: str
    public_note: str
    source_record_ids: list[UUID]
    published_at: datetime
    published_by: str
    content_hash: str


class CalendarEventListRead(BaseModel):
    generated_at: datetime
    results: list[CalendarEventRead]


class AISPositionRead(BaseModel):
    id: UUID
    mmsi: str
    imo: str | None
    vessel_name: str | None
    latitude: float
    longitude: float
    course: float | None
    speed: float | None
    navigation_status: str | None
    corridor: Corridor
    message_at: datetime
    received_at: datetime
    age_minutes: float
    stale: bool
    source: str
    payload_hash: str


class AISPositionListRead(BaseModel):
    generated_at: datetime
    cache_status: Literal["disabled", "empty", "stale", "live"]
    caveat: str
    freshness_minutes: int
    results: list[AISPositionRead]


class PollerHealthRead(BaseModel):
    source_id: UUID
    source_name: str
    state: str
    active: bool
    rights_approved: bool
    poll_interval_seconds: int
    last_poll_started_at: datetime | None
    last_successful_poll_at: datetime | None
    next_poll_at: datetime | None
    consecutive_failures: int
    last_poll_error: str | None


class DeskAlertRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source_id: UUID | None
    kind: DeskAlertKind
    status: DeskAlertStatus
    detected_at: datetime
    notified_at: datetime | None
    resolved_at: datetime | None
    detail: dict[str, object]
    notification_error: str | None
