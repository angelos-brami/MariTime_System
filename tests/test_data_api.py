from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from eastmed_api.data_api import (
    _event_read,
    get_data_claim,
    get_data_event,
    list_data_claims,
    list_data_events,
    list_data_versions,
)
from eastmed_schema.enums import Corridor, EventType
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import ClauseElement

NOW = datetime(2026, 7, 20, 10, 30, tzinfo=UTC)


def published_version(*, claim_id: UUID | None = None) -> SimpleNamespace:
    claim_id = claim_id or uuid4()
    return SimpleNamespace(
        id=uuid4(),
        event_id=uuid4(),
        version_no=2,
        title="Published snapshot title",
        summary_confirmed="Confirmed.",
        summary_reported="Reported.",
        summary_unknown="Unknown.",
        whats_changed="Changed.",
        sentence_claim_map={},
        event_snapshot_json={
            "slug": "published-snapshot",
            "event_type": "navigation_warning",
            "corridor": "hormuz_gulf",
            "status": "developing",
            "severity": 3,
            "occurred_start": "2026-07-20T08:00:00+00:00",
            "occurred_end": None,
        },
        claim_snapshot_json=[
            {
                "id": str(claim_id),
                "text": "Immutable published claim",
                "claimant": "Authority",
                "state": "confirmed",
                "occurred_at": "2026-07-20T08:15:00+00:00",
                "quantity": [{"value": 2}],
                "reviewed_at": "2026-07-20T09:00:00+00:00",
                "first_seen_at": "2026-07-20T08:20:00+00:00",
            }
        ],
        published_at=NOW,
        policy_version="publication-policy-v1",
        content_hash="a" * 64,
    )


class ScalarRows:
    def __init__(self, rows: list[object]) -> None:
        self.rows = rows

    def all(self) -> list[object]:
        return self.rows


class FakeSession:
    def __init__(
        self,
        *,
        scalar_values: list[object | None] | None = None,
        rows: list[object] | None = None,
    ) -> None:
        self.scalar_values = list(scalar_values or [])
        self.rows = list(rows or [])
        self.statements: list[ClauseElement] = []

    def scalar(self, statement: ClauseElement) -> object | None:
        self.statements.append(statement)
        return self.scalar_values.pop(0)

    def scalars(self, statement: ClauseElement) -> ScalarRows:
        self.statements.append(statement)
        return ScalarRows(self.rows)


def test_data_event_is_built_from_immutable_publication_snapshot() -> None:
    version = published_version()
    result = _event_read(version)  # type: ignore[arg-type]

    assert result.id == version.event_id
    assert result.slug == "published-snapshot"
    assert result.event_type is EventType.NAVIGATION_WARNING
    assert result.corridor is Corridor.HORMUZ_GULF
    assert result.severity == 3
    assert result.latest_version_id == version.id


def test_unpublished_event_is_not_returned() -> None:
    session = FakeSession(scalar_values=[None])

    with pytest.raises(LookupError, match="Published event not found"):
        get_data_event(session, event_id=uuid4())  # type: ignore[arg-type]


def test_event_list_uses_an_inner_join_to_latest_published_versions() -> None:
    version = published_version()
    session = FakeSession(scalar_values=[1], rows=[version])

    result = list_data_events(session, corridor=Corridor.HORMUZ_GULF)  # type: ignore[arg-type]

    dialect = postgresql.dialect()  # type: ignore[no-untyped-call]
    sql = str(
        session.statements[1].compile(
            dialect=dialect, compile_kwargs={"literal_binds": True}
        )
    )
    assert "LEFT OUTER JOIN" not in sql
    assert "event_versions.event_snapshot_json" in sql
    assert result.total == 1
    assert [item.id for item in result.results] == [version.event_id]


def test_claim_reads_use_published_snapshot_not_live_claim_row() -> None:
    claim_id = uuid4()
    version = published_version(claim_id=claim_id)
    session = FakeSession(scalar_values=[version, version])

    listed = list_data_claims(session, event_id=version.event_id)  # type: ignore[arg-type]
    fetched = get_data_claim(session, claim_id=claim_id)  # type: ignore[arg-type]

    assert listed[0].text == "Immutable published claim"
    assert listed[0].first_seen_at == datetime(2026, 7, 20, 8, 20, tzinfo=UTC)
    assert fetched == listed[0]


def test_versions_require_at_least_one_publication() -> None:
    session = FakeSession(rows=[])

    with pytest.raises(LookupError, match="Published event not found"):
        list_data_versions(session, event_id=uuid4())  # type: ignore[arg-type]
