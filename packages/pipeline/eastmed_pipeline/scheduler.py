import argparse
import hashlib
import time
from datetime import datetime
from typing import cast
from zoneinfo import ZoneInfo

from eastmed_api.ai_governance import CLAIM_EXTRACTION_SCOPE, capability_decision
from eastmed_api.database import SessionLocal
from eastmed_shared import configure_error_monitoring, get_settings
from eastmed_shared.logging import configure_logging
from redis import Redis
from rq import Queue

from eastmed_pipeline.claim_extraction import eligible_source_records
from eastmed_pipeline.operations import JobQueue, enqueue_due_sources


def run_once() -> int:
    settings = get_settings()
    connection = Redis.from_url(settings.redis_url)
    queue = Queue("ingestion", connection=connection)
    with SessionLocal() as session:
        runs = enqueue_due_sources(session, queue=cast(JobQueue, queue))
    if connection.set("eastmed:scheduler:watchdog", "1", nx=True, ex=45):
        Queue("ingestion-operations", connection=connection).enqueue(
            "eastmed_pipeline.jobs.dead_poller_watchdog_job",
            job_id=f"dead-poller-watchdog-{int(time.time())}",
            job_timeout=60,
            result_ttl=3600,
            failure_ttl=86400,
        )
    if connection.set("eastmed:scheduler:lineage-shadow", "1", nx=True, ex=300):
        Queue("analysis", connection=connection).enqueue(
            "eastmed_pipeline.jobs.lineage_shadow_scan_job",
            job_id=f"lineage-shadow-{int(time.time())}",
            job_timeout=300,
            result_ttl=86400,
            failure_ttl=86400,
        )
    if connection.set("eastmed:scheduler:triage-backfill", "1", nx=True, ex=60):
        Queue("analysis", connection=connection).enqueue(
            "eastmed_pipeline.jobs.triage_backfill_job",
            job_id=f"triage-backfill-{int(time.time())}",
            job_timeout=120,
            result_ttl=3600,
            failure_ttl=86400,
        )
    if connection.set("eastmed:scheduler:customer-deliveries", "1", nx=True, ex=20):
        Queue("deliveries", connection=connection).enqueue(
            "eastmed_pipeline.jobs.customer_delivery_dispatch_job",
            job_id=f"customer-deliveries-{int(time.time())}",
            job_timeout=120,
            result_ttl=3600,
            failure_ttl=86400,
        )
    if settings.embedding_enabled and connection.set(
        "eastmed:scheduler:embeddings", "1", nx=True, ex=240
    ):
        Queue("embeddings", connection=connection).enqueue(
            "eastmed_pipeline.jobs.source_record_embedding_job",
            job_id=f"source-record-embeddings-{int(time.time())}",
            job_timeout=1800,
            result_ttl=86400,
            failure_ttl=86400,
        )
    claim_extraction_allowed = False
    if settings.claim_extraction_enabled:
        with SessionLocal() as session:
            claim_extraction_allowed = capability_decision(
                session,
                scope=CLAIM_EXTRACTION_SCOPE,
                expected_fingerprint=settings.claim_extraction_system_fingerprint,
            ).allowed
    if claim_extraction_allowed and connection.set(
        "eastmed:scheduler:claim-extraction", "1", nx=True, ex=30
    ):
        with SessionLocal() as session:
            record_ids = eligible_source_records(
                session,
                model_version=settings.claim_extraction_model,
            )
        model_key = hashlib.sha256(settings.claim_extraction_model.encode("utf-8")).hexdigest()[:12]
        analysis_queue = Queue("analysis", connection=connection)
        for record_id in record_ids:
            analysis_queue.enqueue(
                "eastmed_pipeline.jobs.claim_extraction_job",
                str(record_id),
                job_id=f"claim-extraction-{record_id}-{model_key}",
                job_timeout=180,
                result_ttl=86400,
                failure_ttl=86400,
            )
    athens_now = datetime.now(ZoneInfo("Europe/Athens"))
    brief_date = athens_now.date().isoformat()
    if athens_now.hour >= 6 and connection.set(
        f"eastmed:scheduler:daily-brief:{brief_date}", "1", nx=True, ex=172800
    ):
        Queue("publication", connection=connection).enqueue(
            "eastmed_pipeline.jobs.daily_brief_compile_job",
            brief_date,
            job_id=f"daily-brief-{brief_date}",
            job_timeout=120,
            result_ttl=86400,
            failure_ttl=86400,
        )
    return len(runs)


def main() -> None:
    parser = argparse.ArgumentParser(description="Schedule approved East Med source pollers")
    parser.add_argument("--once", action="store_true", help="Run one scheduling pass and exit")
    parser.add_argument("--interval", type=int, default=30, help="Seconds between passes")
    args = parser.parse_args()
    settings = get_settings()
    configure_error_monitoring(settings)
    configure_logging(settings.log_level)
    if args.once:
        run_once()
        return
    while True:
        run_once()
        time.sleep(max(args.interval, 5))
