from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import websockets
from eastmed_api.database import SessionLocal
from eastmed_schema.enums import Corridor
from eastmed_schema.models import AISPositionCache
from eastmed_shared import configure_error_monitoring, get_settings
from eastmed_shared.logging import configure_logging
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# AISstream expects [latitude, longitude] corners. These deliberately cover only
# the product corridors and approaches named in the build specification.
CORRIDOR_BOUNDING_BOXES: dict[
    Corridor, tuple[tuple[tuple[float, float], tuple[float, float]], ...]
] = {
    Corridor.HORMUZ_GULF: (((23.5, 52.0), (28.5, 58.5)),),
    Corridor.RED_SEA_BEM_SUEZ: (
        ((10.0, 39.0), (16.0, 46.0)),
        ((28.0, 30.0), (32.5, 35.5)),
    ),
    Corridor.EAST_MED: (((30.0, 18.0), (42.5, 37.0)),),
}

POSITION_MESSAGE_TYPES = (
    "PositionReport",
    "StandardClassBPositionReport",
    "ExtendedClassBPositionReport",
    "LongRangeAisBroadcastMessage",
)


@dataclass(frozen=True, slots=True)
class AISObservation:
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
    payload_hash: str


def subscription_payload(api_key: SecretStr) -> dict[str, object]:
    boxes = [
        [[first[0], first[1]], [second[0], second[1]]]
        for corridor_boxes in CORRIDOR_BOUNDING_BOXES.values()
        for first, second in corridor_boxes
    ]
    return {
        "APIKey": api_key.get_secret_value(),
        "BoundingBoxes": boxes,
        "FilterMessageTypes": list(POSITION_MESSAGE_TYPES),
    }


def corridor_for_position(latitude: float, longitude: float) -> Corridor | None:
    for corridor, boxes in CORRIDOR_BOUNDING_BOXES.items():
        for first, second in boxes:
            lat_min, lat_max = sorted((first[0], second[0]))
            lon_min, lon_max = sorted((first[1], second[1]))
            if lat_min <= latitude <= lat_max and lon_min <= longitude <= lon_max:
                return corridor
    return None


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    return None


def _bounded_number(value: object, *, minimum: float, maximum: float) -> float | None:
    parsed = _number(value)
    return parsed if parsed is not None and minimum <= parsed <= maximum else None


def _message_time(metadata: dict[str, Any], received_at: datetime) -> datetime:
    raw = metadata.get("time_utc") or metadata.get("TimeUtc")
    if not isinstance(raw, str):
        return received_at
    normalized = raw.strip().removesuffix(" UTC")
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        return received_at
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=UTC)
    parsed = parsed.astimezone(UTC)
    if parsed > received_at + timedelta(minutes=5):
        return received_at
    return parsed


def parse_aisstream_message(
    payload: dict[str, Any], *, received_at: datetime | None = None
) -> AISObservation | None:
    received = received_at or datetime.now(UTC)
    if received.tzinfo is None or received.utcoffset() is None:
        raise ValueError("AIS receipt timestamp must include a timezone")
    message_type = payload.get("MessageType")
    if message_type not in POSITION_MESSAGE_TYPES:
        return None
    message = payload.get("Message")
    metadata_value = payload.get("MetaData", payload.get("Metadata", {}))
    if not isinstance(message, dict) or not isinstance(metadata_value, dict):
        return None
    body_value = message.get(str(message_type))
    if not isinstance(body_value, dict):
        return None
    body: dict[str, Any] = body_value
    metadata: dict[str, Any] = metadata_value
    latitude = _number(body.get("Latitude"))
    longitude = _number(body.get("Longitude"))
    if latitude is None:
        latitude = _number(metadata.get("Latitude", metadata.get("latitude")))
    if longitude is None:
        longitude = _number(metadata.get("Longitude", metadata.get("longitude")))
    if (
        latitude is None
        or longitude is None
        or not -90 <= latitude <= 90
        or not -180 <= longitude <= 180
    ):
        return None
    corridor = corridor_for_position(latitude, longitude)
    if corridor is None:
        return None
    mmsi_value = body.get("UserID", metadata.get("MMSI"))
    if isinstance(mmsi_value, bool) or not isinstance(mmsi_value, (str, int)):
        return None
    mmsi = str(mmsi_value).strip()
    if not mmsi.isdigit() or not 1 <= len(mmsi) <= 9 or int(mmsi) == 0:
        return None
    mmsi = mmsi.zfill(9)
    vessel_name_value = metadata.get("ShipName", metadata.get("ship_name"))
    vessel_name = (
        vessel_name_value.strip()[:255]
        if isinstance(vessel_name_value, str) and vessel_name_value.strip()
        else None
    )
    imo_value = metadata.get("IMO", metadata.get("Imo"))
    imo = str(imo_value).strip()[:16] if isinstance(imo_value, (str, int)) else None
    navigation_value = body.get("NavigationalStatus")
    navigation_status = str(navigation_value)[:128] if navigation_value is not None else None
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return AISObservation(
        mmsi=mmsi,
        imo=imo,
        vessel_name=vessel_name,
        latitude=latitude,
        longitude=longitude,
        course=_bounded_number(body.get("Cog"), minimum=0, maximum=359.9),
        speed=_bounded_number(body.get("Sog"), minimum=0, maximum=102.2),
        navigation_status=navigation_status,
        corridor=corridor,
        message_at=_message_time(metadata, received),
        received_at=received.astimezone(UTC),
        payload_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )


def cache_observation(session: Session, observation: AISObservation) -> AISPositionCache:
    row = session.scalar(
        select(AISPositionCache).where(AISPositionCache.mmsi == observation.mmsi).with_for_update()
    )
    values = {
        "imo": observation.imo,
        "vessel_name": observation.vessel_name,
        "latitude": observation.latitude,
        "longitude": observation.longitude,
        "course": observation.course,
        "speed": observation.speed,
        "navigation_status": observation.navigation_status,
        "corridor": observation.corridor,
        "message_at": observation.message_at,
        "received_at": observation.received_at,
        "source": "aisstream.io",
        "payload_hash": observation.payload_hash,
    }
    if row is None:
        row = AISPositionCache(mmsi=observation.mmsi, **values)
        session.add(row)
    elif observation.message_at >= row.message_at:
        for key, value in values.items():
            setattr(row, key, value)
    session.flush()
    return row


async def consume_stream(*, once: bool = False) -> int:
    settings = get_settings()
    if not settings.ais_enabled:
        raise RuntimeError("AIS cache is disabled")
    if settings.aisstream_api_key is None:
        raise RuntimeError("AISstream API key is not configured")
    processed = 0
    async with websockets.connect(
        settings.aisstream_url,
        open_timeout=10,
        ping_interval=20,
        ping_timeout=20,
        max_size=2_000_000,
        max_queue=1_024,
    ) as websocket:
        await websocket.send(json.dumps(subscription_payload(settings.aisstream_api_key)))
        with SessionLocal() as session:
            async for raw in websocket:
                if not isinstance(raw, str):
                    continue
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning("ais.invalid_json")
                    continue
                if not isinstance(payload, dict):
                    continue
                if isinstance(payload.get("error"), str):
                    raise RuntimeError("AISstream rejected the subscription")
                observation = parse_aisstream_message(payload)
                if observation is None:
                    continue
                cache_observation(session, observation)
                session.commit()
                processed += 1
                if once:
                    return processed
    return processed


async def run_forever() -> None:
    delay = 1
    while True:
        try:
            await consume_stream()
            delay = 1
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("ais.stream_disconnected", extra={"retry_seconds": delay})
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)


def main() -> None:
    parser = argparse.ArgumentParser(description="Maintain the thin AIS corridor position cache")
    parser.add_argument("--once", action="store_true", help="Exit after the first cached position")
    args = parser.parse_args()
    settings = get_settings()
    configure_error_monitoring(settings)
    configure_logging(settings.log_level)
    asyncio.run(consume_stream(once=True) if args.once else run_forever())


if __name__ == "__main__":
    main()
