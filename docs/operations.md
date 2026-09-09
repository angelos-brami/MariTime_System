# Phase 2 ingestion operations

## Scheduler contract

`eastmed-scheduler` examines only active, rights-approved RSS and page/PDF-watch sources whose
next-poll timestamp is due. Each enqueue creates a `poller_runs` row before the worker receives
the job. Success,
not-modified, and failure outcomes are recorded with timestamps and error details. A failed run
increments the source failure counter; a later success clears it.

The watchdog opens a `desk_alerts` record when an approved active source has no successful poll
for more than twice its configured interval. It retries an open Telegram notification and closes
the alert after the source recovers. Telegram is internal-only in this slice and obeys the global
outbound kill switch.

For page/PDF watches, the worker checks rights before its first request, fetches and applies
`robots.txt` fail-closed, then conditionally retrieves the document. HTML is reduced to article
text with Trafilatura; PDFs use the bounded generic PDF extractor. The extracted representation
drives exact-change detection while the raw response is retained as the immutable snapshot.

## Safe source activation

1. Run `eastmed-seed-sources` to create inactive candidates.
2. Validate the endpoint/feed and set its host in `EASTMED_ALLOWED_FETCH_HOSTS`.
3. Record counsel's rights basis, notes, reviewer, and approval timestamp.
4. Activate with `PATCH /api/v1/sources/{id}/operations`.
5. Run one manual poll through `POST /api/v1/sources/{id}/poll-now` and inspect its immutable
   snapshot and security scan.
6. Confirm the poller health endpoint reports `healthy` before relying on the source.

Deactivation is immediate through the same endpoint. Existing records remain immutable.

## Airtable migration

Run `eastmed-import-airtable --dry-run` first and record the returned created/updated/unchanged/
skipped counts. The importer maps stable Airtable record IDs into `external_record_refs`, preserves
changed evidence as new immutable records, and scans imported text as untrusted input. After
review, run without `--dry-run`. Keep the weekly job manual until the field mapping has been
validated against the live base.

## Postmark inbound email

Create an active, rights-approved source with `access_method=email` and a unique
`inbound_mailbox_hash`. Configure the Postmark Inbound Message Stream to POST to
`/api/v1/ingest/postmark` using HTTPS and HTTP Basic authentication, then set the matching
`EASTMED_POSTMARK_INBOUND_USERNAME` and `EASTMED_POSTMARK_INBOUND_PASSWORD` values. Production
must also restrict the route to Postmark's documented webhook IP range at the proxy/firewall.

The adapter validates that the payload belongs to the inbound stream, maps `MailboxHash` to the
approved source, uses `MessageID` for webhook idempotency, stores the parsed message as an
immutable `source_record`, and snapshots the original JSON including attachment content. Text is
scanned as untrusted input. Attachment metadata is indexed; format-specific attachment records
remain part of the archived-sample extractor work.

## Error and uptime monitoring

Set `EASTMED_SENTRY_DSN` to enable server-side exception reporting for the API, scheduler, and RQ
worker. PII collection is disabled and performance tracing defaults to zero; set
`EASTMED_SENTRY_TRACES_SAMPLE_RATE` deliberately if tracing is required.

The local Compose stack includes Uptime Kuma 2 on `127.0.0.1:3001`. On first launch, create an
HTTP monitor for `http://api:8000/health` and route failures to the desk-internal Telegram
channel. Keep the Kuma data volume on a local filesystem with POSIX file locking in production.
Do not mount the Docker socket; HTTP health monitoring does not need daemon control.

## Shadow lineage operations

Run `eastmed-analysis-worker` alongside the ingestion worker. Every five minutes the scheduler
scans the latest 72-hour window and generates pending proposals from three independent signals:
the `syndicates_from_id` registry, recognized wire credit lines, and guarded near-duplicate
similarity. Quarantined or injection-suspected records are excluded.

Analysts review proposals through `/api/v1/lineage/proposals`. Acceptance creates or reuses a
lineage root and writes record assignments; rejection requires a reason. The review row is
append-only. The component cannot graduate unless it has at least 100 reviewed proposals, at
least 97% analyst acceptance precision, and at least 60 days of shadow history. Graduation does
not enable automatic acceptance in this release.

The lightweight default uses token cosine plus MinHash. Optional multilingual-E5 embeddings are
stored in immutable, model-versioned rows—not written back into source records. Enable them only
in the dedicated embedding worker with `EASTMED_EMBEDDING_ENABLED=true`. Model downloads should
be pre-cached into the production image; runtime analysis containers should not have general
internet egress.

## Analyst triage console

Set `EASTMED_CONSOLE_USERNAME` and `EASTMED_CONSOLE_PASSWORD` in the Next.js runtime, along with
the server-only `EASTMED_API_BASE_URL` and `EASTMED_DESK_API_TOKEN`. The browser authenticates to
`/console` with HTTP Basic authentication. Next.js route handlers repeat that authentication
check, call the desk API with the token on the server, and never expose the token through a
`NEXT_PUBLIC_` variable. Missing console credentials fail closed with 503.

Each immutable `source_record` receives one pending `triage_item`. Ingestion creates it inline;
the analysis worker also runs a minute-level backfill for records that predate the queue or were
created during a concurrent insert. Deterministic gazetteer and keyword matches are suggestions
only. Analysts use `J/K` to navigate, `A` to attach to an existing event, `E` to create a
monitoring event, and `R` to dismiss with a required reason.

Every resolution updates the queue state and writes both an append-only `triage_decisions` row
and an `audit_log` entry in one database transaction. Reviewers beginning with `model:` are
rejected. The explicit `EASTMED_CONSOLE_DEMO=true` mode serves local UI fixtures and must never be
enabled in a deployed desk.

## Event workspace and publication gate

Use `/console/events` to select an open event. The workspace reads the event header, reviewed
claims, assigned source records, linked evidence, and all immutable versions from
`GET /api/v1/events/{event_id}/workspace`. Manual claims and state changes require a named human
reviewer and write `audit_log` entries. Linking a source as evidence captures its lineage root,
snapshot reference, directness, and current rights decision. A record must already be assigned to
the event through triage before it can be linked.

The composer treats every entered sentence as material. Each sentence must map to one or more
claims, and the public section must agree with the claim state. Confirmed claims require selected
evidence; Reported and Unknown sections are the explicit labels for claims without verified
evidence. Model-proposed claims require human review, sensitivity flags require a distinct second
reviewer, and severity 3–4 versions require a named human sign-off.

Publishing is a two-request protocol:

1. POST the complete draft to `/api/v1/events/{event_id}/versions/preview` and review every gate
   result plus the before/after diff.
2. POST the identical draft and returned `preview_hash` to
   `/api/v1/events/{event_id}/versions`.

The API locks the event, rebuilds the canonical snapshot, and rejects the request if the prior
version, mappings, evidence, claims, policy, or reviewers changed after preview. Successful
publication writes the immutable `event_version`, exact join sets, embedded snapshots, chained
content hash, and append-only audit entry in one transaction.

## Customer watch profiles and alert release

Use `/console/alerts` during the customer onboarding call. Select the account, define at least
one corridor, event type, port/UNLOCODE, or custom GeoJSON area, agree the minimum severity, and
confirm the recipients and channels. Activate only after recording the named desk reviewer,
customer-configuration timestamp, and successful channel tests. Profiles that have not completed
this flow remain inactive and should be treated as onboarding and churn risk, not as a usable
alert audience.

The alert composer accepts only a published immutable event version. Preview the audience before
release and inspect every matched account, profile ID, recipient count, channel count, alert count
for the UTC day, tier cap, and throttle reason. The current versioned defaults live in
`config/alert_rules.yaml`: Watch is S3+ and 3 alerts/day; Desk is S2+ and 8/day; Desk Pro is S1+
and 12/day; Data is S1+ and 20/day. Severity 4 bypasses the daily cap. Changing these values is a
policy change and requires review, tests, and a rules-version change.

Release is always a named human action, including severity 4. One event version may be released
only once. An idempotent retry must use the same analyst and channel set; the API returns the
original receipt. A different analyst or channel set is rejected rather than silently changing
the immutable alert. To change customer-facing content, publish another event version and run a
new audience preview.

## Customer delivery operations

Configure the delivery providers in the worker environment:

- `EASTMED_POSTMARK_SERVER_TOKEN`, `EASTMED_POSTMARK_FROM_EMAIL`, and optionally
  `EASTMED_POSTMARK_MESSAGE_STREAM` for email.
- `EASTMED_TELEGRAM_CUSTOMER_BOT_TOKEN` for the customer bot. Each opted-in Telegram user also
  needs a `chat_id` in `users.channels_json`.
- `EASTMED_PUBLIC_BASE_URL` for links in the immutable message template.
- `EASTMED_DELIVERY_MAX_ATTEMPTS` for the retry ceiling.

Keep `EASTMED_OUTBOUND_ENABLED=false` through setup. Run `eastmed-worker`, confirm it listens to
the `deliveries` queue, test the providers with a non-production account, and only then enable the
switch. Disabling the switch stops new attempts without deleting or failing queued deliveries.
Provider failures persist a bounded error, attempt count, last-attempt time, and exponentially
backed-off next-attempt time; exhausted deliveries move to `failed` for operator investigation.

`GET /api/v1/alerts/delivery-dashboard` and the console dashboard report status/channel totals,
recent attempts, publish-to-delivery p95, and the percentage completed within 60 seconds. In v1,
`delivered_at` means provider acceptance, not inbox read or Telegram view. Investigate p95 at or
above 60 seconds by comparing version publication, alert creation, queue scheduling, attempt, and
provider-acceptance timestamps. Never edit alert snapshots to repair a failed delivery; repair
provider configuration or retry delivery state while preserving the original alert.

## Subscriber portal and daily brief

Configure `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` and `CLERK_SECRET_KEY` only in the Next.js runtime.
Set one high-entropy `EASTMED_PORTAL_API_TOKEN` in both Next.js and FastAPI; it is server-only and
must never use a `NEXT_PUBLIC_` name. Clerk authenticates the browser, and the Next.js server
forwards only Clerk's verified user ID plus the internal token. FastAPI then maps that ID to a
locally provisioned user and requires `active=true`, `portal_enabled=true`, and a contract whose
start/end dates include the current UTC date.

Provision or revoke a subscriber in the account panel on `/console/alerts`, or call
`PATCH /api/v1/users/{user_id}/portal-access`. Portal enablement is deliberately separate from
the user's general active flag so revoking website access does not silently change alert
delivery. Reconcile Clerk users against local access after customer offboarding and contract
renewal changes.

The public root board reads `/api/v1/public/board`; subscriber routes read the same latest
immutable event versions with additional detail. Archive queries use `websearch_to_tsquery` over
the generated `event_versions.search_document` column and its GIN index. Event source excerpts
are returned only when the evidence snapshot recorded `may_publish_excerpt=true`; verify-only
sources retain attribution and lineage without copied text.

At or after 06:00 Europe/Athens, the scheduler enqueues one draft compilation per calendar day.
The job is idempotent while the brief remains a draft and can be refreshed from
`/console/briefs`. A named analyst chooses the compiled version set, writes the introduction and
forward-watch note, and finalizes. The API rejects model actors and versions outside the compiled
set. A database trigger prevents update or deletion after finalization.

`EASTMED_PORTAL_DEMO=true` serves local subscriber fixtures without Clerk. Treat it like the desk
demo switch: never enable it in a deployed environment.

## Quality, corrections, Stage 3, and drills

`/console/quality` owns the source-qualified TTV clock, exact-timestamp PDF reports, two-person
operational correction workflow, public scoreboard, and versioned port/strike calendar. A
correction preview pins the exact version hashes and recipients; issue rejects any altered or
stale preview. Corrections route only to channels on which the original alert reached provider
delivery, then appear on the portal and next non-finalized brief.

360dialog provider acceptance leaves WhatsApp delivery in `sent`. The Basic-authenticated webhook
stores sanitized, idempotent receipts and alone moves it to `delivered` or `failed`. Explicit
customer opt-in is required before a WhatsApp destination is eligible. See R8 for template and
webhook setup.

`/console/data` issues one-time, account-scoped Bearer keys only to Data-tier accounts. The server
stores an HMAC digest using the deployment pepper. Read scopes cover events, immutable versions,
claims, calendar items, and AIS context; no key can mutate or publish. OpenAPI is served at
`/api/v1/openapi.json` and interactive docs at `/api/v1/docs`.

The optional `eastmed-ais-cache` command connects to AISstream over WSS from the backend, subscribes
only to the configured corridor boxes, and retains each vessel's latest position. Every API and
portal rendering carries the interference/spoofing caveat. AIS is physical-context support only
and cannot promote a claim state.

Run R9’s injection and kill-switch drills monthly, its load pass before release and material
traffic changes, and the encrypted restore drill weekly. `operational_drills` stores immutable
results and evidence hashes when `--record` is used. Production workers must use the separate
least-privilege commands and database roles documented under `infra/`.

## Claim-extraction shadow operations

The extraction scheduler is off unless `EASTMED_CLAIM_EXTRACTION_ENABLED=true`. Before enabling,
record a separate model-processing approval on each eligible source, configure the Anthropic API
key only in worker/server environments, and confirm `config/claim_extraction_rules.yaml` remains
in `shadow` mode. The analysis worker receives extraction jobs; no customer or publication queue
is involved.

Analysts work the pending queue at `/console/extraction`. Every accept/edit decision requires an
analyst-selected event and claim state; edit/reject requires reason codes. In shadow mode these
actions record immutable reviews but do not create canonical claims. An independent human should
record QA immediately. Run the per-language evaluation weekly and use its reason-code report for
versioned prompt revisions. A missing manual-control baseline, fewer than 60 days, insufficient
samples, less than 50% time reduction, or any increase over the manual error rate blocks
graduation. Full boundary and endpoint details are in `docs/claim-extraction-shadow.md`.
