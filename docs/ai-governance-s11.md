# S11 AI governance and fail-closed controls

This increment adds the control plane required before East Med expands model behavior. It does not
grant automatic publication authority.

## Security properties

- Every desk API request needs the service-to-service desk token and a registered, active identity.
  Production additionally requires a cryptographically verified OIDC JWT; local development may
  use the explicit `trusted_proxy` mode.
- Desk identities have explicit roles. Passwords are never stored in the East Med database.
- The current Basic-auth console always asserts `password`; configuration cannot relabel it as MFA.
- AI system manifests are canonicalized and SHA-256 fingerprinted. Registered versions are
  immutable at the PostgreSQL trigger layer.
- Capability changes are append-only, revisioned, chained to the previous record, actor-linked,
  and audited.
- Missing global control, missing scoped control, a disabled control, a missing fingerprint, or a
  fingerprint mismatch all deny execution.
- Claim extraction is checked in both the scheduler and the worker, closing the enqueue/execution
  race window.
- Any human desk role can stop a capability without MFA. Enabling shadow or assisted operation
  requires an authorized role, MFA, and an expiry from five minutes to 30 days. Missing or expired
  authorization fails closed. Existing indefinite enablements become inoperative after migration.
- `automated` mode is deliberately locked until multi-owner graduation and revocation are
  implemented as first-class records.
- Primary actors on source approvals, triage, claims, evidence linking, lineage review, extraction
  review/QA, API-key administration, watch profiles, briefs, calendar publication, alerts,
  corrections, and event-version publication are derived from the authenticated principal rather
  than trusted from request-body display names.
- High-severity publication, operational correction, and sensitive-claim review use immutable,
  short-lived, content-bound approval records created by the authenticated second person. Request
  body display names are ignored. Approval IDs are one-use where a release is created.
- Sensitive-claim validity is rechecked at publication time, including the exact claim/evidence
  hash, expiry, strong assurance, reviewer separation, and the current active roles of both people.

## Migration

```powershell
.\.venv\Scripts\python.exe -m alembic -c apps/api/alembic.ini upgrade head
```

Migration `20260720_0006` creates the original governance tables. Migrations
`20260721_0007` through `20260721_0010` add:

- `desk_users`
- `ai_system_versions`
- `ai_capability_controls`
- PostgreSQL enums for desk role, authentication assurance, and capability mode
- immutable update/delete triggers for system versions and control history
- immutable `desk_approvals`, one-use release foreign keys, capability expiry, sensitive-claim
  approval binding, and operational-correction approval binding

## Bootstrap the first identity

The bootstrap command works only while `desk_users` is empty. A repeated identical call is
idempotent; it cannot create or alter a second identity.

```powershell
eastmed-bootstrap-desk-user `
  --issuer eastmed-console `
  --subject desk `
  --email administrator@example.com `
  --display-name "East Med Administrator" `
  --role administrator
```

Use the exact subject emitted by the trusted authentication proxy. After bootstrap, additional
desk identities require an authenticated administrator with phishing-resistant assurance.

## Complete system manifest

Registering a system requires all manifest categories, even when a category is explicitly
`not_applicable` or `not_implemented`:

- provider, model family, and exact snapshot;
- inference settings;
- prompt and output-schema hashes;
- tokenizer and context policy;
- preprocessing, security, retrieval, registry, verifier, adjudication, composer, and calibration
  versions.

The API returns the canonical fingerprint. Set that exact value as
`EASTMED_CLAIM_EXTRACTION_SYSTEM_FINGERPRINT`; model execution also requires matching database
controls.

## Two-key execution gate

Claim extraction runs only when both latest controls permit it:

1. `all_model_calls` is `shadow` or `assisted`;
2. `claim_extraction` is `shadow` or `assisted`, references a registered system version, and its
   fingerprint equals the configured running fingerprint.

There is no permissive default. An empty control table means all model execution is disabled.

## Production OIDC contract

Set `EASTMED_DESK_AUTH_MODE=oidc` plus the exact HTTPS issuer, audience, and JWKS URL. Production
startup rejects `trusted_proxy`. JWT validation allows only configured asymmetric algorithms
(`RS256` and `ES256` by default), checks token type, issuer, strict audience, signature, expiry,
issued-at age, and `azp` when present. Header-supplied subject and assurance are ignored in OIDC
mode.

Configure the identity provider's exact signed `acr` values in
`EASTMED_DESK_OIDC_MFA_ACR_VALUES` and
`EASTMED_DESK_OIDC_PHISHING_RESISTANT_ACR_VALUES`. Unrecognized or absent `acr` values resolve to
`password` and therefore cannot perform MFA-gated actions.

## Local trusted proxy contract

The API accepts these headers only alongside the secret desk service token:

- `X-Desk-Token`
- `X-Desk-Auth-Issuer`
- `X-Desk-Subject`
- `X-Desk-Auth-Assurance`: `password`, `mfa`, `phishing_resistant`, or `service`

This mode is development-only. The assertion boundary must be private and protected against direct
public access. The current Basic-auth console intentionally emits only `password` and cannot
perform MFA-gated approvals.

## Authenticated approval workflows

- A senior analyst approves the exact high-severity event-version preview at
  `POST /api/v1/events/{event_id}/publication-approvals`. The publisher then submits that exact
  hash before the 15-minute expiry.
- A distinct senior analyst performs a sensitivity-flagged claim review at
  `POST /api/v1/events/{event_id}/claims/{claim_id}/second-review`. The binding includes the claim
  and its complete evidence package; claim edits or added evidence clear the approval.
- A senior analyst approves an exact operational correction at
  `POST /api/v1/corrections/approvals`. The drafter releases the exact approved hash before expiry.
- Severity 1-2 event releases do not accept decorative sign-off strings. Non-operational
  corrections remain senior-only and derive both actor fields from the authenticated principal.

## Operational stop

Write a new `disabled` control for the affected scope. Do not update or delete old rows. The latest
revision becomes authoritative, while the complete history and its audit record remain available.

For a platform-wide stop, disable `all_model_calls`. The worker independently rechecks the control,
so already queued claim-extraction jobs return a `governance-disabled:<reason>` result without
contacting the model provider.

## Remaining external and later-stage work

- connect the console to the selected production OIDC provider and provision registered desk
  subjects; the local Basic console cannot impersonate production MFA;
- add multi-owner graduation/revocation records before unlocking automated mode;
- run the AI operations console against two live MFA sessions and the selected identity provider;
- accumulate the required sealed gold-set, per-language recall, and 60-90 day shadow evidence
  before considering any capability graduation.

## Operator surfaces added after S11

- `/console` is the unified launch dashboard with source, delivery, approval, identity, AI-control,
  and incident readiness gates.
- `/console/approvals` is the persistent exact-payload approval inbox. Requests survive browser
  sessions and a distinct MFA senior either rejects them or creates the short-lived release hash.
- `/console/operations` exposes the append-only capability ledger, emergency stop, bounded
  shadow/assisted authorization, exact system fingerprints, and immutable AI incident chronology.
- High/critical incident creation atomically writes a `disabled` capability revision. Resolution
  requires an MFA-authenticated control owner; no incident history can be updated or deleted.
- Portal reliability receipts bind each immutable publication to its exact claims, evidence,
  sentence map, policy, model versions, actors, approval, and content hash.
