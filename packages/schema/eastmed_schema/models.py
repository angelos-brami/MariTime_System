from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
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
    Computed,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
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
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from eastmed_schema.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from eastmed_schema.enums import (
    AccessMethod,
    AccountTier,
    AICapabilityMode,
    AttemptStatus,
    AttestationDecision,
    AuthAssurance,
    AuthorityRole,
    AutomationStatus,
    BriefStatus,
    BusinessStatus,
    CalendarEventType,
    ClaimExtractionDecision,
    ClaimExtractionProposalStatus,
    ClaimExtractionRunStatus,
    ClaimRelationType,
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
    DrillStatus,
    DrillType,
    EventStatus,
    EventType,
    FuelGrade,
    LineageProposalStatus,
    LineageReviewDecision,
    OutboxResponseClass,
    OutboxStatus,
    PollerRunStatus,
    QuantityUnit,
    RecordKind,
    ReservationStatus,
    RightsBasis,
    SourceTier,
    SourceType,
    TaskStatus,
    TriageAction,
    TriageStatus,
    ValueOrigin,
    WorkflowStage,
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
    model_processing_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    model_processing_approved_by: Mapped[str | None] = mapped_column(String(255))
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
        Index("ix_event_versions_search_document", "search_document", postgresql_using="gin"),
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
    publication_approval_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("desk_approvals.id"), unique=True
    )
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    model_versions: Mapped[dict[str, str]] = mapped_column(JSON, default=dict, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    search_document: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('simple', "
            "coalesce(title, '') || ' ' || "
            "coalesce(summary_confirmed, '') || ' ' || "
            "coalesce(summary_reported, '') || ' ' || "
            "coalesce(summary_unknown, '') || ' ' || "
            "coalesce(whats_changed, ''))",
            persisted=True,
        ),
    )


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
    second_review_approval_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("desk_approvals.id"), unique=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sensitivity_flags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ClaimExtractionRun(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "claim_extraction_runs"
    __table_args__ = (
        UniqueConstraint(
            "source_record_id",
            "prompt_version",
            "model_version",
            name="uq_claim_extraction_runs_record_prompt_model",
        ),
        Index("ix_claim_extraction_runs_status_started", "status", "started_at"),
    )

    source_record_id: Mapped[UUID] = mapped_column(
        ForeignKey("source_records.id"), nullable=False, index=True
    )
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    model_version: Mapped[str] = mapped_column(String(255), nullable=False)
    language: Mapped[str] = mapped_column(String(16), nullable=False)
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[ClaimExtractionRunStatus] = mapped_column(
        enum_column(ClaimExtractionRunStatus, "claim_extraction_run_status"), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_hash: Mapped[str | None] = mapped_column(String(64))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    document_char_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    processed_char_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    segment_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    segment_manifest_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, nullable=False
    )
    coverage_complete: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    injection_suspected: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    error_type: Mapped[str | None] = mapped_column(String(255))
    error_message: Mapped[str | None] = mapped_column(Text)


class ClaimExtractionProposal(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "claim_extraction_proposals"
    __table_args__ = (
        UniqueConstraint("run_id", "proposal_index", name="uq_claim_proposals_run_index"),
        Index("ix_claim_proposals_status_record", "status", "source_record_id"),
    )

    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("claim_extraction_runs.id"), nullable=False, index=True
    )
    source_record_id: Mapped[UUID] = mapped_column(
        ForeignKey("source_records.id"), nullable=False, index=True
    )
    proposal_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    claimant: Mapped[str | None] = mapped_column(String(255))
    occurred_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    location_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    quantities_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    hedging_language: Mapped[bool] = mapped_column(Boolean, nullable=False)
    source_sentence_quote: Mapped[str] = mapped_column(Text, nullable=False)
    source_start: Mapped[int] = mapped_column(Integer, nullable=False)
    source_end: Mapped[int] = mapped_column(Integer, nullable=False)
    segment_index: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ClaimExtractionProposalStatus] = mapped_column(
        enum_column(ClaimExtractionProposalStatus, "claim_extraction_proposal_status"),
        default=ClaimExtractionProposalStatus.PENDING,
        server_default=ClaimExtractionProposalStatus.PENDING.value,
        nullable=False,
    )


class ClaimExtractionReview(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "claim_extraction_reviews"
    __table_args__ = (
        CheckConstraint("baseline_seconds > 0", name="claim_review_baseline_positive"),
        CheckConstraint("review_seconds > 0", name="claim_review_duration_positive"),
    )

    proposal_id: Mapped[UUID] = mapped_column(
        ForeignKey("claim_extraction_proposals.id"), unique=True, nullable=False
    )
    decision: Mapped[ClaimExtractionDecision] = mapped_column(
        enum_column(ClaimExtractionDecision, "claim_extraction_decision"), nullable=False
    )
    event_id: Mapped[UUID | None] = mapped_column(ForeignKey("events.id"), index=True)
    reviewer: Mapped[str] = mapped_column(String(255), nullable=False)
    claim_state: Mapped[ClaimState | None] = mapped_column(
        enum_column(ClaimState, "claim_state"), nullable=True
    )
    final_claim_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    reason_codes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    baseline_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    review_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    resulting_claim_id: Mapped[UUID | None] = mapped_column(ForeignKey("claims.id"))
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ClaimExtractionQA(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "claim_extraction_qa"

    review_id: Mapped[UUID] = mapped_column(
        ForeignKey("claim_extraction_reviews.id"), unique=True, nullable=False
    )
    evaluator: Mapped[str] = mapped_column(String(255), nullable=False)
    error_found: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error_codes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


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
    version_from_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("event_versions.id"), index=True
    )
    version_to_id: Mapped[UUID | None] = mapped_column(ForeignKey("event_versions.id"), index=True)
    version_from: Mapped[int] = mapped_column(Integer, nullable=False)
    version_to: Mapped[int] = mapped_column(Integer, nullable=False)
    correction_type: Mapped[CorrectionType] = mapped_column(
        enum_column(CorrectionType, "correction_type")
    )
    note: Mapped[str] = mapped_column(Text, nullable=False)
    propagated_channels_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    impact: Mapped[CorrectionImpact | None] = mapped_column(
        enum_column(CorrectionImpact, "correction_impact")
    )
    root_cause: Mapped[str | None] = mapped_column(Text)
    corrective_action: Mapped[str | None] = mapped_column(Text)
    drafted_by: Mapped[str | None] = mapped_column(String(255))
    signed_off_by: Mapped[str | None] = mapped_column(String(255))
    correction_approval_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("desk_approvals.id"), unique=True
    )
    preview_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    propagation_deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    issued_by: Mapped[str] = mapped_column(String(255), nullable=False)


class CorrectionDelivery(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "correction_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "correction_id",
            "user_id",
            "channel",
            name="uq_correction_deliveries_correction_user_channel",
        ),
        CheckConstraint("attempt_count >= 0", name="correction_attempt_count_nonnegative"),
        Index("ix_correction_deliveries_status_retry", "status", "next_attempt_at"),
    )

    correction_id: Mapped[UUID] = mapped_column(
        ForeignKey("corrections.id"), nullable=False, index=True
    )
    account_id: Mapped[UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    channel: Mapped[DeliveryChannel] = mapped_column(
        enum_column(DeliveryChannel, "delivery_channel")
    )
    recipient: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[DeliveryStatus] = mapped_column(enum_column(DeliveryStatus, "delivery_status"))
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    provider_ref: Mapped[str | None] = mapped_column(String(255))
    message_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class StateKnowledgeReport(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "state_knowledge_reports"

    event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id"), nullable=False, index=True)
    event_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("event_versions.id"), nullable=False, index=True
    )
    account_id: Mapped[UUID | None] = mapped_column(ForeignKey("accounts.id"), index=True)
    requested_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    requested_by: Mapped[str] = mapped_column(String(255), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class Account(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "accounts"
    __table_args__ = (
        CheckConstraint("contract_end >= contract_start", name="account_contract_date_order"),
    )

    company: Mapped[str] = mapped_column(String(255), nullable=False)
    tier: Mapped[AccountTier] = mapped_column(enum_column(AccountTier, "account_tier"))
    contract_start: Mapped[date] = mapped_column(Date, nullable=False)
    contract_end: Mapped[date] = mapped_column(Date, nullable=False)
    billing_ref: Mapped[str | None] = mapped_column(String(255))


class AccountApiKey(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "account_api_keys"
    __table_args__ = (
        UniqueConstraint("secret_hash", name="uq_account_api_keys_secret_hash"),
        Index("ix_account_api_keys_prefix_active", "key_prefix", "revoked_at"),
    )

    account_id: Mapped[UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    secret_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    scopes_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[str | None] = mapped_column(String(255))


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "users"

    account_id: Mapped[UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    phone: Mapped[str | None] = mapped_column(String(64))
    channels_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    auth_subject: Mapped[str | None] = mapped_column(String(255), unique=True, index=True)
    active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    portal_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )


class DailyBrief(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "daily_briefs"
    __table_args__ = (
        CheckConstraint(
            "status <> 'finalized' OR (finalized_at IS NOT NULL AND finalized_by IS NOT NULL)",
            name="daily_brief_finalized_has_reviewer",
        ),
    )

    brief_date: Mapped[date] = mapped_column(Date, unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    introduction: Mapped[str] = mapped_column(Text, default="", nullable=False)
    forward_watch: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[BriefStatus] = mapped_column(
        enum_column(BriefStatus, "brief_status"),
        default=BriefStatus.DRAFT,
        server_default=BriefStatus.DRAFT.value,
        nullable=False,
    )
    items_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    source_version_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    corrections_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, nullable=False
    )
    compiled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    compiled_by: Mapped[str] = mapped_column(String(255), nullable=False)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finalized_by: Mapped[str | None] = mapped_column(String(255))
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


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


class WhatsAppWebhookReceipt(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "whatsapp_webhook_receipts"
    __table_args__ = (
        UniqueConstraint("event_hash", name="uq_whatsapp_webhook_receipts_event_hash"),
        Index("ix_whatsapp_webhook_provider_ref", "provider_ref"),
    )

    event_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_status: Mapped[str] = mapped_column(String(32), nullable=False)
    event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class TTVLog(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "ttv_log"
    __table_args__ = (UniqueConstraint("event_id", name="uq_ttv_log_event"),)

    event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id"), nullable=False, index=True)
    first_credible_signal_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    signal_source_record_id: Mapped[UUID | None] = mapped_column(ForeignKey("source_records.id"))
    holding_line_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_update_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    coverage_window: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(255))
    updated_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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


class OperationalDrill(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "operational_drills"

    drill_type: Mapped[DrillType] = mapped_column(enum_column(DrillType, "drill_type"))
    status: Mapped[DrillStatus] = mapped_column(enum_column(DrillStatus, "drill_status"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    executed_by: Mapped[str] = mapped_column(String(255), nullable=False)
    environment: Mapped[str] = mapped_column(String(64), nullable=False)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class MaritimeCalendarEvent(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "maritime_calendar_events"

    slug: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)


class MaritimeCalendarEventVersion(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "maritime_calendar_event_versions"
    __table_args__ = (
        UniqueConstraint(
            "calendar_event_id",
            "version_no",
            name="uq_maritime_calendar_event_versions_event_version",
        ),
        Index("ix_maritime_calendar_versions_start", "starts_at", "ends_at"),
    )

    calendar_event_id: Mapped[UUID] = mapped_column(
        ForeignKey("maritime_calendar_events.id"), nullable=False, index=True
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[CalendarEventType] = mapped_column(
        enum_column(CalendarEventType, "calendar_event_type")
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    corridor: Mapped[Corridor] = mapped_column(enum_column(Corridor, "corridor"))
    ports_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    public_note: Mapped[str] = mapped_column(Text, nullable=False)
    source_record_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_by: Mapped[str] = mapped_column(String(255), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class AISPositionCache(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "ais_position_cache"
    __table_args__ = (Index("ix_ais_position_cache_corridor_time", "corridor", "message_at"),)

    mmsi: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    imo: Mapped[str | None] = mapped_column(String(16))
    vessel_name: Mapped[str | None] = mapped_column(String(255))
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    course: Mapped[float | None] = mapped_column(Float)
    speed: Mapped[float | None] = mapped_column(Float)
    navigation_status: Mapped[str | None] = mapped_column(String(128))
    corridor: Mapped[Corridor] = mapped_column(enum_column(Corridor, "corridor"))
    message_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)


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


class DeskUser(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "desk_users"
    __table_args__ = (
        UniqueConstraint("auth_issuer", "auth_subject", name="uq_desk_users_issuer_subject"),
        Index("ix_desk_users_active_role", "active", "role"),
    )

    auth_issuer: Mapped[str] = mapped_column(String(255), nullable=False)
    auth_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[DeskRole] = mapped_column(enum_column(DeskRole, "desk_role"), nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)


class DeskApproval(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "desk_approvals"
    __table_args__ = (
        CheckConstraint("expires_at > approved_at", name="approval_expiry_after_approval"),
        Index(
            "ix_desk_approvals_target_binding",
            "approval_type",
            "target_id",
            "binding_hash",
        ),
    )

    approval_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    binding_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    binding_payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    primary_user_id: Mapped[UUID] = mapped_column(ForeignKey("desk_users.id"), nullable=False)
    approved_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("desk_users.id"), nullable=False
    )
    auth_assurance: Mapped[AuthAssurance] = mapped_column(
        enum_column(AuthAssurance, "auth_assurance"), nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DeskApprovalRequest(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "desk_approval_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'cancelled', 'expired')",
            name="approval_request_status_valid",
        ),
        CheckConstraint(
            "expires_at > requested_at",
            name="approval_request_expiry_after_request",
        ),
        CheckConstraint(
            "(status = 'pending' AND decided_at IS NULL AND decided_by_user_id IS NULL) "
            "OR (status <> 'pending' AND decided_at IS NOT NULL)",
            name="approval_request_decision_state_consistent",
        ),
        Index(
            "ix_desk_approval_requests_queue",
            "status",
            "request_type",
            "requested_at",
        ),
        Index(
            "ix_desk_approval_requests_primary_target",
            "primary_user_id",
            "target_id",
            "requested_at",
        ),
    )

    request_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    primary_user_id: Mapped[UUID] = mapped_column(ForeignKey("desk_users.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    request_reason: Mapped[str] = mapped_column(Text, nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("desk_users.id"))
    decision_reason: Mapped[str | None] = mapped_column(Text)
    approval_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("desk_approvals.id"), unique=True
    )


class AISystemVersion(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "ai_system_versions"
    __table_args__ = (
        UniqueConstraint("fingerprint", name="uq_ai_system_versions_fingerprint"),
        Index("ix_ai_system_versions_created_at", "created_at"),
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    manifest_schema_version: Mapped[str] = mapped_column(String(16), nullable=False)
    manifest_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(ForeignKey("desk_users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AICapabilityControl(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "ai_capability_controls"
    __table_args__ = (
        UniqueConstraint("scope", "revision", name="uq_ai_capability_controls_scope_revision"),
        CheckConstraint("risk_tier BETWEEN 0 AND 4", name="risk_tier_range"),
        CheckConstraint(
            "scope = 'all_model_calls' OR mode = 'disabled' OR system_version_id IS NOT NULL",
            name="enabled_scope_has_system_version",
        ),
        Index("ix_ai_capability_controls_scope_time", "scope", "changed_at"),
    )

    scope: Mapped[str] = mapped_column(String(128), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    mode: Mapped[AICapabilityMode] = mapped_column(
        enum_column(AICapabilityMode, "ai_capability_mode"), nullable=False
    )
    risk_tier: Mapped[int] = mapped_column(Integer, nullable=False)
    system_version_id: Mapped[UUID | None] = mapped_column(ForeignKey("ai_system_versions.id"))
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    approval_refs_json: Mapped[dict[str, str]] = mapped_column(JSON, default=dict, nullable=False)
    changed_by_user_id: Mapped[UUID] = mapped_column(ForeignKey("desk_users.id"), nullable=False)
    auth_assurance: Mapped[AuthAssurance] = mapped_column(
        enum_column(AuthAssurance, "auth_assurance"), nullable=False
    )
    previous_control_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ai_capability_controls.id")
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AIIncident(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "ai_incidents"
    __table_args__ = (
        Index("ix_ai_incidents_scope_detected", "capability_scope", "detected_at"),
        Index("ix_ai_incidents_created_at", "created_at"),
    )

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    capability_scope: Mapped[str] = mapped_column(String(128), nullable=False)
    system_version_id: Mapped[UUID | None] = mapped_column(ForeignKey("ai_system_versions.id"))
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reported_by_user_id: Mapped[UUID] = mapped_column(ForeignKey("desk_users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AIIncidentEvent(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "ai_incident_events"
    __table_args__ = (
        UniqueConstraint("incident_id", "sequence", name="uq_ai_incident_events_sequence"),
        CheckConstraint(
            "status IN ('open', 'contained', 'investigating', 'resolved')",
            name="ai_incident_event_status_valid",
        ),
        CheckConstraint(
            "severity IN ('low', 'medium', 'high', 'critical')",
            name="ai_incident_event_severity_valid",
        ),
        Index("ix_ai_incident_events_incident_time", "incident_id", "at"),
    )

    incident_id: Mapped[UUID] = mapped_column(ForeignKey("ai_incidents.id"), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    containment_action: Mapped[str | None] = mapped_column(Text)
    evidence_refs_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    changed_by_user_id: Mapped[UUID] = mapped_column(ForeignKey("desk_users.id"), nullable=False)
    auth_assurance: Mapped[AuthAssurance] = mapped_column(
        enum_column(AuthAssurance, "auth_assurance"), nullable=False
    )
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# --- Private fleet reconciliation context (blueprint work order 2) ----------------
# A separate recon_* namespace for the autonomous analytical path (blueprint 29
# phase 1). Every private parent carries UNIQUE(account_id, id); children reference
# both columns together so a foreign key proves same-tenant membership, not mere
# existence (blueprint 16, 17). account_id is never null.


class ReconVesselMembership(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "recon_vessel_memberships"
    __table_args__ = (
        UniqueConstraint("account_id", "id", name="uq_recon_vessel_memberships_account_id_id"),
        UniqueConstraint(
            "account_id",
            "vessel_ref",
            "valid_from",
            name="uq_recon_vessel_memberships_vessel_from",
        ),
        CheckConstraint("valid_to > valid_from", name="interval_order"),
        Index("ix_recon_vessel_memberships_account_vessel", "account_id", "vessel_ref"),
    )

    account_id: Mapped[UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    vessel_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    authority_role: Mapped[AuthorityRole] = mapped_column(
        enum_column(AuthorityRole, "recon_authority_role"), nullable=False
    )
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_tz: Mapped[str | None] = mapped_column(String(64))


class ReconVoyage(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "recon_voyages"
    __table_args__ = (
        UniqueConstraint("account_id", "id", name="uq_recon_voyages_account_id_id"),
        UniqueConstraint(
            "account_id",
            "source_system",
            "voyage_ref",
            name="uq_recon_voyages_account_source_ref",
        ),
    )

    account_id: Mapped[UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    voyage_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    vessel_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    source_system: Mapped[str] = mapped_column(String(128), nullable=False)


class ReconVoyageVersion(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "recon_voyage_versions"
    __table_args__ = (
        UniqueConstraint("account_id", "id", name="uq_recon_voyage_versions_account_id_id"),
        UniqueConstraint(
            "account_id", "voyage_id", "plan_revision", name="uq_recon_voyage_versions_revision"
        ),
        ForeignKeyConstraint(
            ["account_id", "voyage_id"],
            ["recon_voyages.account_id", "recon_voyages.id"],
            name="fk_recon_voyage_versions_voyage",
        ),
        CheckConstraint("effective_to > effective_from", name="interval_order"),
    )

    account_id: Mapped[UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    voyage_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    plan_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    source_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_to: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    plan_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ReconExpectedJob(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "recon_expected_jobs"
    __table_args__ = (
        UniqueConstraint("account_id", "id", name="uq_recon_expected_jobs_account_id_id"),
        UniqueConstraint(
            "account_id",
            "vessel_ref",
            "required_record_kind",
            "period_start",
            "period_end",
            name="uq_recon_expected_jobs_natural_key",
        ),
        CheckConstraint("period_end > period_start", name="period_order"),
        Index("ix_recon_expected_jobs_account_due", "account_id", "due_at"),
    )

    account_id: Mapped[UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    vessel_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    voyage_ref: Mapped[str | None] = mapped_column(String(128))
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    contract_version: Mapped[str] = mapped_column(String(64), nullable=False)
    result_contract_version: Mapped[str] = mapped_column(String(64), nullable=False)
    required_record_kind: Mapped[RecordKind] = mapped_column(
        enum_column(RecordKind, "recon_record_kind"), nullable=False
    )


class ReconSourceCapture(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "recon_source_captures"
    __table_args__ = (
        UniqueConstraint("account_id", "id", name="uq_recon_source_captures_account_id_id"),
        UniqueConstraint(
            "account_id",
            "source_system",
            "external_ref",
            "source_revision",
            name="uq_recon_source_captures_revision",
        ),
        Index("ix_recon_source_captures_account_captured", "account_id", "captured_at"),
    )

    account_id: Mapped[UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    record_kind: Mapped[RecordKind] = mapped_column(
        enum_column(RecordKind, "recon_record_kind"), nullable=False
    )
    source_system: Mapped[str] = mapped_column(String(128), nullable=False)
    external_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    source_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    raw_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ReconObservation(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "recon_observations"
    __table_args__ = (
        UniqueConstraint("account_id", "id", name="uq_recon_observations_account_id_id"),
        ForeignKeyConstraint(
            ["account_id", "capture_id"],
            ["recon_source_captures.account_id", "recon_source_captures.id"],
            name="fk_recon_observations_capture",
        ),
        ForeignKeyConstraint(
            ["account_id", "expected_job_id"],
            ["recon_expected_jobs.account_id", "recon_expected_jobs.id"],
            name="fk_recon_observations_expected_job",
        ),
        CheckConstraint("period_end > period_start", name="period_order"),
        Index("ix_recon_observations_account_job", "account_id", "expected_job_id"),
    )

    account_id: Mapped[UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    capture_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    expected_job_id: Mapped[UUID | None] = mapped_column(Uuid)
    field: Mapped[str] = mapped_column(String(64), nullable=False)
    item_key: Mapped[str] = mapped_column(String(128), nullable=False)
    fuel_grade: Mapped[FuelGrade] = mapped_column(
        enum_column(FuelGrade, "recon_fuel_grade"), nullable=False
    )
    value: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    unit: Mapped[QuantityUnit] = mapped_column(
        enum_column(QuantityUnit, "recon_quantity_unit"), nullable=False
    )
    origin: Mapped[ValueOrigin] = mapped_column(
        enum_column(ValueOrigin, "recon_value_origin"), nullable=False
    )
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_pointer: Mapped[str] = mapped_column(String(255), nullable=False)
    normalization_version: Mapped[str] = mapped_column(String(64), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ReconOperationalCase(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "recon_operational_cases"
    __table_args__ = (
        UniqueConstraint("account_id", "id", name="uq_recon_operational_cases_account_id_id"),
        UniqueConstraint(
            "account_id", "expected_job_id", name="uq_recon_operational_cases_expected_job"
        ),
        ForeignKeyConstraint(
            ["account_id", "expected_job_id"],
            ["recon_expected_jobs.account_id", "recon_expected_jobs.id"],
            name="fk_recon_operational_cases_expected_job",
        ),
    )

    account_id: Mapped[UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    expected_job_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    automation_status: Mapped[AutomationStatus] = mapped_column(
        enum_column(AutomationStatus, "recon_automation_status"), nullable=False
    )
    business_status: Mapped[BusinessStatus] = mapped_column(
        enum_column(BusinessStatus, "recon_business_status"), nullable=False
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReconCaseVersion(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "recon_case_versions"
    __table_args__ = (
        UniqueConstraint("account_id", "id", name="uq_recon_case_versions_account_id_id"),
        UniqueConstraint(
            "account_id", "case_id", "revision", name="uq_recon_case_versions_revision"
        ),
        ForeignKeyConstraint(
            ["account_id", "case_id"],
            ["recon_operational_cases.account_id", "recon_operational_cases.id"],
            name="fk_recon_case_versions_case",
        ),
    )

    account_id: Mapped[UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    case_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    supersedes_version_id: Mapped[UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# --- Durable orchestration runtime (blueprint work order 5, section 21) -----------
# PostgreSQL holds the authoritative workflow state; Redis only carries wake-ups. The
# lease_epoch on a task is the fencing token: only the current lease holder can commit,
# and an expired lease lets a recovery worker claim a newer epoch.


class ReconWorkflowRun(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "recon_workflow_runs"
    __table_args__ = (
        UniqueConstraint("account_id", "id", name="uq_recon_workflow_runs_account_id_id"),
        CheckConstraint("budget_units >= 0", name="budget_non_negative"),
        ForeignKeyConstraint(
            ["account_id", "case_id"],
            ["recon_operational_cases.account_id", "recon_operational_cases.id"],
            name="fk_recon_workflow_runs_case",
        ),
    )

    account_id: Mapped[UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    case_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    workflow_version: Mapped[str] = mapped_column(String(64), nullable=False)
    system_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    budget_units: Mapped[int] = mapped_column(Integer, nullable=False)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReconWorkflowTask(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "recon_workflow_tasks"
    __table_args__ = (
        UniqueConstraint("account_id", "id", name="uq_recon_workflow_tasks_account_id_id"),
        CheckConstraint("attempts >= 0 AND max_attempts >= 1", name="attempt_counts_valid"),
        ForeignKeyConstraint(
            ["account_id", "run_id"],
            ["recon_workflow_runs.account_id", "recon_workflow_runs.id"],
            name="fk_recon_workflow_tasks_run",
        ),
        Index(
            "ix_recon_workflow_tasks_due",
            "account_id",
            "status",
            "next_attempt_at",
        ),
    )

    account_id: Mapped[UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    run_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    stage: Mapped[WorkflowStage] = mapped_column(
        enum_column(WorkflowStage, "recon_workflow_stage"), nullable=False
    )
    status: Mapped[TaskStatus] = mapped_column(
        enum_column(TaskStatus, "recon_task_status"), nullable=False
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    terminal_reason: Mapped[str | None] = mapped_column(String(64))


class ReconTaskAttempt(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "recon_task_attempts"
    __table_args__ = (
        UniqueConstraint("account_id", "id", name="uq_recon_task_attempts_account_id_id"),
        UniqueConstraint(
            "account_id", "task_id", "attempt_no", name="uq_recon_task_attempts_attempt_no"
        ),
        ForeignKeyConstraint(
            ["account_id", "task_id"],
            ["recon_workflow_tasks.account_id", "recon_workflow_tasks.id"],
            name="fk_recon_task_attempts_task",
        ),
    )

    account_id: Mapped[UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    task_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    lease_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    lease_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[AttemptStatus] = mapped_column(
        enum_column(AttemptStatus, "recon_attempt_status"), nullable=False
    )
    request_id: Mapped[str | None] = mapped_column(String(128))
    artifact_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ReconBudgetLedger(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "recon_budget_ledgers"
    __table_args__ = (
        UniqueConstraint("account_id", "id", name="uq_recon_budget_ledgers_account_id_id"),
        UniqueConstraint("account_id", "period_key", name="uq_recon_budget_ledgers_period"),
        CheckConstraint(
            "reserved_units >= 0 AND spent_units >= 0 AND uncertain_units >= 0",
            name="ledger_non_negative",
        ),
    )

    account_id: Mapped[UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    period_key: Mapped[str] = mapped_column(String(16), nullable=False)
    ceiling_units: Mapped[int] = mapped_column(Integer, nullable=False)
    reserved_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    spent_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    uncertain_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ReconBudgetReservation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "recon_budget_reservations"
    __table_args__ = (
        UniqueConstraint("account_id", "id", name="uq_recon_budget_reservations_account_id_id"),
        CheckConstraint(
            "reserved_units >= 0 AND spent_units >= 0 AND uncertain_units >= 0",
            name="reservation_non_negative",
        ),
        ForeignKeyConstraint(
            ["account_id", "run_id"],
            ["recon_workflow_runs.account_id", "recon_workflow_runs.id"],
            name="fk_recon_budget_reservations_run",
        ),
    )

    account_id: Mapped[UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    run_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    task_id: Mapped[UUID | None] = mapped_column(Uuid)
    period_key: Mapped[str] = mapped_column(String(16), nullable=False)
    reserved_units: Mapped[int] = mapped_column(Integer, nullable=False)
    spent_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    uncertain_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[ReservationStatus] = mapped_column(
        enum_column(ReservationStatus, "recon_reservation_status"), nullable=False
    )


# --- Machine authority (blueprint work order 6, sections 12, 17, 22) --------------
# Standing grants come only from controlled provisioning; no runtime agent, prompt or
# source document may create one. A deterministic policy service attests an exact
# case-revision/payload hash under a current grant, and the executor rechecks before
# dispatch. Both allows and denials are recorded as machine-readable receipts.


class ReconServicePrincipal(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "recon_service_principals"
    __table_args__ = (
        UniqueConstraint("account_id", "id", name="uq_recon_service_principals_account_id_id"),
        UniqueConstraint("account_id", "subject", name="uq_recon_service_principals_subject"),
    )

    account_id: Mapped[UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    subject: Mapped[str] = mapped_column(String(128), nullable=False)
    issuer: Mapped[str] = mapped_column(String(128), nullable=False)
    audience: Mapped[str] = mapped_column(String(128), nullable=False)
    allowed_scopes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    deployment_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )


class ReconStandingGrant(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "recon_standing_grants"
    __table_args__ = (
        UniqueConstraint("account_id", "id", name="uq_recon_standing_grants_account_id_id"),
        UniqueConstraint(
            "account_id", "policy_id", "policy_revision", name="uq_recon_standing_grants_revision"
        ),
        CheckConstraint("valid_to > valid_from", name="grant_interval_order"),
        CheckConstraint("attestation_ttl_seconds > 0", name="grant_ttl_positive"),
    )

    account_id: Mapped[UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    policy_id: Mapped[str] = mapped_column(String(128), nullable=False)
    policy_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    capability: Mapped[str] = mapped_column(String(128), nullable=False)
    allowed_case_types: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    allowed_destinations: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    external_messages: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    financial_commitments: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    required_evidence: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    required_artifact: Mapped[str] = mapped_column(String(128), nullable=False)
    qualified_fingerprints: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    attestation_ttl_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    signing_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )


class ReconActionAttestation(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "recon_action_attestations"
    __table_args__ = (
        UniqueConstraint("account_id", "id", name="uq_recon_action_attestations_account_id_id"),
        ForeignKeyConstraint(
            ["account_id", "grant_id"],
            ["recon_standing_grants.account_id", "recon_standing_grants.id"],
            name="fk_recon_action_attestations_grant",
        ),
        Index(
            "ix_recon_action_attestations_action_key",
            "account_id",
            "action_key",
        ),
    )

    account_id: Mapped[UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    grant_id: Mapped[UUID | None] = mapped_column(Uuid)
    principal_id: Mapped[UUID | None] = mapped_column(Uuid)
    action_key: Mapped[str] = mapped_column(String(128), nullable=False)
    capability: Mapped[str] = mapped_column(String(128), nullable=False)
    case_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    case_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    destination: Mapped[str] = mapped_column(String(128), nullable=False)
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_id: Mapped[str] = mapped_column(String(128), nullable=False)
    policy_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    system_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    signing_key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    decision: Mapped[AttestationDecision] = mapped_column(
        enum_column(AttestationDecision, "recon_attestation_decision"), nullable=False
    )
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# --- Private publication (blueprint work order 7, section 23) ---------------------
# A validated action_intent and its outbox row are committed together with the case
# transition. The intent (proposal) and the case_publication (accepted private effect)
# are immutable snapshots; the outbox carries the mutable dispatch state machine and a
# lease_epoch fencing token. The first release publishes only to the tenant's private
# portal by atomic commit plus an independent content-hash read-back.


class ReconActionIntent(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "recon_action_intents"
    __table_args__ = (
        UniqueConstraint("account_id", "id", name="uq_recon_action_intents_account_id_id"),
        UniqueConstraint("account_id", "action_key", name="uq_recon_action_intents_action_key"),
        ForeignKeyConstraint(
            ["account_id", "case_id"],
            ["recon_operational_cases.account_id", "recon_operational_cases.id"],
            name="fk_recon_action_intents_case",
        ),
        ForeignKeyConstraint(
            ["account_id", "case_version_id"],
            ["recon_case_versions.account_id", "recon_case_versions.id"],
            name="fk_recon_action_intents_case_version",
        ),
        ForeignKeyConstraint(
            ["account_id", "attestation_id"],
            ["recon_action_attestations.account_id", "recon_action_attestations.id"],
            name="fk_recon_action_intents_attestation",
        ),
    )

    account_id: Mapped[UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    case_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    case_version_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    attestation_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    action_key: Mapped[str] = mapped_column(String(128), nullable=False)
    capability: Mapped[str] = mapped_column(String(128), nullable=False)
    destination: Mapped[str] = mapped_column(String(128), nullable=False)
    case_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ReconOutbox(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "recon_outbox"
    __table_args__ = (
        UniqueConstraint("account_id", "id", name="uq_recon_outbox_account_id_id"),
        UniqueConstraint("account_id", "intent_id", name="uq_recon_outbox_intent"),
        CheckConstraint("dispatch_attempts >= 0", name="dispatch_attempts_non_negative"),
        ForeignKeyConstraint(
            ["account_id", "intent_id"],
            ["recon_action_intents.account_id", "recon_action_intents.id"],
            name="fk_recon_outbox_intent",
        ),
        Index("ix_recon_outbox_due", "account_id", "status", "next_attempt_at"),
    )

    account_id: Mapped[UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    intent_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    action_key: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[OutboxStatus] = mapped_column(
        enum_column(OutboxStatus, "recon_outbox_status"), nullable=False
    )
    response_class: Mapped[OutboxResponseClass | None] = mapped_column(
        enum_column(OutboxResponseClass, "recon_outbox_response_class")
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    lease_epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    dispatch_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    provider_reference: Mapped[str | None] = mapped_column(String(128))
    terminal_reason: Mapped[str | None] = mapped_column(String(64))


class ReconCasePublication(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "recon_case_publications"
    __table_args__ = (
        UniqueConstraint(
            "account_id", "id", name="uq_recon_case_publications_account_id_id"
        ),
        UniqueConstraint(
            "account_id", "case_id", "case_revision",
            name="uq_recon_case_publications_revision",
        ),
        ForeignKeyConstraint(
            ["account_id", "intent_id"],
            ["recon_action_intents.account_id", "recon_action_intents.id"],
            name="fk_recon_case_publications_intent",
        ),
        ForeignKeyConstraint(
            ["account_id", "case_id"],
            ["recon_operational_cases.account_id", "recon_operational_cases.id"],
            name="fk_recon_case_publications_case",
        ),
    )

    account_id: Mapped[UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    intent_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    case_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    case_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    destination: Mapped[str] = mapped_column(String(128), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# --- Persisted independent verification verdict (blueprint work order 4, section 19) --
# The work-order-4 verifier recomputes the required facts and arithmetic independently of
# the calculator. Its verdict is persisted here, bound to the exact source (evidence hash),
# expected job, result (content hash) and formula (calculator version) it judged, so the
# publication gate can require a real, matching completion verdict instead of trusting a
# caller-supplied evidence label. The row is immutable: one verdict per case version.


class ReconVerificationVerdict(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "recon_verification_verdicts"
    __table_args__ = (
        UniqueConstraint(
            "account_id", "id", name="uq_recon_verification_verdicts_account_id_id"
        ),
        UniqueConstraint(
            "account_id", "case_version_id",
            name="uq_recon_verification_verdicts_case_version",
        ),
        CheckConstraint("checked >= 0", name="verdict_checked_non_negative"),
        ForeignKeyConstraint(
            ["account_id", "case_id"],
            ["recon_operational_cases.account_id", "recon_operational_cases.id"],
            name="fk_recon_verification_verdicts_case",
        ),
        ForeignKeyConstraint(
            ["account_id", "case_version_id"],
            ["recon_case_versions.account_id", "recon_case_versions.id"],
            name="fk_recon_verification_verdicts_case_version",
        ),
        ForeignKeyConstraint(
            ["account_id", "expected_job_id"],
            ["recon_expected_jobs.account_id", "recon_expected_jobs.id"],
            name="fk_recon_verification_verdicts_expected_job",
        ),
    )

    account_id: Mapped[UUID] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    case_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    case_version_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    expected_job_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    case_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    # The four bindings the publication gate re-checks against the case version.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    calculator_version: Mapped[str] = mapped_column(String(64), nullable=False)
    verifier_version: Mapped[str] = mapped_column(String(64), nullable=False)
    # VerificationStatus / CaseCompletionStatus values (kept as strings, not a DB enum,
    # to avoid coupling the schema package to the shared verifier's enums).
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    completion_status: Mapped[str] = mapped_column(String(32), nullable=False)
    checked: Mapped[int] = mapped_column(Integer, nullable=False)
    has_unresolved: Mapped[bool] = mapped_column(Boolean, nullable=False)
    completes: Mapped[bool] = mapped_column(Boolean, nullable=False)
    findings_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# --- System-wide monthly budget (blueprint section 31) ----------------------------
# A global ceiling across all tenants for one period, above the per-run and per-account
# monthly caps, so concurrent agents in different tenants cannot together exceed the
# system's total spend. Not tenant-scoped: keyed by period alone.


class ReconGlobalBudgetLedger(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "recon_global_budget_ledgers"
    __table_args__ = (
        UniqueConstraint("period_key", name="uq_recon_global_budget_ledgers_period_key"),
        CheckConstraint(
            "reserved_units >= 0 AND spent_units >= 0 AND uncertain_units >= 0",
            name="global_ledger_non_negative",
        ),
    )

    period_key: Mapped[str] = mapped_column(String(16), nullable=False)
    ceiling_units: Mapped[int] = mapped_column(Integer, nullable=False)
    reserved_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    spent_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    uncertain_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
