from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from geoalchemy2 import Geography
from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from apps.api.alembic.snapshots.v0001_base import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)
from apps.api.alembic.snapshots.v0001_enums import (
    AccessMethod,
    AccountTier,
    ClaimRelationType,
    ClaimState,
    CorrectionType,
    Corridor,
    DeliveryChannel,
    DeliveryStatus,
    DeskAlertKind,
    DeskAlertStatus,
    Directness,
    EventStatus,
    EventType,
    LineageProposalStatus,
    LineageReviewDecision,
    PollerRunStatus,
    RightsBasis,
    SourceTier,
    SourceType,
    TriageAction,
    TriageStatus,
)


def enum_column(enum_type: type, name: str) -> Enum:
    return Enum(
        enum_type,
        name=name,
        native_enum=True,
        values_callable=lambda values: [item.value for item in values],
    )


event_version_claims = Table(
    "event_version_claims",
    Base.metadata,
    Column("event_version_id", Uuid, ForeignKey("event_versions.id"), primary_key=True),
    Column("claim_id", Uuid, ForeignKey("claims.id"), primary_key=True),
)

event_version_evidence = Table(
    "event_version_evidence",
    Base.metadata,
    Column("event_version_id", Uuid, ForeignKey("event_versions.id"), primary_key=True),
    Column("evidence_id", Uuid, ForeignKey("evidence.id"), primary_key=True),
)


class Source(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "sources"
    __table_args__ = (
        CheckConstraint(
            "poll_interval_seconds BETWEEN 60 AND 86400",
            name="poll_interval_range",
        ),
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    legal_entity: Mapped[str | None] = mapped_column(String(255))
    source_type: Mapped[SourceType] = mapped_column(enum_column(SourceType, "source_type"))
    tier: Mapped[SourceTier] = mapped_column(enum_column(SourceTier, "source_tier"))
    language: Mapped[str] = mapped_column(String(16), nullable=False)
    country: Mapped[str | None] = mapped_column(String(2))
    access_method: Mapped[AccessMethod] = mapped_column(enum_column(AccessMethod, "access_method"))
    feed_url: Mapped[str | None] = mapped_column(Text)
    inbound_mailbox_hash: Mapped[str | None] = mapped_column(String(128), unique=True, index=True)
    rights_basis: Mapped[RightsBasis] = mapped_column(enum_column(RightsBasis, "rights_basis"))
    rights_notes: Mapped[str | None] = mapped_column(Text)
    rights_reviewed_by: Mapped[str | None] = mapped_column(String(255))
    automation_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    syndicates_from_id: Mapped[UUID | None] = mapped_column(ForeignKey("sources.id"))
    state_affiliation: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)
    last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    etag: Mapped[str | None] = mapped_column(String(512))
    last_modified: Mapped[str | None] = mapped_column(String(255))
    last_successful_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_poll_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    poll_interval_seconds: Mapped[int] = mapped_column(
        Integer, default=3600, server_default="3600", nullable=False
    )
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    last_poll_error: Mapped[str | None] = mapped_column(Text)


class SourceRecord(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "source_records"
    __table_args__ = (
        UniqueConstraint("source_id", "content_hash", name="uq_source_records_source_hash"),
        Index("ix_source_records_published_at", "published_at"),
    )

    source_id: Mapped[UUID] = mapped_column(ForeignKey("sources.id"), nullable=False, index=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_ref_r2: Mapped[str | None] = mapped_column(Text)
    extracted_text: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(String(255))
    lang: Mapped[str | None] = mapped_column(String(16))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    parser_version: Mapped[str] = mapped_column(String(64), nullable=False)
    security_scan: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class TriageItem(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "triage_items"
    __table_args__ = (Index("ix_triage_items_status_created", "status", "created_at"),)

    source_record_id: Mapped[UUID] = mapped_column(
        ForeignKey("source_records.id"), unique=True, nullable=False, index=True
    )
    status: Mapped[TriageStatus] = mapped_column(
        enum_column(TriageStatus, "triage_status"),
        default=TriageStatus.PENDING,
        server_default=TriageStatus.PENDING.value,
        nullable=False,
    )
    detected_corridors: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    detected_ports: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list, nullable=False)
    suggested_event_types: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    assigned_event_id: Mapped[UUID | None] = mapped_column(ForeignKey("events.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[str | None] = mapped_column(String(255))
    dismissal_reason: Mapped[str | None] = mapped_column(Text)


class TriageDecision(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "triage_decisions"

    triage_item_id: Mapped[UUID] = mapped_column(
        ForeignKey("triage_items.id"), nullable=False, index=True
    )
    action: Mapped[TriageAction] = mapped_column(
        enum_column(TriageAction, "triage_action"), nullable=False
    )
    reviewer: Mapped[str] = mapped_column(String(255), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    event_id: Mapped[UUID | None] = mapped_column(ForeignKey("events.id"), index=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SourceRecordEmbedding(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "source_record_embeddings"
    __table_args__ = (
        UniqueConstraint(
            "source_record_id",
            "model_version",
            name="uq_source_record_embeddings_record_model",
        ),
    )

    source_record_id: Mapped[UUID] = mapped_column(
        ForeignKey("source_records.id"), nullable=False, index=True
    )
    model_version: Mapped[str] = mapped_column(String(255), nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(768), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class LineageProposal(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "lineage_proposals"
    __table_args__ = (
        CheckConstraint("score BETWEEN 0 AND 1", name="lineage_proposal_score_range"),
        CheckConstraint(
            "candidate_record_id IS NOT NULL OR proposed_origin_source_id IS NOT NULL "
            "OR proposed_origin_name IS NOT NULL",
            name="lineage_proposal_has_target",
        ),
        Index("ix_lineage_proposals_status_created", "status", "created_at"),
    )

    proposal_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    subject_record_id: Mapped[UUID] = mapped_column(
        ForeignKey("source_records.id"), nullable=False, index=True
    )
    candidate_record_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("source_records.id"), index=True
    )
    proposed_origin_source_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("sources.id"), index=True
    )
    proposed_origin_name: Mapped[str | None] = mapped_column(String(255))
    score: Mapped[float] = mapped_column(Float, nullable=False)
    signals_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[LineageProposalStatus] = mapped_column(
        enum_column(LineageProposalStatus, "lineage_proposal_status"),
        default=LineageProposalStatus.PENDING,
        server_default=LineageProposalStatus.PENDING.value,
        nullable=False,
    )
    component_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class LineageReview(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "lineage_reviews"

    proposal_id: Mapped[UUID] = mapped_column(
        ForeignKey("lineage_proposals.id"), unique=True, nullable=False
    )
    decision: Mapped[LineageReviewDecision] = mapped_column(
        enum_column(LineageReviewDecision, "lineage_review_decision"), nullable=False
    )
    reviewer: Mapped[str] = mapped_column(String(255), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SourceRecordLineage(Base):
    __tablename__ = "source_record_lineage"

    source_record_id: Mapped[UUID] = mapped_column(
        ForeignKey("source_records.id"), primary_key=True
    )
    lineage_root_id: Mapped[UUID] = mapped_column(
        ForeignKey("lineage_roots.id"), nullable=False, index=True
    )
    proposal_id: Mapped[UUID | None] = mapped_column(ForeignKey("lineage_proposals.id"))
    assigned_by: Mapped[str] = mapped_column(String(255), nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PollerRun(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "poller_runs"
    __table_args__ = (Index("ix_poller_runs_source_started", "source_id", "started_at"),)

    source_id: Mapped[UUID] = mapped_column(ForeignKey("sources.id"), nullable=False)
    status: Mapped[PollerRunStatus] = mapped_column(
        enum_column(PollerRunStatus, "poller_run_status"), nullable=False
    )
    queued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    records_created: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    worker_job_id: Mapped[str | None] = mapped_column(String(255))
    error_type: Mapped[str | None] = mapped_column(String(255))
    error_message: Mapped[str | None] = mapped_column(Text)


class DeskAlert(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "desk_alerts"
    __table_args__ = (Index("ix_desk_alerts_open", "status", "detected_at"),)

    source_id: Mapped[UUID | None] = mapped_column(ForeignKey("sources.id"), index=True)
    kind: Mapped[DeskAlertKind] = mapped_column(
        enum_column(DeskAlertKind, "desk_alert_kind"), nullable=False
    )
    status: Mapped[DeskAlertStatus] = mapped_column(
        enum_column(DeskAlertStatus, "desk_alert_status"), nullable=False
    )
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    notification_error: Mapped[str | None] = mapped_column(Text)


class ExternalRecordRef(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "external_record_refs"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "container_id",
            "table_name",
            "external_id",
            name="uq_external_record_identity",
        ),
    )

    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    container_id: Mapped[str] = mapped_column(String(255), nullable=False)
    table_name: Mapped[str] = mapped_column(String(128), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    entity: Mapped[str] = mapped_column(String(128), nullable=False)
    entity_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class Event(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "events"
    __table_args__ = (CheckConstraint("severity BETWEEN 1 AND 4", name="severity_range"),)

    slug: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    event_type: Mapped[EventType] = mapped_column(enum_column(EventType, "event_type"))
    corridor: Mapped[Corridor] = mapped_column(enum_column(Corridor, "corridor"))
    geo: Mapped[Any | None] = mapped_column(Geography(geometry_type="GEOMETRY", srid=4326))
    geo_precision: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[EventStatus] = mapped_column(enum_column(EventStatus, "event_status"))
    severity: Mapped[int] = mapped_column(Integer, nullable=False)
    occurred_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    occurred_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EventVersion(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "event_versions"
    __table_args__ = (
        UniqueConstraint("event_id", "version_no", name="uq_event_versions_event_version"),
    )

    event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id"), nullable=False, index=True)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary_confirmed: Mapped[str] = mapped_column(Text, default="", nullable=False)
    summary_reported: Mapped[str] = mapped_column(Text, default="", nullable=False)
    summary_unknown: Mapped[str] = mapped_column(Text, default="", nullable=False)
    whats_changed: Mapped[str] = mapped_column(Text, default="", nullable=False)
    sentence_claim_map: Mapped[dict[str, list[str]]] = mapped_column(JSON, nullable=False)
    event_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    claim_snapshot_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    evidence_snapshot_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    published_by: Mapped[str] = mapped_column(String(255), nullable=False)
    signed_off_by: Mapped[str | None] = mapped_column(String(255))
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    model_versions: Mapped[dict[str, str]] = mapped_column(JSON, default=dict, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class Claim(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "claims"

    event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id"), nullable=False, index=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    claimant: Mapped[str | None] = mapped_column(String(255))
    claim_state: Mapped[ClaimState] = mapped_column(enum_column(ClaimState, "claim_state"))
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    geo: Mapped[Any | None] = mapped_column(Geography(geometry_type="GEOMETRY", srid=4326))
    quantity_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    proposed_by: Mapped[str] = mapped_column(String(255), nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(String(255))
    second_reviewed_by: Mapped[str | None] = mapped_column(String(255))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sensitivity_flags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LineageRoot(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "lineage_roots"

    description: Mapped[str] = mapped_column(Text, nullable=False)
    origin_source_id: Mapped[UUID | None] = mapped_column(ForeignKey("sources.id"))
    origin_url: Mapped[str | None] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Evidence(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "evidence"

    source_record_id: Mapped[UUID] = mapped_column(
        ForeignKey("source_records.id"), nullable=False, index=True
    )
    claim_id: Mapped[UUID] = mapped_column(ForeignKey("claims.id"), nullable=False, index=True)
    directness: Mapped[Directness] = mapped_column(enum_column(Directness, "directness"))
    lineage_root_id: Mapped[UUID] = mapped_column(ForeignKey("lineage_roots.id"), nullable=False)
    excerpt: Mapped[str | None] = mapped_column(Text)
    capture_snapshot_r2: Mapped[str | None] = mapped_column(Text)
    rights_decision: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class ClaimRelation(Base):
    __tablename__ = "claim_relations"

    claim_a: Mapped[UUID] = mapped_column(ForeignKey("claims.id"), primary_key=True)
    claim_b: Mapped[UUID] = mapped_column(ForeignKey("claims.id"), primary_key=True)
    relation: Mapped[ClaimRelationType] = mapped_column(
        enum_column(ClaimRelationType, "claim_relation_type"), primary_key=True
    )


class Correction(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "corrections"

    event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id"), nullable=False)
    version_from: Mapped[int] = mapped_column(Integer, nullable=False)
    version_to: Mapped[int] = mapped_column(Integer, nullable=False)
    correction_type: Mapped[CorrectionType] = mapped_column(
        enum_column(CorrectionType, "correction_type")
    )
    note: Mapped[str] = mapped_column(Text, nullable=False)
    propagated_channels_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    issued_by: Mapped[str] = mapped_column(String(255), nullable=False)


class Account(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "accounts"

    company: Mapped[str] = mapped_column(String(255), nullable=False)
    tier: Mapped[AccountTier] = mapped_column(enum_column(AccountTier, "account_tier"))
    contract_start: Mapped[date] = mapped_column(Date, nullable=False)
    contract_end: Mapped[date] = mapped_column(Date, nullable=False)
    billing_ref: Mapped[str | None] = mapped_column(String(255))


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "users"

    account_id: Mapped[UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    phone: Mapped[str | None] = mapped_column(String(64))
    channels_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    role: Mapped[str] = mapped_column(String(64), nullable=False)


class WatchProfile(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "watch_profiles"
    __table_args__ = (
        CheckConstraint("min_severity BETWEEN 1 AND 4", name="min_severity_range"),
        UniqueConstraint("account_id", "name", name="uq_watch_profiles_account_name"),
        Index("ix_watch_profiles_active_account", "active", "account_id"),
    )

    account_id: Mapped[UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    corridors: Mapped[list[str]] = mapped_column(ARRAY(String(64)), default=list, nullable=False)
    event_types: Mapped[list[str]] = mapped_column(ARRAY(String(64)), default=list, nullable=False)
    min_severity: Mapped[int] = mapped_column(Integer, nullable=False)
    ports: Mapped[list[str]] = mapped_column(ARRAY(String(128)), default=list, nullable=False)
    custom_geo: Mapped[Any | None] = mapped_column(Geography(geometry_type="GEOMETRY", srid=4326))
    active: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    configured_by: Mapped[str | None] = mapped_column(String(255))
    configured_with_customer_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Alert(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "alerts"
    __table_args__ = (
        CheckConstraint("severity BETWEEN 1 AND 4", name="severity_range"),
        UniqueConstraint("event_version_id", name="uq_alerts_event_version"),
    )

    event_version_id: Mapped[UUID] = mapped_column(ForeignKey("event_versions.id"), nullable=False)
    severity: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    released_by: Mapped[str] = mapped_column(String(255), nullable=False)
    channels_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    audience_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    message_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class Delivery(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "deliveries"
    __table_args__ = (
        UniqueConstraint("alert_id", "user_id", "channel", name="uq_deliveries_alert_user_channel"),
        CheckConstraint("attempt_count >= 0", name="attempt_count_nonnegative"),
        Index("ix_deliveries_status_retry", "status", "next_attempt_at"),
    )

    alert_id: Mapped[UUID] = mapped_column(ForeignKey("alerts.id"), nullable=False)
    account_id: Mapped[UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False, index=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    channel: Mapped[DeliveryChannel] = mapped_column(
        enum_column(DeliveryChannel, "delivery_channel")
    )
    recipient: Mapped[str] = mapped_column(String(512), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[DeliveryStatus] = mapped_column(enum_column(DeliveryStatus, "delivery_status"))
    queued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    provider_ref: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class TTVLog(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "ttv_log"

    event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id"), nullable=False, index=True)
    first_credible_signal_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    signal_source_record_id: Mapped[UUID | None] = mapped_column(ForeignKey("source_records.id"))
    holding_line_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_update_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    coverage_window: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)


class EMEVLog(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "emev_log"

    event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id"), nullable=False, index=True)
    event_version_id: Mapped[UUID | None] = mapped_column(ForeignKey("event_versions.id"))
    work_date: Mapped[date] = mapped_column(Date, nullable=False)
    task: Mapped[str] = mapped_column(String(64), nullable=False)
    minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    analyst: Mapped[str] = mapped_column(String(255), nullable=False)


class UsageEvent(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "usage_events"

    account_id: Mapped[UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False, index=True)
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    event_name: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    properties: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class ValueMemo(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "value_memos"

    account_id: Mapped[UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False, index=True)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    generated_by: Mapped[str] = mapped_column(String(255), nullable=False)
    artifact_ref: Mapped[str | None] = mapped_column(Text)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class AuditLog(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "audit_log"

    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    entity: Mapped[str] = mapped_column(String(128), nullable=False)
    entity_id: Mapped[UUID | None] = mapped_column(Uuid)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PipelineEvaluation(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "pipeline_evaluations"

    component: Mapped[str] = mapped_column(String(64), nullable=False)
    component_version: Mapped[str] = mapped_column(String(64), nullable=False)
    language: Mapped[str] = mapped_column(String(16), nullable=False)
    metric_name: Mapped[str] = mapped_column(String(64), nullable=False)
    metric_value: Mapped[float] = mapped_column(Float, nullable=False)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    graduated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class SubscriptionMetric(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "subscription_metrics"

    metric_date: Mapped[date] = mapped_column(Date, nullable=False)
    account_id: Mapped[UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    arr_eur: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
