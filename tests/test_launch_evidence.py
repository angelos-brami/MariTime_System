from __future__ import annotations

from copy import deepcopy

from eastmed_shared.launch_evidence import validate_launch_evidence


def ready_payload() -> dict[str, object]:
    metric = {
        "reviewed": 30,
        "qa_reviewed": 30,
        "precision": 0.98,
        "recall": 0.97,
        "hallucination_rate": 0.02,
        "missed_claim_rate": 0.03,
    }
    return {
        "infrastructure": {
            "terraform_apply_id": "apply-1",
            "migration_task_id": "migration-1",
            "restore_drill_id": "restore-1",
            "monitoring_acceptance_id": "monitor-1",
            "secret_rotation_id": "rotation-1",
        },
        "identity": {
            "provider": "clerk",
            "operators": [
                {
                    "email": "a@example.com",
                    "provider_user_id": "a",
                    "passkey_mfa_evidence_id": "mfa-a",
                },
                {
                    "email": "b@example.com",
                    "provider_user_id": "b",
                    "passkey_mfa_evidence_id": "mfa-b",
                },
            ],
        },
        "sources": {
            "approved_active_count": 60,
            "signed_rights_register_id": "rights-1",
            "source_health_report_id": "health-1",
            "dead_source_drill_id": "dead-1",
        },
        "ai": {
            "provider": "anthropic",
            "provider_contract_id": "contract-1",
            "system_fingerprint": "a" * 64,
            "shadow_days": 60,
            "gold_evaluation_id": "gold-1",
            "languages": {language: deepcopy(metric) for language in ("en", "el", "tr", "ar")},
        },
        "delivery": {
            "outbound_enabled_during_acceptance": False,
            "postmark_acceptance_id": "postmark-1",
            "telegram_acceptance_id": "telegram-1",
            "whatsapp_offered": False,
            "whatsapp_acceptance_id": "NOT_APPLICABLE",
            "retry_duplicate_kill_switch_evidence_id": "delivery-1",
        },
        "legal": {
            "source_rights_approval_id": "rights-1",
            "privacy_notice_approval_id": "privacy-1",
            "terms_approval_id": "terms-1",
            "dpa_approval_id": "dpa-1",
            "customer_contract_id": "customer-1",
            "insurance_policy_id": "policy-1",
            "operational_contacts_test_id": "contacts-1",
        },
    }


def test_complete_distinct_evidence_passes() -> None:
    checks = validate_launch_evidence(ready_payload())
    assert checks
    assert all(check.passed for check in checks)


def test_placeholders_and_duplicate_operators_fail_closed() -> None:
    payload = ready_payload()
    identity = payload["identity"]
    assert isinstance(identity, dict)
    identity["operators"] = [
        {"email": "REPLACE_ME", "provider_user_id": "same", "passkey_mfa_evidence_id": "mfa"},
        {"email": "REPLACE_ME", "provider_user_id": "same", "passkey_mfa_evidence_id": "mfa"},
    ]
    sources = payload["sources"]
    assert isinstance(sources, dict)
    sources["approved_active_count"] = 59
    states = {check.key: check.passed for check in validate_launch_evidence(payload)}
    assert states["identity"] is False
    assert states["sources"] is False


def test_whatsapp_acceptance_is_required_only_when_offered() -> None:
    payload = ready_payload()
    delivery = payload["delivery"]
    assert isinstance(delivery, dict)
    delivery["whatsapp_offered"] = True
    delivery["whatsapp_acceptance_id"] = "REPLACE_ME"
    states = {check.key: check.passed for check in validate_launch_evidence(payload)}
    assert states["delivery"] is False
