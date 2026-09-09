from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, computed_field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="EASTMED_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: str = "local"
    database_url: str = "postgresql+psycopg://eastmed:eastmed@localhost:5432/eastmed"
    redis_url: str = "redis://localhost:6379/0"
    object_store_endpoint: str = "http://localhost:9000"
    object_store_bucket: str = "eastmed-evidence"
    object_store_access_key: str = "eastmed"
    object_store_secret_key: SecretStr = SecretStr("change-me")
    desk_api_token: SecretStr = SecretStr("change-me-with-at-least-32-characters")
    desk_auth_issuer: str = Field(default="eastmed-console", min_length=3, max_length=255)
    desk_auth_mode: Literal["trusted_proxy", "oidc"] = "trusted_proxy"
    desk_oidc_issuer: str | None = None
    desk_oidc_audience: str | None = None
    desk_oidc_jwks_url: str | None = None
    desk_oidc_authorized_parties: str = ""
    desk_oidc_algorithms: str = "RS256,ES256"
    desk_oidc_token_types: str = "JWT,at+jwt"  # noqa: S105 - JWT media types, not a secret
    desk_oidc_mfa_acr_values: str = ""
    desk_oidc_phishing_resistant_acr_values: str = ""
    desk_oidc_leeway_seconds: int = Field(default=30, ge=0, le=300)
    desk_oidc_max_token_age_seconds: int = Field(default=3_600, ge=60, le=86_400)
    desk_oidc_mfa_max_age_minutes: int = Field(default=10, ge=0, le=60)
    portal_api_token: SecretStr = SecretStr("change-me-portal-token-at-least-32-chars")
    allowed_fetch_hosts: str = ""
    telegram_desk_bot_token: SecretStr | None = None
    telegram_desk_chat_id: str | None = None
    telegram_customer_bot_token: SecretStr | None = None
    whatsapp_360dialog_api_key: SecretStr | None = None
    whatsapp_360dialog_base_url: str = "https://waba-v2.360dialog.io"
    whatsapp_alert_template: str = "eastmed_alert_v1"
    whatsapp_correction_template: str = "eastmed_correction_v1"
    whatsapp_template_language: str = "en"
    whatsapp_webhook_username: str | None = None
    whatsapp_webhook_password: SecretStr | None = None
    ais_enabled: bool = False
    aisstream_api_key: SecretStr | None = None
    aisstream_url: str = "wss://stream.aisstream.io/v0/stream"
    ais_freshness_minutes: int = Field(default=30, ge=1, le=1_440)
    postmark_server_token: SecretStr | None = None
    postmark_from_email: str | None = None
    postmark_message_stream: str = "alerts"
    public_base_url: str = "http://localhost:3000"
    delivery_max_attempts: int = Field(default=5, ge=1, le=20)
    launch_min_active_sources: int = Field(default=60, ge=1, le=10_000)
    airtable_token: SecretStr | None = None
    airtable_base_id: str | None = None
    postmark_inbound_username: str | None = None
    postmark_inbound_password: SecretStr | None = None
    sentry_dsn: str | None = None
    sentry_traces_sample_rate: float = 0.0
    embedding_enabled: bool = False
    embedding_model_name: str = "intfloat/multilingual-e5-base"
    embedding_batch_size: int = 16
    claim_extraction_enabled: bool = False
    claim_extraction_system_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    anthropic_api_key: SecretStr | None = None
    claim_extraction_model: str = Field(
        default="claude-sonnet-5", min_length=1, max_length=255
    )
    claim_extraction_max_chars: int = Field(default=50_000, ge=1_000, le=200_000)
    claim_extraction_segment_overlap_chars: int = Field(default=1_000, ge=0, le=20_000)
    claim_extraction_max_segments: int = Field(default=32, ge=1, le=128)
    claim_extraction_max_proposals_per_run: int = Field(default=500, ge=1, le=5_000)
    claim_extraction_max_output_tokens: int = Field(default=4_096, ge=256, le=16_384)
    claim_extraction_prompt_path: str = "prompts/claim_extraction_v2.txt"
    claim_extraction_rules_path: str = "config/claim_extraction_rules.yaml"
    log_level: str = "INFO"
    outbound_enabled: bool = False
    api_key_pepper: SecretStr = SecretStr("change-me-api-key-pepper")

    @model_validator(mode="after")
    def production_configuration_is_not_placeholder(self) -> Settings:
        if self.claim_extraction_segment_overlap_chars * 4 > self.claim_extraction_max_chars:
            raise ValueError("claim extraction segment overlap cannot exceed 25% of a segment")
        if self.claim_extraction_enabled and self.anthropic_api_key is None:
            raise ValueError("enabled claim extraction requires an Anthropic API key")
        if self.claim_extraction_enabled and self.claim_extraction_system_fingerprint is None:
            raise ValueError("enabled claim extraction requires an approved system fingerprint")
        if self.ais_enabled and self.aisstream_api_key is None:
            raise ValueError("enabled AIS cache requires an AISstream API key")
        if self.ais_enabled and not self.aisstream_url.casefold().startswith("wss://"):
            raise ValueError("enabled AIS cache requires a secure WebSocket URL")
        if self.postmark_server_token is not None and not self.postmark_from_email:
            raise ValueError("Postmark delivery requires a configured sender email")
        if self.desk_auth_mode == "oidc":
            if (
                not self.desk_oidc_issuer
                or not self.desk_oidc_audience
                or not self.desk_oidc_jwks_url
            ):
                raise ValueError("OIDC desk authentication requires issuer, audience, and JWKS URL")
            if not self.desk_oidc_issuer.casefold().startswith("https://"):
                raise ValueError("OIDC desk issuer must use HTTPS")
            if not self.desk_oidc_jwks_url.casefold().startswith("https://"):
                raise ValueError("OIDC desk JWKS URL must use HTTPS")
            parties = self.desk_oidc_authorized_party_set
            if self.environment.casefold() == "production" and not parties:
                raise ValueError("production OIDC requires an authorized web application origin")
            if any(not party.casefold().startswith("https://") for party in parties):
                raise ValueError("OIDC authorized parties must use HTTPS origins")
        if self.environment.casefold() != "production":
            return self
        desk_token = self.desk_api_token.get_secret_value()
        default_desk_token = type(self).model_fields["desk_api_token"].default.get_secret_value()
        if desk_token == default_desk_token or len(desk_token) < 32:
            raise ValueError("production requires a non-placeholder desk API token")
        portal_token = self.portal_api_token.get_secret_value()
        default_portal_token = (
            type(self).model_fields["portal_api_token"].default.get_secret_value()
        )
        if portal_token == default_portal_token or len(portal_token) < 32:
            raise ValueError("production requires a non-placeholder portal API token")
        if "eastmed:eastmed@localhost" in self.database_url:
            raise ValueError("production requires a non-placeholder database URL")
        default_store_secret = (
            type(self).model_fields["object_store_secret_key"].default.get_secret_value()
        )
        if self.object_store_secret_key.get_secret_value() == default_store_secret:
            raise ValueError("production requires a non-placeholder object-store secret")
        if not self.public_base_url.casefold().startswith("https://"):
            raise ValueError("production public_base_url must use HTTPS")
        if self.api_key_pepper.get_secret_value() == "change-me-api-key-pepper":
            raise ValueError("production requires a non-placeholder API-key pepper")
        if self.whatsapp_360dialog_api_key and (
            not self.whatsapp_webhook_username or self.whatsapp_webhook_password is None
        ):
            raise ValueError("production WhatsApp requires Basic-auth webhook credentials")
        whatsapp_url = self.whatsapp_360dialog_base_url.casefold()
        if self.whatsapp_360dialog_api_key and not whatsapp_url.startswith("https://"):
            raise ValueError("production WhatsApp API base URL must use HTTPS")
        if self.desk_auth_mode != "oidc":
            raise ValueError("production requires cryptographically verified OIDC desk identity")
        if self.outbound_enabled and not any(
            (
                self.postmark_server_token,
                self.telegram_customer_bot_token,
                self.whatsapp_360dialog_api_key,
            )
        ):
            raise ValueError(
                "production outbound delivery requires at least one configured provider"
            )
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def allowed_fetch_host_set(self) -> frozenset[str]:
        return frozenset(
            host.strip().lower().rstrip(".")
            for host in self.allowed_fetch_hosts.split(",")
            if host.strip()
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def desk_oidc_authorized_party_set(self) -> frozenset[str]:
        return frozenset(
            party.strip().rstrip("/")
            for party in self.desk_oidc_authorized_parties.split(",")
            if party.strip()
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
