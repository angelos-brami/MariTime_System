import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Annotated
from uuid import UUID

import jwt
from eastmed_schema.enums import AccountTier, AuthAssurance, DeskRole
from eastmed_schema.models import Account, AccountApiKey, DeskUser, User
from eastmed_shared import get_settings
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBasic,
    HTTPBasicCredentials,
    HTTPBearer,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from eastmed_api.database import get_db

postmark_basic = HTTPBasic(auto_error=False)
whatsapp_basic = HTTPBasic(auto_error=False)
data_bearer = HTTPBearer(auto_error=False, scheme_name="EastMedAccountApiKey")


def _csv_values(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


@lru_cache(maxsize=8)
def _oidc_jwks_client(url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(
        url,
        cache_keys=True,
        max_cached_keys=16,
        cache_jwk_set=True,
        lifespan=300,
        timeout=5,
    )


def _decode_desk_oidc_token(token: str) -> dict[str, object]:
    settings = get_settings()
    issuer = settings.desk_oidc_issuer
    audience = settings.desk_oidc_audience
    jwks_url = settings.desk_oidc_jwks_url
    if issuer is None or audience is None or jwks_url is None:
        raise ValueError("OIDC desk authentication is incomplete")
    algorithms = _csv_values(settings.desk_oidc_algorithms)
    if not algorithms or any(algorithm not in {"RS256", "ES256"} for algorithm in algorithms):
        raise ValueError("OIDC desk signing algorithms are invalid")
    header = jwt.get_unverified_header(token)
    if header.get("alg") not in algorithms:
        raise ValueError("OIDC token uses an unauthorized signing algorithm")
    if header.get("typ") not in _csv_values(settings.desk_oidc_token_types):
        raise ValueError("OIDC token type is invalid")
    signing_key = _oidc_jwks_client(jwks_url).get_signing_key_from_jwt(token)
    claims: dict[str, object] = jwt.decode(
        token,
        signing_key.key,
        algorithms=list(algorithms),
        audience=audience,
        issuer=issuer,
        leeway=settings.desk_oidc_leeway_seconds,
        options={
            "require": ["iss", "sub", "aud", "exp", "iat"],
            "strict_aud": True,
        },
    )
    authorized_party = claims.get("azp")
    authorized_parties = settings.desk_oidc_authorized_party_set
    if authorized_parties and authorized_party not in authorized_parties:
        raise ValueError("OIDC token authorized party is invalid")
    issued_at = claims.get("iat")
    if not isinstance(issued_at, int) or isinstance(issued_at, bool):
        raise ValueError("OIDC token issued-at claim is invalid")
    token_age = datetime.now(UTC).timestamp() - issued_at
    if token_age > settings.desk_oidc_max_token_age_seconds:
        raise ValueError("OIDC token is too old")
    return claims


def _oidc_assurance(claims: dict[str, object]) -> AuthAssurance:
    acr = claims.get("acr")
    settings = get_settings()
    if isinstance(acr, str) and acr in _csv_values(
        settings.desk_oidc_phishing_resistant_acr_values
    ):
        return AuthAssurance.PHISHING_RESISTANT
    if isinstance(acr, str) and acr in _csv_values(settings.desk_oidc_mfa_acr_values):
        return AuthAssurance.MFA
    factor_age = claims.get("fva")
    if (
        isinstance(factor_age, list)
        and len(factor_age) == 2
        and all(isinstance(age, int) and not isinstance(age, bool) for age in factor_age)
        and factor_age[1] >= 0
        and factor_age[1] <= settings.desk_oidc_mfa_max_age_minutes
    ):
        return AuthAssurance.MFA
    return AuthAssurance.PASSWORD


@dataclass(frozen=True)
class PortalPrincipal:
    user_id: UUID
    account_id: UUID
    subject: str
    email: str
    company: str
    role: str


@dataclass(frozen=True)
class DeskPrincipal:
    user_id: UUID
    issuer: str
    subject: str
    email: str
    display_name: str
    role: DeskRole
    assurance: AuthAssurance

    @property
    def actor(self) -> str:
        return f"desk:{self.user_id}"


@dataclass(frozen=True)
class DataApiPrincipal:
    api_key_id: UUID
    account_id: UUID
    company: str
    scopes: frozenset[str]


def account_api_key_hash(token: str) -> str:
    pepper = get_settings().api_key_pepper.get_secret_value().encode("utf-8")
    return hmac.new(pepper, token.encode("utf-8"), hashlib.sha256).hexdigest()


def _api_key_prefix(token: str) -> str | None:
    if not token.startswith("em_live_") or "." not in token:
        return None
    prefix = token.removeprefix("em_live_").split(".", 1)[0]
    return prefix if 8 <= len(prefix) <= 16 else None


def require_desk_token(x_desk_token: Annotated[str | None, Header()] = None) -> None:
    expected = get_settings().desk_api_token.get_secret_value()
    if x_desk_token is None or not secrets.compare_digest(x_desk_token, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid desk token")


def require_desk_principal(
    db: Annotated[Session, Depends(get_db)],
    x_desk_token: Annotated[str | None, Header()] = None,
    x_desk_subject: Annotated[str | None, Header()] = None,
    x_desk_auth_issuer: Annotated[str | None, Header()] = None,
    x_desk_auth_assurance: Annotated[str | None, Header()] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> DeskPrincipal:
    require_desk_token(x_desk_token)
    settings = get_settings()
    if settings.desk_auth_mode == "oidc":
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="A signed desk OIDC token is required",
            )
        token = authorization.removeprefix("Bearer ").strip()
        if not token or len(token) > 16_384:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid desk OIDC token",
            )
        try:
            claims = _decode_desk_oidc_token(token)
            issuer_claim = claims.get("iss")
            subject_claim = claims.get("sub")
            if not isinstance(issuer_claim, str) or not isinstance(subject_claim, str):
                raise ValueError("OIDC identity claims are invalid")
            issuer = issuer_claim
            subject = subject_claim
            assurance = _oidc_assurance(claims)
        except (jwt.PyJWTError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid desk OIDC token",
            ) from exc
    else:
        issuer = (x_desk_auth_issuer or "").strip()
        subject = (x_desk_subject or "").strip()
        if issuer != settings.desk_auth_issuer or not subject or len(subject) > 255:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid desk identity assertion",
            )
        try:
            assurance = AuthAssurance((x_desk_auth_assurance or "").strip().casefold())
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid desk authentication assurance",
            ) from exc
    if not subject or len(subject) > 255:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid desk identity assertion",
        )
    user = db.scalar(
        select(DeskUser).where(
            DeskUser.auth_issuer == issuer,
            DeskUser.auth_subject == subject,
            DeskUser.active.is_(True),
        )
    )
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Desk identity is not active",
        )
    return DeskPrincipal(
        user_id=user.id,
        issuer=issuer,
        subject=subject,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        assurance=assurance,
    )


def require_desk_role(principal: DeskPrincipal, allowed: frozenset[DeskRole]) -> None:
    if principal.role not in allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Desk role is not authorized for this action",
        )


def require_outbound_enabled() -> None:
    if not get_settings().outbound_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Outbound kill switch is active",
        )


def require_postmark_basic(
    credentials: Annotated[HTTPBasicCredentials | None, Depends(postmark_basic)],
) -> None:
    settings = get_settings()
    expected_username = settings.postmark_inbound_username
    expected_password = settings.postmark_inbound_password
    if not expected_username or expected_password is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Postmark inbound webhook is not configured",
        )
    valid = bool(
        credentials
        and secrets.compare_digest(credentials.username, expected_username)
        and secrets.compare_digest(credentials.password, expected_password.get_secret_value())
    )
    if not valid:
        # Postmark stops retrying an inbound webhook after a 403 response.
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid webhook auth")


def require_whatsapp_basic(
    credentials: Annotated[HTTPBasicCredentials | None, Depends(whatsapp_basic)],
) -> None:
    settings = get_settings()
    expected_username = settings.whatsapp_webhook_username
    expected_password = settings.whatsapp_webhook_password
    if not expected_username or expected_password is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="WhatsApp webhook is not configured",
        )
    valid = bool(
        credentials
        and secrets.compare_digest(credentials.username, expected_username)
        and secrets.compare_digest(credentials.password, expected_password.get_secret_value())
    )
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid WhatsApp webhook auth",
        )


def require_portal_user(
    db: Annotated[Session, Depends(get_db)],
    x_portal_token: Annotated[str | None, Header()] = None,
    x_portal_subject: Annotated[str | None, Header()] = None,
) -> PortalPrincipal:
    expected = get_settings().portal_api_token.get_secret_value()
    if x_portal_token is None or not secrets.compare_digest(x_portal_token, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid portal token")
    subject = (x_portal_subject or "").strip()
    if not subject or len(subject) > 255:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid portal subject"
        )

    row = db.execute(
        select(User, Account)
        .join(Account, Account.id == User.account_id)
        .where(
            User.auth_subject == subject,
            User.active.is_(True),
            User.portal_enabled.is_(True),
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Portal access is not enabled"
        )
    user, account = row
    today = datetime.now(UTC).date()
    if not account.contract_start <= today <= account.contract_end:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Account contract is inactive"
        )
    return PortalPrincipal(
        user_id=user.id,
        account_id=account.id,
        subject=subject,
        email=user.email,
        company=account.company,
        role=user.role,
    )


def require_data_api_key(
    db: Annotated[Session, Depends(get_db)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(data_bearer)],
) -> DataApiPrincipal:
    token = (
        credentials.credentials if credentials and credentials.scheme.casefold() == "bearer" else ""
    )
    prefix = _api_key_prefix(token)
    if prefix is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
    digest = account_api_key_hash(token)
    rows = db.execute(
        select(AccountApiKey, Account)
        .join(Account, Account.id == AccountApiKey.account_id)
        .where(
            AccountApiKey.key_prefix == prefix,
            AccountApiKey.revoked_at.is_(None),
        )
    ).all()
    match = next(
        (
            (api_key, account)
            for api_key, account in rows
            if secrets.compare_digest(api_key.secret_hash, digest)
        ),
        None,
    )
    if match is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
    api_key, account = match
    now = datetime.now(UTC)
    if api_key.expires_at is not None and api_key.expires_at <= now:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="API key expired")
    if account.tier != AccountTier.DATA:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Data API access is disabled",
        )
    if not account.contract_start <= now.date() <= account.contract_end:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account contract is inactive",
        )
    if api_key.last_used_at is None or api_key.last_used_at < now - timedelta(minutes=5):
        api_key.last_used_at = now
        db.commit()
    return DataApiPrincipal(
        api_key_id=api_key.id,
        account_id=account.id,
        company=account.company,
        scopes=frozenset(str(scope) for scope in api_key.scopes_json),
    )


def require_data_scope(principal: DataApiPrincipal, scope: str) -> None:
    if scope not in principal.scopes:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"API key does not grant {scope}",
        )
