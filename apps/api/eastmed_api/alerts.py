from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, time
from uuid import UUID

from eastmed_schema.enums import Corridor, DeliveryChannel, DeliveryStatus, EventType
from eastmed_schema.models import (
    Account,
    Alert,
    AuditLog,
    Delivery,
    Event,
    EventVersion,
    TriageItem,
    User,
    WatchProfile,
)
from eastmed_shared.alert_rules import AlertRules, load_alert_rules
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from eastmed_api.contracts import (
    AlertAudienceAccountRead,
    AlertPreviewRead,
    AlertReleaseCreate,
    AlertReleaseDraft,
    AlertReleaseRead,
    DeliveryDashboardRead,
    DeliveryRecentRead,
)


class AlertWorkflowError(ValueError):
    pass


@dataclass(frozen=True)
class PlannedDelivery:
    account_id: UUID
    user_id: UUID
    channel: DeliveryChannel
    recipient: str


@dataclass(frozen=True)
class PublishedEvent:
    id: UUID
    slug: str
    event_type: EventType
    corridor: Corridor
    severity: int
    geojson: dict[str, object] | None
    ports: frozenset[str]


@dataclass(frozen=True)
class AlertPlan:
    version: EventVersion
    event: PublishedEvent
    preview: AlertPreviewRead
    deliveries: tuple[PlannedDelivery, ...]


def watch_profile_matches(
    *,
    profile: WatchProfile,
    event: Event | PublishedEvent,
    event_ports: set[str],
    geo_intersects: bool,
    tier_min_severity: int,
) -> bool:
    if not profile.active or profile.configured_with_customer_at is None:
        return False
    if event.severity < max(profile.min_severity, tier_min_severity):
        return False
    if profile.corridors and event.corridor.value not in profile.corridors:
        return False
    if profile.event_types and event.event_type.value not in profile.event_types:
        return False
    if profile.ports and not event_ports.intersection(port.upper() for port in profile.ports):
        return False
    return profile.custom_geo is None or geo_intersects


def user_channel_enabled(user: User, channel: DeliveryChannel) -> bool:
    return user_channel_destination(user, channel) is not None


def user_channel_destination(user: User, channel: DeliveryChannel) -> str | None:
    value = user.channels_json.get(channel.value)
    if value is not True and (not isinstance(value, dict) or value.get("enabled") is False):
        return None
    if channel == DeliveryChannel.TELEGRAM:
        if isinstance(value, dict) and value.get("chat_id"):
            return str(value["chat_id"])
        return None
    if channel == DeliveryChannel.EMAIL:
        return user.email or None
    if channel == DeliveryChannel.WHATSAPP:
        if not isinstance(value, dict) or not value.get("opted_in_at"):
            return None
        raw_phone = value.get("phone") or user.phone
        digits = "".join(character for character in str(raw_phone or "") if character.isdigit())
        return digits if 8 <= len(digits) <= 15 else None
    return None


def percentile_95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(math.ceil(len(ordered) * 0.95) - 1, 0)]


def daily_cap_throttles(
    *,
    alerts_today: int,
    max_alerts_day: int,
    severity: int,
    severity_4_bypasses_daily_cap: bool,
) -> bool:
    return alerts_today >= max_alerts_day and not (severity == 4 and severity_4_bypasses_daily_cap)


def _event_ports(session: Session, event_id: UUID) -> set[str]:
    ports: set[str] = set()
    for detected in session.scalars(
        select(TriageItem.detected_ports).where(TriageItem.assigned_event_id == event_id)
    ).all():
        for port in detected:
            for key in ("name", "unlocode"):
                if value := port.get(key):
                    ports.add(value.upper())
    return ports


def _geo_match_ids(
    session: Session,
    *,
    event_geojson: dict[str, object] | None,
    profiles: list[WatchProfile],
) -> set[UUID]:
    custom_ids = [profile.id for profile in profiles if profile.custom_geo is not None]
    if not custom_ids or event_geojson is None:
        return set()
    geometry = func.ST_SetSRID(
        func.ST_GeomFromGeoJSON(json.dumps(event_geojson, separators=(",", ":"))),
        4326,
    )
    return set(
        session.scalars(
            select(WatchProfile.id).where(
                WatchProfile.id.in_(custom_ids),
                func.ST_Intersects(WatchProfile.custom_geo, geometry),
            )
        ).all()
    )


def _published_event(version: EventVersion, current: Event, session: Session) -> PublishedEvent:
    snapshot = version.event_snapshot_json
    raw_ports = snapshot.get("ports") if snapshot else None
    ports: set[str] = set()
    if isinstance(raw_ports, list):
        for port in raw_ports:
            if not isinstance(port, dict):
                continue
            for key in ("name", "unlocode"):
                value = port.get(key)
                if value:
                    ports.add(str(value).upper())
    else:
        ports = _event_ports(session, current.id)
    raw_geojson = snapshot.get("geojson") if snapshot else None
    geojson = raw_geojson if isinstance(raw_geojson, dict) else None
    return PublishedEvent(
        id=current.id,
        slug=str(snapshot.get("slug") or current.slug),
        event_type=EventType(str(snapshot.get("event_type") or current.event_type.value)),
        corridor=Corridor(str(snapshot.get("corridor") or current.corridor.value)),
        severity=int(snapshot.get("severity") or current.severity),
        geojson=geojson,
        ports=frozenset(ports),
    )


def _alert_preview_hash(
    *,
    version: EventVersion,
    payload: AlertReleaseDraft,
    rules_version: str,
    accounts: list[AlertAudienceAccountRead],
    deliveries: tuple[PlannedDelivery, ...],
) -> str:
    canonical = {
        "event_version_id": str(version.id),
        "event_version_content_hash": version.content_hash,
        "channels": sorted(channel.value for channel in payload.channels),
        "released_by": payload.released_by,
        "note": payload.note,
        "rules_version": rules_version,
        "accounts": [
            account.model_dump(mode="json")
            for account in sorted(accounts, key=lambda item: str(item.account_id))
        ],
        "deliveries": [
            {
                "account_id": str(delivery.account_id),
                "user_id": str(delivery.user_id),
                "channel": delivery.channel.value,
                "recipient": delivery.recipient,
            }
            for delivery in sorted(
                deliveries,
                key=lambda item: (str(item.account_id), str(item.user_id), item.channel.value),
            )
        ],
    }
    return hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def build_alert_plan(
    session: Session,
    *,
    event_version_id: UUID,
    payload: AlertReleaseDraft,
    rules: AlertRules | None = None,
    now: datetime | None = None,
    lock_accounts: bool = False,
) -> AlertPlan:
    active_rules = rules or load_alert_rules()
    timestamp = now or datetime.now(UTC)
    version = session.get(EventVersion, event_version_id)
    if version is None:
        raise LookupError("Event version not found")
    event = session.get(Event, version.event_id)
    if event is None:
        raise LookupError("Event not found")
    published_event = _published_event(version, event, session)

    profile_query = (
        select(WatchProfile, Account)
        .join(Account, Account.id == WatchProfile.account_id)
        .where(
            WatchProfile.active.is_(True),
            WatchProfile.configured_with_customer_at.is_not(None),
            Account.contract_start <= timestamp.date(),
            Account.contract_end >= timestamp.date(),
        )
        .order_by(Account.id, WatchProfile.name)
    )
    if lock_accounts:
        profile_query = profile_query.with_for_update(of=Account)
    profile_rows = list(session.execute(profile_query).tuples().all())
    profiles = [profile for profile, _ in profile_rows]
    geo_matches = _geo_match_ids(
        session,
        event_geojson=published_event.geojson,
        profiles=profiles,
    )
    matched_by_account: dict[UUID, list[WatchProfile]] = defaultdict(list)
    accounts_by_id: dict[UUID, Account] = {}
    for profile, account in profile_rows:
        tier_rule = active_rules.for_tier(account.tier)
        if watch_profile_matches(
            profile=profile,
            event=published_event,
            event_ports=set(published_event.ports),
            geo_intersects=profile.custom_geo is None or profile.id in geo_matches,
            tier_min_severity=tier_rule.min_severity,
        ):
            matched_by_account[account.id].append(profile)
            accounts_by_id[account.id] = account

    account_ids = list(matched_by_account)
    users_by_account: dict[UUID, list[User]] = defaultdict(list)
    if account_ids:
        for user in session.scalars(
            select(User)
            .where(User.account_id.in_(account_ids), User.active.is_(True))
            .order_by(User.email)
        ).all():
            users_by_account[user.account_id].append(user)

    day_start = datetime.combine(timestamp.date(), time.min, tzinfo=UTC)
    alerts_today: dict[UUID, int] = {}
    if account_ids:
        alerts_today = {
            account_id: count
            for account_id, count in session.execute(
                select(Delivery.account_id, func.count(func.distinct(Alert.id)))
                .join(Alert, Alert.id == Delivery.alert_id)
                .where(
                    Delivery.account_id.in_(account_ids),
                    Alert.created_at >= day_start,
                )
                .group_by(Delivery.account_id)
            ).tuples()
        }

    planned: dict[tuple[UUID, DeliveryChannel], PlannedDelivery] = {}
    account_previews: list[AlertAudienceAccountRead] = []
    for account_id, matched_profiles in matched_by_account.items():
        account = accounts_by_id[account_id]
        tier_rule = active_rules.for_tier(account.tier)
        current_count = alerts_today.get(account_id, 0)
        throttled = daily_cap_throttles(
            alerts_today=current_count,
            max_alerts_day=tier_rule.max_alerts_day,
            severity=published_event.severity,
            severity_4_bypasses_daily_cap=active_rules.severity_4_bypasses_daily_cap,
        )
        eligible_users: set[UUID] = set()
        account_delivery_count = 0
        if not throttled:
            for user in users_by_account[account_id]:
                for channel in payload.channels:
                    recipient = user_channel_destination(user, channel)
                    if recipient is None:
                        continue
                    eligible_users.add(user.id)
                    planned[(user.id, channel)] = PlannedDelivery(
                        account_id=account_id,
                        user_id=user.id,
                        channel=channel,
                        recipient=recipient,
                    )
                    account_delivery_count += 1
        account_previews.append(
            AlertAudienceAccountRead(
                account_id=account.id,
                company=account.company,
                tier=account.tier,
                matched_profile_ids=sorted([profile.id for profile in matched_profiles], key=str),
                user_count=len(eligible_users),
                delivery_count=account_delivery_count,
                alerts_today=current_count,
                daily_cap=tier_rule.max_alerts_day,
                throttled=throttled,
                reason=(
                    f"Daily {account.tier.value} cap of {tier_rule.max_alerts_day} reached"
                    if throttled
                    else None
                ),
            )
        )

    existing_alert = session.scalar(
        select(Alert.id).where(Alert.event_version_id == event_version_id)
    )
    blockers: list[str] = []
    if existing_alert is not None:
        blockers.append("This event version already has a released alert")
    if not matched_by_account:
        blockers.append("No configured watch profile matches this event")
    if not planned:
        blockers.append("No eligible recipient channel remains after preferences and throttles")
    planned_deliveries = tuple(planned.values())
    channel_counts = Counter(delivery.channel.value for delivery in planned_deliveries)
    preview_hash = _alert_preview_hash(
        version=version,
        payload=payload,
        rules_version=active_rules.version,
        accounts=account_previews,
        deliveries=planned_deliveries,
    )
    preview = AlertPreviewRead(
        event_version_id=version.id,
        event_id=event.id,
        title=version.title,
        severity=published_event.severity,
        rules_version=active_rules.version,
        matched_profiles=sum(len(profiles) for profiles in matched_by_account.values()),
        account_count=len(matched_by_account),
        user_count=len({delivery.user_id for delivery in planned.values()}),
        planned_deliveries=dict(channel_counts),
        accounts=account_previews,
        blockers=blockers,
        ready=not blockers,
        preview_hash=preview_hash,
    )
    return AlertPlan(
        version=version,
        event=published_event,
        preview=preview,
        deliveries=planned_deliveries,
    )


def release_alert(
    session: Session,
    *,
    event_version_id: UUID,
    payload: AlertReleaseCreate,
    rules: AlertRules | None = None,
    now: datetime | None = None,
) -> AlertReleaseRead:
    timestamp = now or datetime.now(UTC)
    locked_version = session.scalar(
        select(EventVersion).where(EventVersion.id == event_version_id).with_for_update()
    )
    if locked_version is None:
        raise LookupError("Event version not found")
    existing = session.scalar(select(Alert).where(Alert.event_version_id == event_version_id))
    if existing is not None:
        if set(existing.channels_json) != {channel.value for channel in payload.channels}:
            raise AlertWorkflowError("Alert was already released with different channels")
        if existing.released_by != payload.released_by:
            raise AlertWorkflowError("Alert was already released by a different analyst")
        if existing.message_json.get("note") != payload.note:
            raise AlertWorkflowError("Alert was already released with a different analyst note")
        if existing.audience_snapshot_json.get("preview_hash") != payload.preview_hash:
            raise AlertWorkflowError("Alert was already released from a different preview")
        delivery_count = session.scalar(
            select(func.count(Delivery.id)).where(Delivery.alert_id == existing.id)
        )
        return AlertReleaseRead(
            alert_id=existing.id,
            event_version_id=existing.event_version_id,
            delivery_count=int(delivery_count or 0),
            queued_at=existing.created_at,
            idempotent_replay=True,
        )

    plan = build_alert_plan(
        session,
        event_version_id=event_version_id,
        payload=payload,
        rules=rules,
        now=timestamp,
        lock_accounts=True,
    )
    if payload.preview_hash != plan.preview.preview_hash:
        raise AlertWorkflowError(
            "Alert preview is stale; regenerate the affected-customer preview before release"
        )
    if not plan.preview.ready:
        raise AlertWorkflowError("; ".join(plan.preview.blockers))
    message = {
        "title": plan.version.title,
        "confirmed": plan.version.summary_confirmed,
        "reported": plan.version.summary_reported,
        "unknown": plan.version.summary_unknown,
        "whats_changed": plan.version.whats_changed,
        "severity": plan.event.severity,
        "event_slug": plan.event.slug,
        "note": payload.note,
    }
    alert = Alert(
        event_version_id=plan.version.id,
        severity=plan.event.severity,
        created_at=timestamp,
        released_by=payload.released_by,
        channels_json=[channel.value for channel in payload.channels],
        audience_snapshot_json=plan.preview.model_dump(mode="json"),
        message_json=message,
    )
    session.add(alert)
    session.flush()
    for delivery in plan.deliveries:
        session.add(
            Delivery(
                alert_id=alert.id,
                account_id=delivery.account_id,
                user_id=delivery.user_id,
                channel=delivery.channel,
                recipient=delivery.recipient,
                status=DeliveryStatus.QUEUED,
                queued_at=timestamp,
                attempt_count=0,
                next_attempt_at=timestamp,
            )
        )
    session.add(
        AuditLog(
            actor=payload.released_by,
            action="alert.released",
            entity="alert",
            entity_id=alert.id,
            payload_json={
                "event_version_id": str(plan.version.id),
                "channels": alert.channels_json,
                "audience": alert.audience_snapshot_json,
                "message": message,
            },
        )
    )
    session.commit()
    return AlertReleaseRead(
        alert_id=alert.id,
        event_version_id=plan.version.id,
        delivery_count=len(plan.deliveries),
        queued_at=timestamp,
        idempotent_replay=False,
    )


def delivery_dashboard(
    session: Session, *, limit: int = 100, now: datetime | None = None
) -> DeliveryDashboardRead:
    timestamp = now or datetime.now(UTC)
    total = int(session.scalar(select(func.count(Delivery.id))) or 0)
    status_counts = {
        status.value: int(count)
        for status, count in session.execute(
            select(Delivery.status, func.count(Delivery.id)).group_by(Delivery.status)
        ).tuples()
    }
    channel_counts = {
        channel.value: int(count)
        for channel, count in session.execute(
            select(Delivery.channel, func.count(Delivery.id))
            .where(Delivery.status == DeliveryStatus.DELIVERED)
            .group_by(Delivery.channel)
        ).tuples()
    }
    rows = list(
        session.execute(
            select(Delivery, Alert, EventVersion, Account)
            .join(Alert, Alert.id == Delivery.alert_id)
            .join(EventVersion, EventVersion.id == Alert.event_version_id)
            .join(Account, Account.id == Delivery.account_id)
            .order_by(Delivery.queued_at.desc())
            .limit(5000)
        )
        .tuples()
        .all()
    )
    latencies = [
        (delivery.delivered_at - version.published_at).total_seconds()
        for delivery, _, version, _ in rows
        if delivery.delivered_at is not None
    ]
    recent = [
        DeliveryRecentRead(
            id=delivery.id,
            alert_id=alert.id,
            event_version_id=version.id,
            event_title=version.title,
            account_company=account.company,
            recipient=delivery.recipient,
            channel=delivery.channel,
            status=delivery.status,
            queued_at=delivery.queued_at,
            sent_at=delivery.sent_at,
            delivered_at=delivery.delivered_at,
            attempt_count=delivery.attempt_count,
            last_error=delivery.last_error,
        )
        for delivery, alert, version, account in rows[:limit]
    ]
    return DeliveryDashboardRead(
        generated_at=timestamp,
        total=total,
        status_counts=status_counts,
        channel_counts=channel_counts,
        publish_to_delivery_p95_seconds=percentile_95(latencies),
        within_60_seconds_percent=(
            round(sum(latency <= 60 for latency in latencies) / len(latencies) * 100, 1)
            if latencies
            else None
        ),
        recent=recent,
    )
