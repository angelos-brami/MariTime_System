from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from eastmed_schema.enums import Corridor, Directness, EventType, RightsBasis, SourceTier
from eastmed_schema.models import Correction, EventVersion, Source, SourceRecord, UsageEvent
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from eastmed_api.contracts import (
    PortalArchiveRead,
    PortalArchiveResultRead,
    PortalBoardEventRead,
    PortalBoardRead,
    PortalCorridorRead,
    PortalEventCorrectionRead,
    PortalEventRead,
    PortalEventSourceRead,
    PortalEventTimelineRead,
)
from eastmed_api.security import PortalPrincipal

CORRIDOR_LABELS = {
    Corridor.HORMUZ_GULF: "Hormuz & Gulf",
    Corridor.RED_SEA_BEM_SUEZ: "Red Sea · Bab el-Mandeb · Suez",
    Corridor.EAST_MED: "East Mediterranean",
    Corridor.PORT_SPECIFIC: "Port-specific watch",
}


def _latest_versions_query() -> Select[tuple[EventVersion]]:
    latest_numbers = (
        select(EventVersion.event_id, func.max(EventVersion.version_no).label("version_no"))
        .group_by(EventVersion.event_id)
        .subquery()
    )
    return select(EventVersion).join(
        latest_numbers,
        (latest_numbers.c.event_id == EventVersion.event_id)
        & (latest_numbers.c.version_no == EventVersion.version_no),
    )


def _enum_value(enum_type: type[Any], value: object, fallback: Any) -> Any:
    try:
        return enum_type(str(value))
    except ValueError:
        return fallback


def _snapshot_datetime(snapshot: dict[str, Any], key: str) -> datetime | None:
    value = snapshot.get(key)
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _board_event(version: EventVersion) -> PortalBoardEventRead:
    snapshot = version.event_snapshot_json or {}
    return PortalBoardEventRead(
        event_id=UUID(str(snapshot.get("id") or version.event_id)),
        slug=str(snapshot.get("slug") or version.event_id),
        title=version.title,
        event_type=_enum_value(
            EventType,
            snapshot.get("event_type"),
            EventType.NAVIGATION_WARNING,
        ),
        corridor=_enum_value(Corridor, snapshot.get("corridor"), Corridor.PORT_SPECIFIC),
        status=str(snapshot.get("status") or "monitoring"),
        severity=max(1, min(int(snapshot.get("severity") or 1), 4)),
        ports=list(snapshot.get("ports") or []),
        version_no=version.version_no,
        published_at=version.published_at,
        whats_changed=version.whats_changed,
    )


def record_portal_usage(
    session: Session,
    *,
    principal: PortalPrincipal,
    event_name: str,
    properties: dict[str, Any],
) -> None:
    session.add(
        UsageEvent(
            account_id=principal.account_id,
            user_id=principal.user_id,
            event_name=event_name,
            occurred_at=datetime.now(UTC),
            properties=properties,
        )
    )
    session.commit()


def portal_board(session: Session, *, principal: PortalPrincipal | None = None) -> PortalBoardRead:
    versions = list(
        session.scalars(_latest_versions_query().order_by(EventVersion.published_at.desc())).all()
    )
    grouped: dict[Corridor, list[PortalBoardEventRead]] = {corridor: [] for corridor in Corridor}
    for version in versions:
        item = _board_event(version)
        if item.status == "closed":
            continue
        grouped[item.corridor].append(item)

    corridors: list[PortalCorridorRead] = []
    for corridor in Corridor:
        events = grouped[corridor]
        highest = max((event.severity for event in events), default=0)
        state = (
            "critical"
            if highest >= 4
            else "disrupted"
            if highest >= 3
            else "watch"
            if highest >= 2
            else "clear"
        )
        corridors.append(
            PortalCorridorRead(
                corridor=corridor,
                label=CORRIDOR_LABELS[corridor],
                operational_state=state,
                event_count=len(events),
                updated_at=max((event.published_at for event in events), default=None),
                events=events[:20],
            )
        )
    if principal is not None:
        record_portal_usage(
            session, principal=principal, event_name="portal.board_view", properties={}
        )
    return PortalBoardRead(generated_at=datetime.now(UTC), corridors=corridors)


def portal_archive(
    session: Session,
    *,
    principal: PortalPrincipal,
    query: str | None,
    corridor: Corridor | None,
    event_type: EventType | None,
    min_severity: int | None,
    limit: int,
    offset: int,
) -> PortalArchiveRead:
    normalized_query = query.strip() if query else None
    latest = _latest_versions_query().subquery()
    # Re-select through the ORM table so the generated tsvector remains indexable.
    statement = select(EventVersion).join(latest, latest.c.id == EventVersion.id)
    filters: list[Any] = []
    rank_expression: Any = None
    if normalized_query:
        ts_query = func.websearch_to_tsquery("simple", normalized_query)
        filters.append(EventVersion.search_document.op("@@")(ts_query))
        rank_expression = func.ts_rank_cd(EventVersion.search_document, ts_query)
    if corridor is not None:
        filters.append(EventVersion.event_snapshot_json["corridor"].as_string() == corridor.value)
    if event_type is not None:
        filters.append(
            EventVersion.event_snapshot_json["event_type"].as_string() == event_type.value
        )
    if min_severity is not None:
        filters.append(EventVersion.event_snapshot_json["severity"].as_integer() >= min_severity)
    if filters:
        statement = statement.where(*filters)

    count_statement = select(func.count()).select_from(statement.order_by(None).subquery())
    total = int(session.scalar(count_statement) or 0)
    selected: list[tuple[EventVersion, float | None]]
    if rank_expression is not None:
        rows = session.execute(
            statement.add_columns(rank_expression.label("rank"))
            .order_by(rank_expression.desc(), EventVersion.published_at.desc())
            .offset(offset)
            .limit(limit)
        ).all()
        selected = [(row[0], float(row[1])) for row in rows]
    else:
        selected = [
            (item, None)
            for item in session.scalars(
                statement.order_by(EventVersion.published_at.desc()).offset(offset).limit(limit)
            ).all()
        ]
    results = [
        PortalArchiveResultRead(
            **_board_event(item).model_dump(),
            summary_confirmed=item.summary_confirmed,
            rank=rank,
        )
        for item, rank in selected
    ]
    record_portal_usage(
        session,
        principal=principal,
        event_name="portal.archive_search",
        properties={
            "query": normalized_query,
            "corridor": corridor.value if corridor else None,
            "event_type": event_type.value if event_type else None,
            "min_severity": min_severity,
            "result_count": total,
        },
    )
    return PortalArchiveRead(
        query=normalized_query,
        total=total,
        limit=limit,
        offset=offset,
        results=results,
    )


def portal_event(
    session: Session,
    *,
    principal: PortalPrincipal,
    slug: str,
) -> PortalEventRead:
    versions = list(
        session.scalars(
            select(EventVersion)
            .where(EventVersion.event_snapshot_json["slug"].as_string() == slug)
            .order_by(EventVersion.version_no.desc())
        ).all()
    )
    if not versions:
        raise LookupError("Published event not found")
    latest = versions[0]
    snapshot = latest.event_snapshot_json or {}

    evidence_snapshots = list(latest.evidence_snapshot_json or [])
    record_ids: set[UUID] = set()
    for evidence in evidence_snapshots:
        try:
            record_ids.add(UUID(str(evidence.get("source_record_id"))))
        except (TypeError, ValueError):
            continue
    records = {
        record.id: (record, source)
        for record, source in session.execute(
            select(SourceRecord, Source)
            .join(Source, Source.id == SourceRecord.source_id)
            .where(SourceRecord.id.in_(record_ids))
        ).all()
    }
    sources: list[PortalEventSourceRead] = []
    for evidence in evidence_snapshots:
        try:
            record_id = UUID(str(evidence.get("source_record_id")))
            lineage_root_id = UUID(str(evidence.get("lineage_root_id")))
            directness = Directness(str(evidence.get("directness")))
        except (TypeError, ValueError):
            continue
        record_and_source = records.get(record_id)
        if record_and_source is None:
            continue
        record, source = record_and_source
        rights = evidence.get("rights_decision") or {}
        rights_basis = _enum_value(
            RightsBasis,
            rights.get("basis"),
            RightsBasis.DISCOVERY_ONLY,
        )
        excerpt = evidence.get("excerpt") if rights.get("may_publish_excerpt") is True else None
        sources.append(
            PortalEventSourceRead(
                source_record_id=record.id,
                source_name=source.name,
                source_tier=_enum_value(SourceTier, source.tier, SourceTier.E),
                url=record.canonical_url,
                directness=directness,
                lineage_root_id=lineage_root_id,
                excerpt=str(excerpt) if excerpt else None,
                rights_basis=rights_basis,
            )
        )

    event_id = UUID(str(snapshot.get("id") or latest.event_id))
    version_hashes = {version.id: version.content_hash for version in versions}
    corrections = [
        PortalEventCorrectionRead(
            id=correction.id,
            correction_type=correction.correction_type,
            note=correction.note,
            version_from=correction.version_from,
            version_to=correction.version_to,
            affected_version_hash=(
                version_hashes.get(correction.version_from_id, "")
                if correction.version_from_id is not None
                else ""
            ),
            corrected_version_hash=(
                version_hashes.get(correction.version_to_id, "")
                if correction.version_to_id is not None
                else ""
            ),
            issued_at=correction.issued_at,
        )
        for correction in session.scalars(
            select(Correction)
            .where(
                Correction.event_id == event_id,
                Correction.issued_at.is_not(None),
            )
            .order_by(Correction.issued_at.desc())
        )
        if correction.issued_at is not None
    ]
    timeline = [
        PortalEventTimelineRead(
            version_no=version.version_no,
            published_at=version.published_at,
            whats_changed=version.whats_changed,
            content_hash=version.content_hash,
        )
        for version in versions
    ]
    result = PortalEventRead(
        event_id=event_id,
        slug=str(snapshot.get("slug") or slug),
        title=latest.title,
        event_type=_enum_value(
            EventType,
            snapshot.get("event_type"),
            EventType.NAVIGATION_WARNING,
        ),
        corridor=_enum_value(Corridor, snapshot.get("corridor"), Corridor.PORT_SPECIFIC),
        status=str(snapshot.get("status") or "monitoring"),
        severity=max(1, min(int(snapshot.get("severity") or 1), 4)),
        ports=list(snapshot.get("ports") or []),
        occurred_start=_snapshot_datetime(snapshot, "occurred_start"),
        occurred_end=_snapshot_datetime(snapshot, "occurred_end"),
        version_no=latest.version_no,
        latest_version_id=latest.id,
        published_at=latest.published_at,
        content_hash=latest.content_hash,
        summary_confirmed=latest.summary_confirmed,
        summary_reported=latest.summary_reported,
        summary_unknown=latest.summary_unknown,
        whats_changed=latest.whats_changed,
        timeline=timeline,
        sources=sources,
        corrections=corrections,
    )
    record_portal_usage(
        session,
        principal=principal,
        event_name="portal.event_view",
        properties={"event_id": str(event_id), "event_version_id": str(latest.id)},
    )
    return result
