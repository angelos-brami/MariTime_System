from datetime import UTC, datetime, timedelta

from eastmed_schema.models import Source

from eastmed_api.contracts import PollerHealthRead


def poller_health(source: Source, *, now: datetime | None = None) -> PollerHealthRead:
    current = now or datetime.now(UTC)
    if not source.active:
        state = "disabled"
    elif source.automation_approved_at is None:
        state = "blocked_rights"
    else:
        reference = source.last_successful_poll_at or source.automation_approved_at
        overdue_at = reference + timedelta(seconds=source.poll_interval_seconds * 2)
        if current > overdue_at:
            state = "overdue"
        elif source.last_successful_poll_at is None:
            state = "pending_first_poll"
        elif source.consecutive_failures:
            state = "degraded"
        else:
            state = "healthy"
    return PollerHealthRead(
        source_id=source.id,
        source_name=source.name,
        state=state,
        active=source.active,
        rights_approved=source.automation_approved_at is not None,
        poll_interval_seconds=source.poll_interval_seconds,
        last_poll_started_at=source.last_poll_started_at,
        last_successful_poll_at=source.last_successful_poll_at,
        next_poll_at=source.next_poll_at,
        consecutive_failures=source.consecutive_failures,
        last_poll_error=source.last_poll_error,
    )
