from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REQUIRED_LANGUAGES = ("en", "el", "tr", "ar")
PLACEHOLDERS = frozenset({"", "REPLACE_ME", "PENDING", "TODO", "NONE", "NOT_APPLICABLE"})
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class EvidenceCheck:
    key: str
    passed: bool
    detail: str


def _present(value: object) -> bool:
    return isinstance(value, str) and value.strip().upper() not in PLACEHOLDERS


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _metric(value: object, key: str) -> float:
    raw = _mapping(value).get(key)
    return float(raw) if isinstance(raw, int | float) and not isinstance(raw, bool) else -1.0


def validate_launch_evidence(payload: dict[str, Any]) -> list[EvidenceCheck]:
    checks: list[EvidenceCheck] = []

    infrastructure = _mapping(payload.get("infrastructure"))
    infrastructure_fields = (
        "terraform_apply_id",
        "migration_task_id",
        "restore_drill_id",
        "monitoring_acceptance_id",
        "secret_rotation_id",
    )
    infrastructure_ready = all(_present(infrastructure.get(key)) for key in infrastructure_fields)
    checks.append(
        EvidenceCheck(
            "infrastructure",
            infrastructure_ready,
            "applied infrastructure, migrations, restore, monitoring and secret evidence",
        )
    )

    identity = _mapping(payload.get("identity"))
    operators = identity.get("operators")
    operator_rows = operators if isinstance(operators, list) else []
    emails = {
        row.get("email")
        for row in operator_rows
        if isinstance(row, dict) and _present(row.get("email"))
    }
    user_ids = {
        row.get("provider_user_id")
        for row in operator_rows
        if isinstance(row, dict) and _present(row.get("provider_user_id"))
    }
    mfa_rows = sum(
        isinstance(row, dict) and _present(row.get("passkey_mfa_evidence_id"))
        for row in operator_rows
    )
    identity_ready = (
        identity.get("provider") == "clerk"
        and len(operator_rows) >= 2
        and len(emails) >= 2
        and len(user_ids) >= 2
        and mfa_rows >= 2
    )
    checks.append(
        EvidenceCheck(
            "identity",
            identity_ready,
            "two distinct Clerk operators with individual passkey/MFA evidence",
        )
    )

    sources = _mapping(payload.get("sources"))
    approved_count = sources.get("approved_active_count")
    sources_ready = (
        isinstance(approved_count, int)
        and not isinstance(approved_count, bool)
        and approved_count >= 60
        and all(
            _present(sources.get(key))
            for key in (
                "signed_rights_register_id",
                "source_health_report_id",
                "dead_source_drill_id",
            )
        )
    )
    checks.append(
        EvidenceCheck("sources", sources_ready, "60 rights-approved active healthy sources")
    )

    ai = _mapping(payload.get("ai"))
    languages = _mapping(ai.get("languages"))
    language_ready = True
    for language in REQUIRED_LANGUAGES:
        row = _mapping(languages.get(language))
        language_ready = language_ready and (
            _metric(row, "reviewed") >= 30
            and _metric(row, "qa_reviewed") >= 30
            and _metric(row, "precision") >= 0.97
            and _metric(row, "recall") >= 0.97
            and 0 <= _metric(row, "hallucination_rate") <= 0.03
            and 0 <= _metric(row, "missed_claim_rate") <= 0.03
        )
    ai_ready = (
        ai.get("provider") == "anthropic"
        and _present(ai.get("provider_contract_id"))
        and isinstance(ai.get("system_fingerprint"), str)
        and SHA256_PATTERN.fullmatch(ai["system_fingerprint"]) is not None
        and _metric(ai, "shadow_days") >= 60
        and _present(ai.get("gold_evaluation_id"))
        and language_ready
    )
    checks.append(
        EvidenceCheck(
            "ai",
            ai_ready,
            "60-day, dual-reviewed four-language shadow metrics at release thresholds",
        )
    )

    delivery = _mapping(payload.get("delivery"))
    whatsapp_ready = not delivery.get("whatsapp_offered") or _present(
        delivery.get("whatsapp_acceptance_id")
    )
    delivery_ready = (
        delivery.get("outbound_enabled_during_acceptance") is False
        and _present(delivery.get("postmark_acceptance_id"))
        and _present(delivery.get("telegram_acceptance_id"))
        and whatsapp_ready
        and _present(delivery.get("retry_duplicate_kill_switch_evidence_id"))
    )
    checks.append(
        EvidenceCheck(
            "delivery",
            delivery_ready,
            "provider acceptance plus retry, duplicate and kill-switch evidence "
            "while outbound is off",
        )
    )

    legal = _mapping(payload.get("legal"))
    legal_fields = (
        "source_rights_approval_id",
        "privacy_notice_approval_id",
        "terms_approval_id",
        "dpa_approval_id",
        "customer_contract_id",
        "insurance_policy_id",
        "operational_contacts_test_id",
    )
    legal_ready = all(_present(legal.get(key)) for key in legal_fields)
    checks.append(
        EvidenceCheck(
            "legal",
            legal_ready,
            "approved rights/privacy/terms/DPA/contract, bound insurance and tested contacts",
        )
    )
    return checks


def load_launch_evidence(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("production evidence file must contain a mapping")
    return payload
