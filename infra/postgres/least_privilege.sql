-- Run as the database owner after every migration. These are group roles;
-- deployment login roles receive only the membership required by their service.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'eastmed_readonly') THEN
    CREATE ROLE eastmed_readonly NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'eastmed_scheduler') THEN
    CREATE ROLE eastmed_scheduler NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'eastmed_ingestion') THEN
    CREATE ROLE eastmed_ingestion NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'eastmed_analysis') THEN
    CREATE ROLE eastmed_analysis NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'eastmed_publication') THEN
    CREATE ROLE eastmed_publication NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'eastmed_brief_compiler') THEN
    CREATE ROLE eastmed_brief_compiler NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'eastmed_delivery') THEN
    CREATE ROLE eastmed_delivery NOLOGIN;
  END IF;
END
$$;

DO $$
BEGIN
  EXECUTE format(
    'GRANT CONNECT ON DATABASE %I TO eastmed_readonly, eastmed_scheduler, '
    'eastmed_ingestion, eastmed_analysis, eastmed_publication, '
    'eastmed_brief_compiler, eastmed_delivery',
    current_database()
  );
END
$$;
GRANT USAGE ON SCHEMA public TO eastmed_readonly, eastmed_scheduler,
  eastmed_ingestion, eastmed_analysis, eastmed_publication,
  eastmed_brief_compiler, eastmed_delivery;

-- Make this script convergent after earlier, broader grants. The monitoring
-- reader intentionally excludes customer identities, destinations, API key
-- hashes, raw source text and audit payloads.
REVOKE SELECT ON ALL TABLES IN SCHEMA public FROM eastmed_readonly, eastmed_analysis;
REVOKE UPDATE ON sources, poller_runs, desk_alerts FROM eastmed_ingestion;
REVOKE UPDATE ON deliveries, correction_deliveries FROM eastmed_delivery;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM eastmed_brief_compiler;

GRANT SELECT ON sources, poller_runs, events, event_versions, daily_briefs,
  ttv_log, emev_log, operational_drills, maritime_calendar_events,
  maritime_calendar_event_versions, ais_position_cache, pipeline_evaluations,
  subscription_metrics TO eastmed_readonly;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO eastmed_publication;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO eastmed_publication;

-- The background publication queue only compiles a draft brief. It cannot
-- create event versions, finalize briefs, create alerts or enqueue delivery.
GRANT SELECT ON event_versions, daily_briefs, corrections, events
  TO eastmed_brief_compiler;
GRANT INSERT, UPDATE ON daily_briefs TO eastmed_brief_compiler;
GRANT INSERT ON audit_log TO eastmed_brief_compiler;

CREATE OR REPLACE FUNCTION enforce_brief_compiler_draft()
RETURNS trigger AS $$
BEGIN
  IF pg_has_role(current_user, 'eastmed_brief_compiler', 'member')
     AND NOT pg_has_role(current_user, 'eastmed_publication', 'member')
     AND (
       NEW.status <> 'draft'
       OR NEW.finalized_at IS NOT NULL
       OR NEW.finalized_by IS NOT NULL
     ) THEN
    RAISE EXCEPTION 'brief compiler cannot finalize a daily brief';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS daily_briefs_compiler_draft_only ON daily_briefs;
CREATE TRIGGER daily_briefs_compiler_draft_only
BEFORE INSERT OR UPDATE ON daily_briefs
FOR EACH ROW EXECUTE FUNCTION enforce_brief_compiler_draft();

GRANT SELECT ON sources, source_records, poller_runs, claim_extraction_runs
  TO eastmed_scheduler;
GRANT INSERT, UPDATE ON poller_runs TO eastmed_scheduler;
GRANT UPDATE (next_poll_at) ON sources TO eastmed_scheduler;
GRANT INSERT ON audit_log TO eastmed_scheduler;

GRANT SELECT ON sources, source_records, poller_runs, triage_items, desk_alerts
  TO eastmed_ingestion;
GRANT INSERT ON source_records, triage_items, desk_alerts, audit_log TO eastmed_ingestion;
GRANT UPDATE (etag, last_modified, last_successful_poll_at, last_poll_started_at,
  next_poll_at, consecutive_failures, last_poll_error, updated_at) ON sources
  TO eastmed_ingestion;
GRANT UPDATE (status, started_at, finished_at, records_created, worker_job_id,
  error_type, error_message) ON poller_runs TO eastmed_ingestion;
GRANT UPDATE (status, notified_at, resolved_at, detail, notification_error)
  ON desk_alerts TO eastmed_ingestion;

GRANT SELECT ON sources, source_records, triage_items, source_record_embeddings,
  lineage_proposals, lineage_reviews, source_record_lineage, lineage_roots,
  events, claims, evidence, claim_relations, claim_extraction_runs,
  claim_extraction_proposals, claim_extraction_reviews, claim_extraction_qa,
  pipeline_evaluations, desk_alerts TO eastmed_analysis;
GRANT INSERT, UPDATE ON lineage_proposals, source_record_embeddings, triage_items,
  claim_extraction_runs, claim_extraction_proposals, desk_alerts TO eastmed_analysis;
GRANT INSERT ON audit_log TO eastmed_analysis;

GRANT SELECT ON alerts, deliveries, corrections, correction_deliveries TO eastmed_delivery;
GRANT UPDATE (sent_at, delivered_at, status, attempt_count, last_attempt_at,
  next_attempt_at, last_error, provider_ref, updated_at) ON deliveries
  TO eastmed_delivery;
GRANT UPDATE (sent_at, delivered_at, status, attempt_count, last_attempt_at,
  next_attempt_at, last_error, provider_ref, updated_at) ON correction_deliveries
  TO eastmed_delivery;

-- Publication authority must never leak into a worker membership.
REVOKE INSERT ON event_versions FROM PUBLIC, eastmed_readonly, eastmed_scheduler,
  eastmed_ingestion, eastmed_analysis, eastmed_delivery;
REVOKE INSERT ON corrections FROM PUBLIC, eastmed_readonly, eastmed_scheduler,
  eastmed_ingestion, eastmed_analysis, eastmed_brief_compiler, eastmed_delivery;
REVOKE INSERT ON event_versions FROM eastmed_brief_compiler;

ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE SELECT ON TABLES FROM eastmed_readonly;
