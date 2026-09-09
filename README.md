# East Med Maritime Event Intelligence

Phase 2 foundation for a source-traced maritime event-intelligence desk. The system is intentionally a small monolith: PostgreSQL, a Python/FastAPI service, Redis-backed jobs, and a Next.js web application.

## What this foundation enforces

- Source records are immutable and content-addressed.
- Automated fetching is denied until a source has a recorded rights approval.
- Every published event version embeds its exact claim and evidence state and records the join set.
- Severity 3 and 4 publications require human sign-off.
- Confidence assignment and corrections remain human-controlled.
- Customer watch profiles cannot activate until a named analyst records customer onboarding.
- Alert audiences are previewed before a named human releases an immutable event version.
- Email, Telegram, and opted-in WhatsApp deliveries are deduplicated, retry-safe, tier-throttled, receipt-aware, and kill-switch-aware.
- Subscriber identity is bound to active customer contracts and separately provisioned Clerk users.
- Portal search, event pages, corridor boards, and finalized briefs read only published versions.
- Audit records are append-only.
- Extracted documents are treated as untrusted input and scanned for instruction-like content.
- Claim extraction is schema-bound, quote-grounded, and shadow-only until each language meets
  the configured time, QA, error-rate, and duration gates.
- TTV (time-to-verified) and EMEV (analyst minutes per event version) are first-class records.
- Timestamped state-of-knowledge PDFs, correction propagation, and a public quality scoreboard are backed by immutable versions.
- Data-tier API keys are account-scoped, HMAC-stored, revocable, and limited to explicit read scopes.
- AISstream data is a thin corridor cache with a mandatory interference/spoofing caveat, never a claim-confirmation engine.
- Desk API access is bound to registered identities and roles; AI system fingerprints and
  capability controls are immutable, append-only, and fail closed.

## Repository map

```text
apps/api/             FastAPI application and Alembic migrations
apps/web/             Next.js public board and authenticated editorial console
packages/schema/      SQLAlchemy canonical data model
packages/pipeline/    Rights-aware RSS/page/PDF ingestion and operations
packages/shared/      Settings, logging, and shared constants
infra/                Local services and deployment configuration
runbooks/             Operational procedures
scripts/              Local setup and verification helpers
tests/                Backend unit tests
```

## Local setup

1. Copy `.env.example` to `.env` and replace all placeholder secrets.
2. Start PostgreSQL, Redis, and MinIO with `docker compose up -d postgres redis minio`.
3. Create a Python 3.12 virtual environment and run `pip install -e ".[dev]"`.
4. Run `alembic -c apps/api/alembic.ini upgrade head`.
5. Bootstrap the first registered desk identity as described in `docs/ai-governance-s11.md`.
6. Start the API with `uvicorn eastmed_api.main:app --reload`.
7. Start `eastmed-ingestion-worker`, `eastmed-publication-worker`, `eastmed-delivery-worker`, `eastmed-analysis-worker`, and `eastmed-scheduler`.
8. Run `pnpm install`, then `pnpm dev:web` for the web application.

To run the service stack in containers, use `docker compose up --build`. The API is on port
8000; the worker and scheduler have no public ports.

## Sprint 0 migration and source registry

- `eastmed-import-airtable --dry-run` reads `SOURCES`, `EVENTS`, `CLAIMS`, `EVIDENCE`, and
  `EMEV-LOG` using `EASTMED_AIRTABLE_TOKEN` and `EASTMED_AIRTABLE_BASE_ID`. Re-running the
  importer is idempotent; omit `--dry-run` only after reviewing the counts.
- `eastmed-seed-sources` inserts the 20 initial Tier-A candidates as inactive and unapproved.
  Seeding never authorizes a fetch.
- A named rights reviewer activates a candidate through
  `PATCH /api/v1/sources/{id}/operations`; the endpoint refuses activation without both the
  reviewer and approval timestamp.
- New sources created through the API are also inactive until that explicit activation step.
- `GET /api/v1/operations/pollers` and `GET /api/v1/operations/desk-alerts` expose scheduler
  health to the authenticated desk.

Telegram dead-poller notifications require credentials and `EASTMED_OUTBOUND_ENABLED=true`.
The outbound kill switch defaults to false.

Postmark inbound email uses `POST /api/v1/ingest/postmark` with HTTP Basic authentication and
maps each inbound `MailboxHash` to an approved email source. See `docs/operations.md` before
configuring the public webhook.

Lineage assistance runs in shadow mode. `GET /api/v1/lineage/proposals` exposes suggestions for
analyst review; no proposal changes evidence lineage until a human accepts it. The default scorer
uses credit lines, the source syndication registry, token cosine, and MinHash. To add local
multilingual-E5 embeddings, install `.[embeddings]`, enable the embedding settings, and run
`eastmed-embedding-worker`. In Compose use `docker compose --profile embeddings up --build`.

The analyst queue is available at `/console` after configuring the console username/password,
server-side API base URL, and desk token. New source records are queued with deterministic port,
corridor, and event-type suggestions. Attach, new-event, and dismissal actions remain human-only
and write immutable decision and audit records. See `docs/operations.md` for the credential and
deployment boundary.

Open events are available at `/console/events`. Each workspace exposes reviewed claims, assigned
source records, one-click claim-to-evidence linking, the immutable version timeline, and a
sentence-level composer. Analysts must generate a server-side diff preview before publishing;
the returned hash binds the exact title, sentence mappings, claim/evidence snapshots, policy,
reviewers, and previous version. A stale or altered preview is rejected at the publish endpoint.

Customer alert operations are available at `/console/alerts`. A desk analyst configures each
watch profile with the customer, records the reviewer and onboarding timestamp, and tests the
customer's selected channels before activation. The alert composer previews matching profiles,
affected accounts and users, delivery counts, and any tier throttle before the human release
action becomes available. One immutable event version can produce only one alert; repeat release
requests by the same analyst and channel set return the original delivery receipt.

The delivery worker fans out through Postmark email and a customer Telegram bot. Configure
`EASTMED_POSTMARK_SERVER_TOKEN`, `EASTMED_POSTMARK_FROM_EMAIL`,
`EASTMED_TELEGRAM_CUSTOMER_BOT_TOKEN`, and `EASTMED_PUBLIC_BASE_URL`, then explicitly set
`EASTMED_OUTBOUND_ENABLED=true`. The global kill switch defaults to false and leaves queued
deliveries untouched. The dashboard reports publish-to-provider-acceptance p95 against the
60-second v1 target; provider failures retain attempt state and use bounded exponential retries.

The customer portal is available at `/portal`. Configure Clerk's publishable and secret keys in
the Next.js runtime, use the same server-only `EASTMED_PORTAL_API_TOKEN` in Next.js and FastAPI,
then bind each provisioned Clerk user ID from `/console/alerts`. Portal access additionally
requires an active account contract and an active user record. `/portal/archive` searches the
latest published version of each event through a generated Postgres `tsvector` and GIN index.
The scheduler compiles the Athens calendar day's published versions into a draft after 06:00;
analysts review and finalize it at `/console/briefs`. Finalized briefs pin exact version IDs and
are immutable.

Claim-extraction assistance is available at `/console/extraction`. It is disabled by default.
Before a record is eligible, its source must be active and have a separate named human model-
processing approval. When enabled, the analysis worker sends only the bounded source document,
the versioned `claim_extraction_v2` prompt, complete-document segment coverage, and a strict JSON
schema to the configured Anthropic
Sonnet-class model. The model has no tools, does not browse, cannot assign confidence or truth,
and every proposed claim must carry a verbatim quote found in the supplied document. Injection
flags create a desk quarantine alert and produce no proposals.

Analysts accept, edit, or reject proposals with timing and reason codes. Shadow decisions create
no `claims` row and cannot publish. Independent QA is append-only; evaluation is per language and
cannot graduate while the real manual-control error baseline is unset. Configure
`EASTMED_ANTHROPIC_API_KEY`, review `config/claim_extraction_rules.yaml`, then set
the registered fingerprint in `EASTMED_CLAIM_EXTRACTION_SYSTEM_FINGERPRINT`. Enabling the feature
flag is still insufficient: both the global and claim-extraction database controls must permit the
same fingerprint. See `docs/claim-extraction-shadow.md` and `docs/ai-governance-s11.md` for the
boundary and operating procedure.

Phase 2’s quality and Stage 3 surfaces are available at `/console/quality` and `/console/data`.
The subscriber portal includes the correction ledger, quality scoreboard, state-of-knowledge PDF
request desk, versioned port/strike calendar, and thin AIS corridor context. Configure 360dialog,
the account API, and AISstream using `runbooks/R8-stage3-integrations.md`; run and record the
restore, kill-switch, injection, and load procedures in `runbooks/R9-hardening-drills.md`.

Before a production release, apply the group-role grants in
`infra/postgres/least_privilege.sql`, run daily encrypted backups with
`scripts/backup_database.py`, and complete `docs/security-compliance-checklist.md`. External
gates—source-rights counsel review, processor DPAs, contract clauses, E&O coverage, secret-manager
configuration, and network-level egress enforcement—cannot be satisfied by application code and
must have named evidence owners.

On Windows, `scripts/setup.ps1` and `scripts/verify.ps1` provide the equivalent commands.

## Deliberate limits

This is the start of Phase 2, not an autonomous newsroom. The ingestion worker may collect only
counsel-approved sources and cannot assign confidence, publish, or release customer alerts.
Publishing and customer release remain separate, explicit human actions behind coded policy
gates. The candidate registry is deliberately not live until counsel review, allowlist
configuration, and source-specific validation are complete.
