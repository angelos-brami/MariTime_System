from datetime import date
from uuid import UUID

from eastmed_api.ai_governance import CLAIM_EXTRACTION_SCOPE, capability_decision
from eastmed_api.briefs import compile_daily_brief
from eastmed_api.database import SessionLocal
from eastmed_shared import get_settings

from eastmed_pipeline.claim_extraction import (
    build_claim_extraction_provider,
    process_source_record,
)
from eastmed_pipeline.delivery import dispatch_pending_corrections, dispatch_pending_deliveries
from eastmed_pipeline.embeddings import build_embedding_provider, generate_missing_embeddings
from eastmed_pipeline.lineage import generate_lineage_proposals
from eastmed_pipeline.notifications import build_desk_notifier
from eastmed_pipeline.operations import detect_dead_pollers, execute_poller_run
from eastmed_pipeline.triage import backfill_triage_items


def run_source_poll_job(run_id: str) -> int:
    with SessionLocal() as session:
        return execute_poller_run(session, run_id=UUID(run_id))


def dead_poller_watchdog_job() -> int:
    with SessionLocal() as session:
        opened = detect_dead_pollers(session, notifier=build_desk_notifier())
        return len(opened)


def lineage_shadow_scan_job() -> int:
    settings = get_settings()
    with SessionLocal() as session:
        return generate_lineage_proposals(
            session,
            embedding_model_version=(
                settings.embedding_model_name if settings.embedding_enabled else None
            ),
        )


def source_record_embedding_job() -> int:
    settings = get_settings()
    if not settings.embedding_enabled:
        return 0
    with SessionLocal() as session:
        return generate_missing_embeddings(
            session,
            provider=build_embedding_provider(settings),
        )


def triage_backfill_job() -> int:
    with SessionLocal() as session:
        return backfill_triage_items(session)


def customer_delivery_dispatch_job() -> int:
    with SessionLocal() as session:
        delivered = dispatch_pending_deliveries(session)
        delivered += dispatch_pending_corrections(session)
        return delivered


def daily_brief_compile_job(brief_date: str) -> str:
    parsed_date = date.fromisoformat(brief_date)
    with SessionLocal() as session:
        brief = compile_daily_brief(
            session,
            brief_date=parsed_date,
            compiled_by="scheduler",
        )
        return str(brief.id)


def claim_extraction_job(source_record_id: str) -> str:
    settings = get_settings()
    if not settings.claim_extraction_enabled:
        return "disabled"
    with SessionLocal() as session:
        decision = capability_decision(
            session,
            scope=CLAIM_EXTRACTION_SCOPE,
            expected_fingerprint=settings.claim_extraction_system_fingerprint,
        )
        if not decision.allowed:
            return f"governance-disabled:{decision.reason}"
        run = process_source_record(
            session,
            source_record_id=UUID(source_record_id),
            provider=build_claim_extraction_provider(settings),
            settings=settings,
        )
        return run.status.value
