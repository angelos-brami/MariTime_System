from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

from eastmed_schema.enums import Corridor
from eastmed_schema.models import AISPositionCache
from eastmed_shared import get_settings
from sqlalchemy import select
from sqlalchemy.orm import Session

from eastmed_api.contracts import AISPositionListRead, AISPositionRead

AIS_CAVEAT = "AIS-derived; subject to interference/spoofing in conflict areas."


def list_ais_positions(
    session: Session,
    *,
    corridor: Corridor | None = None,
    max_age_minutes: int = 180,
    limit: int = 500,
) -> AISPositionListRead:
    now = datetime.now(UTC)
    lower_bound = now - timedelta(minutes=max_age_minutes)
    query = select(AISPositionCache).where(AISPositionCache.message_at >= lower_bound)
    if corridor is not None:
        query = query.where(AISPositionCache.corridor == corridor)
    rows = session.scalars(query.order_by(AISPositionCache.message_at.desc()).limit(limit)).all()
    freshness_minutes = get_settings().ais_freshness_minutes
    results = []
    for row in rows:
        age = max((now - row.message_at).total_seconds() / 60, 0)
        results.append(
            AISPositionRead(
                id=row.id,
                mmsi=row.mmsi,
                imo=row.imo,
                vessel_name=row.vessel_name,
                latitude=row.latitude,
                longitude=row.longitude,
                course=row.course,
                speed=row.speed,
                navigation_status=row.navigation_status,
                corridor=row.corridor,
                message_at=row.message_at,
                received_at=row.received_at,
                age_minutes=round(age, 1),
                stale=age > freshness_minutes,
                source=row.source,
                payload_hash=row.payload_hash,
            )
        )
    cache_status: Literal["disabled", "empty", "stale", "live"]
    if not get_settings().ais_enabled:
        cache_status = "disabled"
    elif not results:
        cache_status = "empty"
    elif all(item.stale for item in results):
        cache_status = "stale"
    else:
        cache_status = "live"
    return AISPositionListRead(
        generated_at=now,
        cache_status=cache_status,
        caveat=AIS_CAVEAT,
        freshness_minutes=freshness_minutes,
        results=results,
    )
