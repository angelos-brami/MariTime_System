# R10 — Customer delivery provider acceptance

Keep `EASTMED_OUTBOUND_ENABLED=false` and production delivery workers at desired count zero until this
ceremony has passed and a release owner explicitly schedules activation. Test only owned test
addresses/channels; do not send to customers during setup.

## Postmark

1. The owner opens the production Postmark account, creates an `alerts` transactional stream and
   verifies the sending domain with its generated DKIM and Return-Path DNS records.
2. Store only the server token and verified From address in AWS Secrets Manager. Never put the
   account token, server token or DNS private material in evidence or source control.
3. Send one alert and one correction to an owned acceptance mailbox. Preserve provider message ID,
   timestamp, authenticated headers, rendered text/HTML and receipt. Confirm open/link tracking is
   disabled and that a deliberately invalid recipient follows the expected failure path.

## Telegram

1. The owner creates a dedicated bot through BotFather, disables unneeded group/privacy capability,
   adds it only to an owned private test channel and records the channel owner and chat ID.
2. Store the bot token in Secrets Manager. Send alert, correction and over-length-message fixtures.
   Confirm the full-record link, message ID and channel membership. Rotate the token after any
   exposure and repeat acceptance.

## WhatsApp, only if offered

Complete Meta/360dialog business and number verification, execute the required processor/provider
terms, approve the `eastmed_alert_v1` and `eastmed_correction_v1` templates, and store credentials in
Secrets Manager. Use a test phone with separately recorded explicit opt-in. Confirm provider
acceptance is `sent`, not `delivered`, until the signed webhook receipt arrives; verify duplicate
webhooks do not duplicate state and opt-out removes the channel.

## Reliability and shutdown

Run the automated delivery tests and `scripts/run_kill_switch_drill.py` in staging. Prove: one queued
delivery is sent only once; provider failure persists bounded exponential retry state; maximum
attempts ends in failed state; correction reaches every affected channel; duplicate requests and
webhooks are idempotent; and the environment switch plus stopped workers prevents all desk and
customer messages before database access. Disconnect one provider and confirm monitoring/escalation.

Record the immutable evidence IDs in `config/production_evidence.yaml` outside the repository. Two
senior operators verify recipients, no real-customer leakage and kill-switch recovery. Only the
executive launch owner may then schedule a separate outbound activation change. Roll back by setting
the switch false, stopping delivery workers and following R5; provider dashboards are a secondary
stop, not the primary control.
