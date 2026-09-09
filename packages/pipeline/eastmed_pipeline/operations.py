from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

from eastmed_schema.enums import (
    AccessMethod,
    DeskAlertKind,
    DeskAlertStatus,
    PollerRunStatus,
)
from eastmed_schema.models import AuditLog, DeskAlert, PollerRun, Source
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from eastmed_pipeline.ingest import ingest_rss_source
from eastmed_pipeline.notifications import DeskNotifier
from eastmed_pipeline.watcher import ingest_watch_source


class JobQueue(Protocol):
    def enqueue(self, function: Any, *args: Any, **kwargs: Any) -> Any: ...


def _now(value: datetime | None = None) -> datetime:
    return value or datetime.now(UTC)


def _notify_dead_poller(
    *,
    alert: DeskAlert,
    source: Source,
    reference: datetime,
    notifier: DeskNotifier,
    now: datetime,
) -> None:
    message = (
        f"DEAD POLLER — {source.name}\n"
        f"No successful poll since {reference.isoformat()}; "
        f"expected every {source.poll_interval_seconds}s."
    )
    try:
        provider_ref = notifier.notify(message)
        if provider_ref is not None:
            alert.detail = {**alert.detail, "provider_ref": provider_ref}
            alert.status = DeskAlertStatus.NOTIFIED
            alert.notified_at = now
            alert.notification_error = None
    except Exception as exc:
        alert.notification_error = f"{type(exc).__name__}: {str(exc)[:1000]}"


def enqueue_due_sources(
    session: Session,
    *,
    queue: JobQueue,
    now: datetime | None = None,
    limit: int = 100,
) -> list[UUID]:
    current = _now(now)
    sources = session.scalars(
        select(Source)
        .where(
            Source.active.is_(True),
            Source.automation_approved_at.is_not(None),
            Source.access_method.in_([AccessMethod.RSS, AccessMethod.WATCH]),
            Source.feed_url.is_not(None),
            or_(Source.next_poll_at.is_(None), Source.next_poll_at <= current),
        )
        .order_by(Source.next_poll_at.asc().nullsfirst(), Source.tier, Source.name)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    claimed: list[tuple[UUID, UUID, str]] = []
    for source in sources:
        run = PollerRun(source_id=source.id, status=PollerRunStatus.QUEUED)
        session.add(run)
        session.flush()
        job_id = f"poll-{run.id}"
        source.next_poll_at = current + timedelta(seconds=source.poll_interval_seconds)
        claimed.append((source.id, run.id, job_id))
    session.commit()

    enqueued: list[UUID] = []
    for source_id, run_id, job_id in claimed:
        try:
            queue.enqueue(
                "eastmed_pipeline.jobs.run_source_poll_job",
                str(run_id),
                job_id=job_id,
                job_timeout=120,
                result_ttl=86400,
            )
        except Exception as exc:
            failed_run = session.get(PollerRun, run_id)
            failed_source = session.get(Source, source_id)
            if failed_run is None or failed_source is None:
                raise RuntimeError("Claimed poller state disappeared") from exc
            failed_run.status = PollerRunStatus.FAILED
            failed_run.finished_at = current
            failed_run.error_type = type(exc).__name__
            failed_run.error_message = str(exc)[:2000]
            failed_source.next_poll_at = current
            session.add(
                AuditLog(
                    actor="scheduler",
                    action="poller.enqueue_failed",
                    entity="source",
                    entity_id=source_id,
                    payload_json={"error_type": type(exc).__name__},
                )
            )
            session.commit()
            continue
        enqueued_run = session.get(PollerRun, run_id)
        if enqueued_run is None:
            raise RuntimeError("Enqueued poller state disappeared")
        enqueued_run.worker_job_id = job_id
        session.add(
            AuditLog(
                actor="scheduler",
                action="poller.enqueued",
                entity="poller_run",
                entity_id=run_id,
                payload_json={"source_id": str(source_id), "job_id": job_id},
            )
        )
        session.commit()
        enqueued.append(run_id)
    return enqueued


def execute_poller_run(session: Session, *, run_id: UUID) -> int:
    run = session.get(PollerRun, run_id)
    if run is None:
        raise LookupError("Poller run not found")
    if run.status != PollerRunStatus.QUEUED:
        raise ValueError(f"Poller run is already {run.status.value}")
    source = session.get(Source, run.source_id)
    if source is None:
        raise LookupError("Source not found")

    source_id = source.id
    started = datetime.now(UTC)
    run.status = PollerRunStatus.RUNNING
    run.started_at = started
    source.last_poll_started_at = started
    session.commit()
    try:
        if source.access_method == AccessMethod.RSS:
            created = ingest_rss_source(session, source_id=source_id)
        elif source.access_method == AccessMethod.WATCH:
            created = ingest_watch_source(session, source_id=source_id)
        else:
            raise ValueError(f"No poller implemented for {source.access_method.value}")
    except Exception as exc:
        session.rollback()
        failed_run = session.get(PollerRun, run_id)
        failed_source = session.get(Source, source_id)
        if failed_run is None or failed_source is None:
            raise
        failed_run.status = PollerRunStatus.FAILED
        failed_run.finished_at = datetime.now(UTC)
        failed_run.error_type = type(exc).__name__
        failed_run.error_message = str(exc)[:2000]
        failed_source.consecutive_failures += 1
        failed_source.last_poll_error = str(exc)[:2000]
        session.add(
            AuditLog(
                actor="ingestion-worker",
                action="poller.failed",
                entity="poller_run",
                entity_id=run_id,
                payload_json={
                    "source_id": str(failed_source.id),
                    "error_type": type(exc).__name__,
                },
            )
        )
        session.commit()
        raise

    completed_run = session.get(PollerRun, run_id)
    completed_source = session.get(Source, source_id)
    if completed_run is None or completed_source is None:
        raise RuntimeError("Poller state disappeared after ingestion")
    completed_run.status = (
        PollerRunStatus.SUCCEEDED if created > 0 else PollerRunStatus.NOT_MODIFIED
    )
    completed_run.finished_at = datetime.now(UTC)
    completed_run.records_created = created
    completed_source.consecutive_failures = 0
    completed_source.last_poll_error = None
    session.add(
        AuditLog(
            actor="ingestion-worker",
            action="poller.completed",
            entity="poller_run",
            entity_id=run_id,
            payload_json={"source_id": str(completed_source.id), "records_created": created},
        )
    )
    session.commit()
    return created


def run_source_now(session: Session, *, source_id: UUID) -> tuple[UUID, int]:
    source = session.get(Source, source_id)
    if source is None:
        raise LookupError("Source not found")
    run = PollerRun(source_id=source.id, status=PollerRunStatus.QUEUED)
    session.add(run)
    session.flush()
    run_id = run.id
    session.add(
        AuditLog(
            actor="desk-api",
            action="poller.manual_run_created",
            entity="poller_run",
            entity_id=run_id,
            payload_json={"source_id": str(source.id)},
        )
    )
    session.commit()
    return run_id, execute_poller_run(session, run_id=run_id)


def detect_dead_pollers(
    session: Session,
    *,
    notifier: DeskNotifier,
    now: datetime | None = None,
) -> list[UUID]:
    current = _now(now)
    sources = session.scalars(
        select(Source).where(
            Source.active.is_(True),
            Source.automation_approved_at.is_not(None),
            Source.access_method.in_([AccessMethod.RSS, AccessMethod.WATCH]),
        )
    ).all()
    opened: list[UUID] = []
    for source in sources:
        reference = source.last_successful_poll_at or source.automation_approved_at
        if reference is None:
            continue
        overdue_at = reference + timedelta(seconds=source.poll_interval_seconds * 2)
        existing = session.scalar(
            select(DeskAlert).where(
                DeskAlert.source_id == source.id,
                DeskAlert.kind == DeskAlertKind.DEAD_POLLER,
                DeskAlert.status != DeskAlertStatus.RESOLVED,
            )
        )
        if current <= overdue_at:
            if existing and source.last_successful_poll_at:
                existing.status = DeskAlertStatus.RESOLVED
                existing.resolved_at = current
                session.commit()
            continue
        if existing:
            if existing.status == DeskAlertStatus.OPEN:
                _notify_dead_poller(
                    alert=existing,
                    source=source,
                    reference=reference,
                    notifier=notifier,
                    now=current,
                )
                session.commit()
            continue

        alert = DeskAlert(
            source_id=source.id,
            kind=DeskAlertKind.DEAD_POLLER,
            status=DeskAlertStatus.OPEN,
            detected_at=current,
            detail={
                "source": source.name,
                "last_successful_poll_at": (
                    source.last_successful_poll_at.isoformat()
                    if source.last_successful_poll_at
                    else None
                ),
                "poll_interval_seconds": source.poll_interval_seconds,
                "overdue_at": overdue_at.isoformat(),
            },
        )
        session.add(alert)
        session.flush()
        _notify_dead_poller(
            alert=alert,
            source=source,
            reference=reference,
            notifier=notifier,
            now=current,
        )
        session.add(
            AuditLog(
                actor="poller-watchdog",
                action="desk_alert.opened",
                entity="desk_alert",
                entity_id=alert.id,
                payload_json={"kind": alert.kind.value, "source_id": str(source.id)},
            )
        )
        session.commit()
        opened.append(alert.id)
    return opened
