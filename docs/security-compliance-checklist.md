# Security and compliance release checklist

This is an operational gate, not legal advice. The founder and counsel own the non-code evidence.

## Technical controls

- [ ] Deployment login roles inherit exactly one group from `infra/postgres/least_privilege.sql`; ingestion and analysis roles cannot insert event versions or corrections.
- [ ] Ingestion runs without general egress. The network firewall/proxy permits only counsel-approved registry hosts, object storage, DNS, and required monitoring endpoints.
- [ ] Database, Redis, and object storage have no public listener; TLS is used across untrusted network boundaries.
- [ ] Secrets live in the host/managed secret store, not the repository or image. Rotation dates and owners are recorded.
- [ ] Daily managed-KMS-encrypted backups are off-host, checksum-verified, and covered by the weekly R7/R9 restore drill.
- [ ] Sentry PII collection and tracing remain disabled unless a documented review approves them.
- [ ] The monthly injection and kill-switch drills have current passing immutable records.
- [ ] Dependency, lint, type, migration, test, and production-build jobs pass from a clean checkout.

## Data protection

- [ ] Only necessary B2B contacts are stored; offboarding revokes portal, channel, and API-key access.
- [ ] No email/brief tracking pixels are enabled beyond provider delivery confirmation.
- [ ] A record of processing covers customer contacts, alert delivery, authentication, error monitoring, and support.
- [ ] DPAs and retention/deletion terms are signed for hosting, Clerk, Postmark, 360dialog, Sentry, billing, and any analytics processor.
- [ ] Data-subject request and security-incident contacts are named and tested.

## Commercial and editorial controls

- [ ] Counsel-approved terms include information-support-not-navigational-advice, no warranty of completeness, liability cap, sanctions-information disclaimer, and permitted-use restrictions.
- [ ] E&O/professional-indemnity coverage is bound before live real-time alerts.
- [ ] The full source book rights column has current counsel review before automated retrieval is enabled.
- [ ] Every real-time customer has a documented onboarding, channel test, escalation contact, and explicit WhatsApp opt-in where applicable.

## Required evidence gate

- [ ] A controlled copy of `config/production_evidence.example.yaml` contains real immutable evidence IDs and `scripts/check_launch_evidence.py` passes.
- [ ] The current source preflight, rights register, multilingual gold evaluation and provider acceptance receipts are attached to the launch decision.
- [ ] Two distinct senior operators and the executive launch owner sign the launch decision; no model or service account can fill those roles.
