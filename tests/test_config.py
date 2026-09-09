import pytest
from eastmed_shared import Settings
from pydantic import SecretStr, ValidationError


def test_production_rejects_placeholder_security_configuration() -> None:
    with pytest.raises(ValidationError, match="desk API token"):
        Settings(environment="production")


def test_production_accepts_explicit_https_security_configuration() -> None:
    settings = Settings(
        environment="production",
        database_url="postgresql+psycopg://app:secret@db.internal/eastmed",
        desk_api_token=SecretStr("d" * 48),
        portal_api_token=SecretStr("p" * 48),
        object_store_secret_key=SecretStr("s" * 48),
        public_base_url="https://intelligence.example",
        api_key_pepper=SecretStr("k" * 48),
        desk_auth_mode="oidc",
        desk_oidc_issuer="https://identity.example",
        desk_oidc_audience="eastmed-desk",
        desk_oidc_jwks_url="https://identity.example/.well-known/jwks.json",
        desk_oidc_authorized_parties="https://intelligence.example",
    )

    assert settings.environment == "production"


def test_production_rejects_unsigned_trusted_proxy_identity() -> None:
    with pytest.raises(ValidationError, match="cryptographically verified OIDC"):
        Settings(
            environment="production",
            database_url="postgresql+psycopg://app:secret@db.internal/eastmed",
            desk_api_token=SecretStr("d" * 48),
            portal_api_token=SecretStr("p" * 48),
            object_store_secret_key=SecretStr("s" * 48),
            public_base_url="https://intelligence.example",
            api_key_pepper=SecretStr("k" * 48),
        )


def test_production_oidc_requires_an_authorized_console_origin() -> None:
    with pytest.raises(ValidationError, match="authorized web application origin"):
        Settings(
            environment="production",
            database_url="postgresql+psycopg://app:secret@db.internal/eastmed",
            desk_api_token=SecretStr("d" * 48),
            portal_api_token=SecretStr("p" * 48),
            object_store_secret_key=SecretStr("s" * 48),
            public_base_url="https://intelligence.example",
            api_key_pepper=SecretStr("k" * 48),
            desk_auth_mode="oidc",
            desk_oidc_issuer="https://identity.example",
            desk_oidc_audience="eastmed-desk",
            desk_oidc_jwks_url="https://identity.example/.well-known/jwks.json",
        )


def test_production_claim_extraction_requires_anthropic_key_when_enabled() -> None:
    with pytest.raises(ValidationError, match="Anthropic API key"):
        Settings(
            environment="production",
            database_url="postgresql+psycopg://app:secret@db.internal/eastmed",
            desk_api_token=SecretStr("d" * 48),
            portal_api_token=SecretStr("p" * 48),
            object_store_secret_key=SecretStr("s" * 48),
            public_base_url="https://intelligence.example",
            claim_extraction_enabled=True,
        )


def test_enabled_claim_extraction_requires_registered_system_fingerprint() -> None:
    with pytest.raises(ValidationError, match="system fingerprint"):
        Settings(
            claim_extraction_enabled=True,
            anthropic_api_key=SecretStr("provider-key"),
        )


def test_enabled_ais_cache_requires_key_and_secure_websocket() -> None:
    with pytest.raises(ValidationError, match="AISstream API key"):
        Settings(ais_enabled=True)
    with pytest.raises(ValidationError, match="secure WebSocket"):
        Settings(
            ais_enabled=True,
            aisstream_api_key=SecretStr("ais-key"),
            aisstream_url="ws://stream.example.test",
        )


def test_production_outbound_requires_a_delivery_provider() -> None:
    with pytest.raises(ValidationError, match="at least one configured provider"):
        Settings(
            environment="production",
            database_url="postgresql+psycopg://app:secret@db.internal/eastmed",
            desk_api_token=SecretStr("d" * 48),
            portal_api_token=SecretStr("p" * 48),
            object_store_secret_key=SecretStr("s" * 48),
            public_base_url="https://intelligence.example",
            api_key_pepper=SecretStr("k" * 48),
            desk_auth_mode="oidc",
            desk_oidc_issuer="https://identity.example",
            desk_oidc_audience="eastmed-desk",
            desk_oidc_jwks_url="https://identity.example/.well-known/jwks.json",
            desk_oidc_authorized_parties="https://intelligence.example",
            outbound_enabled=True,
        )


def test_postmark_delivery_requires_sender_email() -> None:
    with pytest.raises(ValidationError, match="sender email"):
        Settings(postmark_server_token=SecretStr("postmark-token"))
