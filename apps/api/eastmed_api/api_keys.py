from __future__ import annotations

import secrets
from datetime import UTC, datetime
from uuid import UUID

from eastmed_schema.enums import AccountTier
from eastmed_schema.models import Account, AccountApiKey, AuditLog
from sqlalchemy import select
from sqlalchemy.orm import Session

from eastmed_api.contracts import ApiKeyCreate, ApiKeyIssuedRead, ApiKeyRead
from eastmed_api.security import account_api_key_hash


class ApiKeyWorkflowError(ValueError):
    pass


def _human(value: str, action: str) -> str:
    actor = value.strip()
    if not 2 <= len(actor) <= 255 or actor.casefold().startswith("model:"):
        raise ApiKeyWorkflowError(f"a named human must {action} the API key")
    return actor


def issue_account_api_key(session: Session, payload: ApiKeyCreate) -> ApiKeyIssuedRead:
    actor = _human(payload.created_by, "create")
    account = session.get(Account, payload.account_id)
    if account is None:
        raise LookupError("Account not found")
    if account.tier != AccountTier.DATA:
        raise ApiKeyWorkflowError("read-only API keys are available only to Data accounts")
    now = datetime.now(UTC)
    if payload.expires_at is not None:
        if payload.expires_at.tzinfo is None or payload.expires_at.utcoffset() is None:
            raise ApiKeyWorkflowError("API key expiry must include a timezone")
        if payload.expires_at <= now:
            raise ApiKeyWorkflowError("API key expiry must be in the future")
    prefix = secrets.token_hex(5)
    token = f"em_live_{prefix}.{secrets.token_urlsafe(32)}"
    api_key = AccountApiKey(
        account_id=account.id,
        name=payload.name.strip(),
        key_prefix=prefix,
        secret_hash=account_api_key_hash(token),
        scopes_json=list(payload.scopes),
        created_at=now,
        created_by=actor,
        expires_at=payload.expires_at,
        last_used_at=None,
        revoked_at=None,
        revoked_by=None,
    )
    session.add(api_key)
    session.flush()
    session.add(
        AuditLog(
            actor=actor,
            action="account_api_key.created",
            entity="account_api_key",
            entity_id=api_key.id,
            payload_json={
                "account_id": str(account.id),
                "name": api_key.name,
                "key_prefix": prefix,
                "scopes": api_key.scopes_json,
                "expires_at": payload.expires_at.isoformat() if payload.expires_at else None,
            },
        )
    )
    session.commit()
    session.refresh(api_key)
    public = ApiKeyRead.model_validate(api_key)
    return ApiKeyIssuedRead(**public.model_dump(), api_key=token)


def list_account_api_keys(session: Session, *, account_id: UUID | None = None) -> list[ApiKeyRead]:
    query = select(AccountApiKey).order_by(AccountApiKey.created_at.desc())
    if account_id is not None:
        query = query.where(AccountApiKey.account_id == account_id)
    return [ApiKeyRead.model_validate(row) for row in session.scalars(query).all()]


def revoke_account_api_key(session: Session, *, api_key_id: UUID, revoked_by: str) -> ApiKeyRead:
    actor = _human(revoked_by, "revoke")
    api_key = session.scalar(
        select(AccountApiKey).where(AccountApiKey.id == api_key_id).with_for_update()
    )
    if api_key is None:
        raise LookupError("API key not found")
    if api_key.revoked_at is None:
        api_key.revoked_at = datetime.now(UTC)
        api_key.revoked_by = actor
        session.add(
            AuditLog(
                actor=actor,
                action="account_api_key.revoked",
                entity="account_api_key",
                entity_id=api_key.id,
                payload_json={
                    "account_id": str(api_key.account_id),
                    "key_prefix": api_key.key_prefix,
                },
            )
        )
        session.commit()
        session.refresh(api_key)
    return ApiKeyRead.model_validate(api_key)
