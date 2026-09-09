# R8 — Stage 3 integrations

## WhatsApp through 360dialog

1. Obtain approval for `eastmed_alert_v1` and `eastmed_correction_v1`; keep their parameter order aligned with `eastmed_pipeline.delivery`.
2. Store the 360dialog API key and webhook Basic-auth credentials in the deployment secret manager. Never put them in a committed `.env` file.
3. Configure the account webhook as `POST /api/v1/webhooks/360dialog` with the same custom Basic-auth header.
4. Record a customer’s explicit WhatsApp opt-in in `/console/alerts`. A phone number without the opt-in control cannot become a destination.
5. Send against a non-production account. Provider acceptance must show `sent`; only a status webhook may move it to `delivered` or `failed`.
6. Inspect unmatched receipts and failures before enabling a live profile. The receipt table stores provider IDs and sanitized status details, never the recipient number.

## Data API

1. Create or upgrade an active account to the Data tier.
2. In `/console/data`, issue the minimum scopes required. Copy the returned secret once; the database stores only an HMAC digest.
3. Test with `Authorization: Bearer <key>` against `/api/v1/data/events`. Use `/api/v1/openapi.json` for the contract.
4. Revoke the key immediately on customer offboarding, suspected exposure, or integration retirement.
5. Keys cannot publish, mutate records, or access desk endpoints. Contract expiry and account-tier downgrade fail closed at authentication time.

## AIS corridor context

1. Set the AISstream API key in the secret manager and enable the dedicated `eastmed-ais-cache` process. The key must never reach the browser.
2. Confirm `/api/v1/portal/ais` reports `live` and positions are limited to the configured corridor boxes.
3. Treat `stale`, `empty`, and `disabled` as degraded context, not as evidence that no vessel is present.
4. The caveat **“AIS-derived; subject to interference/spoofing in conflict areas.”** must remain present in every API response and rendered map.
5. AIS may support physical-plausibility review. It may never solely confirm or deny a claim, and this cache must not grow into routing, dark-fleet, or satellite-tracking infrastructure.
