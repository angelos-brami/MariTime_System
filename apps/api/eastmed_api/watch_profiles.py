from __future__ import annotations

import json
from datetime import UTC, date, datetime
from uuid import UUID

from eastmed_schema.enums import Corridor, EventType
from eastmed_schema.models import Account, AuditLog, User, WatchProfile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from eastmed_api.contracts import (
    AccountRead,
    AccountUserRead,
    CustomerChannelsUpdate,
    PortalAccessUpdate,
    WatchProfileCreate,
    WatchProfileRead,
    WatchProfileUpdate,
)


class WatchProfileError(ValueError):
    pass


def list_accounts(session: Session, *, active_on: date | None = None) -> list[AccountRead]:
    query = select(Account).order_by(Account.company)
    if active_on is not None:
        query = query.where(
            Account.contract_start <= active_on,
            Account.contract_end >= active_on,
        )
    accounts = list(session.scalars(query).all())
    users_by_account: dict[UUID, list[AccountUserRead]] = {account.id: [] for account in accounts}
    if accounts:
        for user in session.scalars(
            select(User)
            .where(User.account_id.in_([account.id for account in accounts]))
            .order_by(User.email)
        ).all():
            users_by_account[user.account_id].append(
                AccountUserRead(
                    id=user.id,
                    email=user.email,
                    phone=user.phone,
                    channels=user.channels_json,
                    role=user.role,
                    auth_subject=user.auth_subject,
                    active=user.active,
                    portal_enabled=user.portal_enabled,
                )
            )
    return [
        AccountRead(
            id=account.id,
            company=account.company,
            tier=account.tier,
            contract_start=account.contract_start,
            contract_end=account.contract_end,
            users=users_by_account[account.id],
        )
        for account in accounts
    ]


def update_portal_access(
    session: Session,
    *,
    user_id: UUID,
    payload: PortalAccessUpdate,
) -> AccountUserRead:
    user = session.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None:
        raise LookupError("User not found")
    if payload.auth_subject:
        existing = session.scalar(
            select(User).where(
                User.auth_subject == payload.auth_subject,
                User.id != user.id,
            )
        )
        if existing is not None:
            raise WatchProfileError("Clerk user subject is already assigned")
    user.auth_subject = payload.auth_subject
    user.portal_enabled = payload.portal_enabled
    session.add(
        AuditLog(
            actor=payload.updated_by,
            action="user.portal_access_updated",
            entity="user",
            entity_id=user.id,
            payload_json={
                "auth_subject": payload.auth_subject,
                "portal_enabled": payload.portal_enabled,
            },
        )
    )
    session.commit()
    session.refresh(user)
    return AccountUserRead(
        id=user.id,
        email=user.email,
        phone=user.phone,
        channels=user.channels_json,
        role=user.role,
        auth_subject=user.auth_subject,
        active=user.active,
        portal_enabled=user.portal_enabled,
    )


def update_customer_channels(
    session: Session,
    *,
    user_id: UUID,
    payload: CustomerChannelsUpdate,
) -> AccountUserRead:
    user = session.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None:
        raise LookupError("User not found")
    now = datetime.now(UTC)
    channels: dict[str, object] = {"email": payload.email_enabled}
    if payload.telegram_chat_id:
        channels["telegram"] = {
            "enabled": True,
            "chat_id": payload.telegram_chat_id.strip(),
        }
    else:
        channels["telegram"] = {"enabled": False}
    if payload.whatsapp_enabled and payload.whatsapp_phone:
        digits = "".join(character for character in payload.whatsapp_phone if character.isdigit())
        user.phone = f"+{digits}"
        channels["whatsapp"] = {
            "enabled": True,
            "phone": digits,
            "opted_in_at": now.isoformat(),
            "opted_in_by": payload.updated_by.strip(),
        }
    else:
        channels["whatsapp"] = {"enabled": False}
    user.channels_json = channels
    session.add(
        AuditLog(
            actor=payload.updated_by.strip(),
            action="user.customer_channels_updated",
            entity="user",
            entity_id=user.id,
            payload_json={
                "email_enabled": payload.email_enabled,
                "telegram_enabled": bool(payload.telegram_chat_id),
                "whatsapp_enabled": payload.whatsapp_enabled,
                "whatsapp_opted_in_at": (now.isoformat() if payload.whatsapp_enabled else None),
            },
        )
    )
    session.commit()
    session.refresh(user)
    return AccountUserRead(
        id=user.id,
        email=user.email,
        phone=user.phone,
        channels=user.channels_json,
        role=user.role,
        auth_subject=user.auth_subject,
        active=user.active,
        portal_enabled=user.portal_enabled,
    )


def _profile_read(
    profile: WatchProfile, account: Account, custom_geojson: str | None
) -> WatchProfileRead:
    return WatchProfileRead(
        id=profile.id,
        account_id=profile.account_id,
        account_company=account.company,
        account_tier=account.tier,
        name=profile.name,
        corridors=[Corridor(value) for value in profile.corridors],
        event_types=[EventType(value) for value in profile.event_types],
        min_severity=profile.min_severity,
        ports=profile.ports,
        custom_geojson=json.loads(custom_geojson) if custom_geojson else None,
        active=profile.active,
        configured_by=profile.configured_by,
        configured_with_customer_at=profile.configured_with_customer_at,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


def get_watch_profile(session: Session, *, profile_id: UUID) -> WatchProfileRead:
    row = session.execute(
        select(WatchProfile, Account, func.ST_AsGeoJSON(WatchProfile.custom_geo))
        .join(Account, Account.id == WatchProfile.account_id)
        .where(WatchProfile.id == profile_id)
    ).one_or_none()
    if row is None:
        raise LookupError("Watch profile not found")
    profile, account, custom_geojson = row
    return _profile_read(profile, account, custom_geojson)


def list_watch_profiles(
    session: Session, *, account_id: UUID | None = None
) -> list[WatchProfileRead]:
    query = (
        select(WatchProfile, Account, func.ST_AsGeoJSON(WatchProfile.custom_geo))
        .join(Account, Account.id == WatchProfile.account_id)
        .order_by(Account.company, WatchProfile.name)
    )
    if account_id is not None:
        query = query.where(WatchProfile.account_id == account_id)
    return [
        _profile_read(profile, account, custom_geojson)
        for profile, account, custom_geojson in session.execute(query).tuples().all()
    ]


def _geo_expression(custom_geojson: dict[str, object] | None) -> object | None:
    if custom_geojson is None:
        return None
    serialized = json.dumps(custom_geojson, separators=(",", ":"))
    if len(serialized.encode("utf-8")) > 100_000:
        raise WatchProfileError("Custom geofence is too large")
    return func.ST_SetSRID(func.ST_GeomFromGeoJSON(serialized), 4326)


def _ensure_activation_is_complete(profile: WatchProfile) -> None:
    if profile.active and (
        not profile.configured_by
        or not profile.configured_with_customer_at
        or not any(
            (
                profile.corridors,
                profile.event_types,
                profile.ports,
                profile.custom_geo is not None,
            )
        )
    ):
        raise WatchProfileError(
            "Active profile requires customer configuration, reviewer, and watch criteria"
        )


def create_watch_profile(session: Session, *, payload: WatchProfileCreate) -> WatchProfileRead:
    account = session.get(Account, payload.account_id)
    if account is None:
        raise LookupError("Account not found")
    duplicate = session.scalar(
        select(WatchProfile.id).where(
            WatchProfile.account_id == payload.account_id,
            WatchProfile.name == payload.name,
        )
    )
    if duplicate is not None:
        raise WatchProfileError("Account already has a watch profile with this name")
    profile = WatchProfile(
        account_id=payload.account_id,
        name=payload.name,
        corridors=[corridor.value for corridor in payload.corridors],
        event_types=[event_type.value for event_type in payload.event_types],
        min_severity=payload.min_severity,
        ports=payload.ports,
        custom_geo=_geo_expression(payload.custom_geojson),
        active=payload.active,
        configured_by=payload.configured_by,
        configured_with_customer_at=payload.configured_with_customer_at,
    )
    _ensure_activation_is_complete(profile)
    session.add(profile)
    session.flush()
    session.add(
        AuditLog(
            actor=payload.configured_by or "desk-api",
            action="watch_profile.created",
            entity="watch_profile",
            entity_id=profile.id,
            payload_json={
                **payload.model_dump(mode="json"),
                "custom_geojson": payload.custom_geojson,
            },
        )
    )
    session.commit()
    return get_watch_profile(session, profile_id=profile.id)


def update_watch_profile(
    session: Session, *, profile_id: UUID, payload: WatchProfileUpdate
) -> WatchProfileRead:
    profile = session.get(WatchProfile, profile_id)
    if profile is None:
        raise LookupError("Watch profile not found")
    fields = payload.model_fields_set - {"updated_by"}
    if "name" in fields and payload.name is not None and payload.name != profile.name:
        duplicate = session.scalar(
            select(WatchProfile.id).where(
                WatchProfile.account_id == profile.account_id,
                WatchProfile.name == payload.name,
                WatchProfile.id != profile.id,
            )
        )
        if duplicate is not None:
            raise WatchProfileError("Account already has a watch profile with this name")
    before = {
        "name": profile.name,
        "corridors": profile.corridors,
        "event_types": profile.event_types,
        "min_severity": profile.min_severity,
        "ports": profile.ports,
        "active": profile.active,
        "configured_by": profile.configured_by,
        "configured_with_customer_at": (
            profile.configured_with_customer_at.isoformat()
            if profile.configured_with_customer_at
            else None
        ),
    }
    for field in fields:
        value = getattr(payload, field)
        if field == "corridors" and value is not None:
            value = [corridor.value for corridor in value]
        elif field == "event_types" and value is not None:
            value = [event_type.value for event_type in value]
        elif field == "custom_geojson":
            field = "custom_geo"
            value = _geo_expression(payload.custom_geojson)
        setattr(profile, field, value)
    _ensure_activation_is_complete(profile)
    session.add(
        AuditLog(
            actor=payload.updated_by,
            action="watch_profile.updated",
            entity="watch_profile",
            entity_id=profile.id,
            payload_json={
                "before": before,
                "changes": payload.model_dump(mode="json", exclude={"updated_by"}),
            },
        )
    )
    session.commit()
    return get_watch_profile(session, profile_id=profile.id)


def disable_watch_profile(session: Session, *, profile_id: UUID, reviewer: str) -> WatchProfileRead:
    if reviewer.casefold().startswith("model:"):
        raise WatchProfileError("A model cannot disable a customer watch profile")
    profile = session.get(WatchProfile, profile_id)
    if profile is None:
        raise LookupError("Watch profile not found")
    profile.active = False
    session.add(
        AuditLog(
            actor=reviewer,
            action="watch_profile.disabled",
            entity="watch_profile",
            entity_id=profile.id,
            payload_json={"account_id": str(profile.account_id)},
        )
    )
    session.commit()
    return get_watch_profile(session, profile_id=profile.id)
