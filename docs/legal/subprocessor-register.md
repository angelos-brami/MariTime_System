# Proposed subprocessor register

**DRAFT — every row is pending contract, privacy and transfer review unless a signed evidence ID is
added. Do not infer approval from “proposed use”.**

| Provider | Proposed use | Data categories | Required decision/evidence | Status |
|---|---|---|---|---|
| Amazon Web Services | EU-region hosting, database, cache, storage, secrets, logs | service and customer operational data | region, DPA, transfer terms, retention, shared-responsibility review | pending |
| Clerk | workforce/customer authentication and MFA | identity, email, session/security metadata | production DPA, locations, MFA/passkey policy, deletion | pending |
| Anthropic | shadow claim extraction | rights-approved source text; no customer contacts by design | commercial/API terms, DPA, retention/training controls, region/transfer review | pending |
| Postmark | transactional email delivery | recipient, subject/body, delivery metadata | verified domain, DPA, retention, message-stream approval | pending |
| Telegram | optional customer/desk delivery | chat identifier and alert text | role mapping, terms/privacy review, customer disclosure/consent | pending |
| 360dialog / Meta WhatsApp | optional template alert delivery | phone, template text, delivery metadata | both provider contracts, business verification, template and opt-in evidence | pending |
| Sentry | error reporting with PII/tracing disabled by default | pseudonymous technical error metadata | DPA, region, scrubbing test, retention, transfer review | pending |

Owner: [privacy lead]. Review cadence: before activation, on material vendor/processing changes and
at least annually. Customer notice date, objection window, signed agreement IDs and transfer
assessment IDs must be added to the controlled register outside source control.
