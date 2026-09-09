# R5 — Outbound kill switch

1. The founder or senior analyst may disable outbound delivery when integrity is uncertain.
2. Set `EASTMED_OUTBOUND_ENABLED=false` at the deployment control plane and stop delivery workers.
3. Confirm that email, Telegram, WhatsApp, board publication, and alert queues cannot fan out.
4. Continue internal ingestion and review only if the incident does not compromise them.
5. Reactivation requires documented cause, scope review, and a two-person check.
6. Test this procedure monthly in staging and record elapsed shutdown time.

Use `python scripts/run_kill_switch_drill.py --record` for the coded evidence pass. It must complete before any outbound worker is restarted.

The foundation defaults outbound delivery to disabled.
