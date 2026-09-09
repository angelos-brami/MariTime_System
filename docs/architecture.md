# Phase 2 foundation architecture

## Trust boundaries

```text
approved source -> restricted fetcher -> immutable source_record -> analyst review
                                                            |
                                                            v
                                                  claim/evidence graph
                                                            |
                                                   coded publish gate
                                                            |
                                                  version + audit snapshot
                                                            |
                                                  human alert preview/release
                                                            |
                                               retry-safe channel deliveries
```

The ingestion boundary cannot publish. Source text is untrusted data. It is content-addressed, scanned, and stored with the rights decision that allowed collection. Model-assisted components will receive no tools or network access and must return schema-valid proposals.

The publication boundary is separate. It verifies that every sentence maps to claims, claim states match their public sections, confirmed claims have evidence, model proposals have human review, sensitive claims have a second review, and severity 3–4 events have human sign-off.

## Data invariants

1. `source_records` are never updated in place. A changed upstream item becomes another record.
2. A source needs both `automation_approved_at` and `rights_reviewed_by` before any fetch.
3. `event_versions` are immutable publication snapshots with exact claim/evidence join sets and embedded copies of their editorial state.
4. The publication hash is computed over canonical content, sentence-to-claim mappings, exact
   claim/evidence snapshots, reviewer identities, policy/model versions, and the previous version
   hash. The publish endpoint accepts only the unchanged server-previewed hash.
5. `audit_log` rejects update and delete operations at the database layer.
6. TTV timestamps distinguish signal, holding line, and verified update.
7. EMEV records analyst time by task and event version.
8. Derived embeddings are immutable and model-versioned separately from source records.
9. Lineage proposals have no effect until an analyst creates an immutable accept/reject review.
10. Triage state is mutable, but each analyst resolution is preserved in immutable
    `triage_decisions` and `audit_log` rows.
11. Material edits invalidate an existing second review; a sensitive claim must be reviewed again
    by a distinct analyst before publication.
12. An active watch profile requires a named analyst, a recorded customer-configuration time,
    and at least one corridor, event type, port, or custom PostGIS geofence.
13. Watch matching is deterministic over the immutable event version's event metadata, assigned
    triage ports, and PostGIS intersection. Tier severity floors and daily caps are policy-file
    inputs; severity 4 bypasses the daily cap under the versioned v1 rule.
14. Alert release is human-only and unique per immutable event version. The alert row preserves
    the exact message, audience, channel set, policy version, and throttle decisions presented in
    preview. Database triggers reject alert update and delete operations.
15. A delivery is unique per alert, user, and channel. Provider attempts mutate only delivery
    state; they never alter the alert snapshot. The global outbound kill switch defaults off and
    leaves queued rows available for later dispatch.

## Current slice

Implemented: schema and frozen baseline migration, API shell, rights registry and activation gate,
safe RSS and robots-aware page/PDF ingestion, PDF email attachments with quarantine, local
snapshot store, RQ workers and scheduler, poll telemetry, dead-poller desk alerts, a
kill-switch-aware Telegram notifier, idempotent Airtable importer, disabled 20-source Tier-A
candidate registry, shadow lineage proposals and immutable analyst reviews, optional
model-versioned embeddings, authenticated triage queue and audited analyst actions, event
workspace, claim/evidence authoring, stale-safe publication preview and gate, immutable version
timeline, customer watch-profile onboarding, PostGIS audience matching, human alert preview and
release, Postmark/Telegram fan-out with persisted retries, delivery SLA dashboard, public and
contract-bound subscriber portals, full-text archive, immutable daily briefs, claim-extraction
shadow runs and proposals, analyst review/QA, per-language graduation evaluation, CI, and tests.

Ready but not connected: the Airtable importer, Postmark inbound adapter, Telegram desk notifier,
Postmark customer sender, Telegram customer bot, Sentry SDK, Uptime Kuma service, authenticated
console, and optional E5 worker require credentials, model weights, or first-run configuration;
all source candidates require rights and model-processing approval and validation. The generic
PDF extractor still needs source-format profiles and archived-sample suites. Claim extraction is
implemented but remains disabled and in shadow mode until credentials, source approvals, and real
manual-control error baselines exist. R2 storage and production infrastructure are not yet
implemented.
