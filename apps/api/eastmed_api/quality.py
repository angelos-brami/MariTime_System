from __future__ import annotations

from datetime import UTC, datetime, timedelta
from statistics import median
from uuid import UUID

from eastmed_schema.enums import SourceTier
from eastmed_schema.models import (
    AuditLog,
    Correction,
    Event,
    EventVersion,
    Source,
    SourceRecord,
    TTVLog,
)
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from eastmed_api.contracts import (
    PublicCorrectionRead,
    QualityScoreboardRead,
    TTVCreate,
    TTVRead,
    TTVUpdate,
)


class QualityWorkflowError(ValueError):
    pass


def _human(value: str, action: str) -> str:
    actor = value.strip()
    if not 2 <= len(actor) <= 255 or actor.casefold().startswith("model:"):
        raise QualityWorkflowError(f"a named human must {action} the TTV record")
    return actor


def _aware(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise QualityWorkflowError(f"{label} must include a timezone")
    if value > datetime.now(UTC) + timedelta(minutes=5):
        raise QualityWorkflowError(f"{label} cannot be future-dated")
    return value


def _read_ttv(log: TTVLog) -> TTVRead:
    if (
        log.signal_source_record_id is None
        or log.created_by is None
        or log.updated_by is None
        or log.created_at is None
        or log.updated_at is None
    ):
        raise QualityWorkflowError("TTV record is incomplete")
    return TTVRead.model_validate(log)


def create_ttv_log(session: Session, payload: TTVCreate) -> TTVRead:
    actor = _human(payload.recorded_by, "record")
    signal_at = _aware(payload.first_credible_signal_at, "first credible signal time")
    if session.get(Event, payload.event_id) is None:
        raise LookupError("Event not found")
    source_row = session.execute(
        select(SourceRecord, Source)
        .join(Source, Source.id == SourceRecord.source_id)
        .where(SourceRecord.id == payload.signal_source_record_id)
    ).one_or_none()
    if source_row is None:
        raise LookupError("Signal source record not found")
    _, source = source_row
    if source.tier == SourceTier.D:
        raise QualityWorkflowError("Tier D sources cannot start a first-credible-signal clock")
    if source.tier == SourceTier.E and not payload.corroborated_tier_e:
        raise QualityWorkflowError(
            "Tier E signals require explicit corroboration before starting the TTV clock"
        )
    now = datetime.now(UTC)
    log = TTVLog(
        event_id=payload.event_id,
        first_credible_signal_at=signal_at,
        signal_source_record_id=payload.signal_source_record_id,
        holding_line_at=None,
        verified_update_at=None,
        coverage_window=payload.coverage_window,
        notes=payload.notes.strip() if payload.notes else None,
        created_by=actor,
        updated_by=actor,
        created_at=now,
        updated_at=now,
    )
    session.add(log)
    session.flush()
    session.add(
        AuditLog(
            actor=actor,
            action="ttv.created",
            entity="ttv_log",
            entity_id=log.id,
            payload_json={
                "event_id": str(payload.event_id),
                "first_credible_signal_at": signal_at.isoformat(),
                "source_record_id": str(payload.signal_source_record_id),
                "source_tier": source.tier.value,
                "tier_e_corroborated": payload.corroborated_tier_e,
                "coverage_window": payload.coverage_window,
            },
        )
    )
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise QualityWorkflowError("a TTV clock already exists for this event") from exc
    session.refresh(log)
    return _read_ttv(log)


def update_ttv_log(session: Session, *, event_id: UUID, payload: TTVUpdate) -> TTVRead:
    actor = _human(payload.updated_by, "update")
    log = session.scalar(select(TTVLog).where(TTVLog.event_id == event_id).with_for_update())
    if log is None:
        raise LookupError("TTV record not found")
    holding = (
        _aware(payload.holding_line_at, "holding-line time")
        if payload.holding_line_at is not None
        else log.holding_line_at
    )
    verified = (
        _aware(payload.verified_update_at, "verified-update time")
        if payload.verified_update_at is not None
        else log.verified_update_at
    )
    if holding is not None and holding < log.first_credible_signal_at:
        raise QualityWorkflowError("holding-line time cannot precede the first credible signal")
    if verified is not None and verified < log.first_credible_signal_at:
        raise QualityWorkflowError("verified-update time cannot precede the first credible signal")
    log.holding_line_at = holding
    log.verified_update_at = verified
    if payload.coverage_window is not None:
        log.coverage_window = payload.coverage_window
    if payload.notes is not None:
        log.notes = payload.notes.strip() or None
    log.updated_by = actor
    log.updated_at = datetime.now(UTC)
    session.add(
        AuditLog(
            actor=actor,
            action="ttv.updated",
            entity="ttv_log",
            entity_id=log.id,
            payload_json={
                "event_id": str(event_id),
                "holding_line_at": holding.isoformat() if holding else None,
                "verified_update_at": verified.isoformat() if verified else None,
                "coverage_window": log.coverage_window,
            },
        )
    )
    session.commit()
    session.refresh(log)
    return _read_ttv(log)


def _minutes(start: datetime, end: datetime | None) -> float | None:
    return (end - start).total_seconds() / 60 if end is not None else None


def _percent(numerator: int, denominator: int) -> float | None:
    return round(numerator * 100 / denominator, 1) if denominator else None


def quality_scoreboard(session: Session) -> QualityScoreboardRead:
    published_count = int(session.scalar(select(func.count(EventVersion.id))) or 0)
    correction_rows = list(
        session.scalars(
            select(Correction)
            .where(Correction.issued_at.is_not(None))
            .order_by(Correction.issued_at.desc())
        ).all()
    )
    event_ids = {row.event_id for row in correction_rows}
    version_ids = {
        version_id
        for row in correction_rows
        for version_id in (row.version_from_id, row.version_to_id)
        if version_id is not None
    }
    events = {
        event.id: event
        for event in session.scalars(select(Event).where(Event.id.in_(event_ids))).all()
    }
    versions = {
        version.id: version
        for version in session.scalars(
            select(EventVersion).where(EventVersion.id.in_(version_ids))
        ).all()
    }
    public_corrections: list[PublicCorrectionRead] = []
    correction_on_time = 0
    for correction in correction_rows:
        event = events.get(correction.event_id)
        affected = (
            versions.get(correction.version_from_id)
            if correction.version_from_id is not None
            else None
        )
        corrected = (
            versions.get(correction.version_to_id) if correction.version_to_id is not None else None
        )
        if event is None or affected is None or corrected is None or correction.issued_at is None:
            continue
        issued_on_time = correction.issued_at <= correction.detected_at + timedelta(minutes=60)
        correction_on_time += int(issued_on_time)
        public_corrections.append(
            PublicCorrectionRead(
                id=correction.id,
                event_id=event.id,
                event_slug=event.slug,
                correction_type=correction.correction_type,
                note=correction.note,
                affected_version_hash=affected.content_hash,
                corrected_version_hash=corrected.content_hash,
                issued_at=correction.issued_at,
                issued_within_60_minutes=issued_on_time,
            )
        )

    ttv_logs = list(
        session.scalars(
            select(TTVLog)
            .where(TTVLog.coverage_window.is_(True))
            .order_by(TTVLog.first_credible_signal_at)
        ).all()
    )
    holding_minutes = [
        value
        for log in ttv_logs
        if (value := _minutes(log.first_credible_signal_at, log.holding_line_at)) is not None
    ]
    verified_minutes = [
        value
        for log in ttv_logs
        if (value := _minutes(log.first_credible_signal_at, log.verified_update_at)) is not None
    ]
    return QualityScoreboardRead(
        generated_at=datetime.now(UTC),
        published_version_count=published_count,
        correction_count=len(public_corrections),
        correction_rate_percent=round(len(public_corrections) * 100 / published_count, 2)
        if published_count
        else 0.0,
        corrections_within_60_minutes_percent=_percent(correction_on_time, len(public_corrections)),
        ttv_coverage_count=len(ttv_logs),
        holding_line_median_minutes=round(median(holding_minutes), 1) if holding_minutes else None,
        verified_update_median_minutes=round(median(verified_minutes), 1)
        if verified_minutes
        else None,
        holding_line_within_15_minutes_percent=_percent(
            sum(value <= 15 for value in holding_minutes), len(ttv_logs)
        ),
        verified_update_within_45_minutes_percent=_percent(
            sum(value <= 45 for value in verified_minutes), len(ttv_logs)
        ),
        corrections=public_corrections,
    )
