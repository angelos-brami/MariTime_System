from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from eastmed_api.poller_health import poller_health
from eastmed_schema.enums import AccessMethod, RightsBasis, SourceTier, SourceType
from eastmed_schema.models import Source

NOW = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)


def make_source(**overrides: object) -> Source:
    values: dict[str, object] = {
        "id": uuid4(),
        "name": "Official feed",
        "source_type": SourceType.OFFICIAL,
        "tier": SourceTier.A,
        "language": "en",
        "access_method": AccessMethod.RSS,
        "feed_url": "https://example.test/feed.xml",
        "rights_basis": RightsBasis.PUBLIC_ADVISORY,
        "rights_reviewed_by": "counsel:one",
        "automation_approved_at": NOW - timedelta(hours=1),
        "active": True,
        "poll_interval_seconds": 600,
        "consecutive_failures": 0,
    }
    values.update(overrides)
    return Source(**values)


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"active": False}, "disabled"),
        ({"automation_approved_at": None}, "blocked_rights"),
        ({"automation_approved_at": NOW - timedelta(minutes=5)}, "pending_first_poll"),
        (
            {
                "automation_approved_at": NOW - timedelta(hours=1),
                "last_successful_poll_at": NOW - timedelta(minutes=21),
            },
            "overdue",
        ),
        (
            {
                "last_successful_poll_at": NOW - timedelta(minutes=5),
                "consecutive_failures": 2,
            },
            "degraded",
        ),
        ({"last_successful_poll_at": NOW - timedelta(minutes=5)}, "healthy"),
    ],
)
def test_poller_health_states(overrides: dict[str, object], expected: str) -> None:
    health = poller_health(make_source(**overrides), now=NOW)
    assert health.state == expected
