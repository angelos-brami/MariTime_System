from eastmed_schema import Base

from apps.api.alembic.snapshots import v0001_models  # noqa: F401
from apps.api.alembic.snapshots.v0001_base import Base as SnapshotBase


def test_initial_migration_snapshot_remains_a_subset_of_current_schema() -> None:
    assert set(SnapshotBase.metadata.tables) <= set(Base.metadata.tables)
    for table_name, table in SnapshotBase.metadata.tables.items():
        assert set(table.columns.keys()) <= set(Base.metadata.tables[table_name].columns.keys())


def test_publication_snapshot_columns_are_in_the_migration() -> None:
    columns = SnapshotBase.metadata.tables["event_versions"].columns
    assert "event_snapshot_json" in columns
    assert "claim_snapshot_json" in columns
    assert "evidence_snapshot_json" in columns


def test_lineage_shadow_tables_are_in_the_migration() -> None:
    tables = SnapshotBase.metadata.tables
    assert "lineage_proposals" in tables
    assert "lineage_reviews" in tables
    assert "source_record_lineage" in tables
    assert "source_record_embeddings" in tables
    assert "embedding" not in tables["source_records"].columns
    assert "embedding" in tables["source_record_embeddings"].columns


def test_triage_tables_are_in_the_migration() -> None:
    tables = SnapshotBase.metadata.tables
    assert "triage_items" in tables
    assert "triage_decisions" in tables
    assert "detected_ports" in tables["triage_items"].columns
    assert "payload_json" in tables["triage_decisions"].columns


def test_alert_delivery_columns_are_in_the_migration() -> None:
    tables = SnapshotBase.metadata.tables
    assert "active" in tables["watch_profiles"].columns
    assert "audience_snapshot_json" in tables["alerts"].columns
    assert "queued_at" in tables["deliveries"].columns
    assert "attempt_count" in tables["deliveries"].columns
    assert "next_attempt_at" in tables["deliveries"].columns
    assert "account_id" in tables["deliveries"].columns
    assert "recipient" in tables["deliveries"].columns


def test_portal_v1_columns_are_in_the_canonical_schema() -> None:
    tables = Base.metadata.tables
    assert "daily_briefs" in tables
    assert "auth_subject" in tables["users"].columns
    assert "active" in tables["users"].columns
    assert "portal_enabled" in tables["users"].columns
    assert "search_document" in tables["event_versions"].columns


def test_claim_extraction_shadow_tables_are_in_the_canonical_schema() -> None:
    tables = Base.metadata.tables
    assert "model_processing_approved_at" in tables["sources"].columns
    assert "model_processing_approved_by" in tables["sources"].columns
    assert "claim_extraction_runs" in tables
    assert "claim_extraction_proposals" in tables
    assert "claim_extraction_reviews" in tables
    assert "claim_extraction_qa" in tables
    assert "coverage_complete" in tables["claim_extraction_runs"].columns
    assert "segment_manifest_json" in tables["claim_extraction_runs"].columns
    assert "source_start" in tables["claim_extraction_proposals"].columns


def test_quality_and_correction_tables_are_in_the_canonical_schema() -> None:
    tables = Base.metadata.tables
    assert "correction_deliveries" in tables
    assert "state_knowledge_reports" in tables
    assert "preview_hash" in tables["corrections"].columns
    assert "correction_approval_id" in tables["corrections"].columns
    assert "version_from_id" in tables["corrections"].columns
    assert "version_to_id" in tables["corrections"].columns
    assert "corrections_json" in tables["daily_briefs"].columns
    assert "created_by" in tables["ttv_log"].columns


def test_stage3_and_hardening_tables_are_in_the_canonical_schema() -> None:
    tables = Base.metadata.tables
    assert "account_api_keys" in tables
    assert "whatsapp_webhook_receipts" in tables
    assert "operational_drills" in tables
    assert "maritime_calendar_events" in tables
    assert "maritime_calendar_event_versions" in tables
    assert "ais_position_cache" in tables
    assert "secret_hash" in tables["account_api_keys"].columns
    assert "payload_hash" in tables["ais_position_cache"].columns


def test_s11_ai_governance_tables_are_in_the_canonical_schema() -> None:
    tables = Base.metadata.tables
    assert "desk_users" in tables
    assert "desk_approvals" in tables
    assert "ai_system_versions" in tables
    assert "ai_capability_controls" in tables
    assert "auth_subject" in tables["desk_users"].columns
    assert "fingerprint" in tables["ai_system_versions"].columns
    assert "revision" in tables["ai_capability_controls"].columns
    assert "previous_control_id" in tables["ai_capability_controls"].columns
    assert "expires_at" in tables["ai_capability_controls"].columns
    assert "publication_approval_id" in tables["event_versions"].columns
    assert "second_review_approval_id" in tables["claims"].columns
    assert "desk_approval_requests" in tables
    assert "request_hash" in tables["desk_approval_requests"].columns
    assert "ai_incidents" in tables
    assert "ai_incident_events" in tables
    assert "containment_action" in tables["ai_incident_events"].columns
